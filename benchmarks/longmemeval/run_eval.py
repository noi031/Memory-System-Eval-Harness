#!/usr/bin/env python3
"""LongMemEval benchmark evaluation script.

流程:
  1. 逐题隔离导入: 每题各自导入自己的 haystack_sessions (per-question 隔离)
  2. 逐题 QA: search EchoMem -> build prompt -> LLM answer
  3. 官方 accuracy 评测: 按题型用 LLM judge yes/no

用法见 docs/usage.md
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins import load_agent_plugin
from benchmarks.longmemeval.dataset import load_dataset
from benchmarks.longmemeval.evaluate import evaluate_longmemeval
from benchmarks.longmemeval.import_memory import import_longmemeval_memory
from benchmarks.longmemeval.parallel import run_parallel
from benchmarks.longmemeval.qa import build_qa_tasks, run_longmemeval_qa
from benchmarks.longmemeval.reporting import build_summary
from benchmarks.longmemeval.selection import (
    parse_question_ids,
    select_jobs_and_plans,
)
from shared.dataset_io import resolve_dataset_path
from shared.eval_base import (
    EvalRun,
    add_agent_plugin_args,
    add_eval_args,
    build_config_from_args,
    results_root_for,
    validate_eval_config,
)
from shared.import_guard import require_complete_imports
from shared.llm_client import LLMClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LongMemEval benchmark evaluation")
    parser.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dataset", default="", help="LongMemEval JSON 数据集路径 (不指定则自动查找或下载)")
    parser.add_argument("--sample", default="all", help="筛选 sample (all 或 index/id)")
    parser.add_argument("--questions", type=int, default=0, help="限制 QA 数量 (0=all)")
    parser.add_argument(
        "--question-ids",
        default="",
        help="Comma-separated question/native/sample ids",
    )
    parser.add_argument("--random-count", type=int, default=0)
    parser.add_argument("--random-seed", type=int, default=30)
    parallel = parser.add_argument_group("Parallel execution")
    parallel.add_argument(
        "--parallel-shards",
        type=int,
        default=1,
        help="Split selected questions across isolated CLI shard processes",
    )
    parallel.add_argument(
        "--parallel-workers",
        type=int,
        default=2,
        help="Maximum number of shard processes to run concurrently",
    )
    parallel.add_argument(
        "--parallel-dry-run",
        action="store_true",
        help="Write the shard manifest without starting evaluation processes",
    )
    add_agent_plugin_args(parser, default_plugin="vikingbot")
    add_eval_args(parser)
    # judge 参数
    g = parser.add_argument_group("Judge")
    g.add_argument("--judge-model", default=os.getenv("JUDGE_MODEL", ""), help="Judge LLM 模型名 (默认同 --llm-model)")
    g.add_argument("--judge-api-key", default=os.getenv("JUDGE_TOKEN", ""), help="Judge API key (默认同 --llm-api-key)")
    g.add_argument("--judge-base-url", default=os.getenv("JUDGE_BASE_URL", ""), help="Judge base URL (默认同 --llm-base-url)")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = build_config_from_args(args)
    config.sample_filter = args.sample
    config.question_limit = args.questions
    validate_eval_config(config)
    if args.random_count < 0:
        raise ValueError("random count must be >= 0")
    if args.parallel_shards < 1 or args.parallel_workers < 1:
        raise ValueError("parallel shards and workers must be >= 1")
    dataset_path = resolve_dataset_path("longmemeval", args.dataset)
    config.dataset_path = dataset_path
    question_ids = parse_question_ids(args.question_ids)
    if args.check:
        jobs, plans = load_dataset(dataset_path, sample_filter=args.sample)
        jobs, plans = select_jobs_and_plans(
            jobs,
            plans,
            question_ids=question_ids,
            limit=config.question_limit,
            random_count=args.random_count,
            random_seed=args.random_seed,
        )
        if not jobs or not plans:
            raise ValueError("dataset/sample filter produced no LongMemEval questions")
        print(
            f"[check] OK benchmark=longmemeval dataset={dataset_path} questions={len(jobs)}"
        )
        return
    if args.parallel_shards > 1:
        jobs, plans = load_dataset(dataset_path, sample_filter=args.sample)
        jobs, plans = select_jobs_and_plans(
            jobs,
            plans,
            question_ids=question_ids,
            limit=config.question_limit,
            random_count=args.random_count,
            random_seed=args.random_seed,
        )
        if not jobs or not plans:
            raise ValueError(
                "dataset/sample filter produced no LongMemEval questions"
            )
        root = results_root_for(Path(__file__).parent, args.out_dir)
        output_dir = root / (
            "parallel_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        )
        summary = run_parallel(
            argv=sys.argv[1:],
            question_ids=[job.question_id for job in jobs],
            output_dir=output_dir,
            shard_count=args.parallel_shards,
            worker_count=args.parallel_workers,
            dry_run=args.parallel_dry_run,
        )
        if not args.parallel_dry_run and summary["status"] != "completed":
            raise SystemExit(2)
        return

    run = EvalRun(
        benchmark_name="longmemeval",
        results_root=results_root_for(Path(__file__).parent, args.out_dir),
        config=config,
        echomem_log_dir=getattr(args, "echomem_log_dir", ""),
    )
    log = run.logger

    # 加载数据集
    log.info("加载 LongMemEval 数据集: %s", dataset_path)
    jobs, plans = load_dataset(dataset_path, sample_filter=args.sample)
    log.info("共 %d 个问题", len(jobs))

    jobs, plans = select_jobs_and_plans(
        jobs,
        plans,
        question_ids=question_ids,
        limit=config.question_limit,
        random_count=args.random_count,
        random_seed=args.random_seed,
    )
    if question_ids:
        log.info("按 question id 选择 %d 题", len(jobs))
    elif args.random_count > 0:
        log.info("随机选择 %d 题 (seed=%d)", len(jobs), args.random_seed)
    elif config.question_limit > 0:
        log.info("限制 QA 数量为 %d", len(jobs))
    if not jobs or not plans:
        message = "dataset/sample filter produced no LongMemEval questions"
        run.save_summary({
            "status": "failed",
            "phase": "dataset",
            "dataset": dataset_path,
            "sample_filter": args.sample,
            "error": message,
        })
        raise ValueError(message)

    # 加载 agent 插件 (在记忆操作之前, setup 内部创建 memory_client)
    agent_config = {**vars(args), "benchmark_name": "longmemeval", "run_id": run.result_dir.name}
    agent_plugin = load_agent_plugin(args.agent_plugin, agent_config)
    echomem = agent_plugin.memory_client
    echomem.health()
    evaluation_identity = {
        "mode": "reused" if getattr(args, "reuse_memory_account", True) else "isolated",
        "retention": "existing" if getattr(args, "reuse_memory_account", True) else (
            "kept" if getattr(args, "keep_memory_account", False) else "ephemeral"
        ),
        "tenant_id": echomem.account,
        "user_id": echomem.user_id,
    }
    log.info(
        "Memory identity: %s tenant=%s user=%s",
        evaluation_identity.get("mode", "none"),
        evaluation_identity.get("tenant_id", ""),
        evaluation_identity.get("user_id", ""),
    )

    # -- 阶段 1: 逐题隔离导入或复用已有记忆 --
    log.info("=" * 60)
    reuse_existing_memory = getattr(args, "reuse_memory_account", True)
    if reuse_existing_memory:
        log.info("阶段 1: 跳过导入，复用 account-wide 已有记忆")
    else:
        log.info("阶段 1: 逐题导入 haystack sessions (共 %d 题)", len(plans))
    import_report = import_longmemeval_memory(
        jobs,
        plans,
        echomem,
        config,
        run.result_dir,
        log,
        reuse_existing_memory=reuse_existing_memory,
    )
    if reuse_existing_memory:
        log.info("已有记忆复用模式：未执行任何写入或 commit")
    else:
        log.info(
            "导入完成: %d/%d 成功",
            import_report.completed,
            import_report.total,
        )
        try:
            require_complete_imports(
                import_report.rows,
                allow_incomplete=args.allow_incomplete_imports,
            )
        except RuntimeError as exc:
            run.save_summary({
                "status": "failed",
                "phase": "import",
                "dataset": dataset_path,
                "import_ok": import_report.completed,
                "import_total": import_report.total,
                "error": str(exc),
            })
            log.error("%s", exc)
            raise SystemExit(2) from exc

    # -- 阶段 2: 逐题 QA --
    log.info("=" * 60)
    log.info("阶段 2: QA (共 %d 题, 并发=%d)", len(jobs), config.concurrency)

    qa_tasks = build_qa_tasks(
        jobs,
        import_report.question_to_session,
        config,
        agent_id=echomem.agent_id,
    )
    qa_results = run_longmemeval_qa(
        qa_tasks,
        agent_plugin,
        config,
        run.result_dir,
        log,
    )

    # -- 阶段 3: 官方 accuracy 评测 --
    log.info("=" * 60)
    log.info("阶段 3: LLM Judge (yes/no per question type)")

    judge_llm = LLMClient(
        base_url=args.judge_base_url or config.llm_base_url,
        api_key=args.judge_api_key or config.llm_api_key,
        model=args.judge_model or config.llm_model,
        temperature=0.0,
        max_tokens=256,
        timeout_s=config.llm_timeout_s,
        max_retries=config.llm_retries,
    )

    evaluation_report = evaluate_longmemeval(
        qa_results,
        jobs,
        judge_llm,
        run.result_dir,
        log,
    )
    log.info(
        "Judge 完成: %d/%d correct, accuracy=%.2f%%",
        evaluation_report.correct,
        evaluation_report.graded,
        evaluation_report.overall_accuracy * 100,
    )
    for task_type, stats in evaluation_report.per_type.items():
        log.info(
            "  %s: %d/%d (%.1f%%)",
            task_type,
            stats["correct"],
            stats["total"],
            stats["accuracy"] * 100,
        )

    # 收集 EchoMem 日志
    run.collect_echomem_logs()

    summary = build_summary(
        dataset_path=dataset_path,
        jobs=jobs,
        import_report=import_report,
        reuse_existing_memory=reuse_existing_memory,
        qa_results=qa_results,
        evaluation_report=evaluation_report,
        evaluation_identity=evaluation_identity,
    )
    run.save_summary(summary)

    if summary["status"] != "completed":
        log.error("评测包含运行错误，结果不能作为正式分数")
        raise SystemExit(2)

    log.info("=" * 60)
    log.info("评测完成! 结果目录: %s", run.result_dir)
    log.info(
        "Accuracy: %.2f%% (%d/%d)",
        evaluation_report.overall_accuracy * 100,
        evaluation_report.correct,
        evaluation_report.graded,
    )
    agent_plugin.teardown()


if __name__ == "__main__":
    main()

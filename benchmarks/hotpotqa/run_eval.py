#!/usr/bin/env python3
"""HotpotQA benchmark evaluation script.

流程:
  1. 导入记忆 (两种模式):
     - per_question (默认): 每题各自导入自己的 context passages
     - global: 所有题的 passages 合并导入一个共享 session
  2. 逐题 QA: search EchoMem -> build prompt -> LLM answer
  3. 官方 F1/EM 评测 (无需 LLM judge)

用法见 docs/usage.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins import load_agent_plugin
from benchmarks.hotpotqa.dataset import load_dataset
from benchmarks.hotpotqa.evaluate import evaluate_hotpotqa, load_references
from benchmarks.hotpotqa.import_memory import import_hotpotqa_memory
from benchmarks.hotpotqa.qa import build_qa_tasks, run_hotpotqa_qa
from benchmarks.hotpotqa.reporting import build_summary
from benchmarks.hotpotqa.selection import (
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HotpotQA benchmark evaluation")
    parser.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dataset", default="", help="HotpotQA JSON 数据集路径 (不指定则自动查找或下载)")
    parser.add_argument("--sample", default="all", help="筛选 sample (all 或 sample index/id)")
    parser.add_argument("--questions", type=int, default=0, help="限制 QA 数量 (0=all)")
    parser.add_argument(
        "--question-ids",
        default="",
        help="Comma-separated question/native/sample ids",
    )
    parser.add_argument("--import-mode", default="per_question",
                        choices=["per_question", "global"],
                        help="导入模式: per_question=每题各自导入; global=合并共享 session")
    add_agent_plugin_args(parser, default_plugin="vikingbot")
    add_eval_args(parser)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = build_config_from_args(args)
    config.sample_filter = args.sample
    config.question_limit = args.questions
    validate_eval_config(config)
    dataset_path = resolve_dataset_path("hotpotqa", args.dataset)
    config.dataset_path = dataset_path
    question_ids = parse_question_ids(args.question_ids)
    if args.check:
        jobs, plans = load_dataset(dataset_path, sample_filter=args.sample)
        jobs, plans = select_jobs_and_plans(
            jobs,
            plans,
            question_ids=question_ids,
            limit=config.question_limit,
        )
        if not jobs or not plans:
            raise ValueError("dataset/sample filter produced no HotpotQA questions")
        print(
            f"[check] OK benchmark=hotpotqa dataset={dataset_path} questions={len(jobs)}"
        )
        return

    run = EvalRun(
        benchmark_name="hotpotqa",
        results_root=results_root_for(Path(__file__).parent, args.out_dir),
        config=config,
        echomem_log_dir=getattr(args, "echomem_log_dir", ""),
    )
    log = run.logger

    # 加载数据集
    log.info("加载 HotpotQA 数据集: %s", dataset_path)
    jobs, plans = load_dataset(dataset_path, sample_filter=args.sample)
    log.info("共 %d 个问题", len(jobs))

    jobs, plans = select_jobs_and_plans(
        jobs,
        plans,
        question_ids=question_ids,
        limit=config.question_limit,
    )
    if question_ids:
        log.info("按 question id 选择 %d 题", len(jobs))
    elif config.question_limit > 0:
        log.info("限制 QA 数量为 %d", len(jobs))
    if not jobs or not plans:
        message = "dataset/sample filter produced no HotpotQA questions"
        run.save_summary({
            "status": "failed",
            "phase": "dataset",
            "dataset": dataset_path,
            "sample_filter": args.sample,
            "error": message,
        })
        raise ValueError(message)

    # 加载 agent 插件 (在记忆操作之前, setup 内部创建 memory_client)
    agent_config = {**vars(args), "benchmark_name": "hotpotqa", "run_id": run.result_dir.name}
    agent_plugin = load_agent_plugin(args.agent_plugin, agent_config)
    echomem = agent_plugin.memory_client
    echomem.health()
    evaluation_identity = {
        "mode": "fresh",
        "tenant_id": echomem.account,
        "user_id": echomem.user_id,
    }
    log.info(
        "Memory identity: %s tenant=%s user=%s",
        evaluation_identity.get("mode", "none"),
        evaluation_identity.get("tenant_id", ""),
        evaluation_identity.get("user_id", ""),
    )

    # -- 阶段 1: 导入记忆 --
    log.info("=" * 60)
    log.info("阶段 1: 导入记忆 (模式=%s)", args.import_mode)
    import_report = import_hotpotqa_memory(
        jobs,
        plans,
        echomem,
        config,
        run.result_dir,
        log,
        import_mode=args.import_mode,
    )
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
    qa_results = run_hotpotqa_qa(
        qa_tasks,
        agent_plugin,
        config,
        run.result_dir,
        log,
    )

    # -- 阶段 3: 官方 answer/supporting-fact/joint 评测 --
    log.info("=" * 60)
    log.info("阶段 3: HotpotQA 官方指标")
    references = load_references(Path(dataset_path))
    evaluation_report = evaluate_hotpotqa(
        qa_results,
        references,
        run.result_dir,
    )
    log.info(
        "评测完成: answer F1=%.4f EM=%.4f, support F1=%.4f EM=%.4f, "
        "joint F1=%.4f EM=%.4f",
        evaluation_report.answer_f1,
        evaluation_report.answer_em,
        evaluation_report.supporting_facts_f1,
        evaluation_report.supporting_facts_em,
        evaluation_report.joint_f1,
        evaluation_report.joint_em,
    )

    # 收集 EchoMem 日志
    run.collect_echomem_logs()

    summary = build_summary(
        dataset_path=dataset_path,
        import_mode=args.import_mode,
        jobs=jobs,
        import_report=import_report,
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
        "answer_F1=%.4f answer_EM=%.4f joint_F1=%.4f joint_EM=%.4f "
        "(%d questions)",
        evaluation_report.answer_f1,
        evaluation_report.answer_em,
        evaluation_report.joint_f1,
        evaluation_report.joint_em,
        len(qa_results),
    )
    agent_plugin.teardown()


if __name__ == "__main__":
    main()

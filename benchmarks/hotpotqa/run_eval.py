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
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tqdm import tqdm

from shared.dataset import load_hotpotqa, resolve_dataset_path
from shared.eval_base import EvalConfig, EvalRun, add_eval_args, add_agent_plugin_args, build_config_from_args
from shared.judge import answer_f1_em
from shared.benchmark_runner import run_qa_phase
from agents import load_agent_plugin


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HotpotQA benchmark evaluation")
    parser.add_argument("--dataset", default="", help="HotpotQA JSON 数据集路径 (不指定则自动查找或下载)")
    parser.add_argument("--sample", default="all", help="筛选 sample (all 或 sample index/id)")
    parser.add_argument("--questions", default="0", help="限制 QA 数量 (0=all)")
    parser.add_argument("--agent-plugin", default="baseline_mem",
                        help="Agent 插件名 (baseline_mem / echo_agent / bare_llm)")
    parser.add_argument("--import-mode", default="per_question",
                        choices=["per_question", "global"],
                        help="导入模式: per_question=每题各自导入; global=合并共享 session")
    add_eval_args(parser)
    add_agent_plugin_args(parser)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = build_config_from_args(args)
    dataset_path = resolve_dataset_path("hotpotqa", args.dataset)
    config.dataset_path = dataset_path
    config.sample_filter = args.sample
    config.question_limit = int(args.questions)

    run = EvalRun(
        benchmark_name="hotpotqa",
        results_root=Path(__file__).parent / "results",
        config=config,
        run_args={k: v for k, v in vars(args).items() if not k.startswith("_")},
    )
    log = run.logger

    # 加载数据集
    log.info("加载 HotpotQA 数据集: %s", dataset_path)
    jobs, plans = load_hotpotqa(dataset_path, sample_filter=args.sample)
    log.info("共 %d 个问题", len(jobs))

    if config.question_limit > 0:
        jobs = jobs[: config.question_limit]
        log.info("限制 QA 数量为 %d", len(jobs))

    # 加载 agent 插件 (load_agent_plugin 内部调 setup, 完成客户端初始化)
    config_dict = {k: v for k, v in vars(args).items()}
    plugin = load_agent_plugin(args.agent_plugin, config_dict)
    args.echomem_auth_key = config_dict.get("echomem_auth_key", "")
    log.info("agent_plugin=%s", args.agent_plugin)

    # -- 阶段 1: 导入记忆 --
    log.info("=" * 60)
    log.info("阶段 1: 导入记忆 (模式=%s)", args.import_mode)

    question_to_session: dict[str, str] = {}  # question_id -> echomem session_id
    import_results: list[dict] = []

    if args.import_mode == "global":
        # 所有 passages 合并到一个共享 session
        log.info("合并所有 context 到共享 session...")
        all_memories = []
        for plan in plans:
            for ev in plan.get("events", []):
                if ev.get("text"):
                    all_memories.append({"text": ev.get("text", ""), "time": ev.get("time", "")})
        start = time.monotonic()
        shared_sid = plugin.inject_memories(all_memories)
        elapsed = time.monotonic() - start
        log.info("共导入 %d 条 passage (%.1fs)", len(all_memories), elapsed)
        import_results.append({
            "session_id": shared_sid, "status": "completed",
            "messages": len(all_memories), "elapsed_s": round(elapsed, 1),
        })
        for job in jobs:
            question_to_session[job.question_id] = shared_sid

    else:
        # per_question: 每题各自导入
        for job, plan in tqdm(list(zip(jobs, plans)), desc="导入记忆", unit="q"):
            try:
                memories = [
                    {"text": ev.get("text", ""), "time": ev.get("time", "")}
                    for ev in plan.get("events", []) if ev.get("text")
                ]
                start = time.monotonic()
                sid = plugin.inject_memories(memories)
                elapsed = time.monotonic() - start
                question_to_session[job.question_id] = sid
                import_results.append({
                    "question_id": job.question_id,
                    "session_id": sid,
                    "status": "completed",
                    "messages": len(memories),
                    "elapsed_s": round(elapsed, 1),
                })
            except Exception as e:
                log.error("  导入 %s 失败: %s", job.question_id, e)
                import_results.append({"question_id": job.question_id, "status": "error", "error": str(e)})

    ok_imports = sum(1 for r in import_results if r["status"] == "completed")
    log.info("导入完成: %d/%d 成功", ok_imports, len(import_results))

    # 保存导入结果
    import_csv = run.result_dir / "import_results.csv"
    with open(import_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["question_id", "session_id", "status", "messages", "elapsed_s", "error"])
        writer.writeheader()
        for r in import_results:
            writer.writerow(r)

    # -- 阶段 2: 逐题 QA --
    log.info("=" * 60)
    log.info("阶段 2: QA (共 %d 题, 并发=%d)", len(jobs), config.concurrency)

    def resolve_session(job):
        return question_to_session.get(job.question_id, "")

    qa_results = run_qa_phase(plugin, jobs, resolve_session, config.concurrency, log)

    # 保存 QA 结果
    qa_csv = run.result_dir / "qa_results.csv"
    with open(qa_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "question_id", "question", "answer", "response",
            "retrieval_error", "llm_error", "elapsed_s",
            "prompt_tokens", "completion_tokens", "num_retrieved",
        ])
        writer.writeheader()
        for r in qa_results:
            writer.writerow(r.to_csv_row())
    log.info("QA 结果已保存: %s", qa_csv)

    # -- 阶段 3: F1/EM 评测 --
    log.info("=" * 60)
    log.info("阶段 3: F1/EM 评测 (官方指标)")

    eval_results: list[dict] = []
    f1_scores: list[float] = []
    em_scores: list[float] = []

    for r in tqdm(qa_results, desc="评测", unit="q"):
        f1, em = answer_f1_em(r.response, r.answer)
        f1_scores.append(f1)
        em_scores.append(em)
        eval_results.append({
            "question_id": r.question_id,
            "question": r.question,
            "answer": r.answer,
            "response": r.response,
            "f1": round(f1, 4),
            "em": em,
        })

    # 保存评测结果
    eval_csv = run.result_dir / "eval_results.csv"
    with open(eval_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["question_id", "question", "answer", "response", "f1", "em"])
        writer.writeheader()
        for r in eval_results:
            writer.writerow(r)

    avg_f1 = sum(f1_scores) / max(len(f1_scores), 1)
    avg_em = sum(em_scores) / max(len(em_scores), 1)
    log.info("F1/EM 完成: avg_F1=%.4f, avg_EM=%.4f", avg_f1, avg_em)

    # 收集 EchoMem 日志
    run.collect_echomem_logs()

    # 保存 summary
    summary = {
        "benchmark": "hotpotqa",
        "dataset": dataset_path,
        "import_mode": args.import_mode,
        "total_questions": len(jobs),
        "import_ok": ok_imports,
        "import_total": len(import_results),
        "qa_count": len(qa_results),
        "qa_errors": sum(1 for r in qa_results if r.llm_error),
        "retrieval_errors": sum(1 for r in qa_results if r.retrieval_error),
        "avg_f1": round(avg_f1, 4),
        "avg_em": round(avg_em, 4),
        "avg_qa_elapsed_s": round(sum(r.elapsed_s for r in qa_results) / max(len(qa_results), 1), 2),
        "total_prompt_tokens": sum(r.prompt_tokens for r in qa_results),
        "total_completion_tokens": sum(r.completion_tokens for r in qa_results),
    }
    run.save_summary(summary)

    log.info("=" * 60)
    log.info("评测完成! 结果目录: %s", run.result_dir)
    log.info("avg_F1=%.4f  avg_EM=%.4f  (%d questions)", avg_f1, avg_em, len(qa_results))


if __name__ == "__main__":
    main()

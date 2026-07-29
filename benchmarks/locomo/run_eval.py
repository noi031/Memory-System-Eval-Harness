#!/usr/bin/env python3
"""LoCoMo benchmark evaluation script.

流程:
  1. 集中导入所有 sample 的 conversation sessions 到 EchoMem (open -> add_messages -> commit -> poll)
  2. 逐题 QA: search EchoMem -> build prompt -> LLM answer (仅检索不写入)
  3. LLM judge: CORRECT / WRONG

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

# 确保能 import shared 包
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tqdm import tqdm

from shared.dataset import load_locomo, locomo_session_batches, resolve_dataset_path
from shared.eval_base import EvalConfig, EvalRun, add_eval_args, add_agent_plugin_args, build_config_from_args
from shared.llm_client import LLMClient
from shared.judge import locomo_judge
from shared.benchmark_runner import run_qa_phase
from agents import load_agent_plugin


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LoCoMo benchmark evaluation")
    parser.add_argument("--dataset", default="", help="LoCoMo JSON 数据集路径 (不指定则自动查找或下载)")
    parser.add_argument("--sample", default="all", help="筛选 sample (all 或 sample_id)")
    parser.add_argument("--questions", default="0", help="限制 QA 数量 (0=all)")
    parser.add_argument("--agent-plugin", default="baseline_mem",
                        help="Agent 插件名 (baseline_mem / echo_agent / bare_llm)")
    add_eval_args(parser)
    add_agent_plugin_args(parser)
    # judge 参数
    g = parser.add_argument_group("Judge")
    g.add_argument("--judge-model", default="", help="Judge LLM 模型名 (默认同 --llm-model)")
    g.add_argument("--judge-api-key", default="", help="Judge API key (默认同 --llm-api-key)")
    g.add_argument("--judge-base-url", default="", help="Judge base URL (默认同 --llm-base-url)")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = build_config_from_args(args)
    dataset_path = resolve_dataset_path("locomo", args.dataset)
    config.dataset_path = dataset_path
    config.sample_filter = args.sample
    config.question_limit = int(args.questions)

    # 创建评测运行
    run = EvalRun(
        benchmark_name="locomo",
        results_root=Path(__file__).parent / "results",
        config=config,
        run_args={k: v for k, v in vars(args).items() if not k.startswith("_")},
    )
    log = run.logger

    # 加载数据集
    log.info("加载 LoCoMo 数据集: %s", dataset_path)
    jobs, plans = load_locomo(dataset_path, sample_filter=args.sample)
    log.info("共 %d 个 sample, %d 个 QA 问题", len(plans), len(jobs))

    if config.question_limit > 0:
        jobs = jobs[: config.question_limit]
        log.info("限制 QA 数量为 %d", len(jobs))

    # 加载 agent 插件 (load_agent_plugin 内部调 setup, 完成客户端初始化)
    config_dict = {k: v for k, v in vars(args).items()}
    plugin = load_agent_plugin(args.agent_plugin, config_dict)
    args.echomem_auth_key = config_dict.get("echomem_auth_key", "")
    log.info("agent_plugin=%s", args.agent_plugin)

    # -- 阶段 1: 集中导入所有 session --
    log.info("=" * 60)
    log.info("阶段 1: 导入记忆 (共 %d 个 sample)", len(plans))

    import_results: list[dict] = []
    sample_to_session_ids: dict[str, list[str]] = {}

    for plan in tqdm(plans, desc="导入记忆", unit="sample"):
        sample_id = plan["sample_id"]
        try:
            memories = [
                {"text": ev.get("text", ""), "time": ev.get("time", "")}
                for ev in plan.get("events", []) if ev.get("text")
            ]
            start = time.monotonic()
            sid = plugin.inject_memories(memories)
            elapsed = time.monotonic() - start
            sample_to_session_ids[sample_id] = [sid]
            import_results.append({
                "sample_id": sample_id,
                "session_id": sid,
                "archive_id": "",
                "status": "completed",
                "elapsed_s": round(elapsed, 1),
                "message_count": len(memories),
            })
            log.info("  sample %s: completed (%.1fs, %d msgs)",
                     sample_id, elapsed, len(memories))
        except Exception as e:
            log.error("  sample %s 导入失败: %s", sample_id, e)
            import_results.append({
                "sample_id": sample_id,
                "session_id": "",
                "archive_id": "",
                "status": "error",
                "error": str(e),
            })

    # 保存导入结果
    import_csv = run.result_dir / "import_results.csv"
    with open(import_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["sample_id", "session_id", "archive_id", "status", "elapsed_s", "message_count", "error"])
        writer.writeheader()
        for r in import_results:
            writer.writerow(r)
    log.info("导入结果已保存: %s", import_csv)

    ok_imports = sum(1 for r in import_results if r["status"] == "completed")
    log.info("导入完成: %d/%d 成功", ok_imports, len(import_results))

    # -- 阶段 2: 逐题 QA --
    log.info("=" * 60)
    log.info("阶段 2: QA (共 %d 题, 并发=%d)", len(jobs), config.concurrency)

    def resolve_session(job):
        session_ids = sample_to_session_ids.get(job.sample_id, [])
        return session_ids[0] if session_ids else ""

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

    # -- 阶段 3: LLM Judge --
    log.info("=" * 60)
    log.info("阶段 3: Judge (共 %d 题)", len(qa_results))

    judge_llm = LLMClient(
        base_url=args.judge_base_url or getattr(args, "llm_base_url", ""),
        api_key=args.judge_api_key or getattr(args, "llm_api_key", ""),
        model=args.judge_model or getattr(args, "llm_model", "doubao-seed-2.0-pro"),
        temperature=0.0,
        max_tokens=256,
        timeout_s=getattr(args, "llm_timeout_s", 120.0),
        max_retries=getattr(args, "llm_retries", 3),
    )

    judge_results: list[dict] = []
    for r in tqdm(qa_results, desc="Judge", unit="q"):
        verdict, reasoning = locomo_judge(judge_llm, r.question, r.answer, r.response)
        judge_results.append({
            "question_id": r.question_id,
            "question": r.question,
            "answer": r.answer,
            "response": r.response,
            "verdict": verdict,
            "reasoning": reasoning,
        })

    # 保存 judge 结果
    judge_csv = run.result_dir / "judge_results.csv"
    with open(judge_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["question_id", "question", "answer", "response", "verdict", "reasoning"])
        writer.writeheader()
        for r in judge_results:
            writer.writerow(r)

    # 统计
    correct = sum(1 for r in judge_results if r["verdict"] == "CORRECT")
    wrong = sum(1 for r in judge_results if r["verdict"] == "WRONG")
    accuracy = correct / len(judge_results) if judge_results else 0.0

    log.info("Judge 完成: %d CORRECT, %d WRONG, accuracy=%.2f%%", correct, wrong, accuracy * 100)

    # 收集 EchoMem 日志
    run.collect_echomem_logs()

    # 保存 summary
    summary = {
        "benchmark": "locomo",
        "dataset": dataset_path,
        "sample_filter": args.sample,
        "total_samples": len(plans),
        "total_questions": len(jobs),
        "import_ok": ok_imports,
        "import_total": len(import_results),
        "qa_count": len(qa_results),
        "qa_errors": sum(1 for r in qa_results if r.llm_error),
        "retrieval_errors": sum(1 for r in qa_results if r.retrieval_error),
        "judge_correct": correct,
        "judge_wrong": wrong,
        "accuracy": round(accuracy, 4),
        "avg_qa_elapsed_s": round(sum(r.elapsed_s for r in qa_results) / max(len(qa_results), 1), 2),
        "total_prompt_tokens": sum(r.prompt_tokens for r in qa_results),
        "total_completion_tokens": sum(r.completion_tokens for r in qa_results),
    }
    run.save_summary(summary)

    log.info("=" * 60)
    log.info("评测完成! 结果目录: %s", run.result_dir)
    log.info("Accuracy: %.2f%% (%d/%d)", accuracy * 100, correct, len(judge_results))


if __name__ == "__main__":
    main()

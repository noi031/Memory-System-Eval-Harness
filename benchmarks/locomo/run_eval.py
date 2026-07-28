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
from shared.echomem_client import EchoMemClient
from shared.eval_base import EvalConfig, EvalRun, add_echomem_args, add_llm_args, add_eval_args, build_config_from_args
from shared.llm_client import LLMClient
from shared.qa import answer_one_question, QAResult, build_qa_prompt
from shared.judge import locomo_judge


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LoCoMo benchmark evaluation")
    parser.add_argument("--dataset", default="", help="LoCoMo JSON 数据集路径 (不指定则自动查找或下载)")
    parser.add_argument("--sample", default="all", help="筛选 sample (all 或 sample_id)")
    parser.add_argument("--questions", default="0", help="限制 QA 数量 (0=all)")
    # 共享参数
    add_echomem_args(parser)
    add_llm_args(parser)
    add_eval_args(parser)
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
    )
    log = run.logger

    # 加载数据集
    log.info("加载 LoCoMo 数据集: %s", dataset_path)
    jobs, plans = load_locomo(dataset_path, sample_filter=args.sample)
    log.info("共 %d 个 sample, %d 个 QA 问题", len(plans), len(jobs))

    if config.question_limit > 0:
        jobs = jobs[: config.question_limit]
        log.info("限制 QA 数量为 %d", len(jobs))

    # 创建 EchoMem 客户端
    echomem = EchoMemClient(
        base_url=config.echomem_url,
        auth_key=config.echomem_auth_key,
        account=config.account,
        user_id=config.user_id,
        agent_id=config.agent_id,
        workspace=config.workspace,
        timeout_s=60.0,
        max_retries=3,
    )

    # 创建 LLM 客户端
    llm = LLMClient(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        temperature=config.llm_temperature,
        max_tokens=config.llm_max_tokens,
        timeout_s=config.llm_timeout_s,
        max_retries=config.llm_retries,
    )

    # -- 阶段 1: 集中导入所有 session --
    log.info("=" * 60)
    log.info("阶段 1: 导入记忆 (共 %d 个 sample)", len(plans))

    import_results: list[dict] = []
    sample_to_session_ids: dict[str, list[str]] = {}

    for plan in tqdm(plans, desc="导入记忆", unit="sample"):
        sample_id = plan["sample_id"]
        # 从原始数据中重新构建 session batches
        # plan 中已有 events, 但我们需要按 session 分组
        # 通过 sample_id 找到原始 sample 数据
        # 这里直接用 plan 中的数据构建批次
        # plan 包含 events 列表, 每个 event 有 {time, text}
        # 我们将所有 events 作为一个 session 导入
        try:
            sid = echomem.open_session(title=f"locomo_{sample_id}")
            sample_to_session_ids[sample_id] = [sid]

            # 添加消息
            events = plan.get("events", [])
            for ev in tqdm(events, desc=f"  {sample_id}", unit="msg", leave=False):
                text = ev.get("text", "")
                if not text:
                    continue
                echomem.add_message(sid, "user", text, created_at=ev.get("time", ""))

            # Commit
            archive_id = echomem.commit_session(sid)
            result = echomem.poll_commit(
                sid, archive_id,
                timeout_s=config.commit_timeout_s,
                poll_interval_s=config.commit_poll_interval_s,
            )
            import_results.append({
                "sample_id": sample_id,
                "session_id": sid,
                "archive_id": archive_id,
                "status": result.status,
                "elapsed_s": round(result.elapsed_s, 1),
                "message_count": len(events),
            })
            log.info("  sample %s: %s (%.1fs, %d msgs)",
                     sample_id, result.status, result.elapsed_s, len(events))
        except Exception as e:
            log.error("  sample %s 导入失败: %s", sample_id, e)
            import_results.append({
                "sample_id": sample_id,
                "session_id": sid if "sid" in dir() else "",
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

    qa_results: list[QAResult] = []
    qa_tasks: list[dict] = []

    for job in jobs:
        # 使用 sample 的 session 作为检索范围
        session_ids = sample_to_session_ids.get(job.sample_id, [])
        qa_tasks.append({
            "question_id": job.question_id,
            "question": job.question,
            "answer": job.answer,
            "top_k": config.top_k,
            "memory_budget_chars": config.memory_budget_chars,
            "session_id": session_ids[0] if session_ids else "",
            "agent_id": config.agent_id,
        })

    from concurrent.futures import ThreadPoolExecutor, as_completed

    pbar = tqdm(total=len(qa_tasks), desc="QA", unit="q")
    with ThreadPoolExecutor(max_workers=config.concurrency) as pool:
        futures = {}
        for idx, task in enumerate(qa_tasks):
            fut = pool.submit(
                answer_one_question,
                echomem=echomem,
                llm=llm,
                question_id=task["question_id"],
                question=task["question"],
                answer=task["answer"],
                top_k=task["top_k"],
                memory_budget_chars=task["memory_budget_chars"],
                session_id=task["session_id"],
                agent_id=task["agent_id"],
            )
            futures[fut] = idx

        results_buffer: list[QAResult | None] = [None] * len(qa_tasks)
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results_buffer[idx] = fut.result()
            except Exception as e:
                log.error("QA %d 失败: %s", idx, e)
                results_buffer[idx] = QAResult(
                    question_id=qa_tasks[idx]["question_id"],
                    question=qa_tasks[idx]["question"],
                    answer=qa_tasks[idx]["answer"],
                    response="",
                    llm_error=str(e),
                )
            pbar.update(1)
            r = results_buffer[idx]
            if r:
                log.info("  Q[%s] -> %s", r.question_id, r.response[:100])
    pbar.close()

    qa_results = [r for r in results_buffer if r is not None]

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
        base_url=args.judge_base_url or config.llm_base_url,
        api_key=args.judge_api_key or config.llm_api_key,
        model=args.judge_model or config.llm_model,
        temperature=0.0,
        max_tokens=256,
        timeout_s=config.llm_timeout_s,
        max_retries=config.llm_retries,
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

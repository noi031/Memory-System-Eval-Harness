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
import csv
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tqdm import tqdm

from shared.dataset import load_longmemeval, longmemeval_session_batches, resolve_dataset_path
from shared.echomem_client import EchoMemClient
from shared.eval_base import EvalConfig, EvalRun, add_echomem_args, add_llm_args, add_eval_args, build_config_from_args
from shared.llm_client import LLMClient
from shared.qa import answer_one_question, QAResult
from shared.judge import longmemeval_judge


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LongMemEval benchmark evaluation")
    parser.add_argument("--dataset", default="", help="LongMemEval JSON 数据集路径 (不指定则自动查找或下载)")
    parser.add_argument("--sample", default="all", help="筛选 sample (all 或 index/id)")
    parser.add_argument("--questions", default="0", help="限制 QA 数量 (0=all)")
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
    dataset_path = resolve_dataset_path("longmemeval", args.dataset)
    config.dataset_path = dataset_path
    config.sample_filter = args.sample
    config.question_limit = int(args.questions)

    run = EvalRun(
        benchmark_name="longmemeval",
        results_root=Path(__file__).parent / "results",
        config=config,
    )
    log = run.logger

    # 加载数据集
    log.info("加载 LongMemEval 数据集: %s", dataset_path)
    jobs, plans = load_longmemeval(dataset_path, sample_filter=args.sample)
    log.info("共 %d 个问题", len(jobs))

    if config.question_limit > 0:
        jobs = jobs[: config.question_limit]
        plans = plans[: config.question_limit]
        log.info("限制 QA 数量为 %d", len(jobs))

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

    llm = LLMClient(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        temperature=config.llm_temperature,
        max_tokens=config.llm_max_tokens,
        timeout_s=config.llm_timeout_s,
        max_retries=config.llm_retries,
    )

    # -- 阶段 1: 逐题隔离导入 --
    log.info("=" * 60)
    log.info("阶段 1: 逐题导入 haystack sessions (共 %d 题)", len(plans))

    question_to_session: dict[str, str] = {}
    import_results: list[dict] = []

    for job, plan in tqdm(list(zip(jobs, plans)), desc="导入记忆", unit="q"):
        try:
            # 获取 session batches
            batches = plan.get("session_batches") or []
            if not batches:
                # 从 events 构建
                batches = [{"session_key": "default", "date_time": "", "messages": [
                    {"role": "user", "content": ev.get("text", ""), "created_at": ev.get("time", "")}
                    for ev in plan.get("events", []) if ev.get("text")
                ]}]

            # 每题开一个 EchoMem session, 把所有 haystack session 的消息导入
            sid = echomem.open_session(title=f"longmemeval_{job.question_id}")
            msg_count = 0
            for batch in batches:
                for msg in batch.get("messages", []):
                    content = msg.get("content", "")
                    if not content:
                        continue
                    echomem.add_message(
                        sid,
                        msg.get("role", "user"),
                        content,
                        created_at=msg.get("created_at", ""),
                        role_id=msg.get("speaker", msg.get("role", "")),
                    )
                    msg_count += 1

            archive_id = echomem.commit_session(sid)
            result = echomem.poll_commit(sid, archive_id,
                                         timeout_s=config.commit_timeout_s,
                                         poll_interval_s=config.commit_poll_interval_s)
            question_to_session[job.question_id] = sid
            import_results.append({
                "question_id": job.question_id,
                "session_id": sid,
                "status": result.status,
                "messages": msg_count,
                "sessions": len(batches),
                "elapsed_s": round(result.elapsed_s, 1),
            })
            log.info("  %s: %s (%.1fs, %d msgs, %d sessions)",
                     job.question_id, result.status, result.elapsed_s, msg_count, len(batches))
        except Exception as e:
            log.error("  导入 %s 失败: %s", job.question_id, e)
            import_results.append({"question_id": job.question_id, "status": "error", "error": str(e)})

    ok_imports = sum(1 for r in import_results if r["status"] == "completed")
    log.info("导入完成: %d/%d 成功", ok_imports, len(import_results))

    import_csv = run.result_dir / "import_results.csv"
    with open(import_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["question_id", "session_id", "status", "messages", "sessions", "elapsed_s", "error"])
        writer.writeheader()
        for r in import_results:
            writer.writerow(r)

    # -- 阶段 2: 逐题 QA --
    log.info("=" * 60)
    log.info("阶段 2: QA (共 %d 题, 并发=%d)", len(jobs), config.concurrency)

    qa_tasks = []
    for job in jobs:
        qa_tasks.append({
            "question_id": job.question_id,
            "question": job.question,
            "answer": job.answer,
            "top_k": config.top_k,
            "memory_budget_chars": config.memory_budget_chars,
            "session_id": question_to_session.get(job.question_id, ""),
            "agent_id": config.agent_id,
        })

    results_buffer: list[QAResult | None] = [None] * len(qa_tasks)
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

    eval_results: list[dict] = []
    type_acc: dict[str, list[bool]] = {}

    for r, job in tqdm(list(zip(qa_results, jobs)), desc="Judge", unit="q"):
        task_type = job.category
        abstention = "_abs" in r.question_id
        is_correct = longmemeval_judge(
            judge_llm, task_type, r.question, r.answer, r.response, abstention=abstention
        )
        eval_results.append({
            "question_id": r.question_id,
            "question_type": task_type,
            "question": r.question,
            "answer": r.answer,
            "response": r.response,
            "correct": is_correct,
        })
        type_acc.setdefault(task_type, []).append(is_correct)

    eval_csv = run.result_dir / "eval_results.csv"
    with open(eval_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["question_id", "question_type", "question", "answer", "response", "correct"])
        writer.writeheader()
        for r in eval_results:
            writer.writerow(r)

    # 统计
    all_correct = sum(1 for r in eval_results if r["correct"])
    accuracy = all_correct / max(len(eval_results), 1)
    per_type: dict[str, dict] = {}
    for t, scores in type_acc.items():
        per_type[t] = {
            "correct": sum(scores),
            "total": len(scores),
            "accuracy": round(sum(scores) / max(len(scores), 1), 4),
        }

    log.info("Judge 完成: %d/%d correct, accuracy=%.2f%%", all_correct, len(eval_results), accuracy * 100)
    for t, s in per_type.items():
        log.info("  %s: %d/%d (%.1f%%)", t, s["correct"], s["total"], s["accuracy"] * 100)

    # 收集 EchoMem 日志
    run.collect_echomem_logs()

    summary = {
        "benchmark": "longmemeval",
        "dataset": dataset_path,
        "total_questions": len(jobs),
        "import_ok": ok_imports,
        "import_total": len(import_results),
        "qa_count": len(qa_results),
        "qa_errors": sum(1 for r in qa_results if r.llm_error),
        "retrieval_errors": sum(1 for r in qa_results if r.retrieval_error),
        "accuracy": round(accuracy, 4),
        "correct": all_correct,
        "total": len(eval_results),
        "per_type": per_type,
        "avg_qa_elapsed_s": round(sum(r.elapsed_s for r in qa_results) / max(len(qa_results), 1), 2),
        "total_prompt_tokens": sum(r.prompt_tokens for r in qa_results),
        "total_completion_tokens": sum(r.completion_tokens for r in qa_results),
    }
    run.save_summary(summary)

    log.info("=" * 60)
    log.info("评测完成! 结果目录: %s", run.result_dir)
    log.info("Accuracy: %.2f%% (%d/%d)", accuracy * 100, all_correct, len(eval_results))


if __name__ == "__main__":
    main()

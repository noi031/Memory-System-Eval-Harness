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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tqdm import tqdm

from shared.dataset import load_hotpotqa, resolve_dataset_path
from shared.echomem_client import EchoMemClient
from shared.eval_base import EvalConfig, EvalRun, add_echomem_args, add_llm_args, add_eval_args, build_config_from_args
from shared.llm_client import LLMClient
from shared.qa import answer_one_question, QAResult
from shared.judge import answer_f1_em


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HotpotQA benchmark evaluation")
    parser.add_argument("--dataset", default="", help="HotpotQA JSON 数据集路径 (不指定则自动查找或下载)")
    parser.add_argument("--sample", default="all", help="筛选 sample (all 或 sample index/id)")
    parser.add_argument("--questions", default="0", help="限制 QA 数量 (0=all)")
    parser.add_argument("--import-mode", default="per_question",
                        choices=["per_question", "global"],
                        help="导入模式: per_question=每题各自导入; global=合并共享 session")
    add_echomem_args(parser)
    add_llm_args(parser)
    add_eval_args(parser)
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
    )
    log = run.logger

    # 加载数据集
    log.info("加载 HotpotQA 数据集: %s", dataset_path)
    jobs, plans = load_hotpotqa(dataset_path, sample_filter=args.sample)
    log.info("共 %d 个问题", len(jobs))

    if config.question_limit > 0:
        jobs = jobs[: config.question_limit]
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

    # -- 阶段 1: 导入记忆 --
    log.info("=" * 60)
    log.info("阶段 1: 导入记忆 (模式=%s)", args.import_mode)

    question_to_session: dict[str, str] = {}  # question_id -> echomem session_id
    import_results: list[dict] = []

    if args.import_mode == "global":
        # 所有 passages 合并到一个共享 session
        log.info("合并所有 context 到共享 session...")
        shared_sid = echomem.open_session(title="hotpotqa_global")
        total_msgs = 0
        for plan in tqdm(plans, desc="导入 passages", unit="plan"):
            for ev in plan.get("events", []):
                text = ev.get("text", "")
                if text:
                    echomem.add_message(shared_sid, "user", text, created_at=ev.get("time", ""))
                    total_msgs += 1
        log.info("共导入 %d 条 passage", total_msgs)
        archive_id = echomem.commit_session(shared_sid)
        result = echomem.poll_commit(shared_sid, archive_id,
                                     timeout_s=config.commit_timeout_s,
                                     poll_interval_s=config.commit_poll_interval_s)
        log.info("共享 session commit: %s (%.1fs)", result.status, result.elapsed_s)
        import_results.append({"session_id": shared_sid, "status": result.status, "messages": total_msgs})
        for job in jobs:
            question_to_session[job.question_id] = shared_sid

    else:
        # per_question: 每题各自导入
        for job, plan in tqdm(list(zip(jobs, plans)), desc="导入记忆", unit="q"):
            try:
                sid = echomem.open_session(title=f"hotpotqa_{job.question_id}")
                events = plan.get("events", [])
                for ev in events:
                    text = ev.get("text", "")
                    if text:
                        echomem.add_message(sid, "user", text, created_at=ev.get("time", ""))
                archive_id = echomem.commit_session(sid)
                result = echomem.poll_commit(sid, archive_id,
                                             timeout_s=config.commit_timeout_s,
                                             poll_interval_s=config.commit_poll_interval_s)
                question_to_session[job.question_id] = sid
                import_results.append({
                    "question_id": job.question_id,
                    "session_id": sid,
                    "status": result.status,
                    "messages": len(events),
                    "elapsed_s": round(result.elapsed_s, 1),
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

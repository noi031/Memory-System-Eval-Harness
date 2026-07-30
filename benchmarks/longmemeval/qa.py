"""LongMemEval QA execution."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from tqdm import tqdm

from shared.eval_base import EvalConfig
from shared.qa import BASE_QA_FIELDS, QAResult


QA_FIELDS = (*BASE_QA_FIELDS, "retrieval_items_json")


def build_qa_tasks(jobs, question_to_session: dict[str, str], config: EvalConfig, agent_id: str = ""):
    return [{
        "question_id": job.question_id,
        "sample_id": job.sample_id,
        "category": job.category,
        "question": job.question,
        "answer": job.answer,
        "top_k": config.top_k,
        "memory_budget_chars": config.memory_budget_chars,
        "session_id": question_to_session.get(job.question_id, ""),
        "agent_id": agent_id,
        "question_time": job.query_time,
    } for job in jobs]


def run_longmemeval_qa(
    tasks,
    agent_plugin,
    memory_client,
    llm,
    config: EvalConfig,
    result_dir: Path,
    log,
) -> list[QAResult]:
    progress = tqdm(total=len(tasks), desc="QA", unit="q")

    def on_progress(_done: int, result: QAResult) -> None:
        progress.update(1)
        log.info("  Q[%s] -> %s", result.question_id, result.response[:100])

    try:
        results = agent_plugin.run_qa(
            tasks,
            memory_client,
            llm,
            concurrency=config.concurrency,
            question_timeout_s=config.question_timeout_s,
            progress_callback=on_progress,
        )
    finally:
        progress.close()

    output_path = result_dir / "qa_results.csv"
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=QA_FIELDS)
        writer.writeheader()
        for result in results:
            row = result.to_csv_row()
            row["retrieval_items_json"] = json.dumps(
                result.retrieval_items,
                ensure_ascii=False,
            )
            writer.writerow(row)
    log.info("QA 结果已保存: %s", output_path)
    return results

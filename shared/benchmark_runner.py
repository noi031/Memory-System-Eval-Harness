"""Shared benchmark QA phase: runs questions through an agent plugin.

Extracted from the three benchmark runners (locomo/hotpotqa/longmemeval)
which had identical Phase 2 logic: build tasks, submit to ThreadPoolExecutor,
collect QAResults. The only difference was how session_id is resolved per
job, which is now a callback.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from tqdm import tqdm

from agents.base import AgentPlugin

logger = logging.getLogger("eval.benchmark_runner")


@dataclass
class QAResult:
    """Result of a single QA question."""

    question_id: str
    question: str
    answer: str
    response: str
    retrieval_items: list[dict[str, Any]] = field(default_factory=list)
    retrieval_error: str = ""
    llm_error: str = ""
    elapsed_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def to_csv_row(self) -> dict[str, str]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "answer": self.answer,
            "response": self.response,
            "retrieval_error": self.retrieval_error,
            "llm_error": self.llm_error,
            "elapsed_s": f"{self.elapsed_s:.2f}",
            "prompt_tokens": str(self.prompt_tokens),
            "completion_tokens": str(self.completion_tokens),
            "num_retrieved": str(len(self.retrieval_items)),
        }


def _agent_response_to_qa(
    resp, question_id: str, question: str, answer: str,
) -> QAResult:
    extra = resp.extra or {}
    return QAResult(
        question_id=question_id,
        question=question,
        answer=answer,
        response=resp.text,
        retrieval_items=resp.memory_items or [],
        retrieval_error=extra.get("retrieval_error", ""),
        llm_error=resp.error or "",
        elapsed_s=extra.get("elapsed_s", 0.0),
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
    )


def run_qa_phase(
    plugin: AgentPlugin,
    jobs: list,
    session_resolver: Callable,
    concurrency: int = 4,
    log: logging.Logger | None = None,
) -> list[QAResult]:
    """Run the QA phase through an agent plugin.

    Args:
        plugin: Agent plugin (must support send_message).
        jobs: List of job objects, each with question_id, question, answer.
        session_resolver: Callable(job) -> session_id string.
        concurrency: Number of concurrent QA tasks.
        log: Logger instance.

    Returns:
        List of QAResult in the same order as jobs.
    """
    if log is None:
        log = logger

    results: list[QAResult | None] = [None] * len(jobs)
    pbar = tqdm(total=len(jobs), desc="QA", unit="q")

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        for idx, job in enumerate(jobs):
            session_id = session_resolver(job)
            fut = pool.submit(plugin.send_message, session_id, job.question)
            futures[fut] = idx

        for fut in as_completed(futures):
            idx = futures[fut]
            job = jobs[idx]
            try:
                resp = fut.result()
                results[idx] = _agent_response_to_qa(
                    resp, job.question_id, job.question, job.answer,
                )
            except Exception as e:
                log.error("QA %d 失败: %s", idx, e)
                results[idx] = QAResult(
                    question_id=job.question_id,
                    question=job.question,
                    answer=job.answer,
                    response="",
                    llm_error=str(e),
                )
            pbar.update(1)
            r = results[idx]
            if r:
                log.info("  Q[%s] -> %s", r.question_id, r.response[:100])
    pbar.close()

    return [r for r in results if r is not None]  # type: ignore

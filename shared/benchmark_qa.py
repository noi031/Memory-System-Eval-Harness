"""Shared concurrent QA runner that calls agent_plugin.send_message and constructs QAResult.

All benchmark qa.py modules use this instead of agent_plugin.run_qa().
The runner takes a list of task dicts (same format the benchmarks already build),
calls send_message for each, and converts AgentResponse into QAResult.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from plugins.base import AgentResponse
from shared.qa import QAResult

logger = logging.getLogger("eval.benchmark_qa")


def run_concurrent_qa(
    agent_plugin,
    tasks: list[dict[str, Any]],
    *,
    concurrency: int = 4,
    question_timeout_s: float = 120.0,
    progress_callback=None,
) -> list[QAResult]:
    """Run send_message for each task concurrently, constructing QAResult.

    Args:
        agent_plugin: An AgentPlugin instance with send_message().
        tasks: List of task dicts (question_id, question, answer, sample_id,
               category, question_time, session_id, etc.).
        concurrency: Max parallel workers.
        question_timeout_s: Per-question timeout (informational; plugins enforce it).
        progress_callback: Optional callback(done_count: int, result: QAResult).

    Returns:
        List of QAResult in the same order as *tasks*.
    """
    if not tasks:
        return []

    results: list[QAResult | None] = [None] * len(tasks)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_to_idx: dict[Any, int] = {}
        for idx, task in enumerate(tasks):
            fut = pool.submit(_run_one, agent_plugin, task, question_timeout_s)
            future_to_idx[fut] = idx

        done_count = 0
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:
                logger.exception("Task %s failed: %s", tasks[idx].get("question_id", "?"), exc)
                results[idx] = _error_result(tasks[idx], str(exc))
            done_count += 1
            if progress_callback:
                progress_callback(done_count, results[idx])

    return results  # type: ignore[return-value]


def _run_one(agent_plugin, task: dict[str, Any], timeout_s: float) -> QAResult:
    """Execute a single task via send_message and build a QAResult."""
    extra = dict(task)  # pass the full task dict as extra context
    extra.setdefault("question_timeout_s", timeout_s)
    session_id = task.get("session_id", "")
    question = task.get("question", "")
    start = time.monotonic()

    resp: AgentResponse = agent_plugin.send_message(
        session_id, question, "/", extra=extra,
    )

    elapsed = time.monotonic() - start
    extra_out = resp.extra or {}

    return QAResult(
        question_id=task.get("question_id", ""),
        question=question,
        answer=task.get("answer", ""),
        response=resp.text,
        retrieval_items=resp.memory_items or [],
        retrieval_error=extra_out.get("retrieval_error", ""),
        llm_error=resp.error or "",
        elapsed_s=extra_out.get("elapsed_s", elapsed),
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
        tool_call_count=extra_out.get("tool_call_count", 0),
        iterations=extra_out.get("iterations", 1),
        qa_profile=extra_out.get("qa_profile", ""),
        sample_id=task.get("sample_id", ""),
        category=task.get("category", ""),
        retrieval_latency_s=extra_out.get("retrieval_latency_s", 0.0),
        llm_latency_s=extra_out.get("llm_latency_s", extra_out.get("elapsed_s", elapsed)),
        model_usage_observed=resp.prompt_tokens > 0 or resp.completion_tokens > 0,
        trace=extra_out.get("trace", {}),
    )


def _error_result(task: dict[str, Any], error_msg: str) -> QAResult:
    """Build a QAResult for a task that raised an exception."""
    return QAResult(
        question_id=task.get("question_id", ""),
        question=task.get("question", ""),
        answer=task.get("answer", ""),
        response="",
        llm_error=error_msg,
        sample_id=task.get("sample_id", ""),
        category=task.get("category", ""),
    )

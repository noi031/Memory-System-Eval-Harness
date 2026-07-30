"""Bare LLM QA flow: single-turn retrieve -> prompt -> LLM.

This is the QA strategy for the bare_llm agent plugin: search the memory
client, assemble a system+memory+question prompt, and call the LLM once.
No tool-calling loop, no iterative retrieval.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from memories import MemoryClient, SearchResult
from shared.llm_client import LLMClient, LLMResponse
from shared.qa import QAResult

logger = logging.getLogger("eval.bare_llm.qa")


def build_qa_prompt(
    question: str,
    memory_items: list[SearchResult],
    memory_budget_chars: int = 8000,
    system_prompt: str = "",
    question_time: str = "",
) -> list[dict[str, str]]:
    """Build the message list for the LLM: system + memory block + question.

    The memory items are concatenated into a single user message block,
    truncated to ``memory_budget_chars``.
    """
    if not system_prompt:
        system_prompt = (
            "You are a helpful assistant with access to the user's personal memories. "
            "Answer questions based on the provided memory context. "
            "If the memories do not contain the answer, say you don't know. "
            "Keep your answer concise."
        )

    memory_parts: list[str] = []
    total_chars = 0
    for item in memory_items:
        text = item.content or ""
        if not text:
            continue
        if total_chars + len(text) > memory_budget_chars:
            remaining = memory_budget_chars - total_chars
            if remaining > 100:
                memory_parts.append(text[:remaining] + "...")
            break
        memory_parts.append(text)
        total_chars += len(text)

    memory_block = "\n---\n".join(memory_parts) if memory_parts else "(no relevant memories found)"

    time_context = f"Current date: {question_time}\n\n" if question_time.strip() else ""
    user_content = (
        f"Memory Context:\n{memory_block}\n\n"
        f"{time_context}Question: {question}\n\nAnswer:"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def answer_one_question(
    echomem: MemoryClient,
    llm: LLMClient,
    question_id: str,
    question: str,
    answer: str,
    top_k: int = 10,
    memory_budget_chars: int = 8000,
    session_id: str = "",
    agent_id: str = "",
    question_timeout_s: float = 120.0,
    question_time: str = "",
    sample_id: str = "",
    category: str = "",
) -> QAResult:
    """Search memory, build prompt, call LLM, return QAResult."""
    start = time.monotonic()
    deadline = start + question_timeout_s if question_timeout_s > 0 else None

    def remaining_time() -> float | None:
        if deadline is None:
            return None
        return max(0.001, deadline - time.monotonic())

    # 1. Retrieve memories
    retrieval_error = ""
    items: list[SearchResult] = []
    retrieval_started = time.monotonic()
    try:
        items = echomem.search(
            question,
            top_k=top_k,
            session_id=session_id,
            agent_id=agent_id,
            timeout_s=remaining_time(),
        )
    except Exception as e:
        retrieval_error = str(e)
        logger.warning("Retrieval failed for %s: %s", question_id, e)
    retrieval_elapsed = time.monotonic() - retrieval_started

    # 2. Build prompt
    orchestration_started = time.monotonic()
    messages = build_qa_prompt(
        question,
        items,
        memory_budget_chars,
        question_time=question_time,
    )
    orchestration_elapsed = time.monotonic() - orchestration_started

    # 3. Call LLM
    if deadline is not None and time.monotonic() >= deadline:
        resp = LLMResponse(
            content="",
            prompt_tokens=0,
            completion_tokens=0,
            elapsed_s=time.monotonic() - start,
            error=f"question deadline exceeded after {question_timeout_s:g}s",
        )
    else:
        resp = llm.chat(messages, timeout_s=remaining_time())
    if resp.error:
        logger.warning("LLM call failed for %s: %s", question_id, resp.error)

    return QAResult(
        question_id=question_id,
        question=question,
        answer=answer,
        response=resp.content,
        retrieval_items=[
            r.to_dict()
            for r in items
        ],
        retrieval_error=retrieval_error,
        llm_error=resp.error,
        elapsed_s=time.monotonic() - start,
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
        sample_id=sample_id,
        category=category,
        retrieval_latency_s=retrieval_elapsed,
        orchestration_latency_s=orchestration_elapsed,
        llm_latency_s=resp.elapsed_s,
        model_retry_count=resp.retry_count,
        model_usage_observed=resp.usage_observed,
    )


def run_concurrent_qa(
    tasks: list[dict[str, Any]],
    echomem: MemoryClient,
    llm: LLMClient,
    concurrency: int = 4,
    question_timeout_s: float = 120.0,
    progress_callback=None,
) -> list[QAResult]:
    """Run multiple QA tasks concurrently with ThreadPoolExecutor.

    Each *task* dict must have: question_id, question, answer.
    Optional keys: top_k, memory_budget_chars, session_id, agent_id.
    """
    results: list[QAResult] = [None] * len(tasks)  # type: ignore

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_to_idx: dict[Any, int] = {}
        for idx, task in enumerate(tasks):
            fut = pool.submit(
                answer_one_question,
                echomem=echomem,
                llm=llm,
                question_id=task["question_id"],
                question=task["question"],
                answer=task["answer"],
                top_k=task.get("top_k", 10),
                memory_budget_chars=task.get("memory_budget_chars", 8000),
                session_id=task.get("session_id", ""),
                agent_id=task.get("agent_id", ""),
                question_timeout_s=question_timeout_s,
                question_time=task.get("question_time", ""),
                sample_id=task.get("sample_id", ""),
                category=task.get("category", ""),
            )
            future_to_idx[fut] = idx

        done_count = 0
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:
                logger.error("QA task %d failed: %s", idx, e)
                results[idx] = QAResult(
                    question_id=tasks[idx]["question_id"],
                    question=tasks[idx]["question"],
                    answer=tasks[idx]["answer"],
                    response="",
                    llm_error=str(e),
                    sample_id=tasks[idx].get("sample_id", ""),
                    category=tasks[idx].get("category", ""),
                )
            done_count += 1
            if progress_callback:
                progress_callback(done_count, results[idx])

    return results  # type: ignore

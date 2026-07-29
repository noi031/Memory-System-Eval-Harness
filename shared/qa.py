"""Common QA flow: search EchoMem, build prompt, call LLM, return answer."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from backends import MemoryClient, SearchResult
from .llm_client import LLMClient, LLMResponse

logger = logging.getLogger("eval.qa")

BASE_QA_FIELDS = (
    "question_id",
    "sample_id",
    "category",
    "question",
    "answer",
    "response",
    "retrieval_error",
    "llm_error",
    "elapsed_s",
    "end_to_end_ms",
    "retrieval_latency_ms",
    "injection_total_ms",
    "llm_total_ms",
    "prompt_tokens",
    "completion_tokens",
    "answer_prompt_tokens",
    "answer_completion_tokens",
    "answer_total_tokens",
    "model_retry_count",
    "num_retrieved",
    "retrieval_count",
    "retrieval_status",
    "answer_status",
    "model_status",
    "health_status",
    "tool_call_count",
    "iterations",
    "qa_profile",
    "evidence_policy",
    "evidence_origin",
    "retrieval_source_mode",
    "platform_evidence_injection_enabled",
    "qa_memory_writeback_enabled",
)


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
    tool_call_count: int = 0
    iterations: int = 1
    qa_profile: str = "vikingboat0411"
    sample_id: str = ""
    category: str = ""
    retrieval_latency_s: float = 0.0
    orchestration_latency_s: float = 0.0
    llm_latency_s: float = 0.0
    model_retry_count: int | None = None
    model_usage_observed: bool = False
    retrieval_status: str = ""
    answer_status: str = ""
    model_status: str = ""
    health_status: str = ""
    trace: dict[str, Any] = field(default_factory=dict)

    def resolved_statuses(self) -> tuple[str, str, str, str]:
        retrieval = self.retrieval_status or (
            "error"
            if self.retrieval_error
            else "ok" if self.retrieval_items else "empty"
        )
        response = self.response.strip()
        answer = self.answer_status or (
            "failed"
            if self.llm_error
            else "empty_or_unknown"
            if not response or response.lower() == "unknown"
            else "ok"
        )
        model = self.model_status or (
            "failed" if self.llm_error else "ok"
        )
        if self.health_status:
            health = self.health_status
        elif self.retrieval_error:
            health = "retrieval_error"
        elif retrieval != "ok":
            health = "retrieval_empty"
        elif self.llm_error:
            lowered = self.llm_error.lower()
            health = (
                "question_timeout"
                if "deadline" in lowered or "timed out" in lowered
                else "api_error"
            )
        elif answer != "ok":
            health = "answer_empty"
        else:
            health = "ok"
        return retrieval, answer, model, health

    def to_csv_row(self) -> dict[str, str]:
        retrieval, answer, model, health = self.resolved_statuses()
        answer_total = self.prompt_tokens + self.completion_tokens
        usage_observed = self.model_usage_observed or answer_total > 0
        return {
            "question_id": self.question_id,
            "sample_id": self.sample_id,
            "category": self.category,
            "question": self.question,
            "answer": self.answer,
            "response": self.response,
            "retrieval_error": self.retrieval_error,
            "llm_error": self.llm_error,
            "elapsed_s": f"{self.elapsed_s:.2f}",
            "end_to_end_ms": f"{self.elapsed_s * 1000:.1f}",
            "retrieval_latency_ms": f"{self.retrieval_latency_s * 1000:.1f}",
            "injection_total_ms": (
                f"{(self.retrieval_latency_s + self.orchestration_latency_s) * 1000:.1f}"
            ),
            "llm_total_ms": f"{self.llm_latency_s * 1000:.1f}",
            "prompt_tokens": str(self.prompt_tokens),
            "completion_tokens": str(self.completion_tokens),
            "answer_prompt_tokens": (
                str(self.prompt_tokens) if usage_observed else ""
            ),
            "answer_completion_tokens": (
                str(self.completion_tokens) if usage_observed else ""
            ),
            "answer_total_tokens": str(answer_total) if usage_observed else "",
            "model_retry_count": (
                str(self.model_retry_count)
                if self.model_retry_count is not None
                else ""
            ),
            "num_retrieved": str(len(self.retrieval_items)),
            "retrieval_count": str(len(self.retrieval_items)),
            "retrieval_status": retrieval,
            "answer_status": answer,
            "model_status": model,
            "health_status": health,
            "tool_call_count": str(self.tool_call_count),
            "iterations": str(self.iterations),
            "qa_profile": self.qa_profile,
            "evidence_policy": "blackbox",
            "evidence_origin": "echomemory_http_api",
            "retrieval_source_mode": "echo_http_native",
            "platform_evidence_injection_enabled": "false",
            "qa_memory_writeback_enabled": "false",
        }


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
    """Search EchoMem, build prompt, call LLM, return QAResult."""
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


def write_results_csv(path, results: list[QAResult]) -> None:
    """Write QA results to a CSV file."""
    import csv
    if not results:
        return
    fieldnames = [*results[0].to_csv_row().keys(), "retrieval_items"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            row = r.to_csv_row()
            # Add retrieval items as JSON for debugging
            row["retrieval_items"] = str(r.retrieval_items)[:2000]
            writer.writerow(row)

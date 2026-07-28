"""Common QA flow: search EchoMem, build prompt, call LLM, return answer."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from .echomem_client import EchoMemClient, SearchResult
from .llm_client import LLMClient, LLMResponse

logger = logging.getLogger("eval.qa")


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


def build_qa_prompt(
    question: str,
    memory_items: list[SearchResult],
    memory_budget_chars: int = 8000,
    system_prompt: str = "",
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

    user_content = f"Memory Context:\n{memory_block}\n\nQuestion: {question}\n\nAnswer:"

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def answer_one_question(
    echomem: EchoMemClient,
    llm: LLMClient,
    question_id: str,
    question: str,
    answer: str,
    top_k: int = 10,
    memory_budget_chars: int = 8000,
    session_id: str = "",
    agent_id: str = "",
) -> QAResult:
    """Search EchoMem, build prompt, call LLM, return QAResult."""
    import time
    start = time.monotonic()

    # 1. Retrieve memories
    retrieval_error = ""
    items: list[SearchResult] = []
    try:
        items = echomem.search(question, top_k=top_k, session_id=session_id, agent_id=agent_id)
    except Exception as e:
        retrieval_error = str(e)
        logger.warning("Retrieval failed for %s: %s", question_id, e)

    # 2. Build prompt
    messages = build_qa_prompt(question, items, memory_budget_chars)

    # 3. Call LLM
    resp: LLMResponse = llm.chat(messages)
    if resp.error:
        logger.warning("LLM call failed for %s: %s", question_id, resp.error)

    return QAResult(
        question_id=question_id,
        question=question,
        answer=answer,
        response=resp.content,
        retrieval_items=[
            {"uri": r.uri, "score": r.score, "content": r.content[:500], "type": r.memory_type}
            for r in items
        ],
        retrieval_error=retrieval_error,
        llm_error=resp.error,
        elapsed_s=time.monotonic() - start,
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
    )


def run_concurrent_qa(
    tasks: list[dict[str, Any]],
    echomem: EchoMemClient,
    llm: LLMClient,
    concurrency: int = 4,
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
    fieldnames = list(results[0].to_csv_row().keys())
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            row = r.to_csv_row()
            # Add retrieval items as JSON for debugging
            row["retrieval_items"] = str(r.retrieval_items)[:2000]
            writer.writerow(row)

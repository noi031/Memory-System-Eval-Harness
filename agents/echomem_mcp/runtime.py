"""Tool-call loop for the echomem_mcp agent plugin.

The LLM is given EchoMem's MCP tools as OpenAI function-calling definitions.
It decides when to search memory and which URIs to read.  Each tool call is
forwarded to the MCP server via ``McpClient``.

This is the QA strategy for the echomem_mcp agent plugin, mirroring how
vikingbot keeps its own tool-call runtime in ``agents/vikingbot/runtime.py``.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from agents.echomem_mcp.mcp_client import McpClient
from shared.llm_client import LLMClient
from shared.qa import QAResult

logger = logging.getLogger("eval.echomem_mcp.runtime")

# -- MCP tool definitions (OpenAI function-calling format) ----------------

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "memory_query",
            "description": (
                "Search long-term memory for information relevant to the query. "
                "Returns ranked results with scores and source URIs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default 8).",
                        "default": 8,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": (
                "Read the full content of one or more memory items by URI. "
                "Pass a single echo:// URI or a comma-separated list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "uris": {
                        "type": "string",
                        "description": "An echo:// URI or comma-separated list of URIs.",
                    },
                },
                "required": ["uris"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list",
            "description": "List memory entries under a URI prefix.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uri": {
                        "type": "string",
                        "description": "URI prefix to list entries under.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Recurse into sub-directories (default false).",
                        "default": False,
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum recursion depth (default 3).",
                        "default": 3,
                    },
                },
                "required": ["uri"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find memory URIs matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern, e.g. echo://resources/**/*.md",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]

_SYSTEM_PROMPT = (
    "You are a helpful assistant with access to a memory system via tools. "
    "Use the memory_query tool to find relevant information, and the read tool "
    "to read full content of specific memory items. "
    "Answer the user's question concisely based on what you find. "
    "If the memory does not contain the answer, say you don't know."
)


# -- Single-question tool-call loop --------------------------------------

def answer_one_mcp_question(
    mcp_factory: Callable[[], McpClient],
    llm: LLMClient,
    *,
    question_id: str,
    question: str,
    answer: str,
    max_iterations: int = 10,
    question_timeout_s: float = 120.0,
    question_time: str = "",
    sample_id: str = "",
    category: str = "",
) -> QAResult:
    """Run a tool-call loop for one question.

    Creates a fresh MCP session, presents MCP tools to the LLM, and iterates
    until the LLM produces a final answer (no tool calls) or max_iterations
    is reached.
    """
    start = time.monotonic()
    deadline = start + question_timeout_s if question_timeout_s > 0 else None

    def remaining() -> float | None:
        if deadline is None:
            return None
        return max(0.001, deadline - time.monotonic())

    tool_calls_total = 0
    iterations = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    usage_observed = False
    response_text = ""
    llm_error = ""
    retrieval_items: list[dict[str, Any]] = []

    try:
        mcp = mcp_factory()
        mcp.initialize(timeout_s=remaining())
    except Exception as e:
        logger.warning("MCP initialize failed for %s: %s", question_id, e)
        return QAResult(
            question_id=question_id,
            question=question,
            answer=answer,
            response="",
            retrieval_error=str(e),
            elapsed_s=time.monotonic() - start,
            sample_id=sample_id,
            category=category,
        )

    time_context = f"Current date: {question_time}.\n\n" if question_time.strip() else ""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"{time_context}Answer the following question: {question}"},
    ]

    try:
        for iteration in range(1, max_iterations + 1):
            iterations = iteration
            rem = remaining()
            if rem is not None and rem <= 0:
                llm_error = f"question deadline exceeded after {question_timeout_s:g}s"
                break

            resp = llm.chat_with_tools(messages, MCP_TOOLS, timeout_s=rem)
            total_prompt_tokens += resp.prompt_tokens
            total_completion_tokens += resp.completion_tokens
            usage_observed = usage_observed or resp.usage_observed

            if resp.error:
                llm_error = resp.error
                break

            tool_calls = resp.tool_calls
            if not tool_calls:
                response_text = resp.content
                break

            # Append the assistant message (with tool_calls) to conversation
            messages.append({
                "role": "assistant",
                "content": resp.content or "",
                "tool_calls": tool_calls,
            })

            # Execute each tool call via MCP
            for tc in tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                try:
                    result_text = mcp.call_tool(tool_name, args, timeout_s=remaining())
                    tool_calls_total += 1
                    if tool_name == "memory_query":
                        retrieval_items.append({
                            "tool": tool_name,
                            "query": args.get("query", ""),
                            "result": result_text[:2000],
                        })
                except Exception as e:
                    result_text = f"Error calling {tool_name}: {e}"
                    logger.warning("Tool %s failed for %s: %s", tool_name, question_id, e)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result_text,
                })
        else:
            # Max iterations reached; force a final answer without tools
            messages.append({
                "role": "user",
                "content": "You have reached the tool-use iteration limit. Do not call any more tools. Answer the question directly now.",
            })
            rem = remaining()
            resp = llm.chat_with_tools(messages, [], timeout_s=rem)
            total_prompt_tokens += resp.prompt_tokens
            total_completion_tokens += resp.completion_tokens
            usage_observed = usage_observed or resp.usage_observed
            if resp.error:
                llm_error = resp.error
            else:
                response_text = resp.content

    except Exception as e:
        llm_error = str(e)
        logger.warning("MCP question %s failed: %s", question_id, e)
    finally:
        try:
            mcp.close()
        except Exception:
            pass

    elapsed = time.monotonic() - start

    return QAResult(
        question_id=question_id,
        question=question,
        answer=answer,
        response=response_text,
        retrieval_items=retrieval_items,
        llm_error=llm_error,
        elapsed_s=elapsed,
        prompt_tokens=total_prompt_tokens,
        completion_tokens=total_completion_tokens,
        tool_call_count=tool_calls_total,
        iterations=iterations,
        qa_profile="echomem_mcp",
        sample_id=sample_id,
        category=category,
        llm_latency_s=elapsed,
        model_usage_observed=usage_observed,
    )


# -- Concurrency wrapper -------------------------------------------------

def run_concurrent_mcp_qa(
    tasks: list[dict[str, Any]],
    mcp_factory: Callable[[], McpClient],
    llm: LLMClient,
    *,
    concurrency: int = 4,
    question_timeout_s: float = 120.0,
    max_iterations: int = 10,
    progress_callback=None,
) -> list[QAResult]:
    """Run multiple MCP QA tasks concurrently."""
    results: list[QAResult] = [None] * len(tasks)  # type: ignore

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_to_idx: dict[Any, int] = {}
        for idx, task in enumerate(tasks):
            fut = pool.submit(
                answer_one_mcp_question,
                mcp_factory=mcp_factory,
                llm=llm,
                question_id=task["question_id"],
                question=task["question"],
                answer=task["answer"],
                max_iterations=max_iterations,
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
                logger.error("MCP QA task %d failed: %s", idx, e)
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

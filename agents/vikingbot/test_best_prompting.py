"""Committed v2 test-best EchoMemory prompt assembly."""

from __future__ import annotations

import platform
import time
from datetime import datetime
from typing import Any

from backends import SearchResult

from .prompting import build_question_prompt, format_memory


def build_test_best_messages(
    question: str,
    question_time: str,
    items: list[SearchResult],
    user_memory_budget_chars: int,
    agent_memory_budget_chars: int,
) -> list[dict[str, Any]]:
    user_items: list[SearchResult] = []
    agent_items: list[SearchResult] = []
    for item in items:
        if (
            "/agent/" in item.uri.lower()
            or item.memory_type.lower().startswith("agent")
        ):
            agent_items.append(item)
        else:
            user_items.append(item)
    user_memory = format_memory(user_items, user_memory_budget_chars)
    agent_memory = format_memory(agent_items, agent_memory_budget_chars)
    runtime = (
        f"{'macOS' if platform.system() == 'Darwin' else platform.system()} "
        f"{platform.machine()}, Python {platform.python_version()}"
    )
    system = f"""# MemoryBench Agent

You are an AI assistant using EchoMemory as the memory backend.
When acquiring information, data, and knowledge, you **prioritize using EchoMemory evidence above all other sources**.
You have access only to the EchoMemory tools exposed in this request.

## Runtime
{runtime}

## Workspace
EchoMemory is accessed through its public HTTP-backed memory tools. Do not assume access to local files, databases, shell commands, or hidden workspace artifacts.

IMPORTANT:
- When responding to direct questions or conversations, reply directly with your text response.
- Always be helpful, accurate, and concise. When using tools, think step by step: what you know, what you need, and why you chose this tool.
## EchoMemory Memory Retrieval
- For questions about the user's remembered facts, preferences, profile, or personal context, use memory_search for the current question before saying there is no relevant record.
- A previous empty search result does not prove that a different follow-up question has no memory; search again when the requested fact changes.
- Injected memory entries may contain full content or a URI with partial content that can be read for more detail.
- For relevant summary or URI entries, use memory_read_many on their URIs to fetch full details to help resolve the query.

## Memory
- Long-term memories are created by EchoMemory session commit.
"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    tz = time.strftime("%Z") or "UTC"
    evidence = (
        f"### user memories:\n{user_memory or '(none)'}\n\n"
        f"### agent memories:\n{agent_memory or '(none)'}"
    )
    memory_message = "\n\n---\n\n".join([
        f"## Current Time: {now} ({tz})",
        "## Current Session\nChannel: cli",
        f"## memory_search(query=[user_query])\n{evidence}",
        (
            "Reply in the same language as the user's query, ignoring the "
            "language of the reference materials. User's query:"
        ),
    ])
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": memory_message},
        {
            "role": "user",
            "content": build_question_prompt(question, question_time),
        },
    ]

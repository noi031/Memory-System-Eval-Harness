"""Committed v2 EchoMemory VikingBoat-style prompt."""

from __future__ import annotations

import platform
import time
from datetime import datetime
from typing import Any

from backends import SearchResult

from .prompting import build_question_prompt, format_memory


def build_v2_aligned_messages(
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
- The only authoritative evidence is returned by the exposed EchoMemory HTTP-backed memory tools.
- For questions about remembered facts or personal context, use memory_search before concluding that no relevant record exists.
- Search results may contain partial summaries. Use memory_read_many on relevant summary or session URIs when more detail is needed.
- A previous empty search does not prove that a different requested fact has no memory. Search again when the information need changes, but avoid duplicate calls with the same intent.
- Use semantic search for concepts and memory_grep for exact text or identifiers. Scope reads and grep to relevant sessions when possible.
- Base the answer only on returned evidence, preserve exact names, dates, and values when present, and do not invent unsupported details.
- Stop when the evidence is sufficient and answer directly and concisely. Reply 'unknown' only when the available evidence does not support an answer.

## Memory
- Long-term memories are created by EchoMemory session commit.

## Evaluation alignment
This run keeps the VikingBoat-style message layout and retrieval budgets for comparability, but the memory backend and exposed tools are EchoMemory."""
    now = (
        question_time
        if question_time.strip() and question_time.strip() != "-"
        else datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    )
    tz = time.strftime("%Z") or "UTC"
    evidence = (
        f"### user memories:\n{user_memory or '(none)'}\n\n"
        f"### agent memories:\n{agent_memory or '(none)'}"
    )
    memory_message = "\n\n---\n\n".join([
        f"## Current Time: {now} ({tz})",
        (
            "## Current Session\n"
            "Channel: cli\n"
            "**Group chat session.** Current user ID: user\n"
            "Multiple users can participate in this conversation. Each user "
            "message is prefixed with the user ID in brackets like "
            "@<user_id>. You should pay attention to who is speaking to "
            "understand the context."
        ),
        f"## memory_search(query=[user_query])\n{evidence}",
        (
            "Use the retrieved memories as context and answer the user query "
            "directly. User's query:"
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

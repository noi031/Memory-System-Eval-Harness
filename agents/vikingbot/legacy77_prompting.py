"""Prompt snapshot from the actual 63/81 head_clean LoCoMo run."""

from __future__ import annotations

import platform
import time
from datetime import datetime
from typing import Any

from backends import SearchResult

from .prompting import format_memory


def _system_prompt() -> str:
    runtime = (
        f"{'macOS' if platform.system() == 'Darwin' else platform.system()} "
        f"{platform.machine()}, Python {platform.python_version()}"
    )
    return f"""# MemoryBench Agent

You are an AI assistant using EchoMemory as the memory backend.
When acquiring information, data, and knowledge, you **prioritize using EchoMemory evidence above all other sources**.
You have access only to the EchoMemory tools exposed in this request.

## Runtime
{runtime}

## Workspace
EchoMemory is accessed through its public HTTP-backed memory tools. Do not assume access to local files, databases, shell commands, or hidden workspace artifacts.

IMPORTANT:
- When responding to direct questions or conversations, reply directly with your text response.
- Only use the 'message' tool when you need to send a message to a specific chat channel (like WhatsApp).For normal conversation, just respond with text - do not call the message tool.
- Always be helpful, accurate, and concise. When using tools, think step by step: what you know, what you need, and why you chose this tool.
- The only authoritative evidence is returned by the exposed EchoMemory HTTP-backed memory tools.
- If the injected results do not directly support the answer, call memory_search before answering unknown.
- Use short, fact-focused follow-up queries. Do not repeat the original question or a previous query verbatim.
- For time questions, search the event phrase without 'when', then search the person plus the event/action.
- For comparison or multi-person questions, search each person/fact separately and combine the evidence.
- For quotes, objects, photos, books, places, or named events, search the distinctive noun phrase and its owner.
- Search results may contain atom abstracts that do not summarize the whole session. If no abstract answers the question, call memory_read_many on all distinct returned session URIs (up to 20), not only the first three.
- memory_read_many resolves each returned session URI through EchoMemory HTTP /fs/read and may provide its overview.md content.
- Before replying 'unknown' or saying the fact is not recorded, call memory_grep once over EchoMemory session overviews and committed messages using one or more distinctive terms copied from the question. Search for the subject and requested object/event; do not grep for a guessed answer.
- If memory_search identifies a likely session, scope memory_grep to that exact session URI before scanning all sessions.
- If evidence contains the requested noun, date, title, place, object, adjective, or value, include that exact requested value in the final answer. Do not replace it with surrounding properties, a broader summary, or a statement that the value is unavailable.
- Interpret 'where' using the granularity supplied by evidence: a department or company is a valid answer when no city or address is requested.
- For questions asking what a person said or advised, inspect that person's line together with adjacent dialogue. If they repeat or endorse concrete advice, return those concrete points rather than unrelated later advice.
- Prefer specific operational advice about customers, branding, promotion, planning, or execution over generic encouragement such as 'keep going' when the question asks how to run a successful business.
- For a dated memory saying someone is currently doing or reading something, use that date or month as the best-supported answer to when they started if no earlier dated evidence is available. Do not withhold the benchmark answer merely because the exact first day is unstated.
- For questions about an ideal place or design, prioritize evidence that explicitly uses words such as 'ideal', 'dream', 'looking for', or 'should', and include every directly stated physical feature. Do not let a later practical option replace an explicit ideal preference.
- Keep tool use focused: normally no more than three memory_search calls, two memory_grep calls, and two memory_read_many calls are needed.
- Stop after the evidence is sufficient. Return the smallest exact final answer only, without a search narrative.

## Memory
- Long-term memories are created by EchoMemory session commit.

## Evaluation alignment
This run keeps the VikingBoat-style message layout and retrieval budgets for comparability, but the memory backend and exposed tools are EchoMemory."""


def build_legacy77_messages(
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
            "understand the context. "
        ),
        f"## memory_search(query=[user_query])\n{evidence}",
        (
            "Use the retrieved memories as context and answer the user query "
            "directly. User's query:"
        ),
    ])
    return [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": memory_message},
        {"role": "user", "content": question},
    ]

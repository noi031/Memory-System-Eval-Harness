from __future__ import annotations

import argparse
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from echomemory_qa_common import LONGMEMEVAL_ABSTAIN_TEXT, MEMORY_SEARCH_TOOL_NAME, compact
from openviking_memory_qa import build_vikingbot_question_prompt


def build_vikingboat_lite_messages(
    args: argparse.Namespace,
    job: Any,
    user_memory: str,
    agent_memory: str,
    has_memory: bool,
    focus_snippets: str = "",
) -> list[dict[str, Any]]:
    runtime = f"{'macOS' if platform.system() == 'Darwin' else platform.system()} {platform.machine()}, Python {platform.python_version()}"
    workspace_display = str(Path.cwd().resolve())
    tool_loop_enabled = bool(getattr(args, "vikingboat_tool_loop", False))
    tool_set = str(getattr(args, "tool_set", "search_read") or "search_read").strip()
    prompt_context_mode = str(
        getattr(args, "prompt_context_mode", "vikingbot_aligned") or "vikingbot_aligned"
    ).strip()
    legacy_eval_bundle = prompt_context_mode == "legacy_eval"
    prompt_system_mode = str(
        getattr(args, "prompt_system_mode", "vikingbot_aligned") or "vikingbot_aligned"
    ).strip()
    session_context_mode = str(
        getattr(args, "session_context_mode", "single") or "single"
    ).strip()
    current_time_mode = str(
        getattr(args, "current_time_mode", "runtime") or "runtime"
    ).strip()
    legacy_eval_prompt = legacy_eval_bundle or prompt_system_mode == "legacy_eval"
    group_chat_session = legacy_eval_bundle or session_context_mode == "group"
    question_time_as_current = legacy_eval_bundle or current_time_mode == "question_time"
    read_tool_guidance = (
        "\n- For relevant summary or URI entries, use memory_read_many on their URIs to fetch full details to help resolve the query."
        if tool_set != "search_only"
        else ""
    )
    execution_mode_note = (
        "- This run does not allow interactive tool execution. Use only the retrieved evidence already included below.\n"
        "- Never emit tool calls, XML tags, search plans, or 'let me search' style text.\n"
        "- Answer directly and concisely; if the evidence is insufficient, reply with 'unknown'."
        if not tool_loop_enabled
        else (
            "- The only authoritative evidence is returned by the exposed EchoMemory HTTP-backed memory tools.\n"
            "- For questions about remembered facts or personal context, use memory_search before concluding that no relevant record exists.\n"
            "- A previous empty search does not prove that a different requested fact has no memory. Search again when the information need changes, but avoid duplicate calls with the same intent.\n"
            "- Base the answer only on returned evidence, preserve exact names, dates, and values when present, and do not invent unsupported details.\n"
            "- Stop when the evidence is sufficient and answer directly and concisely. Reply 'unknown' only when the available evidence does not support an answer."
            f"{read_tool_guidance}"
        )
        if legacy_eval_prompt
        else (
            "## EchoMemory Memory Retrieval\n"
            "- For questions about the user's remembered facts, preferences, profile, or personal context, use memory_search for the current question before saying there is no relevant record.\n"
            "- A previous empty search result does not prove that a different follow-up question has no memory; search again when the requested fact changes.\n"
            "- Injected memory entries may contain full content or a URI with partial content that can be read for more detail."
            f"{read_tool_guidance}"
        )
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
{execution_mode_note}

## Memory
- Long-term memories are created by EchoMemory session commit.
{'''
## Evaluation alignment
This run keeps the VikingBoat-style message layout and retrieval budgets for comparability, but the memory backend and exposed tools are EchoMemory.
''' if legacy_eval_prompt else ''}"""
    question_time = str(getattr(job, "query_time", "") or "").strip()
    now = (
        question_time
        if question_time_as_current and question_time and question_time != "-"
        else datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    )
    tz = time.strftime("%Z") or "UTC"
    if group_chat_session:
        session_context = (
            "## Current Session\n"
            f"Channel: {getattr(args, 'vikingbot_channel', 'cli') or 'cli'}\n"
            "**Group chat session.** Current user ID: user\n"
            "Multiple users can participate in this conversation. Each user message is prefixed with the user ID in brackets like @<user_id>. "
            "You should pay attention to who is speaking to understand the context. "
        )
    else:
        session_context = (
            "## Current Session\n"
            f"Channel: {getattr(args, 'vikingbot_channel', 'cli') or 'cli'}"
        )
    evidence = (
        f"### user memories:\n{user_memory or '(none)'}\n\n"
        f"### agent memories:\n{agent_memory or '(none)'}"
    )
    memory_parts = [
        f"## Current Time: {now} ({tz})",
        session_context,
    ]
    if has_memory:
        memory_parts.append(f"## {MEMORY_SEARCH_TOOL_NAME}(query=[user_query])\n{evidence}")
    memory_parts.append(
        "Use the retrieved memories as context and answer the user query directly. User's query:"
        if legacy_eval_prompt
        else "Reply in the same language as the user's query, ignoring the language of the reference materials. User's query:"
    )
    memory_message = "\n\n---\n\n".join(memory_parts)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": memory_message},
        {"role": "user", "content": build_vikingbot_question_prompt(job)},
    ]


def build_vikingbot_agent_aligned_messages(
    args: argparse.Namespace,
    job: Any,
    user_memory: str,
    agent_memory: str,
    has_memory: bool,
    focus_snippets: str = "",
) -> list[dict[str, Any]]:
    # Keep the VikingBot-compatible three-message layout while using an
    # EchoMemory-specific system prompt and the actual exposed tool names.
    return build_vikingboat_lite_messages(
        args,
        job,
        user_memory,
        agent_memory,
        has_memory,
        focus_snippets=focus_snippets,
    )


def format_longmemeval_memories_for_prompt(
    items: list[dict[str, Any]],
    *,
    memory_content_fn: Callable[[dict[str, Any]], str],
    memory_uri_fn: Callable[[dict[str, Any]], str],
) -> str:
    if not items:
        return "(No relevant memories found)"
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        content = compact(memory_content_fn(item), 2200).strip()
        if not content:
            continue
        uri = memory_uri_fn(item).strip()
        key = (uri, content)
        if key in seen:
            continue
        seen.add(key)
        prefix = f"[{uri}] " if uri else ""
        lines.append(f"- {prefix}{content}")
    return "\n".join(lines) if lines else "(No relevant memories found)"


def build_longmemeval_messages(
    job: Any,
    prompt_items: list[dict[str, Any]],
    official_prompt_builder: Any | None,
    *,
    focus_snippets: str = "",
    memory_content_fn: Callable[[dict[str, Any]], str],
    memory_uri_fn: Callable[[dict[str, Any]], str],
    abstain_text: str = LONGMEMEVAL_ABSTAIN_TEXT,
) -> list[dict[str, str]]:
    if callable(official_prompt_builder):
        search_results: list[dict[str, Any]] = []
        for item in prompt_items:
            memory = str(item.get("content") or item.get("abstract") or "").strip()
            if not memory:
                continue
            search_results.append(
                {
                    "memory": memory,
                    "score": item.get("score", 0.0),
                    "raw_rank": item.get("rank"),
                }
            )
        prompt = official_prompt_builder(
            question=job.question,
            search_results=search_results,
            question_date=job.query_time or "unknown",
        )
        # OpenViking v0.4.7's run_eval.py prepends this field after building
        # the shared LongMemEval answer prompt.
        question_type = str(getattr(job, "category", "") or "").strip()
        if question_type:
            prompt = f"Question Type: {question_type}\n\n{prompt}"
        if focus_snippets:
            prompt += (
                "\n\nHigh-signal retrieved lines:\n"
                f"{compact(focus_snippets, 1800)}\n"
                "\nUse the memories above, but do not ignore these high-signal lines when they directly answer the question."
            )
        return [{"role": "user", "content": prompt}]

    question_type = str(getattr(job, "category", "") or "longmemeval").strip()
    question_date = str(getattr(job, "query_time", "") or "").strip() or "unknown"
    memories_text = format_longmemeval_memories_for_prompt(
        prompt_items,
        memory_content_fn=memory_content_fn,
        memory_uri_fn=memory_uri_fn,
    )
    system = (
        "You are answering LongMemEval using only the retrieved memories from past conversations.\n"
        "Return answer text only.\n"
        "Do not output reasoning tags, XML, markdown bullets, or explanations.\n"
        "Rules:\n"
        f"1. Current date is {question_date}; compute temporal references relative to it when needed.\n"
        "2. For knowledge-update and multi-session questions, combine relevant memories and let the most recent matching fact win.\n"
        "3. For single-session-assistant questions, recover the exact assistant-provided fact, not a related user fact.\n"
        "4. For single-session-preference questions, personalize suggestions using known likes and dislikes; do not abstain if the memories contain usable preferences.\n"
        "5. If the question refers to the wrong role, title, entity, or variant, abstain instead of guessing.\n"
        "6. For counting, comparison, savings, and duration questions, compute the answer when the supporting facts are present.\n"
        f"7. If the memories do not support the answer, reply exactly: {abstain_text}"
    )
    user_parts = [
        f"Question Type: {question_type}",
        f"Question Date: {question_date}",
        "",
        "Memories:",
        memories_text,
    ]
    if focus_snippets:
        user_parts.extend(["", "Focused Evidence:", compact(focus_snippets, 1800)])
    user_parts.extend(["", f"Question: {job.question}", "", "Final answer:"])
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def format_memory_section(
    items: list[dict[str, Any]],
    max_chars: int,
    *,
    hit_score_fn: Callable[[dict[str, Any]], float],
    memory_content_fn: Callable[[dict[str, Any]], str],
) -> str:
    lines, _included = _materialize_memory_section_detailed(
        items,
        max_chars,
        hit_score_fn=hit_score_fn,
        memory_content_fn=memory_content_fn,
    )
    return "\n".join(lines)


def _materialize_memory_section_detailed(
    items: list[dict[str, Any]],
    max_chars: int,
    *,
    hit_score_fn: Callable[[dict[str, Any]], float],
    memory_content_fn: Callable[[dict[str, Any]], str],
) -> tuple[list[str], list[dict[str, Any]]]:
    lines: list[str] = []
    included: list[dict[str, Any]] = []
    used = 0
    seen_hashes: set[int] = set()
    for index, item in enumerate(items, 1):
        uri = str(item.get("uri") or "")
        score = hit_score_fn(item)
        link = (
            f'<memory index="{index}" type="link">\n'
            f"  <uri>{uri}</uri>\n"
            f"  <score>{score:.3f}</score>\n"
            f"</memory>"
        )
        content = memory_content_fn(item).strip()
        if content:
            content_hash = hash(content)
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            full = (
                f'<memory index="{index}" type="full">\n'
                f"  <uri>{uri}</uri>\n"
                f"  <score>{score:.3f}</score>\n"
                f"  <content>{content}</content>\n"
                f"</memory>"
            )
            needed = len(full) + (1 if lines else 0)
            if used + needed <= max_chars:
                lines.append(full)
                used += needed
                included.append(item)
                continue
        link_needed = len(link) + (1 if lines else 0)
        if used + link_needed <= max_chars:
            lines.append(link)
            used += link_needed
            included.append(item)
    return lines, included


def format_memory_section_detailed(
    items: list[dict[str, Any]],
    max_chars: int,
    *,
    hit_score_fn: Callable[[dict[str, Any]], float],
    memory_content_fn: Callable[[dict[str, Any]], str],
) -> tuple[str, list[dict[str, Any]]]:
    lines, included = _materialize_memory_section_detailed(
        items,
        max_chars,
        hit_score_fn=hit_score_fn,
        memory_content_fn=memory_content_fn,
    )
    return "\n".join(lines), included


def select_memory_items_detailed(
    items: list[dict[str, Any]],
    max_chars: int,
    *,
    hit_score_fn: Callable[[dict[str, Any]], float],
    memory_content_fn: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    _lines, included = _materialize_memory_section_detailed(
        items,
        max_chars,
        hit_score_fn=hit_score_fn,
        memory_content_fn=memory_content_fn,
    )
    return included


def summarize_injected_layers(
    items: list[dict[str, Any]],
    *,
    memory_type_fn: Callable[[dict[str, Any]], str],
    memory_content_fn: Callable[[dict[str, Any]], str],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        key = memory_type_fn(item)
        result[key] = result.get(key, 0) + len(memory_content_fn(item))
    return result


def build_messages(job: Any, user_memory: str, agent_memory: str, has_memory: bool) -> list[dict[str, str]]:
    system = (
        "# EchoMemory Question Answering\n\n"
        "You are a helpful, accurate, and very concise assistant. "
        "Read the retrieved memories carefully, then answer with the smallest exact fact that satisfies the question. "
        "For multi-hop questions, combine facts across multiple retrieved memories when they clearly connect to the same answer. "
        "Do not add explanations, background, or adjacent facts unless the question asks for them. "
        "If any retrieved line directly answers the question, use that answer and do not say the information is missing. "
        "For recommendation or suggestion questions, do not just restate the user's requested features or repeat the question. "
        "Prefer 1-3 concrete recommended items or activities grounded in the retrieved memories. "
        "If the memories only reveal the user's preferences, turn them into a short, concrete suggestion. "
        "For list questions, return only the listed items. "
        "If the memory is insufficient, say you do not know."
    )
    evidence = (
        f"### user memories:\n{user_memory or '(none)'}\n\n"
        f"### agent memories:\n{agent_memory or '(none)'}"
    )
    memory_parts = ["## Current Session\nChannel: cli"]
    if has_memory:
        memory_parts.append(evidence)
    memory_parts.append("Use the retrieved memories as context and answer the user query directly.")
    memory_message = "\n\n---\n\n".join(memory_parts)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": memory_message},
        {"role": "user", "content": build_vikingbot_question_prompt(job)},
    ]

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Callable

from echomemory_qa_common import (
    ECHOMEMORY_VIKINGBOAT_TOOL_SET,
    MEMORY_GLOB_TOOL_NAME,
    MEMORY_GREP_TOOL_NAME,
    MEMORY_LIST_TOOL_NAME,
    MEMORY_MULTI_READ_TOOL_NAME,
    MEMORY_SEARCH_TOOL_NAME,
    compact,
)
from memory.vikingboat_alignment import VIKINGBOT_TOOL_MIN_SCORE


def memory_uri(item: dict[str, Any]) -> str:
    return str(item.get("uri") or item.get("path") or item.get("id") or "")


def _strip_raw_turn_metadata(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    speaker_match = None
    for match in re.finditer(r"\[(?!turn=|session_date=|turn_time=|time_expression=|current=)([^\]=]{1,40})\]", cleaned):
        speaker_match = match
    if speaker_match:
        tail = cleaned[speaker_match.end() :].strip()
        tail = re.sub(r"^[A-Z]\d+:\d+:\s*", "", tail).strip()
        if tail:
            return tail
    cleaned = re.sub(r"^(?:session_date=[^\[]+\s*)+", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"(?:\[(?:turn|session_date|turn_time|created_at|speaker)=[^\]]+\]\s*)+", "", cleaned, flags=re.I).strip()
    return cleaned


def _strip_session_summary_metadata(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines()]
    kept: list[str] = []
    for line in lines:
        if not line:
            continue
        if line.lower().startswith("## session metadata"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _strip_event_memory_metadata(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    statement_match = re.search(r"\bstatement:\s*(.+?)(?:\s+-\s+event_time:|\s+<!--|$)", cleaned, flags=re.I | re.S)
    if statement_match:
        statement = " ".join(statement_match.group(1).split()).strip(" -")
        if statement:
            return statement
    cleaned = re.sub(r"^[^<\n]*?\b(?:event_id|statement):\s*", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"\s*<!--[\s\S]*$", "", cleaned).strip()
    return cleaned


def sanitize_memory_content(item: dict[str, Any], text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    raw = str(item.get("memory_type") or item.get("type") or "memory").strip().lower()
    uri = str(item.get("uri") or item.get("path") or item.get("id") or "").strip().lower()
    memory_type = raw or "memory"
    if uri.endswith("/overview.md") or uri.endswith("/abstract.md") or uri.endswith("/summary"):
        memory_type = "session_summary"
    elif "messages.jsonl#turn=" in uri:
        memory_type = "raw_turn"
    elif "event" in raw or "/events/" in uri:
        memory_type = "event_memory"
    elif raw == "segment_memory":
        memory_type = "segment_memory"
    if memory_type in {"raw_turn", "segment_memory"}:
        cleaned = _strip_raw_turn_metadata(cleaned)
    elif memory_type == "session_summary":
        cleaned = _strip_session_summary_metadata(cleaned)
    elif memory_type == "event_memory":
        cleaned = _strip_event_memory_metadata(cleaned)
    cleaned = re.sub(r"\s*<!--[\s\S]*$", "", cleaned).strip()
    return cleaned


def memory_content(item: dict[str, Any]) -> str:
    raw = str(
        item.get("content")
        or item.get("text")
        or item.get("abstract")
        or item.get("overview")
        or item.get("summary")
        or item.get("preview")
        or ""
    )
    return sanitize_memory_content(item, raw)


def log_retrieved_memory_preview(
    job: Any,
    hits: list[dict[str, Any]],
    *,
    question_no: int | None = None,
    max_items: int = 5,
    preview_chars: int = 220,
    hit_score_fn: Callable[[dict[str, Any]], float],
    memory_type_fn: Callable[[dict[str, Any]], str],
) -> None:
    prefix = f"[memory] q{question_no}" if question_no else "[memory]"
    print(
        f"{prefix} {job.question_id} retrieved={len(hits)} "
        f"sample={job.sample_id}",
        flush=True,
    )
    for index, item in enumerate(sorted(hits, key=hit_score_fn, reverse=True)[:max_items], 1):
        uri = compact(memory_uri(item), 180)
        memory_type = memory_type_fn(item)
        score = hit_score_fn(item)
        preview = compact(memory_content(item), preview_chars)
        print(
            f"[memory]   #{index} score={score:.3f} type={memory_type} uri={uri}",
            flush=True,
        )
        if preview:
            print(f"[memory]      {preview}", flush=True)


def log_retrieval_resolution(
    job: Any,
    *,
    question_no: int | None = None,
    initial_hits: int,
    tool_search_hits: int,
    tool_read_calls: int,
    effective_hits: int,
) -> None:
    prefix = f"[memory] q{question_no}" if question_no else "[memory]"
    print(
        f"{prefix} {job.question_id} initial_hits={initial_hits} "
        f"tool_search_hits={tool_search_hits} tool_reads={tool_read_calls} "
        f"effective_hits={effective_hits}",
        flush=True,
    )


def cache_memory_items(cache: dict[str, dict[str, Any]], items: list[dict[str, Any]]) -> None:
    for item in items:
        uri = memory_uri(item)
        if uri and uri not in cache:
            cache[uri] = item


def split_user_agent_hits(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    user_hits: list[dict[str, Any]] = []
    agent_hits: list[dict[str, Any]] = []
    for item in items:
        uri = memory_uri(item).lower()
        memory_type = str(item.get("memory_type") or item.get("type") or "").lower()
        owner = str(item.get("owner") or item.get("scope") or "").lower()
        if "/agent/" in uri or memory_type.startswith("agent") or owner == "agent":
            agent_hits.append(item)
        else:
            user_hits.append(item)
    return user_hits, agent_hits


def search_result_kind(item: dict[str, Any]) -> str:
    raw = str(item.get("memory_type") or item.get("type") or item.get("backend") or "").lower()
    uri = memory_uri(item).lower()
    if "skill" in raw or "/skills/" in uri:
        return "skills"
    if "resource" in raw or "/resources/" in uri:
        return "resources"
    return "memories"


def echomemory_search_payload(
    items: list[dict[str, Any]],
    min_score: float,
    limit: int,
    *,
    hit_score_fn: Callable[[dict[str, Any]], float],
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {"memories": [], "resources": [], "skills": []}
    emitted = 0
    for item in items:
        score = hit_score_fn(item)
        if score < min_score:
            continue
        uri = memory_uri(item)
        if not uri:
            continue
        emitted += 1
        grouped[search_result_kind(item)].append(
            {
                "index": emitted,
                "uri": uri,
                "abstract": compact(memory_content(item), 700),
                "is_leaf": True,
                "score": round(score, 6),
            }
        )
        if emitted >= limit:
            break
    return {"count": emitted, **grouped}


async def execute_echomemory_search_tool(
    args: argparse.Namespace,
    sdk: Any,
    tool_args: dict[str, Any],
    cache: dict[str, dict[str, Any]],
    *,
    retrieve_fn: Callable[[argparse.Namespace, Any, str], Any],
    hit_score_fn: Callable[[dict[str, Any]], float],
) -> tuple[str, str, int]:
    query = str(tool_args.get("query") or "").strip()
    if not query:
        return "No results found for empty query", "", 0
    try:
        min_score = float(tool_args.get("min_score") if tool_args.get("min_score") is not None else args.tool_min_score)
    except (TypeError, ValueError):
        min_score = args.tool_min_score

    tool_query_args = argparse.Namespace(**vars(args))
    tool_query_args.top_k = max(int(args.top_k), int(args.tool_search_limit))
    hits, retrieval_error, _timing = await retrieve_fn(tool_query_args, sdk, query)
    cache_memory_items(cache, hits)
    payload = echomemory_search_payload(hits, min_score, int(args.tool_search_limit), hit_score_fn=hit_score_fn)
    if payload["count"] == 0:
        return f"No results found for query: {query}", retrieval_error, 0
    return json.dumps(payload, ensure_ascii=False, indent=2), retrieval_error, int(payload["count"])


def execute_echomemory_multi_read_tool(
    tool_args: dict[str, Any],
    cache: dict[str, dict[str, Any]],
) -> str:
    raw_uris = tool_args.get("uris")
    if isinstance(raw_uris, str):
        uris = [raw_uris]
    elif isinstance(raw_uris, list):
        uris = [str(uri) for uri in raw_uris if str(uri or "").strip()]
    else:
        uris = []
    if not uris:
        return "Error: No URIs provided."
    lines = [f"Multi-read results for {len(uris)} resources (level: read):"]
    for uri in uris[:20]:
        lines.append(f"\n--- START OF {uri} ---")
        item = cache.get(uri)
        content = memory_content(item or {})
        lines.append(content if content else f"ERROR: Error reading from EchoMemory memory item: empty content for {uri}")
        lines.append(f"--- END OF {uri} ---")
    if len(uris) > 20:
        lines.append(f"\nSkipped {len(uris) - 20} URIs to keep the tool result bounded.")
    return "\n".join(lines)


def execute_echomemory_list_tool(cache: dict[str, dict[str, Any]], uri: str = "", recursive: bool = False) -> str:
    prefix = str(uri or "").strip()
    rows = []
    for item in sorted(cache.values(), key=lambda value: memory_uri(value)):
        item_uri = memory_uri(item)
        if prefix and not item_uri.startswith(prefix):
            continue
        rows.append(
            str(
                {
                    "name": Path(item_uri.rstrip("/")).name or item_uri,
                    "size": len(memory_content(item)),
                    "uri": item_uri,
                    "isDir": False,
                }
            )
        )
        if not recursive and len(rows) >= 30:
            break
    return "\n".join(rows) if rows else f"No resources found at {prefix or 'cached EchoMemory results'}"


def execute_echomemory_grep_tool(tool_args: dict[str, Any], cache: dict[str, dict[str, Any]]) -> str:
    raw_patterns = tool_args.get("pattern")
    patterns = raw_patterns if isinstance(raw_patterns, list) else [raw_patterns]
    patterns = [str(pattern or "").strip() for pattern in patterns if str(pattern or "").strip()]
    uri_prefix = str(tool_args.get("uri") or "").strip()
    flags = re.I if tool_args.get("case_insensitive") else 0
    if not patterns:
        return "No matches found for patterns: ''"
    results: list[str] = []
    total = 0
    for item in cache.values():
        item_uri = memory_uri(item)
        if uri_prefix and not item_uri.startswith(uri_prefix):
            continue
        content = memory_content(item)
        for pattern in patterns:
            try:
                regex = re.compile(pattern, flags)
            except re.error:
                regex = re.compile(re.escape(pattern), flags)
            for line_no, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    if not results or results[-1] != f"\n📄 {item_uri}":
                        results.append(f"\n📄 {item_uri}")
                    results.append(f"   Line {line_no} (pattern: '{pattern}'):")
                    results.append(f"   {line[:600]}")
                    total += 1
                    if total >= 60:
                        return f"Found {total} matches across {len(patterns)} patterns:\n" + "\n".join(results)
    if not results:
        return "No matches found for patterns: " + ", ".join(f"'{pattern}'" for pattern in patterns)
    return f"Found {total} matches across {len(patterns)} patterns:\n" + "\n".join(results)


def execute_echomemory_glob_tool(tool_args: dict[str, Any], cache: dict[str, dict[str, Any]]) -> str:
    pattern = str(tool_args.get("pattern") or "*").strip() or "*"
    uri_prefix = str(tool_args.get("uri") or "").strip()
    regex = re.escape(pattern).replace("\\*\\*", ".*").replace("\\*", "[^/]*")
    compiled = re.compile(f"^{regex}$")
    matches = []
    for item in sorted(cache.values(), key=lambda value: memory_uri(value)):
        item_uri = memory_uri(item)
        if uri_prefix and not item_uri.startswith(uri_prefix):
            continue
        name = item_uri.split("://", 1)[-1]
        if compiled.search(name) or compiled.search(Path(item_uri).name):
            matches.append(item_uri)
        if len(matches) >= 80:
            break
    if not matches:
        return f"No files found for pattern: {pattern}"
    return "Found " + str(len(matches)) + " files:\n" + "\n".join(f"📄 {uri}" for uri in matches)


def echomemory_search_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MEMORY_SEARCH_TOOL_NAME,
            "description": (
                "Using query to search EchoMemory long-term memories and supporting context. "
                "This operation performs semantic retrieval, not full character matching. Please avoid repeated calls with similar queries as much as possible."
                "bad-case: after searching with 'Nate Joanna dog playdate 3:00 pm', another search was performed using 'Nate Joanna dog playdate'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "min_score": {
                        "type": "number",
                        "description": "Minimum relevance score threshold",
                        "default": VIKINGBOT_TOOL_MIN_SCORE,
                    },
                    "target_uri": {
                        "type": "string",
                        "description": "Optional EchoMemory URI prefix to limit search scope. If omitted, search all available memory.",
                    },
                },
                "required": ["query"],
            },
        },
    }


def echomemory_multi_read_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MEMORY_MULTI_READ_TOOL_NAME,
            "description": "Read full content from multiple EchoMemory items. Returns complete content for all URIs with no truncation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uris": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of EchoMemory URIs to read from.",
                    },
                },
                "required": ["uris"],
            },
        },
    }


def echomemory_list_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MEMORY_LIST_TOOL_NAME,
            "description": "List cached EchoMemory items by URI prefix.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "The parent EchoMemory URI prefix to list."},
                    "recursive": {"type": "boolean", "description": "Whether to list recursively", "default": False},
                },
                "required": ["uri"],
            },
        },
    }


def echomemory_grep_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MEMORY_GREP_TOOL_NAME,
            "description": "Search cached EchoMemory item content using regex patterns. Supports multiple patterns to search concurrently. Please avoid repeated calls with similar queries as much as possible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "The EchoMemory URI prefix to search within."},
                    "pattern": {"type": "array", "items": {"type": "string"}, "description": "Regex pattern or array of regex patterns to search for"},
                    "case_insensitive": {"type": "boolean", "description": "Case-insensitive search", "default": False},
                },
                "required": ["uri", "pattern"],
            },
        },
    }


def echomemory_glob_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MEMORY_GLOB_TOOL_NAME,
            "description": "Find cached EchoMemory item URIs using glob patterns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern to match."},
                    "uri": {"type": "string", "description": "Optional EchoMemory URI prefix to search within.", "default": ""},
                },
                "required": ["pattern"],
            },
        },
    }


def echomemory_tool_definitions(
    args: argparse.Namespace,
    *,
    normalize_tool_set_fn: Callable[..., str],
) -> list[dict[str, Any]]:
    tool_set = normalize_tool_set_fn(
        getattr(args, "tool_set", ""),
        vikingboat_compat=bool(getattr(args, "vikingboat_compat", False)),
    )
    if tool_set == "search_only":
        return [echomemory_search_tool_definition()]
    if tool_set == ECHOMEMORY_VIKINGBOAT_TOOL_SET:
        return [
            echomemory_search_tool_definition(),
            echomemory_multi_read_tool_definition(),
            echomemory_list_tool_definition(),
            echomemory_grep_tool_definition(),
            echomemory_glob_tool_definition(),
        ]
    return [echomemory_search_tool_definition(), echomemory_multi_read_tool_definition()]


async def execute_echomemory_tool(
    args: argparse.Namespace,
    sdk: Any,
    name: str,
    parsed_args: dict[str, Any],
    cache: dict[str, dict[str, Any]],
    *,
    retrieve_fn: Callable[[argparse.Namespace, Any, str], Any],
    hit_score_fn: Callable[[dict[str, Any]], float],
) -> tuple[str, str, int]:
    if name == MEMORY_SEARCH_TOOL_NAME:
        return await execute_echomemory_search_tool(
            args,
            sdk,
            parsed_args,
            cache,
            retrieve_fn=retrieve_fn,
            hit_score_fn=hit_score_fn,
        )
    if name == MEMORY_MULTI_READ_TOOL_NAME:
        return execute_echomemory_multi_read_tool(parsed_args, cache), "", 0
    if name == MEMORY_LIST_TOOL_NAME:
        return execute_echomemory_list_tool(cache, str(parsed_args.get("uri") or ""), bool(parsed_args.get("recursive"))), "", 0
    if name == MEMORY_GREP_TOOL_NAME:
        return execute_echomemory_grep_tool(parsed_args, cache), "", 0
    if name == MEMORY_GLOB_TOOL_NAME:
        return execute_echomemory_glob_tool(parsed_args, cache), "", 0
    return f"Error executing {name}: unsupported tool", "", 0


def search_payload_uris(result_text: str, limit: int) -> list[str]:
    try:
        payload = json.loads(result_text)
    except Exception:
        return []
    uris: list[str] = []
    for group_name in ("memories", "resources", "skills"):
        group = payload.get(group_name) if isinstance(payload, dict) else None
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri") or "").strip()
            if uri and uri not in uris:
                uris.append(uri)
            if len(uris) >= limit:
                return uris
    return uris

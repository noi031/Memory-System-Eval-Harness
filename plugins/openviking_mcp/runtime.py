"""Tool definitions and execution helpers for the openviking_mcp agent plugin.

The LLM is given the memory backend's operations (search, read, list, glob)
as OpenAI function-calling definitions. It decides when to search memory and
which URIs to read. Each tool call is executed via the MemoryClient protocol,
not via a dedicated MCP server -- OpenViking exposes a REST API, not MCP.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backends.memory_types import MemoryClient

logger = logging.getLogger("eval.openviking_mcp.runtime")

# -- Tool definitions (OpenAI function-calling format) ------------------

MEMORY_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": (
                "Search memory for information relevant to the query. "
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
            "name": "memory_read",
            "description": (
                "Read the full content of one or more memory items by URI. "
                "Pass a single viking:// URI or a comma-separated list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "uris": {
                        "type": "string",
                        "description": "A viking:// URI or comma-separated list of URIs.",
                    },
                },
                "required": ["uris"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_list",
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
                },
                "required": ["uri"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_glob",
            "description": "Find memory URIs matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern, e.g. viking://**/*.md",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]

_SYSTEM_PROMPT = (
    "You are a helpful assistant with access to a memory system via tools. "
    "Use the memory_search tool to find relevant information, and the memory_read "
    "tool to read full content of specific memory items. "
    "Answer the user's question concisely based on what you find. "
    "If the memory does not contain the answer, say you don't know."
)


# -- Tool execution via MemoryClient -------------------------------------

def _execute_tool(
    memory_client: MemoryClient,
    name: str,
    args: dict[str, Any],
    *,
    timeout_s: float | None,
    search_limit: int = 8,
) -> str:
    """Execute a single tool call through the MemoryClient protocol."""
    if name == "memory_search":
        query = str(args.get("query") or "").strip()
        if not query:
            return "No results found for empty query"
        limit = int(args.get("limit") or search_limit)
        items = memory_client.search(query, top_k=limit, timeout_s=timeout_s)
        results = [
            {
                "uri": item.uri,
                "score": round(item.score, 4),
                "preview": item.content[:500] if item.content else "",
                "type": item.memory_type,
            }
            for item in items
        ]
        return json.dumps(results, ensure_ascii=False) if results else "No results found"

    if name == "memory_read":
        uris_str = str(args.get("uris") or "").strip()
        if not uris_str:
            return "Error: No URIs provided"
        uris = [u.strip() for u in uris_str.split(",") if u.strip()]
        results: dict[str, str] = {}
        for uri in uris:
            content = memory_client.fs_read(uri, timeout_s=timeout_s)
            results[uri] = content or f"ERROR: No content found for {uri}"
        return json.dumps(results, ensure_ascii=False)

    if name == "memory_list":
        uri = str(args.get("uri") or "").strip()
        if not uri:
            return "Error: No URI provided"
        recursive = bool(args.get("recursive", False))
        entries = memory_client.fs_list(uri, recursive=recursive, timeout_s=timeout_s)
        return (
            json.dumps(entries, ensure_ascii=False)
            if entries
            else f"No entries found at {uri}"
        )

    if name == "memory_glob":
        pattern = str(args.get("pattern") or "").strip()
        if not pattern:
            return "Error: No pattern provided"
        entries = memory_client.fs_glob(pattern, timeout_s=timeout_s)
        uris = [
            str(e.get("uri") or "")
            for e in entries
            if str(e.get("uri") or "").strip()
        ]
        return (
            f"Found {len(uris)} entries:\n" + "\n".join(uris)
            if uris
            else f"No entries found for pattern: {pattern}"
        )

    return f"Unknown tool: {name}"

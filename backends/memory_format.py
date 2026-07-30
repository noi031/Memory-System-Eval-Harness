"""Shared memory formatting helpers for plugins that inject retrieved memories into prompts."""

from __future__ import annotations

from typing import Any


def format_memory_section(items: list[Any], budget_chars: int = 0) -> str:
    """Format a list of SearchResult-like items into a text block for prompt injection.

    Each item should have ``.uri``, ``.score``, and ``.content`` attributes
    (or matching dict keys). When *budget_chars* > 0, items are added until
    the total character budget is exhausted.
    """
    if not items:
        return ""
    sections: list[str] = []
    total = 0
    for i, item in enumerate(items, 1):
        uri = getattr(item, "uri", "") or (item.get("uri", "") if isinstance(item, dict) else "")
        score = getattr(item, "score", 0.0) or (item.get("score", 0.0) if isinstance(item, dict) else 0.0)
        content = getattr(item, "content", "") or (item.get("content", "") if isinstance(item, dict) else "")
        block = f"[{i}] (score: {score:.2f}) uri: {uri}\n{content}"
        if budget_chars > 0 and total + len(block) > budget_chars:
            break
        sections.append(block)
        total += len(block)
    return "### Retrieved memories:\n\n" + "\n\n".join(sections)

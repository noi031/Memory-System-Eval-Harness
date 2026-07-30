"""Final-answer cleanup used by the historical VikingBot runtime."""

from __future__ import annotations

import re


def sanitize_final_answer_text(answer: str) -> str:
    """Remove tool-loop residue without rewriting a valid answer."""
    text = str(answer or "").strip()
    if not text:
        return ""
    text = text.replace("\r", "\n")
    text = re.sub(
        r"<mem_thinking>[\s\S]*?</mem_thinking>",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"<judge_thinking>[\s\S]*?</judge_thinking>",
        " ",
        text,
        flags=re.I,
    )
    for pattern in (
        r"<\|?DSML\|?[\s\S]*$",
        r"<｜DSML｜[\s\S]*$",
        r"<memory_search[\s\S]*$",
        r"<functioncall[\s\S]*$",
        r"<function[\s\S]*$",
        r"<invoke[\s\S]*$",
        r"<execute[\s\S]*$",
    ):
        text = re.sub(pattern, "", text, flags=re.I)
    text = re.sub(r"`{3}[\s\S]*?`{3}", "", text)
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"^(?:answer_[0-9a-z]+(?:_abs)?\s+)+",
        "",
        text,
        flags=re.I,
    ).strip()
    text = re.sub(r"^(?:turn_\d+\s+)+", "", text, flags=re.I).strip()
    text = re.sub(r"^(?:\[[^][]{1,80}\]\s*)+", "", text).strip()
    text = re.sub(
        r"^(?:memory_\d+\s*:\s*)+",
        "",
        text,
        flags=re.I,
    ).strip()
    text = re.sub(
        r"^(?:by the way|speaking of|actually|well|anyway|meanwhile|"
        r"incidentally)\s*,\s*",
        "",
        text,
        flags=re.I,
    ).strip()
    lead_patterns = (
        r"^based on (?:the )?(?:available|retrieved) memor(?:y|ies)"
        r"\s*(?:[:,;-]\s*)?",
        r"^based on my (?:knowledge|memory)\s*(?:[:,;-]\s*)?",
        r"^(?:let me|i(?:'ll| will)) (?:check|search|retrieve|look up)"
        r"[^.!?]*[.!?]\s*",
        r"^searching for[^.!?]*[.!?]\s*",
    )
    changed = True
    while changed and text:
        changed = False
        for pattern in lead_patterns:
            updated = re.sub(pattern, "", text, flags=re.I).strip()
            if updated != text:
                text = updated
                changed = True
    for phrase in (
        "让我搜索一下。",
        "让我搜索一下",
        "我来搜索一下。",
        "我来搜索一下",
        "让我查一下。",
        "让我查一下",
        "根据记忆中的信息，",
        "基于记忆中的信息，",
    ):
        text = text.replace(phrase, "").strip()
    filtered_sentences: list[str] = []
    for sentence in re.split(r"(?<=[.!?。！？])\s+", text):
        piece = sentence.strip()
        if not piece:
            continue
        if re.search(
            r"\b(let me|i(?:'ll| will)) "
            r"(?:search|retrieve|look up|check)\b",
            piece,
            flags=re.I,
        ):
            continue
        if re.search(r"(让我|我来|我会).*(搜索|查询|检索|查一下)", piece):
            continue
        filtered_sentences.append(piece)
    return re.sub(r"\s+", " ", " ".join(filtered_sentences)).strip(" -:\n\t")

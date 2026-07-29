"""Dynamic evaluation metrics and configurable quality judging."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from shared.llm_client import LLMClient


def load_evaluator_config(path: str) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"评测器配置文件不存在: {source}")
    import yaml

    with source.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    return payload if isinstance(payload, dict) else {}


def collect_round_metrics(
    round_data: dict[str, Any],
    reply_result: dict[str, Any],
    _send_time: float,
    prefetch_committed: bool,
    memory_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reply = str(reply_result.get("reply") or "")
    ttft = reply_result.get("ttft_ms")
    done = reply_result.get("done_event") or {}
    return {
        "round_id": round_data.get("id", ""),
        "query": round_data.get("query", ""),
        "reply": reply,
        "reply_length": len(reply),
        "query_length": len(str(round_data.get("query") or "")),
        "ttft_ms": round(ttft, 1) if ttft is not None else None,
        "cached_tokens": int(
            done.get("cachedTokens") or done.get("cached_tokens") or 0
        ),
        "prompt_tokens": int(
            done.get("promptTokens") or done.get("prompt_tokens") or 0
        ),
        "prefetch_committed": prefetch_committed,
        "is_new_session": bool(round_data.get("new_session")),
        "is_injection": bool(round_data.get("is_injection")),
        "complexity": round_data.get("complexity", ""),
        "ground_facts": round_data.get("ground_facts", []),
        "error": reply_result.get("error", ""),
        "relevant_memory": json.dumps(
            memory_items or [],
            ensure_ascii=False,
        ),
    }


def compute_summary(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    queries = [row for row in rounds if not row.get("is_injection")]
    ttft = [row["ttft_ms"] for row in queries if row.get("ttft_ms") is not None]
    cached = [row["cached_tokens"] for row in queries if row.get("cached_tokens")]
    prompt = [row["prompt_tokens"] for row in queries if row.get("prompt_tokens")]
    lengths = [row["reply_length"] for row in queries]
    ordered_ttft = sorted(ttft)
    return {
        "total_queries": len(queries),
        "total_rounds": len(rounds),
        "errors": sum(bool(row.get("error")) for row in queries),
        "prefetch_committed_count": sum(
            bool(row.get("prefetch_committed")) for row in queries
        ),
        "avg_ttft_ms": round(sum(ttft) / len(ttft), 1) if ttft else None,
        "median_ttft_ms": (
            round(ordered_ttft[len(ordered_ttft) // 2], 1)
            if ordered_ttft
            else None
        ),
        "p95_ttft_ms": (
            round(ordered_ttft[int(len(ordered_ttft) * 0.95)], 1)
            if len(ordered_ttft) >= 2
            else None
        ),
        "avg_cached_tokens": (
            round(sum(cached) / len(cached), 1) if cached else None
        ),
        "avg_prompt_tokens": (
            round(sum(prompt) / len(prompt), 1) if prompt else None
        ),
        "avg_reply_length": (
            round(sum(lengths) / len(lengths), 1) if lengths else 0
        ),
    }


def evaluate_quality(
    llm: LLMClient,
    evaluator_config: dict[str, Any],
    query: str,
    reply: str,
    ground_facts: list[str],
    recalled_memories: str = "",
) -> dict[str, Any]:
    dimensions = evaluator_config.get("dimensions") or []
    template = str(evaluator_config.get("evaluate_prompt") or "")
    if not template:
        return {"error": "evaluate_prompt missing in config", "score": None}
    criteria = "\n".join(
        f"{index}. {dimension.get('display_name', dimension.get('name', ''))} "
        f"(0-{dimension.get('max_score', 0)}分): "
        f"{dimension.get('description', '')}"
        for index, dimension in enumerate(dimensions, 1)
    )
    prompt = template.format(
        query=query,
        reply=reply,
        ground_facts=(
            "\n".join(f"- {fact}" for fact in ground_facts)
            if ground_facts
            else "N/A"
        ),
        recalled_memories=recalled_memories or "N/A",
        dimension_criteria=criteria,
    )
    response = llm.chat([
        {
            "role": "system",
            "content": "You are a response quality evaluator. Output only valid JSON.",
        },
        {"role": "user", "content": prompt},
    ])
    if response.error:
        return {"error": response.error, "score": None}

    dimension_info = {
        dimension["name"]: {
            "display_name": dimension.get(
                "display_name",
                dimension.get("name", ""),
            ),
            "max_score": dimension.get("max_score", 0),
        }
        for dimension in dimensions
    }
    try:
        match = re.search(r"\{[\s\S]*\}", response.content)
        if not match:
            raise ValueError("JSON object missing")
        raw = json.loads(match.group())
        dimension_scores: dict[str, float] = {}
        raw_scores = raw.get("dimension_scores") or {}
        for dimension in dimensions:
            name = dimension["name"]
            maximum = float(dimension["max_score"])
            try:
                score = float(raw_scores.get(name, raw.get(name, 0)))
            except (TypeError, ValueError):
                score = 0
            dimension_scores[name] = min(max(0, score), maximum)
        try:
            total = float(raw.get("score", 0))
        except (TypeError, ValueError):
            total = 0
        return {
            "score": min(max(0, total), 100),
            "dimension_scores": dimension_scores,
            "dimension_info": dimension_info,
            "quality_reason": raw.get("reason", ""),
            "strengths": raw.get("strengths") or [],
            "weaknesses": raw.get("weaknesses") or [],
            "hallucination_detected": raw.get("hallucination_detected"),
            "task_completed": raw.get("task_completed"),
            "matched_facts": raw.get("matched_facts"),
            "total_facts": raw.get("total_facts"),
            "recall_helped": raw.get("recall_helped"),
        }
    except Exception:
        return {
            "error": "parse failed",
            "raw": response.content[:500],
            "score": None,
            "dimension_info": dimension_info,
        }

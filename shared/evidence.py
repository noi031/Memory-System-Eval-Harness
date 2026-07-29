"""Validation for retrieval evidence persisted by dataset QA runners."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from shared.csv_io import read_dict_rows


def _items(row: dict[str, Any]) -> tuple[list[Any], str]:
    raw = (
        row.get("retrieval_items_json")
        or row.get("retrieval_items")
        or "[]"
    )
    if isinstance(raw, list):
        return raw, ""
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        return [], str(exc)
    if not isinstance(parsed, list):
        return [], "retrieval evidence is not a JSON list"
    return parsed, ""


def _valid_score(value: Any) -> bool:
    try:
        float(value)
        return value not in (None, "")
    except (TypeError, ValueError):
        return False


def validate_evidence_rows(
    rows: list[dict[str, Any]],
    *,
    sample_limit: int = 12,
) -> dict[str, Any]:
    parse_errors = 0
    empty_rows = 0
    total_items = 0
    valid_items = 0
    missing = Counter()
    examples: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        items, error = _items(row)
        if error:
            parse_errors += 1
            if len(examples) < sample_limit:
                examples.append({
                    "row": row_index + 1,
                    "question_id": row.get("question_id") or "",
                    "issue": "invalid_json",
                    "error": error,
                })
            continue
        if not items:
            empty_rows += 1
            continue
        for item_index, item in enumerate(items):
            total_items += 1
            if not isinstance(item, dict):
                missing["item_not_object"] += 1
                continue
            item_missing: list[str] = []
            if not str(
                item.get("content")
                or item.get("text")
                or item.get("abstract")
                or ""
            ).strip():
                item_missing.append("content")
            if not str(
                item.get("uri")
                or item.get("evidence_uri")
                or item.get("path")
                or ""
            ).strip():
                item_missing.append("uri")
            if not _valid_score(item.get("score")):
                item_missing.append("score")
            if item_missing:
                missing.update(item_missing)
                if len(examples) < sample_limit:
                    examples.append({
                        "row": row_index + 1,
                        "question_id": row.get("question_id") or "",
                        "item_index": item_index,
                        "issue": "missing_fields",
                        "missing": item_missing,
                    })
            else:
                valid_items += 1
    status = (
        "fail"
        if parse_errors or (rows and total_items == 0)
        else "warn"
        if empty_rows or valid_items != total_items
        else "ok"
    )
    return {
        "status": status,
        "rows": len(rows),
        "empty_rows": empty_rows,
        "parse_error_rows": parse_errors,
        "total_items": total_items,
        "valid_items": valid_items,
        "missing_fields": dict(missing),
        "examples": examples,
    }


def validate_evidence_csv(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    rows = read_dict_rows(source)
    return {"path": str(source), **validate_evidence_rows(rows)}

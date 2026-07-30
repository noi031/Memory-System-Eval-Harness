"""Backend-neutral helpers for recovering persisted QA CSV rows."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable

from shared.csv_io import read_dict_rows


def read_csv(path: str | Path) -> list[dict[str, str]]:
    return read_dict_rows(path)


def write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames or ["question_id"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def question_id(row: dict[str, Any]) -> str:
    return str(
        row.get("question_id")
        or row.get("native_question_id")
        or row.get("sample_id")
        or ""
    ).strip()


def qa_row_failed(row: dict[str, Any]) -> bool:
    response = str(row.get("response") or "").strip()
    retrieval_status = str(row.get("retrieval_status") or "").strip().lower()
    return bool(
        not response
        or str(row.get("llm_error") or "").strip()
        or str(row.get("retrieval_error") or "").strip()
        or str(row.get("model_status") or "").strip().lower() == "failed"
        or str(row.get("answer_status") or "").strip().lower()
        in {"failed", "empty_or_unknown"}
        or retrieval_status not in {"", "ok"}
        or str(row.get("health_status") or "").strip().lower()
        in {
            "api_error",
            "timeout",
            "rate_limited",
            "retrieval_empty",
            "retrieval_error",
            "question_timeout",
        }
    )


def recovery_question_ids(
    mode: str,
    rows: list[dict[str, Any]],
    expected_question_ids: Iterable[str],
) -> list[str]:
    expected = list(dict.fromkeys(
        str(value).strip()
        for value in expected_question_ids
        if str(value).strip()
    ))
    by_id = {question_id(row): row for row in rows if question_id(row)}
    if mode == "failed":
        return [
            value
            for value in expected
            if value in by_id and qa_row_failed(by_id[value])
        ]
    if mode == "missing":
        return [value for value in expected if value not in by_id]
    if mode == "failed-or-missing":
        return [
            value
            for value in expected
            if value not in by_id or qa_row_failed(by_id[value])
        ]
    raise ValueError(f"unknown recovery mode: {mode}")


def merge_recovered_rows(
    original_rows: list[dict[str, Any]],
    retry_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    recovered = {
        question_id(row): row
        for row in retry_rows
        if question_id(row) and not qa_row_failed(row)
    }
    merged: list[dict[str, Any]] = []
    replaced: list[str] = []
    seen: set[str] = set()
    for row in original_rows:
        row_id = question_id(row)
        if row_id and row_id in recovered:
            merged.append(recovered[row_id])
            replaced.append(row_id)
        else:
            merged.append(row)
        if row_id:
            seen.add(row_id)
    appended: list[str] = []
    for row in retry_rows:
        row_id = question_id(row)
        if not row_id or row_id in seen or qa_row_failed(row):
            continue
        merged.append(row)
        seen.add(row_id)
        appended.append(row_id)
    return merged, {
        "recovered": len(replaced) + len(appended),
        "replaced": replaced,
        "appended": appended,
        "retry_failures": [
            question_id(row)
            for row in retry_rows
            if question_id(row) and qa_row_failed(row)
        ],
    }

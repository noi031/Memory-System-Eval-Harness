#!/usr/bin/env python3
"""Deterministic statistics for LoCoMo judge results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.csv_io import read_dict_rows


def read_judge_rows(path: Path) -> list[dict[str, str]]:
    return read_dict_rows(path)


def summarize_judge_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    correct = sum(
        1 for row in rows
        if str(row.get("verdict") or row.get("result") or "").upper() == "CORRECT"
    )
    wrong = sum(
        1 for row in rows
        if str(row.get("verdict") or row.get("result") or "").upper() == "WRONG"
    )
    errors = sum(
        1 for row in rows
        if str(row.get("judge_error") or "").strip()
        or str(row.get("verdict") or row.get("result") or "").upper() == "ERROR"
    )
    graded = correct + wrong
    return {
        "total": len(rows),
        "correct": correct,
        "wrong": wrong,
        "errors": errors,
        "graded": graded,
        "accuracy": round(correct / graded, 4) if graded else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize LoCoMo judge CSV")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    summary = summarize_judge_rows(read_judge_rows(input_path))
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

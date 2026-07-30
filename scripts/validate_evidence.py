#!/usr/bin/env python3
"""Validate retrieval evidence stored in a dataset QA CSV."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.evidence import validate_evidence_csv


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate retrieval_items_json in a QA result CSV"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero for warnings as well as failures",
    )
    args = parser.parse_args()
    report = validate_evidence_csv(args.input)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] == "fail" or (
        args.strict and report["status"] != "ok"
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

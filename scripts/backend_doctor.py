#!/usr/bin/env python3
"""Inspect the current memory backend registry and contracts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backends import available_backends


def build_report() -> dict:
    rows = available_backends()
    failed = [
        row["id"]
        for row in rows
        if not bool((row.get("contract") or {}).get("ok"))
    ]
    registered = [str(row.get("id") or "") for row in rows]
    status = (
        "ok"
        if registered == ["echomemory"] and not failed
        else "fail"
    )
    return {
        "status": status,
        "expected_backends": ["echomemory"],
        "registered_backends": registered,
        "failed_backends": failed,
        "backends": rows,
        "safe_to_share": True,
        "secrets_included": False,
    }


def render_text(report: dict) -> str:
    lines = [
        "Memory Backend Doctor",
        f"Status: {report['status']}",
        "Expected: echomemory",
        "Registered: " + ", ".join(report["registered_backends"]),
    ]
    for row in report["backends"]:
        contract = row.get("contract") or {}
        lines.append(
            f"- {row['id']}: contract={contract.get('status', 'unknown')} "
            f"capabilities={len(row.get('capabilities') or [])}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check memory backend registration and contracts"
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
    )
    args = parser.parse_args()
    report = build_report()
    print(
        json.dumps(report, ensure_ascii=False, indent=2)
        if args.format == "json"
        else render_text(report)
    )
    if report["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

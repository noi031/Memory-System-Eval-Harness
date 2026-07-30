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

from memories import available_memories


_EXPECTED_BACKENDS = ["echomemory", "openviking", "none"]
_CONTRACT_EXEMPT = {"none"}


def build_report() -> dict:
    rows = available_memories()
    failed = [
        row["id"]
        for row in rows
        if row["id"] not in _CONTRACT_EXEMPT
        and not bool((row.get("contract") or {}).get("ok"))
    ]
    registered = [str(row.get("id") or "") for row in rows]
    status = (
        "ok"
        if registered == _EXPECTED_BACKENDS and not failed
        else "fail"
    )
    return {
        "status": status,
        "expected_backends": _EXPECTED_BACKENDS,
        "registered_backends": registered,
        "failed_backends": failed,
        "memories": rows,
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
    for row in report["memories"]:
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

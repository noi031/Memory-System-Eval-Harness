#!/usr/bin/env python3
"""Inspect available memory client implementations and their interfaces.

Since memory backends are now part of agent plugins (not a separate
registry), this script verifies that the memory client classes can be
imported and expose the expected interface methods.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backends.memory_types import NullMemoryClient


_REQUIRED_METHODS = (
    "health",
    "open_session",
    "add_message",
    "commit_session",
    "poll_commit",
    "search",
    "fs_read",
    "fs_list",
    "fs_glob",
    "close",
)


def _check_client(name: str, module_path: str, class_name: str) -> dict:
    try:
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
    except Exception as exc:
        return {
            "id": name,
            "importable": False,
            "error": str(exc),
            "contract": {"ok": False, "status": "import_failed"},
        }
    missing = [m for m in _REQUIRED_METHODS if not hasattr(cls, m)]
    return {
        "id": name,
        "importable": True,
        "class": class_name,
        "module": module_path,
        "contract": {
            "ok": not missing,
            "status": "ok" if not missing else "missing_methods",
            "missing_methods": missing,
        },
    }


def build_report() -> dict:
    rows = [
        _check_client(
            "echomem",
            "backends.echomem.client",
            "EchoMemClient",
        ),
        _check_client(
            "openviking",
            "backends.openviking.client",
            "OpenVikingClient",
        ),
        _check_client(
            "none",
            "backends.memory_types",
            "NullMemoryClient",
        ),
    ]
    failed = [
        row["id"]
        for row in rows
        if row["id"] != "none"
        and not bool((row.get("contract") or {}).get("ok"))
    ]
    registered = [str(row.get("id") or "") for row in rows]
    status = "ok" if not failed else "fail"
    return {
        "status": status,
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
        "Registered: " + ", ".join(report["registered_backends"]),
    ]
    for row in report["backends"]:
        contract = row.get("contract") or {}
        lines.append(
            f"- {row['id']}: contract={contract.get('status', 'unknown')} "
            f"importable={row.get('importable', False)}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check memory backend client implementations and interfaces"
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

"""Memory provenance checks for reproducible LoCoMo evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from benchmarks.locomo.import_memory import selected_session_batches


ENGINE_SESSIONS_ROOT = "echo://engine/echo0_plugin/sessions"


def expected_session_count(
    plans: list[dict[str, Any]],
    *,
    session_mode: str,
    max_sessions: int,
) -> int:
    return sum(
        len(selected_session_batches(
            plan,
            session_mode=session_mode,
            max_sessions=max_sessions,
        ))
        for plan in plans
    )


def inspect_memory_provenance(
    memory_client,
    *,
    dataset_path: str | Path,
    plans: list[dict[str, Any]],
    session_mode: str,
    max_sessions: int,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    dataset = Path(dataset_path).expanduser().resolve()
    expected = expected_session_count(
        plans,
        session_mode=session_mode,
        max_sessions=max_sessions,
    )
    session_uris: set[str] = set()
    patterns = (
        f"{ENGINE_SESSIONS_ROOT}/*/overview.md",
        f"{ENGINE_SESSIONS_ROOT}/*/messages.jsonl",
    )
    for pattern in patterns:
        for entry in memory_client.fs_glob(pattern, timeout_s=timeout_s):
            uri = str(entry.get("uri") or "").strip()
            if not uri or "/sessions/" not in uri:
                continue
            session_uris.add(uri.rsplit("/", 1)[0])
    actual = len(session_uris)
    return {
        "schema_version": 1,
        "status": "matched" if actual == expected else "mismatch",
        "dataset": str(dataset),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "session_mode": session_mode,
        "max_sessions": max_sessions,
        "expected_session_count": expected,
        "actual_session_count": actual,
        "session_uris": sorted(session_uris),
        "patterns": list(patterns),
    }


def write_memory_provenance(
    output_dir: str | Path,
    provenance: dict[str, Any],
) -> Path:
    destination = Path(output_dir) / "memory_provenance.json"
    destination.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destination

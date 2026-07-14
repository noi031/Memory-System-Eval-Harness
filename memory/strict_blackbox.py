from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.generate_html_report import (
    load_results,
    observed_blackbox_metrics,
    strict_metric_definitions,
)


STRICT_BLACKBOX_METRICS_FILENAME = "strict_blackbox_metrics.json"
STRICT_BLACKBOX_SCHEMA_VERSION = 1


def _import_summary(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(summary, dict):
        return None
    import_summary = summary.get("import_summary")
    if isinstance(import_summary, dict):
        return import_summary
    summary_json = summary.get("summary_json")
    if isinstance(summary_json, dict) and isinstance(summary_json.get("import_summary"), dict):
        return summary_json["import_summary"]
    return None


def strict_blackbox_metrics_path(csv_path: Path) -> Path:
    return csv_path.parent / STRICT_BLACKBOX_METRICS_FILENAME


def _source_signature(csv_path: Path, import_summary: dict[str, Any] | None) -> str:
    stat = csv_path.stat()
    payload = {
        "schema_version": STRICT_BLACKBOX_SCHEMA_VERSION,
        "csv_path": str(csv_path.resolve()),
        "csv_size": stat.st_size,
        "csv_mtime_ns": stat.st_mtime_ns,
        "import_summary": import_summary or {},
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_existing_snapshot(path: Path, source_signature: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(snapshot, dict) or snapshot.get("source_signature") != source_signature:
        return None
    return snapshot


def build_strict_blackbox_snapshot(
    csv_path: Path,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_csv = csv_path.expanduser().resolve()
    rows = load_results(str(resolved_csv))
    import_summary = _import_summary(summary)
    artifact_path = strict_blackbox_metrics_path(resolved_csv).resolve()
    metrics = observed_blackbox_metrics(rows, import_summary)
    metrics["internal_memory_injection_tokens"] = None
    metrics["initial_memory_import_time_ms"] = None
    return {
        "schema_version": STRICT_BLACKBOX_SCHEMA_VERSION,
        "kind": "strict_blackbox_metrics",
        "mode": "strict_observed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "artifact_path": str(artifact_path),
        "source": str(resolved_csv),
        "source_csv": str(resolved_csv),
        "source_signature": _source_signature(resolved_csv, import_summary),
        "row_count": len(rows),
        "metrics": metrics,
        "definitions": strict_metric_definitions(),
        "unavailable": {
            "internal_memory_injection_tokens": None,
            "initial_memory_import_time_ms": None,
        },
    }


def ensure_strict_blackbox_snapshot(
    csv_path: Path,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    resolved_csv = csv_path.expanduser().resolve()
    if resolved_csv.suffix.lower() != ".csv" or not resolved_csv.exists() or not resolved_csv.is_file():
        return None
    import_summary = _import_summary(summary)
    signature = _source_signature(resolved_csv, import_summary)
    artifact_path = strict_blackbox_metrics_path(resolved_csv)
    existing = _read_existing_snapshot(artifact_path, signature)
    if existing is not None:
        return existing
    snapshot = build_strict_blackbox_snapshot(resolved_csv, summary)
    temporary_path = artifact_path.with_suffix(f"{artifact_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(artifact_path)
    return snapshot


def merge_strict_blackbox_snapshot(
    summary: dict[str, Any] | None,
    csv_path: Path,
) -> dict[str, Any]:
    merged = dict(summary or {})
    try:
        snapshot = ensure_strict_blackbox_snapshot(csv_path, merged)
    except Exception:
        return merged
    if snapshot is not None:
        merged["strict_blackbox"] = snapshot
        merged["strict_blackbox_metrics_path"] = snapshot.get("artifact_path") or ""
    return merged

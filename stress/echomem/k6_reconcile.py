#!/usr/bin/env python3
"""Reconcile native k6 counters with the runner's request evidence."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

INCONCLUSIVE = "INCONCLUSIVE"
PASS = "PASS"
FAIL = "FAIL"


def rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def metric_count(summary: dict[str, Any], metric: str) -> float | None:
    value = ((summary.get("metrics") or {}).get(metric) or {}).get("values")
    if isinstance(value, dict):
        for key in ("count", "passes", "fails"):
            if key in value:
                try:
                    return float(value[key])
                except (TypeError, ValueError):
                    return None
    return None


def reconcile(k6_summary: Path, runner_dir: Path) -> dict[str, Any]:
    if not k6_summary.exists():
        return {"status": INCONCLUSIVE, "reason": "k6 summary is missing"}
    payload = json.loads(k6_summary.read_text(encoding="utf-8"))
    k6_data = payload.get("scenario") if isinstance(payload.get("scenario"), dict) else payload
    search = rows(runner_dir / "search_results.csv")
    commits = rows(runner_dir / "commit_results.csv")
    if not search and not commits:
        return {"status": INCONCLUSIVE, "reason": "runner request evidence is missing"}
    k6_http_reqs = metric_count(k6_data, "http_reqs")
    k6_http_failed = metric_count(k6_data, "http_req_failed")
    runner_total = len(search) + len(commits)
    result = {
        "status": PASS,
        "k6_http_requests": k6_http_reqs,
        "k6_http_failed_metric": k6_http_failed,
        "runner_search_requests": len(search),
        "runner_commit_requests": len(commits),
        "runner_total_requests": runner_total,
        "evidence": ["k6-summary.json", "search_results.csv", "commit_results.csv"],
        "real_http": payload.get("real_http", True),
        "mock_model": payload.get("mock_model", False),
    }
    if k6_http_reqs is not None and k6_http_reqs < runner_total:
        result["status"] = FAIL
        result["reason"] = "k6 request count is lower than runner evidence"
    if result["mock_model"]:
        result["status"] = FAIL
        result["reason"] = "k6 summary claims mock_model=true"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k6-summary", required=True, type=Path)
    parser.add_argument("--runner-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = reconcile(args.k6_summary, args.runner_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())

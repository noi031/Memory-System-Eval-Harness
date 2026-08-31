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


def metric_value(summary: dict[str, Any], metric: str, *keys: str) -> float | None:
    value = ((summary.get("metrics") or {}).get(metric) or {}).get("values")
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                try:
                    return float(value[key])
                except (TypeError, ValueError):
                    return None
    return None


def k6_request_rows(path: Path) -> list[dict[str, Any]]:
    """Read k6's ``--out json=...`` newline-delimited request stream."""
    if not path.is_file():
        return []
    result: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("type") != "Point":
                continue
            data = item.get("data") or {}
            tags = data.get("tags") or {}
            if data.get("metric") == "http_reqs":
                result.append({
                    "time": item.get("data", {}).get("time"),
                    "status": tags.get("status"),
                    "operation": tags.get("operation"),
                    "tenant": tags.get("tenant"),
                    "request_id": tags.get("request_id"),
                    "url": tags.get("url"),
                })
    return result


def reconcile(
    k6_summary: Path,
    runner_dir: Path,
    request_stream: Path | None = None,
) -> dict[str, Any]:
    if not k6_summary.exists():
        return {"status": INCONCLUSIVE, "reason": "k6 summary is missing"}
    payload = json.loads(k6_summary.read_text(encoding="utf-8"))
    k6_data = payload.get("scenario") if isinstance(payload.get("scenario"), dict) else payload
    search = rows(runner_dir / "search_results.csv")
    commits = rows(runner_dir / "commit_results.csv")
    if not search and not commits:
        return {"status": INCONCLUSIVE, "reason": "runner request evidence is missing"}
    k6_http_reqs = metric_value(k6_data, "http_reqs", "count")
    k6_http_failed = metric_value(k6_data, "http_req_failed", "rate")
    k6_http_failed_count = metric_value(k6_data, "http_req_failed", "fails")
    runner_total = len(search) + len(commits)
    result = {
        "status": PASS,
        "k6_http_requests": k6_http_reqs,
        "k6_http_failed_rate": k6_http_failed,
        "k6_http_failed_count": k6_http_failed_count,
        "runner_search_requests": len(search),
        "runner_commit_requests": len(commits),
        "runner_total_requests": runner_total,
        "evidence": ["k6-summary.json", "search_results.csv", "commit_results.csv"],
        "real_http": payload.get("real_http", True),
        "mock_model": payload.get("mock_model", False),
    }
    stream_rows = k6_request_rows(request_stream) if request_stream else []
    if stream_rows:
        runner_ids = {
            row.get("request_id", "")
            for row in search + commits
            if row.get("request_id")
        }
        k6_ids = {row.get("request_id", "") for row in stream_rows if row.get("request_id")}
        result["request_stream"] = {
            "k6_requests": len(stream_rows),
            "k6_request_ids": len(k6_ids),
            "runner_request_ids": len(runner_ids),
            "missing_in_runner": sorted(k6_ids - runner_ids)[:100],
            "missing_in_k6": sorted(runner_ids - k6_ids)[:100],
        }
        if k6_ids != runner_ids:
            result["status"] = INCONCLUSIVE
            result["reason"] = "request-level request_id sets differ"
    if k6_http_failed is not None and k6_http_failed > 0:
        result["status"] = FAIL
        result["reason"] = "k6 reports failed HTTP requests"
    elif k6_http_reqs is not None and k6_http_reqs != runner_total:
        result["status"] = INCONCLUSIVE
        result["reason"] = (
            "k6 and Runner request totals differ; aggregate counts cannot prove "
            "same-window request-level reconciliation"
        )
    if result["mock_model"]:
        result["status"] = FAIL
        result["reason"] = "k6 summary claims mock_model=true"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k6-summary", required=True, type=Path)
    parser.add_argument("--runner-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--k6-request-stream", type=Path, default=None)
    args = parser.parse_args()
    result = reconcile(args.k6_summary, args.runner_dir, args.k6_request_stream)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())

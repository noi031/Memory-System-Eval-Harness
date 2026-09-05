"""Result aggregation and output for load runs.

Produces a per-op console table, ``summary.json`` and ``records.csv``
under the output directory.  Aggregation is derived purely from
:class:`~performance.records.RequestRecord` objects.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from performance.engine import RunResult
from performance.profile import Profile
from performance.records import CSV_FIELDS, RequestRecord
from performance.stats import (
    as_json_safe,
    histogram,
    latency_summary,
    rps,
    top,
)


def summarize(result: RunResult, profile: Profile) -> dict[str, Any]:
    """Aggregate records into a JSON-safe summary dict."""

    records = result.records
    elapsed_s = result.elapsed_s
    latencies = [r.stage_ms for r in records if r.status == "ok"]
    failed = [r for r in records if r.status == "error"]

    by_op: dict[str, list[RequestRecord]] = {}
    by_tenant: dict[int, list[RequestRecord]] = {}
    for record in records:
        by_op.setdefault(record.op, []).append(record)
        by_tenant.setdefault(record.tenant_idx, []).append(record)

    steps_summary: dict[str, Any] = {}
    for op in sorted(by_op):
        op_records = by_op[op]
        op_latencies = [r.stage_ms for r in op_records if r.status == "ok"]
        steps_summary[op] = {
            "count": len(op_records),
            "ok": sum(1 for r in op_records if r.status == "ok"),
            "fail": sum(1 for r in op_records if r.status == "error"),
            "rps": rps(len(op_records), elapsed_s),
            "latency_ms": latency_summary(op_latencies),
            "http_status": histogram(
                str(r.http_status) for r in op_records if r.http_status is not None
            ),
            "error": histogram(
                r.error_type for r in op_records if r.error_type
            ),
        }

    tenant_names = {index: tenant.name for index, tenant in enumerate(profile.tenants)}
    tenants_summary: list[dict[str, Any]] = []
    for tenant_idx in sorted(by_tenant):
        tenant_records = by_tenant[tenant_idx]
        tenant_latencies = [r.stage_ms for r in tenant_records if r.status == "ok"]
        tenants_summary.append(
            {
                "tenant_idx": tenant_idx,
                "name": tenant_names.get(tenant_idx, ""),
                "count": len(tenant_records),
                "ok": sum(1 for r in tenant_records if r.status == "ok"),
                "fail": sum(1 for r in tenant_records if r.status == "error"),
                "rps": rps(len(tenant_records), elapsed_s),
                "latency_ms": latency_summary(tenant_latencies),
                "error": histogram(r.error_type for r in tenant_records if r.error_type),
            }
        )

    transaction_records = [r for r in records if r.op == "transaction"]
    transactions_ok = sum(1 for r in transaction_records if r.status == "ok")

    summary: dict[str, Any] = {
        "scenario": {
            "name": profile.name,
            "description": profile.description,
        },
        "run": {
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "elapsed_s": round(elapsed_s, 3),
            "workers": profile.load.workers,
            "duration_s": profile.load.duration_s,
            "mix": profile.load.mix,
        },
        "total": {
            "count": len(records),
            "ok": len(records) - len(failed),
            "fail": len(failed),
            "rps": rps(len(records), elapsed_s),
            "latency_ms": latency_summary(latencies),
            "http_status": histogram(
                str(r.http_status) for r in records if r.http_status is not None
            ),
            "error": histogram(r.error_type for r in records if r.error_type),
            "burst": sum(1 for r in records if r.extra == "burst"),
        },
        "transactions": {
            "count": len(transaction_records),
            "ok": transactions_ok,
            "fail": len(transaction_records) - transactions_ok,
            "rps": rps(len(transaction_records), elapsed_s),
        },
        "steps": steps_summary,
        "tenants": tenants_summary,
    }
    return as_json_safe(summary)


def write_records(out_dir: Path, records: list[RequestRecord], summary: dict[str, Any]) -> None:
    """Write summary.json and records.csv into ``out_dir``."""

    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    with (out_dir / "records.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_csv_row())


def write_outputs(out_dir: Path, result: RunResult, summary: dict[str, Any]) -> None:
    """Write summary.json and records.csv into ``out_dir``."""

    write_records(out_dir, result.records, summary)


def print_summary(summary: dict[str, Any], stream: TextIO = sys.stdout) -> None:
    """Render the summary as a console table."""

    print(f"scenario: {summary['scenario']['name']}", file=stream)
    total = summary["total"]
    print(
        f"requests: {total['count']}  ok={total['ok']}  fail={total['fail']}  "
        f"rps={total['rps']}  txn_ok={summary['transactions']['ok']}/"
        f"{summary['transactions']['count']}  burst={total['burst']}  "
        f"elapsed={summary['run']['elapsed_s']}s",
        file=stream,
    )
    header = (
        "op             count   ok  fail   rps   mean    p50    p95    p99    max "
        "status                    error"
    )
    print(header, file=stream)
    print("-" * len(header), file=stream)
    for op, step in summary["steps"].items():
        latency = step["latency_ms"]
        status_text = ", ".join(
            f"{key}:{count}" for key, count in top(step["http_status"], 2).items()
        )
        error_text = ", ".join(
            f"{key}:{count}" for key, count in top(step["error"], 2).items()
        )
        print(
            f"{op:<14} {step['count']:>6} {step['ok']:>4} {step['fail']:>4} "
            f"{_fmt_float(step['rps'], 6)} {_fmt_float(latency['mean'], 6)} "
            f"{_fmt_float(latency['p50'], 6)} {_fmt_float(latency['p95'], 6)} "
            f"{_fmt_float(latency['p99'], 6)} {_fmt_float(latency['max'], 6)} "
            f"{status_text:<26} {error_text}",
            file=stream,
        )

    tenants = summary.get("tenants")
    if tenants:
        print(file=stream)
        tenant_header = (
            "tenant   count   ok  fail   rps   mean    p50    p95    p99    max  error"
        )
        print(tenant_header, file=stream)
        print("-" * len(tenant_header), file=stream)
        for tenant in tenants:
            latency = tenant["latency_ms"]
            label = tenant["name"] or str(tenant["tenant_idx"])
            error_text = ", ".join(
                f"{key}:{count}" for key, count in top(tenant["error"], 2).items()
            )
            print(
                f"{label:<8} {tenant['count']:>6} {tenant['ok']:>4} {tenant['fail']:>4} "
                f"{_fmt_float(tenant['rps'], 6)} {_fmt_float(latency['mean'], 6)} "
                f"{_fmt_float(latency['p50'], 6)} {_fmt_float(latency['p95'], 6)} "
                f"{_fmt_float(latency['p99'], 6)} {_fmt_float(latency['max'], 6)} "
                f"{error_text}",
                file=stream,
            )

    custom = summary.get("custom")
    if custom:
        print("custom report:", file=stream)
        print(json.dumps(custom, ensure_ascii=False, indent=2), file=stream)


def _fmt_float(value: Any, width: int) -> str:
    if value is None:
        return "-".rjust(width)
    return f"{float(value):>{width}.1f}"

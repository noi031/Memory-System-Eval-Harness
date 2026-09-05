"""通用套件能力：case records 汇总与单 case 执行。

``summarize_case_records`` 把一批 RequestRecord 汇总为 case 契约摘要
（``{"metrics": {...}}``，search/commit/fairness/per_tenant），op 映射与
锚点判定可参数化；``run_case`` 用 ``engine.Engine`` 进程内执行单个 case
（load_scene + Engine.run），把汇总结果经 ``report.write_records`` 写成
case 目录下的 summary.json / records.csv，Engine 异常记 ``ENV_ERROR`` 不
中断套件。target 可通过 ``summarize`` / ``write_evidence`` 挂自己的汇总
扩展（如 details/parameters）与证据 CSV。
"""

from __future__ import annotations

import statistics
import threading
from pathlib import Path
from typing import Any, Callable

from performance.engine import Engine, load_scene
from performance.profile import Profile
from performance.records import RequestRecord
from performance.report import write_records
from performance.stats import percentile


def _seconds(stage_ms: float) -> float:
    return stage_ms / 1000.0


def _rate_limited(record: RequestRecord) -> bool:
    """限流样本：http_4xx 且带 Retry-After 或 reason_code。"""
    return (
        record.status == "error"
        and record.error_type == "http_4xx"
        and (record.retry_after_s is not None or record.reason_code != "")
    )


def summarize_case_records(
    records: list[RequestRecord],
    *,
    search_op: str = "read",
    commit_submit_op: str = "commit_submit",
    commit_done_op: str = "commit_done",
    is_anchor: Callable[[str], bool] | None = None,
) -> dict:
    """records → case 契约摘要（只含 ``metrics``）。

    search = ``search_op`` 记录，commit = ``commit_submit_op`` /
    ``commit_done_op`` 记录；per_tenant 按 tenant_idx 分组；延迟统一为秒
    （stage_ms/1000）。``is_anchor`` 为 None 时 quality_asserted 记 0。
    """
    reads = [r for r in records if r.op == search_op]
    ok_reads = [r for r in reads if r.status == "ok"]
    read_latencies = [_seconds(r.stage_ms) for r in ok_reads]

    submits = [r for r in records if r.op == commit_submit_op]
    dones = [r for r in records if r.op == commit_done_op]
    ok_dones = [r for r in dones if r.status == "ok"]

    completed_by_tenant: dict[str, int] = {}
    for record in ok_dones:
        key = str(record.tenant_idx)
        completed_by_tenant[key] = completed_by_tenant.get(key, 0) + 1

    def _pct(values: list[float], p: float) -> float | None:
        value = percentile(values, p)
        return round(value, 3) if value is not None else None

    search = {
        "submitted": len(reads),
        "succeeded": len(ok_reads),
        "errors": len(reads) - len(ok_reads),
        "success_rate": (len(ok_reads) / len(reads)) if reads else None,
        "rate_limited_count": sum(1 for r in reads if _rate_limited(r)),
        "quality_asserted": (
            sum(1 for r in reads if is_anchor(r.query)) if is_anchor is not None else 0
        ),
        "quality_failures": sum(1 for r in reads if not r.quality_ok),
        "latency": {
            "mean_s": (
                round(statistics.mean(read_latencies), 3) if read_latencies else None
            ),
            "p50_s": _pct(read_latencies, 50),
            "p95_s": _pct(read_latencies, 95),
            "p99_s": _pct(read_latencies, 99),
        },
    }
    submitted = len(submits)
    completed = len(ok_dones)
    commit = {
        "submitted": submitted,
        "completed": completed,
        "failed": sum(1 for r in dones if r.status != "ok")
        + sum(1 for r in submits if r.status != "ok"),
        "success_rate": (completed / submitted) if submitted else None,
        "rate_limited_count": sum(1 for r in submits if _rate_limited(r)),
    }

    per_tenant: dict[str, dict[str, Any]] = {}
    for tenant_idx in sorted({str(r.tenant_idx) for r in records}):
        tenant_reads = [r for r in ok_reads if str(r.tenant_idx) == tenant_idx]
        tenant_submits = [
            r for r in submits if r.status == "ok" and str(r.tenant_idx) == tenant_idx
        ]
        tenant_dones = [
            _seconds(r.stage_ms) for r in ok_dones if str(r.tenant_idx) == tenant_idx
        ]
        if not tenant_reads and not tenant_submits and not tenant_dones:
            continue
        entry: dict[str, Any] = {}
        if tenant_submits or tenant_dones:
            entry["commit"] = {
                "submitted": len(tenant_submits),
                "completed": len(tenant_dones),
            }
            if tenant_dones:
                entry["commit"]["completion"] = {"p50_s": _pct(tenant_dones, 50)}
        if tenant_reads:
            entry["search"] = {
                "submitted": sum(1 for r in reads if str(r.tenant_idx) == tenant_idx),
                "succeeded": len(tenant_reads),
                "latency": {
                    "p50_s": _pct([_seconds(r.stage_ms) for r in tenant_reads], 50),
                    "p95_s": _pct([_seconds(r.stage_ms) for r in tenant_reads], 95),
                },
            }
        per_tenant[tenant_idx] = entry

    return {
        "metrics": {
            "search": search,
            "commit": commit,
            "fairness": {"commit_completed_per_tenant": completed_by_tenant},
            "per_tenant": per_tenant,
        },
    }


def run_case(
    case: dict,
    profile: Profile,
    *,
    scene_path: Path,
    case_dir: Path,
    timeout_s: float | None = None,
    summarize: Callable[[list[RequestRecord]], dict] | None = None,
    write_evidence: Callable[[Path, list[RequestRecord]], None] | None = None,
) -> dict:
    """执行单个 case：load_scene + Engine.run，写产物并返回 run dict。

    Engine 异常记 ``ENV_ERROR``；``timeout_s`` 用守护线程包裹 Engine.run，
    超时记 ``TIMEOUT`` 并返回已收集的（可能为空）records 摘要。
    ``summarize`` 缺省为通用 ``summarize_case_records``；
    ``write_evidence`` 在 summary.json/records.csv 之后写 target 专属
    证据文件。
    """
    scene = load_scene(scene_path)
    runner_timeout = False
    status = "completed"
    records: list[RequestRecord] = []
    try:
        if timeout_s and timeout_s > 0:
            holder: dict[str, Any] = {}

            def _execute() -> None:
                holder["result"] = Engine(profile, scene).run()

            thread = threading.Thread(target=_execute, daemon=True)
            thread.start()
            thread.join(timeout_s)
            if thread.is_alive():
                runner_timeout = True
                status = "TIMEOUT"
            else:
                records = holder["result"].records
        else:
            records = Engine(profile, scene).run().records
    except Exception:
        status = "ENV_ERROR"
    summarize_fn = summarize or summarize_case_records
    summary = summarize_fn(records)
    write_records(case_dir, records, summary)
    if write_evidence is not None:
        write_evidence(case_dir, records)
    return {
        "scenario": case["label"],
        "scenario_label": case["label"],
        "scene": case["scene"],
        "repetition": 1,
        "policy": "server-observe",
        "status": status,
        "duration_s": float(profile.load.duration_s),
        "case_timeout_s": float(timeout_s or 0),
        "runner_timeout": runner_timeout,
        "output_dir": str(case_dir.resolve()),
        "summary": summary,
    }

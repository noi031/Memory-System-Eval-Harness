"""单 instance profile 的正式套件执行。

``run_case`` 用 ``performance.engine.Engine`` 进程内执行单个 case
（load_scene + Engine.run），把 records 汇总成契约摘要并写 case 目录下的
summary.json / records.csv / commit_results.csv / search_results.csv；
Engine 异常记 ``ENV_ERROR`` 不中断套件。

``run_suite`` 编排一个 instance profile 的完整正式套件：prepare_command →
preflight → 灌种（TenantPreparer）→ 逐 case 执行 → acceptance 求值 →
写 suite.json / acceptance.json。探针编排（probe_artifacts）本轮留空。
"""

from __future__ import annotations

import csv
import json
import shutil
import statistics
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from performance.engine import Engine, load_scene
from performance.profile import Profile, TenantSpec
from performance.records import CSV_FIELDS, RequestRecord
from performance.stats import percentile
from performance.targets.echomem.acceptance.evaluate import (
    evaluate_pr421_acceptance,
)
from performance.targets.echomem.acceptance.preflight import run_preflight
from performance.targets.echomem.acceptance.seed import (
    TenantPreparer,
    load_tenant_specs,
)
from performance.targets.echomem.orchestrator.suites import (
    QuickSpec,
    build_case_profile,
    select_cases,
)
from performance.targets.echomem.protocol import is_anchor_query

SCENES_DIR = Path(__file__).resolve().parent.parent / "scenes"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seconds(stage_ms: float) -> float:
    return stage_ms / 1000.0


def _rate_limited(record: RequestRecord) -> bool:
    """限流样本：http_4xx 且带 Retry-After 或 reason_code（对齐 features.py）。"""
    return (
        record.status == "error"
        and record.error_type == "http_4xx"
        and (record.retry_after_s is not None or record.reason_code != "")
    )


def summarize_case_records(records: list[RequestRecord]) -> dict:
    """records → suite per-run summary（对齐 evaluate/scheduler 期望结构）。

    search = op=="read"，commit = commit_submit/commit_done；per_tenant 按
    tenant_idx 分组；延迟统一为秒（stage_ms/1000）。
    """
    reads = [r for r in records if r.op == "read"]
    ok_reads = [r for r in reads if r.status == "ok"]
    read_latencies = [_seconds(r.stage_ms) for r in ok_reads]

    submits = [r for r in records if r.op == "commit_submit"]
    dones = [r for r in records if r.op == "commit_done"]
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
        "quality_asserted": sum(1 for r in reads if is_anchor_query(r.query)),
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
            entry["commit"] = {"submitted": len(tenant_submits), "completed": len(tenant_dones)}
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
        "details": {"identity_mode": "independent_auth_keys", "quality_seed": []},
        "parameters": {"commit_delay_threshold_s": 10.0, "search_delay_threshold_s": 2.5},
    }


# ---------------------------------------------------------------------- #
#  单 case 执行与产物                                                    #
# ---------------------------------------------------------------------- #

def _write_commit_results(case_dir: Path, records: list[RequestRecord]) -> None:
    """commit_results.csv：每条 commit_submit 一行，按 commit_done 对账状态。"""
    done_by_session = {
        r.session_id: r
        for r in records
        if r.op == "commit_done" and r.session_id
    }
    rows: list[dict[str, Any]] = []
    for record in records:
        if record.op != "commit_submit":
            continue
        done = done_by_session.get(record.session_id)
        if done is not None and done.status == "ok":
            status, done_ms = "completed", done.stage_ms
        elif done is not None:
            status, done_ms = "failed", done.stage_ms
        else:
            status, done_ms = "submitted", None
        rows.append(
            {
                "tenant_idx": record.tenant_idx,
                "session_id": record.session_id,
                "archive_id": record.archive_id,
                "status": status,
                "submit_ms": round(record.stage_ms, 3),
                "done_ms": round(done_ms, 3) if done_ms is not None else "",
                "end_to_end_s": round(done_ms / 1000.0, 3) if done_ms is not None else "",
            }
        )
    with (case_dir / "commit_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "tenant_idx", "session_id", "archive_id", "status",
                "submit_ms", "done_ms", "end_to_end_s",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_search_results(case_dir: Path, records: list[RequestRecord]) -> None:
    """search_results.csv：每条 read 一行，含质量字段与拒绝响应证据。"""
    rows: list[dict[str, Any]] = []
    for record in records:
        if record.op != "read":
            continue
        if record.status == "ok":
            status_code = "200"
        elif record.error_type == "http_4xx" and (
            record.retry_after_s is not None or record.reason_code
        ):
            status_code = "429"
        else:
            status_code = "500"
        rows.append(
            {
                "query": record.query,
                "hit_count": record.hit_count,
                "quality_ok": record.quality_ok,
                "degraded": record.degraded,
                "status_code": status_code,
                "end_to_end_s": round(record.stage_ms / 1000.0, 3),
                "tenant": record.tenant_idx,
                "session_id": record.session_id,
                "retry_after_s": record.retry_after_s or "",
                "reason_code": record.reason_code,
            }
        )
    with (case_dir / "search_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "query", "hit_count", "quality_ok", "degraded", "status_code",
                "end_to_end_s", "tenant", "session_id", "retry_after_s", "reason_code",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_case_outputs(
    case_dir: Path,
    records: list[RequestRecord],
    summary: dict[str, Any],
) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (case_dir / "records.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_csv_row())
    _write_commit_results(case_dir, records)
    _write_search_results(case_dir, records)


def run_case(
    case: dict,
    profile: Profile,
    *,
    case_dir: Path,
    timeout_s: float | None = None,
) -> dict:
    """执行单个 case：load_scene + Engine.run，写产物并返回 run dict。

    Engine 异常记 ``ENV_ERROR``；``timeout_s`` 用守护线程包裹 Engine.run，
    超时记 ``TIMEOUT`` 并返回已收集的（可能为空）records 摘要。
    """
    scene = load_scene(SCENES_DIR / f"{case['scene']}.py")
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
    summary = summarize_case_records(records)
    _write_case_outputs(case_dir, records, summary)
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


# ---------------------------------------------------------------------- #
#  suite 编排                                                             #
# ---------------------------------------------------------------------- #

def _run_prepare_command(command: str) -> dict:
    """prepare_command 经 bash -lc 执行；Windows 无 bash 时降级为 NOT_RUN。"""
    if shutil.which("bash") is None:
        return {
            "status": "NOT_RUN",
            "command": command,
            "reason": "bash not available (Windows); command not executed",
        }
    completed = subprocess.run(
        ["bash", "-lc", command], capture_output=True, text=True, check=False
    )
    return {
        "status": "ok" if completed.returncode == 0 else "INCONCLUSIVE",
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def _finalize_suite(manifest: dict, suite_dir: Path) -> dict:
    if "acceptance" not in manifest:
        manifest["acceptance"] = evaluate_pr421_acceptance(manifest)
    (suite_dir / "suite.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (suite_dir / "acceptance.json").write_text(
        json.dumps(manifest["acceptance"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def run_suite(
    profile: dict,
    *,
    suite_dir: Path,
    quick: QuickSpec | None = None,
    profile_name: str = "4u8g",
    base_url: str = "",
    timeout_s: float = 120.0,
    scenarios: list[str] | None = None,
) -> dict:
    """执行单个 instance profile 的正式套件，返回 manifest。

    profile = instance-profiles JSON 里的单个 profile dict（name/base_url/
    tenant_config/preflight_config/allow_partial_tenants/quick_include_seed/
    prepare_command 等）。各阶段失败按 prepare/preflight/seed 段记录并提前
    返回（仍写 suite.json / acceptance.json）。``scenarios`` 非空时按 label
    过滤 case（默认跑 profile 全量）。
    """
    suite_dir = Path(suite_dir)
    suite_dir.mkdir(parents=True, exist_ok=True)
    base_url = (base_url or str(profile.get("base_url") or "")).rstrip("/")
    manifest: dict[str, Any] = {
        "created_at": _now_iso(),
        "base_url": base_url,
        "profile": profile_name,
        "instance_profile": str(profile.get("name") or ""),
        "tenant_config": str(profile.get("tenant_config") or ""),
        "preflight_config": str(profile.get("preflight_config") or ""),
        "allow_partial_tenants": bool(profile.get("allow_partial_tenants")),
        "metrics_enabled": bool(profile.get("metrics_enabled", True)),
        "resource_profile": profile.get("resource_profile") or {},
        "output_root": str(suite_dir.resolve()),
        "scenarios": [],
        "repeats": 1,
        "policies": ["server-observe"],
        "duration_cap_s": quick.duration_cap_s if quick else 0.0,
        "server_observation_mode": True,
        "client_admission_enabled": False,
        "probe_artifacts": {},
        "runs": [],
    }
    cases = select_cases(profile_name, scenarios)
    manifest["scenarios"] = [case["label"] for case in cases]

    prepare_command = profile.get("prepare_command")
    if prepare_command:
        prepare = _run_prepare_command(str(prepare_command))
        manifest["prepare"] = prepare
        if prepare["status"] != "ok":
            return _finalize_suite(manifest, suite_dir)

    preflight_config = profile.get("preflight_config")
    if preflight_config:
        preflight = run_preflight(str(preflight_config), timeout_s=30.0)
        manifest["preflight"] = {**preflight, "config": str(preflight_config)}
        if not preflight["ok"]:
            return _finalize_suite(manifest, suite_dir)
    else:
        manifest["preflight"] = {
            "status": "NOT_RUN", "config": "", "engines_checked": 0,
            "engines": [], "digest": "",
        }

    contexts = None
    seed_sessions = max(int(case.get("sessions_per_tenant", 1)) for case in cases)
    include_seed = bool(profile.get("quick_include_seed"))
    if quick is not None and not include_seed and not quick.include_seed:
        seed_sessions = min(seed_sessions, 1)
    seed_messages = max(int(case.get("messages_per_session", 3)) for case in cases)
    tenant_config = profile.get("tenant_config")
    if tenant_config:
        try:
            specs = load_tenant_specs(
                str(tenant_config),
                tenant_count=max(int(case["tenants"]) for case in cases),
            )
            preparer = TenantPreparer(base_url, tenant_specs=specs)
            contexts = preparer.prepare(
                seed_sessions, seed_messages, commit_poll_timeout_s=600.0
            )
            manifest["seed"] = {
                "status": "completed",
                "tenant_count": len(contexts),
                "identity_mode": preparer.identity_mode(),
                "seed_sessions_per_tenant": seed_sessions,
                "seed_messages_per_session": seed_messages,
            }
        except Exception as exc:
            manifest["seed"] = {"status": "ENV_ERROR", "error": str(exc)}
            return _finalize_suite(manifest, suite_dir)
    else:
        manifest["seed"] = {"status": "skipped", "reason": "no tenant_config"}

    for case in cases:
        tenant_count = case["tenants"]
        case_profile = build_case_profile(
            case,
            base_url=base_url,
            tenant_count=tenant_count,
            auth_headers={},
            quick=quick,
        )
        if contexts:
            usable = contexts[:tenant_count] if tenant_count > 0 else contexts
            case_profile.tenants = [
                TenantSpec(
                    name=ctx.tenant_id or f"tenant-{index}",
                    headers={"X-Auth-Key": ctx.auth_key} if ctx.auth_key else {},
                )
                for index, ctx in enumerate(usable)
            ]
            case_profile.params["queries"] = [
                query for ctx in usable for query in ctx.queries
            ]
        run = run_case(
            case, case_profile, case_dir=suite_dir / case["label"],
            timeout_s=timeout_s,
        )
        manifest["runs"].append(run)

    manifest["acceptance"] = evaluate_pr421_acceptance(manifest)
    return _finalize_suite(manifest, suite_dir)

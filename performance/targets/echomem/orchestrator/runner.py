"""单 instance profile 的正式套件执行。

``run_case`` 经通用层 ``performance.suite.run_case`` 执行单个 case：
echomem 侧只挂 summarize 包装（通用 metrics + details/parameters）与
commit/search 证据 CSV；Engine 异常记 ``ENV_ERROR`` 不中断套件。

``run_suite`` 编排一个 instance profile 的完整正式套件：prepare_command →
preflight → 灌种（TenantPreparer）→ 逐 case 执行 → acceptance 求值 →
写 suite.json / acceptance.json。探针编排（probe_artifacts）本轮留空。
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any

from performance.profile import Profile, TenantSpec
from performance.records import RequestRecord
from performance.suite import (
    run_case as run_case_impl,
    summarize_case_records as summarize_case_metrics,
)
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
from performance.util import now_iso, run_command

SCENES_DIR = Path(__file__).resolve().parent.parent / "scenes"


def summarize_case_records(records: list[RequestRecord]) -> dict:
    """records → suite per-run summary（通用 metrics + echomem 扩展）。

    通用层 ``suite.summarize_case_records`` 只产出 metrics；这里补
    details/parameters（identity_mode/quality_seed/延迟阈值），并用
    ``is_anchor_query`` 计算 quality_asserted。
    """
    summary = summarize_case_metrics(records, is_anchor=is_anchor_query)
    summary["details"] = {"identity_mode": "independent_auth_keys", "quality_seed": []}
    summary["parameters"] = {
        "commit_delay_threshold_s": 10.0,
        "search_delay_threshold_s": 2.5,
    }
    return summary


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


def _write_case_evidence(case_dir: Path, records: list[RequestRecord]) -> None:
    """commit/search 证据 CSV（echomem 专属，写在通用 summary/records 之后）。"""
    _write_commit_results(case_dir, records)
    _write_search_results(case_dir, records)


def run_case(
    case: dict,
    profile: Profile,
    *,
    case_dir: Path,
    timeout_s: float | None = None,
) -> dict:
    """执行单个 case（经通用层 run_case）：load_scene + Engine.run 并写产物。

    通用层负责执行与 summary.json/records.csv；这里挂上 echomem 的
    summarize 包装（metrics + details/parameters）与 commit/search 证据 CSV。
    """
    return run_case_impl(
        case,
        profile,
        scene_path=SCENES_DIR / f"{case['scene']}.py",
        case_dir=case_dir,
        timeout_s=timeout_s,
        summarize=summarize_case_records,
        write_evidence=_write_case_evidence,
    )


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
    result = run_command(["bash", "-lc", command], timeout_s=1800.0)
    return {
        "status": "ok" if result["status"] == "PASS" else "INCONCLUSIVE",
        "command": command,
        "returncode": result["returncode"],
        "stdout_tail": result["stdout"][-2000:],
        "stderr_tail": result["stderr"][-4000:],
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
        "created_at": now_iso(),
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

#!/usr/bin/env python3
"""Run the seven EchoMem target checks from one profile-aware entry point.

The suite orchestrates existing real-HTTP probes. It does not mock the target
service and does not change EchoMem code. Missing deployment controls are
reported as INCONCLUSIVE instead of being silently skipped.
"""

from __future__ import annotations

import argparse
import csv
import errno
import fcntl
import html
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .scheduler_acceptance import evaluate as evaluate_scheduler_acceptance
except ImportError:
    from scheduler_acceptance import evaluate as evaluate_scheduler_acceptance

OBJECTIVES = [
    ("O1", "单实例最大用户量 / 热用户量"),
    ("O2", "多规格实例调度与 config"),
    ("O3", "单租户故障下 Search P95 劣化 <= 20%"),
    ("O4", "多租户公平性 Jain >= 0.9"),
    ("O5", "Commit 洪泛时 Search P95 <= 5s"),
    ("O6", "202 Commit 崩溃恢复后 100% 重放且不丢序"),
    ("O7", "每层每租户四元组可观测指标"),
]
INCONCLUSIVE = "INCONCLUSIVE"
PASS = "PASS"
FAIL = "FAIL"

# The quick matrix is intended to return actionable black-box observations
# on a real model within a bounded window.  The report4/report6 A/B/D cases
# duplicate the same read/write/barrier signals and are too expensive when
# every real Commit is polled to completion.  Full acceptance still uses the
# complete catalog; quick mode is explicitly a smoke/diagnostic run.
QUICK_SCENARIOS = (
    "baseline,tenant-skew,search-priority-blackbox,"
    "saturation,capacity-2,capacity-4"
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_profiles(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    profiles = payload.get("profiles") if isinstance(payload, dict) else payload
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("profiles config must contain a non-empty profiles list")
    return [item for item in profiles if isinstance(item, dict) and item.get("name")]


def run_command(
    command: list[str],
    *,
    timeout_s: float,
    redact_values: set[str] | None = None,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    redact_values = redact_values or set()

    def safe_command() -> list[str]:
        return [
            "***configured***" if item in redact_values else item
            for item in command
        ]

    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        return {
            "status": "PASS" if completed.returncode == 0 else "FAIL",
            "returncode": completed.returncode,
            "command": safe_command(),
            "stdout": completed.stdout[-12000:],
            "stderr": completed.stderr[-12000:],
            "elapsed_s": (datetime.now(timezone.utc) - started).total_seconds(),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "TIMEOUT",
            "returncode": 124,
            "command": safe_command(),
            "stdout": str(exc.stdout or "")[-12000:],
            "stderr": str(exc.stderr or "")[-12000:],
            "elapsed_s": (datetime.now(timezone.utc) - started).total_seconds(),
        }


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _first_completed_commit_csv(formal_root: Path) -> tuple[Path, str] | None:
    candidates = sorted(formal_root.glob("**/commit_results.csv"))
    for path in candidates:
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, csv.Error):
            continue
        for row in rows:
            if (
                str(row.get("status") or "").lower()
                in {"completed", "complete", "success", "succeeded"}
            ):
                return path, str(row.get("tenant") or "")
    return None


def _resolve_auth_key(
    tenant_config: Path,
    tenant_selector: str = "",
) -> tuple[str, str]:
    """Resolve credentials for the tenant that produced the evidence.

    ``commit_results.csv`` historically stored either a zero-based tenant
    index or the tenant id.  Falling back to the first configured key makes a
    valid session look like an HTTP 400 when the completed row belongs to a
    different tenant, so selection must follow the evidence row.
    """
    try:
        payload = read_json(tenant_config)
        entries = payload.get("tenants") or []
        if not isinstance(entries, list) or not entries:
            return "", ""
        item: dict[str, Any] | None = None
        selector = str(tenant_selector or "").strip()
        if selector.isdigit():
            index = int(selector)
            if 0 <= index < len(entries) and isinstance(entries[index], dict):
                item = entries[index]
        if item is None:
            for candidate in entries:
                if (
                    isinstance(candidate, dict)
                    and str(candidate.get("tenant_id") or candidate.get("id") or "").strip()
                    == selector
                ):
                    item = candidate
                    break
        if item is None and not selector and isinstance(entries[0], dict):
            item = entries[0]
        if item is None:
            return "", ""
        direct = str(item.get("auth_key") or "")
        env_name = str(item.get("auth_key_env") or "")
        return direct or os.getenv(env_name, ""), env_name
    except (OSError, IndexError, TypeError):
        return "", ""


def _resolve_profile_path(value: str, profiles_path: Path) -> str:
    """Resolve relative profile paths next to the profile manifest."""
    if not value:
        return ""
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (profiles_path.parent / path).resolve())


def _materialize_fault_plan(
    plan_path: Path,
    *,
    base_url: str,
    output_path: Path,
) -> Path:
    """Resolve the selected service address in a run-local fault plan."""
    payload = read_json(plan_path)

    def replace(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace("${BASE_URL}", base_url.rstrip("/"))
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {str(key): replace(item) for key, item in value.items()}
        return value

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(replace(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def _add_option(command: list[str], flag: str, value: Any) -> None:
    if value not in (None, ""):
        command.extend([flag, str(value)])


def _run_limit_failure_sweep(
    profile: dict[str, Any],
    *,
    profile_dir: Path,
    profiles_path: Path,
    formal_root: Path,
    timeout_s: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run an optional real admission sweep after the bounded suite.

    The formal ``saturation`` case measures Search while Commit is busy.  It
    does not guarantee a queue-full response, so it cannot prove the
    429/503/Retry-After/reason_code contract by itself.  This separate sweep
    intentionally drives the public endpoints at explicit worker levels and
    keeps the raw rows for audit.
    """
    config = profile.get("limit_failure_sweep")
    if not isinstance(config, dict):
        return {}, {
            "limit_failure_sweep": {
                "status": "INCONCLUSIVE",
                "reason": "profile 未配置真实限流阶梯",
            }
        }

    tenant_value = str(profile.get("tenant_config") or "")
    tenant_path = Path(_resolve_profile_path(tenant_value, profiles_path))
    output = profile_dir / "limit-failure-sweep"
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "performance.probes.limit_failure_sweep",
        "--base-url",
        str(profile.get("base_url") or "http://127.0.0.1:8010"),
        "--tenant-config",
        str(tenant_path),
        "--out-dir",
        str(output),
        "--levels",
        str(config.get("levels") or "16,64,128,256"),
        "--timeout-s",
        str(config.get("timeout_s") or 8.0),
    ]
    session_root = str(config.get("session_root") or "").strip()
    if session_root:
        resolved_session_root = _resolve_profile_path(session_root, profiles_path)
        command += ["--session-root", resolved_session_root]
    else:
        # New sessions make the sweep independent of whichever formal case
        # happened to finish first and avoid cross-run session contamination.
        command += ["--session-root", str(formal_root), "--create-sessions"]
    for key, flag in (
        ("search_count", "--search-count"),
        ("open_count", "--open-count"),
        ("commit_count", "--commit-count"),
        ("workers", "--workers"),
    ):
        _add_option(command, flag, config.get(key))
    execution = run_command(command, timeout_s=min(timeout_s, 1800))
    commands = {"limit_failure_sweep": execution}
    summary_path = output / "summary.json"
    payload = read_json(summary_path)
    if not payload:
        return {}, commands
    return {
        "limit_failure_sweep": {
            **payload,
            "path": str(summary_path),
            "requests_path": str(output / "requests.csv"),
        }
    }, commands


def _run_configured_probes(
    profile: dict[str, Any],
    *,
    profile_dir: Path,
    profiles_path: Path,
    formal_root: Path,
    timeout_s: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run explicitly configured real probes and return artifacts/metadata."""
    artifacts: dict[str, Any] = {}
    commands: dict[str, Any] = {}
    base_url = str(profile.get("base_url") or "http://127.0.0.1:8010")
    tenant_path = Path(
        _resolve_profile_path(str(profile.get("tenant_config") or ""), profiles_path)
    )
    commit_artifact = _first_completed_commit_csv(formal_root)
    commit_csv = commit_artifact[0] if commit_artifact else None
    tenant_index = commit_artifact[1] if commit_artifact else ""
    auth_key, auth_key_env = _resolve_auth_key(tenant_path, tenant_index)
    redact = {auth_key} if auth_key else set()

    capability = profile.get("capability_probe")
    if isinstance(capability, dict):
        output = profile_dir / "capability-probe.json"
        command = [
            sys.executable,
            "-m",
            "performance.probes.capability_probe",
            "--base-url",
            base_url,
            "--out",
            str(output),
        ]
        if auth_key:
            command += ["--auth-key", auth_key]
        elif auth_key_env:
            command += ["--auth-key-env", auth_key_env]
        for key, flag in (
            ("session_id", "--session-id"),
            ("health_path", "--health-path"),
            ("metrics_path", "--metrics-path"),
            ("cursor_path", "--cursor-path"),
            ("operation_path", "--operation-path"),
            ("conflict_path", "--conflict-path"),
            ("ttl_path", "--ttl-path"),
            ("engine_path", "--engine-path"),
            ("fault_path", "--fault-path"),
            ("timeout_s", "--timeout-s"),
        ):
            _add_option(command, flag, capability.get(key))
        execution = run_command(
            command, timeout_s=min(timeout_s, 180), redact_values=redact
        )
        commands["capability_probe"] = execution
        payload = read_json(output)
        if payload:
            artifacts["capability_probe"] = {**payload, "path": str(output)}

    if commit_csv and tenant_path.is_file():
        output = profile_dir / "blackbox-contract-probe.json"
        command = [
            sys.executable,
            "-m",
            "performance.probes.blackbox_contract_probe",
            "--base-url",
            base_url,
            "--commit-csv",
            str(commit_csv),
            "--tenant",
            tenant_index,
            "--out",
            str(output),
        ]
        if auth_key:
            command += ["--auth-key", auth_key]
        elif auth_key_env:
            command += ["--auth-key-env", auth_key_env]
        execution = run_command(
            command, timeout_s=min(timeout_s, 180), redact_values=redact
        )
        commands["blackbox_probe"] = execution
        payload = read_json(output)
        if payload:
            artifacts["blackbox_contract_probe"] = {**payload, "path": str(output)}
    else:
        commands["blackbox_probe"] = {
            "status": "INCONCLUSIVE",
            "reason": (
                "本轮没有完成 Commit 或租户配置不存在，"
                "无法从真实 session 启动黑盒契约探测"
            ),
        }

    sweep_artifacts, sweep_commands = _run_limit_failure_sweep(
        profile,
        profile_dir=profile_dir,
        profiles_path=profiles_path,
        formal_root=formal_root,
        timeout_s=timeout_s,
    )
    artifacts.update(sweep_artifacts)
    commands.update(sweep_commands)

    recovery = profile.get("commit_recovery")
    if isinstance(recovery, dict) and tenant_path.is_file():
        output = profile_dir / "commit-recovery.json"
        command = [
            sys.executable,
            "-m",
            "performance.probes.commit_recovery_probe",
            "--base-url",
            base_url,
            "--container",
            str(recovery.get("container") or ""),
            "--tenant-config",
            str(tenant_path),
            "--out",
            str(output),
        ]
        for key, flag in (
            ("tenant", "--tenant"),
            ("kill_delay_s", "--kill-delay-s"),
            ("messages", "--messages"),
            ("content_chars", "--content-chars"),
            ("recovery_timeout_s", "--recovery-timeout-s"),
            ("poll_s", "--poll-s"),
        ):
            _add_option(command, flag, recovery.get(key))
        execution = run_command(command, timeout_s=min(timeout_s, 900), redact_values=redact)
        commands["commit_recovery"] = execution
        payload = read_json(output)
        if payload:
            artifacts["commit_recovery"] = {**payload, "path": str(output)}

    fault_plan_value = profile.get("fault_plan")
    if fault_plan_value:
        plan_path = Path(_resolve_profile_path(str(fault_plan_value), profiles_path))
        if plan_path.is_file():
            plan_path = _materialize_fault_plan(
                plan_path,
                base_url=base_url,
                output_path=profile_dir / "fault-plan.resolved.json",
            )
        output_dir = profile_dir / "fault-suite"
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "performance.probes.fault_suite",
            "--plan",
            str(plan_path),
            "--out-dir",
            str(output_dir),
            "--base-url",
            base_url,
        ]
        if auth_key:
            command += ["--auth-key", auth_key]
        if commit_csv:
            command += ["--commit-csv", str(commit_csv)]
        execution = run_command(command, timeout_s=min(timeout_s, 900), redact_values=redact)
        commands["fault_suite"] = execution
        payload = read_json(output_dir / "fault-suite.json")
        if payload:
            artifacts["fault_suite"] = {
                **payload,
                "path": str(output_dir / "fault-suite.json"),
            }

    return artifacts, commands


def acceptance_by_name(suite: dict[str, Any]) -> dict[str, dict[str, Any]]:
    acceptance = suite.get("acceptance") or {}
    return {
        str(item.get("name")): item
        for item in acceptance.get("checks") or []
        if isinstance(item, dict) and item.get("name")
    }


def objective_statuses(
    suite: dict[str, Any],
    *,
    recovery_configured: bool,
    metrics_configured: bool,
) -> list[dict[str, Any]]:
    checks = acceptance_by_name(suite)
    blackbox = suite.get("blackbox_contract_probe") or {}
    recovery = suite.get("commit_recovery") or {}
    fault_suite = suite.get("fault_suite") or {}
    capability = suite.get("capability_probe") or {}

    recovery_for_scheduler = dict(recovery)
    message_reconciliation = recovery_for_scheduler.get("message_reconciliation")
    if (
        "message_set_reconciled" not in recovery_for_scheduler
        and isinstance(message_reconciliation, dict)
    ):
        recovery_for_scheduler["message_set_reconciled"] = (
            str(message_reconciliation.get("status") or "") == PASS
        )
    if (
        "replay_verified" not in recovery_for_scheduler
        and isinstance(recovery_for_scheduler.get("idempotency_reconciliation"), dict)
    ):
        recovery_for_scheduler["replay_verified"] = (
            str(
                recovery_for_scheduler["idempotency_reconciliation"].get("status") or ""
            )
            == PASS
        )
    strict_acceptance = evaluate_scheduler_acceptance(
        suite,
        capability=capability,
        recovery=recovery_for_scheduler,
        fault=fault_suite,
    )
    strict_by_name = {
        str(item.get("name")): item
        for item in strict_acceptance.get("checks") or []
        if isinstance(item, dict) and item.get("name")
    }

    def strict(name: str, fallback: str = INCONCLUSIVE) -> dict[str, Any]:
        item = strict_by_name.get(name)
        return item if isinstance(item, dict) else {"status": fallback}

    def status(name: str, fallback: str = "INCONCLUSIVE") -> str:
        return str((checks.get(name) or {}).get("status") or fallback)

    def fault_case_statuses() -> list[str]:
        return [
            str((case.get("execution") or {}).get("result", {}).get("status") or INCONCLUSIVE)
            for case in fault_suite.get("cases") or []
            if isinstance(case, dict)
        ]

    fault_cases = fault_case_statuses()
    fault_has_search_observation = any(
        isinstance(case, dict)
        and (
            "search_p95" in case
            or "search" in case
            or "observer" in case
            or "isolation_ratio" in case
        )
        for case in fault_suite.get("cases") or []
    )
    recovery_reconcile = recovery.get("message_reconciliation") or {}
    cursor_reconcile = recovery.get("cursor_reconciliation") or {}
    idempotency_reconcile = recovery.get("idempotency_reconciliation") or {}
    recovery_statuses = [
        str(recovery.get("status") or INCONCLUSIVE),
        str(recovery_reconcile.get("status") or INCONCLUSIVE),
        str(cursor_reconcile.get("status") or INCONCLUSIVE),
        str(idempotency_reconcile.get("status") or INCONCLUSIVE),
    ]

    # O1 is a capacity ladder. It is a measured upper bound for active test
    # identities, not a product DAU forecast.
    capacity = [
        run for run in suite.get("runs") or []
        if str(run.get("scenario") or "").startswith("capacity-")
    ]
    capacity_ok = bool(capacity) and all(
        str(run.get("status")) == "completed" for run in capacity
    )
    instance_profiles = suite.get("instance_profiles")
    completed_profiles = (
        [
            item for item in instance_profiles
            if isinstance(item, dict)
            and str(item.get("status") or "").lower()
            in {"completed", "pass", "passed"}
            and int(item.get("completed_runs") or 0) > 0
        ]
        if isinstance(instance_profiles, list)
        else []
    )
    multi_spec_status = PASS if len(completed_profiles) >= 2 else INCONCLUSIVE

    def strict_observed(name: str) -> Any:
        return strict(name).get("observed", {})

    return [
        {
            "id": "O1",
            "name": OBJECTIVES[0][1],
            "status": strict("DAU / 最大热用户容量")["status"],
            "reason": strict("DAU / 最大热用户容量").get("reason", ""),
            "observed": strict_observed("DAU / 最大热用户容量"),
            "evidence": "scheduler_acceptance: DAU / 最大热用户容量",
        },
        {
            "id": "O2",
            "name": OBJECTIVES[1][1],
            "status": multi_spec_status,
            "reason": (
                "至少两种规格均有真实完成场景，可比较调度与 config"
                if multi_spec_status == PASS
                else "当前只完成单一规格或没有真实场景结果；仅有 profile 配置不能证明多规格调度"
            ),
            "observed": {"completed_profiles": completed_profiles},
            "evidence": completed_profiles,
        },
        {
            "id": "O3",
            "name": OBJECTIVES[2][1],
            "status": strict("单租户故障隔离")["status"],
            "reason": strict("单租户故障隔离").get("reason", ""),
            "observed": strict_observed("单租户故障隔离"),
            "evidence": "scheduler_acceptance: 单租户故障隔离",
        },
        {
            "id": "O4",
            "name": OBJECTIVES[3][1],
            "status": strict("Commit/Search 公平性 Jain")["status"],
            "reason": strict("Commit/Search 公平性 Jain").get("reason", ""),
            "observed": strict_observed("Commit/Search 公平性 Jain"),
            "evidence": "scheduler_acceptance: Commit/Search 公平性 Jain",
        },
        {
            "id": "O5",
            "name": OBJECTIVES[4][1],
            "status": strict("Search 优先于 Commit")["status"],
            "reason": strict("Search 优先于 Commit").get("reason", ""),
            "observed": strict_observed("Search 优先于 Commit"),
            "evidence": "scheduler_acceptance: Search 优先于 Commit",
        },
        {
            "id": "O6",
            "name": OBJECTIVES[5][1],
            "status": strict("Commit kill-9 恢复与重放")["status"],
            "reason": strict("Commit kill-9 恢复与重放").get("reason", ""),
            "observed": strict_observed("Commit kill-9 恢复与重放"),
            "evidence": "scheduler_acceptance: Commit kill-9 恢复与重放",
        },
        {
            "id": "O7",
            "name": OBJECTIVES[6][1],
            "status": strict("分层/分租户调度可观测性")["status"],
            "reason": strict("分层/分租户调度可观测性").get("reason", ""),
            "observed": strict_observed("分层/分租户调度可观测性"),
            "evidence": "scheduler_acceptance: 分层/分租户调度可观测性",
        },
    ]


def render_report(result: dict[str, Any], path: Path) -> None:
    rows = []
    for profile in result.get("profiles") or []:
        for objective in profile.get("objectives") or []:
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(profile.get('name')))}</td>"
                f"<td>{html.escape(str(objective.get('id')))} "
                f"{html.escape(str(objective.get('name')))}</td>"
                f"<td class='{html.escape(str(objective.get('status')).lower())}'>"
                f"{html.escape(str(objective.get('status')))}</td>"
                f"<td>{html.escape(str(objective.get('reason')))}"
                f"<br><code>{html.escape(json.dumps(objective.get('observed', {}), ensure_ascii=False, sort_keys=True))}</code></td>"
                f"<td><code>{html.escape(str(objective.get('evidence')))}</code></td>"
                "</tr>"
            )
    details = []
    for profile in result.get("profiles") or []:
        details.append(f"<h3>{html.escape(str(profile.get('name')))}</h3>")
        for key, label in (
            ("capability_probe", "能力探针"),
            ("blackbox_contract_probe", "黑盒契约探针"),
            ("limit_failure_sweep", "真实限流阶梯"),
            ("commit_recovery", "Commit 崩溃恢复探针"),
            ("fault_suite", "故障套件"),
        ):
            payload = profile.get(key)
            if not isinstance(payload, dict):
                continue
            checks_detail = payload.get("checks") or payload.get("cases") or []
            details.append(
                f"<details><summary>{label}："
                f"<strong>{html.escape(str(payload.get('status', '未返回')))}</strong>"
                "</summary>"
            )
            if payload.get("reason"):
                details.append(f"<p>{html.escape(str(payload['reason']))}</p>")
            if isinstance(checks_detail, list) and checks_detail:
                details.append(
                    "<table><thead><tr><th>检查项</th><th>状态</th><th>HTTP/耗时</th>"
                    "<th>说明</th></tr></thead><tbody>"
                )
                for item in checks_detail:
                    item = item if isinstance(item, dict) else {}
                    execution = item.get("execution") if isinstance(item.get("execution"), dict) else {}
                    nested = execution.get("result") if isinstance(execution.get("result"), dict) else {}
                    details.append(
                        "<tr>"
                        f"<td>{html.escape(str(item.get('name') or item.get('kind') or 'case'))}</td>"
                        f"<td>{html.escape(str(item.get('status') or nested.get('status') or ''))}</td>"
                        f"<td>{html.escape(str(item.get('http_status') or item.get('elapsed_s') or ''))}</td>"
                        f"<td>{html.escape(str(item.get('reason') or nested.get('reason') or ''))}</td>"
                        "</tr>"
                    )
                details.append("</tbody></table>")
            details.append(
                f"<p class='muted'>制品：<code>{html.escape(str(payload.get('path', '')))}</code></p></details>"
            )
    doc = f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EchoMem 七项目标自动化验收</title>
<style>
body{{font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#17212b;background:#f5f7f8;margin:0}}
main{{max-width:1280px;margin:auto;padding:28px 18px 56px}}section{{background:#fff;border:1px solid #dfe6ea;padding:18px;margin-top:14px}}
h1{{margin:0 0 6px;font-size:25px}}.muted{{color:#687784}}table{{border-collapse:collapse;width:100%}}
th,td{{border-bottom:1px solid #e7ecef;padding:9px;text-align:left;vertical-align:top}}th{{background:#f7f9fa}}
.pass{{color:#197c62;font-weight:700}}.fail,.timeout{{color:#b6403b;font-weight:700}}.inconclusive{{color:#9a6a00;font-weight:700}}
code{{background:#f0f3f5;padding:2px 4px}}.scroll{{overflow:auto}}
</style><main>
<section><h1>EchoMem 七项目标自动化验收</h1>
<div class="muted">生成时间：{html.escape(result.get("created_at", ""))} · 真实 HTTP：是 · mock 模型：否</div>
<p>报告只依据实际运行证据判定；缺少部署控制或服务端指标时标记为 INCONCLUSIVE，不推断为通过。</p></section>
<section class="scroll"><h2>逐 profile 目标状态</h2>
<table><thead><tr><th>Profile</th><th>目标</th><th>状态</th><th>说明</th><th>证据</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></section>
<section class="scroll"><h2>探针与黑盒证据明细</h2>
<p class="muted">这里显示真实 HTTP 探针实际检查到的内容。没有真实输入、控制能力或服务端观测时，状态保持 INCONCLUSIVE。</p>
{"".join(details)}</section>
</main></html>"""
    path.write_text(doc, encoding="utf-8")


def _acquire_output_lock(out_dir: Path):
    """Prevent two objective jobs from writing the same evidence tree."""
    lock_path = out_dir / ".objective-suite.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            raise RuntimeError(
                f"objective output directory is already locked: {out_dir}"
            ) from exc
        raise
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    return handle


def main() -> int:
    parser = argparse.ArgumentParser(description="Run EchoMem objective acceptance suite")
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--profile", default="", help="只运行一个 profile；默认运行全部")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--quick", action="store_true", help="bounded smoke matrix")
    parser.add_argument("--scenarios", default="", help="覆盖场景列表，逗号分隔")
    parser.add_argument("--quick-duration-cap-s", type=float, default=30.0)
    parser.add_argument("--quick-case-timeout-s", type=float, default=120.0)
    parser.add_argument(
        "--quick-barrier-count-cap",
        type=int,
        default=2,
        help=(
            "quick 模式的 barrier Commit 上限，默认 2；这是有界诊断值，不代表完整验收负载。"
            "完整套件请显式传 --barrier-count-cap 0"
        ),
    )
    parser.add_argument("--quick-commit-timeout-s", type=float, default=30.0)
    parser.add_argument("--quick-commit-max-attempts", type=int, default=1)
    parser.add_argument("--quick-commit-retry-backoff-s", type=float, default=0.0)
    parser.add_argument(
        "--quick-include-seed",
        action="store_true",
        help="quick 默认跳过真实模型灌种；打开后保留灌种",
    )
    parser.add_argument("--timeout-s", type=float, default=7200.0)
    parser.add_argument(
        "--skip-run",
        action="store_true",
        help="只根据已有 suite.json 生成总报告",
    )
    parser.add_argument(
        "--suite-path",
        type=Path,
        default=None,
        help="配合 --skip-run 读取已有 formal suite.json；不重新发送压测请求",
    )
    args = parser.parse_args()

    profiles = load_profiles(args.profiles)
    if args.profile:
        profiles = [item for item in profiles if str(item["name"]) == args.profile]
    if not profiles:
        parser.error("没有匹配的 profile")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    output_lock = None
    if not args.skip_run:
        try:
            output_lock = _acquire_output_lock(args.out_dir)
        except RuntimeError as exc:
            parser.error(str(exc))
    output_profiles: list[dict[str, Any]] = []
    for profile in profiles:
        name = str(profile["name"])
        profile_dir = args.out_dir / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        command_result: dict[str, Any] = {}
        suite_path = profile_dir / "suite.json"

        if not args.skip_run:
            prepare = str(profile.get("prepare_command") or "").strip()
            if prepare:
                command_result["prepare"] = run_command(
                    ["bash", "-lc", prepare], timeout_s=min(args.timeout_s, 900)
                )
                if command_result["prepare"]["status"] != "PASS":
                    output_profiles.append({
                        **profile,
                        "name": name,
                        "command": command_result,
                        "objectives": objective_statuses(
                            {}, recovery_configured=False, metrics_configured=False
                        ),
                    })
                    continue
            tenant_config = _resolve_profile_path(
                str(profile.get("tenant_config") or ""), args.profiles
            )
            preflight_config = _resolve_profile_path(
                str(profile.get("preflight_config") or ""), args.profiles
            )
            if not tenant_config or not preflight_config:
                command_result["run"] = {
                    "status": "INCONCLUSIVE",
                    "reason": "profile 缺少 tenant_config 或 preflight_config",
                }
            else:
                scenarios = args.scenarios or (QUICK_SCENARIOS if args.quick else "")
                command = [
                    sys.executable,
                    "-m",
                    "performance.formal_suite",
                    "--base-url",
                    str(profile.get("base_url") or "http://127.0.0.1:8010"),
                    "--tenant-config",
                    tenant_config,
                    "--preflight-config",
                    preflight_config,
                    "--profile",
                    # A quick run on the single available 4U8G instance uses
                    # the bounded catalog.  The full catalog includes long
                    # report(6) and capacity cases that are useful for formal
                    # acceptance but make a diagnostic run look stuck.
                    "4u8g" if args.quick and name.upper() == "4U8G" else "complete",
                    "--instance-profile",
                    name,
                    "--repeats",
                    "1",
                    "--out-dir",
                    str(profile_dir / "formal"),
                ]
                if scenarios:
                    command += [
                        "--scenarios", scenarios,
                        "--duration-cap-s", str(args.quick_duration_cap_s),
                        "--case-timeout-s", str(args.quick_case_timeout_s),
                        "--barrier-count-cap", str(args.quick_barrier_count_cap),
                        "--commit-timeout-s", str(args.quick_commit_timeout_s),
                        "--commit-max-attempts", str(args.quick_commit_max_attempts),
                        "--commit-retry-backoff-s",
                        str(args.quick_commit_retry_backoff_s),
                    ]
                    if args.quick:
                        command += ["--quick-mode"]
                if args.quick and not args.quick_include_seed:
                    command += ["--skip-seed", "--seed-sessions-per-tenant", "0"]
                command_result["run"] = run_command(command, timeout_s=args.timeout_s)
                formal_root = profile_dir / "formal"
                candidates = []
                if (formal_root / "suite.json").is_file():
                    candidates.append(formal_root / "suite.json")
                candidates.extend(sorted(formal_root.glob("*/suite.json")))
                if candidates:
                    suite_path = candidates[-1]
        else:
            configured_suite = str(
                (
                    args.suite_path
                    if args.suite_path is not None
                    else profile.get("suite_path") or profile.get("suite") or ""
                )
            ).strip()
            if configured_suite:
                suite_path = Path(configured_suite).expanduser().resolve()
                command_result["run"] = {
                    "status": "PASS",
                    "mode": "read-only-audit",
                    "reason": "只读取已有 suite.json，不重新发送压测请求",
                }

        suite = read_json(suite_path)
        formal_root = (
            suite_path.parent
            if args.skip_run and suite_path.is_file()
            else profile_dir / "formal"
        )
        probe_artifacts, probe_commands = _run_configured_probes(
            profile,
            profile_dir=profile_dir,
            profiles_path=args.profiles,
            formal_root=formal_root,
            timeout_s=args.timeout_s,
        )
        suite = {**suite, **probe_artifacts}
        command_result.update(probe_commands)

        completed_runs = 0
        for item in suite.get("runs") or []:
            if not isinstance(item, dict):
                continue
            summary = item.get("summary")
            if not isinstance(summary, dict):
                continue
            metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
            search = metrics.get("search") if isinstance(metrics.get("search"), dict) else {}
            commit = metrics.get("commit") if isinstance(metrics.get("commit"), dict) else {}
            if int(search.get("submitted") or 0) > 0 or int(commit.get("submitted") or 0) > 0:
                completed_runs += 1
        profile_execution_status = (
            "completed"
            if completed_runs > 0
            else str(command_result.get("run", {}).get("status") or "not_run")
        )
        output_profiles.append({
            **profile,
            "name": name,
            "suite": str(suite_path),
            "profile_execution_status": profile_execution_status,
            "completed_runs": completed_runs,
            **probe_artifacts,
            "command": command_result,
            "objectives": objective_statuses(
                {
                    **suite,
                    "profile_name": name,
                    "instance_profiles": [{
                        "name": name,
                        "status": profile_execution_status,
                        "completed_runs": completed_runs,
                    }],
                },
                recovery_configured=bool(profile.get("commit_recovery")),
                metrics_configured=bool(profile.get("metrics_enabled", True)),
            ),
        })

    completed_profile_records = [
        {
            "name": str(profile.get("name") or ""),
            "status": str(profile.get("profile_execution_status") or ""),
            "completed_runs": int(profile.get("completed_runs") or 0),
        }
        for profile in output_profiles
    ]
    completed_profile_count = sum(
        1
        for item in completed_profile_records
        if item["status"] == "completed" and item["completed_runs"] > 0
    )
    for profile in output_profiles:
        for objective in profile.get("objectives") or []:
            if objective.get("id") == "O2":
                objective["status"] = PASS if completed_profile_count >= 2 else INCONCLUSIVE
                objective["reason"] = (
                    "至少两种规格均有真实完成场景，可比较调度与 config"
                    if completed_profile_count >= 2
                    else "当前只完成单一规格或没有真实场景结果；仅有 profile 配置不能证明多规格调度"
                )
                objective["evidence"] = completed_profile_records
    result = {
        "created_at": now(),
        "profiles": output_profiles,
        "objectives": OBJECTIVES,
        "instance_profiles": completed_profile_records,
        "multi_spec_completed_count": completed_profile_count,
    }
    (args.out_dir / "objective-suite.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    render_report(result, args.out_dir / "objective-suite.html")
    print(args.out_dir / "objective-suite.html")
    return 0 if output_profiles else 2


if __name__ == "__main__":
    raise SystemExit(main())

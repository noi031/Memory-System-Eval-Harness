#!/usr/bin/env python3
"""Run the seven EchoMem target checks from one profile-aware entry point.

The suite orchestrates existing real-HTTP probes. It does not mock the target
service and does not change EchoMem code. Missing deployment controls are
reported as INCONCLUSIVE instead of being silently skipped.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

QUICK_SCENARIOS = (
    "A@1,B@1,D@1,baseline,mixed,commit-barrier,saturation,"
    "tenant-skew,search-priority-blackbox,capacity-2,capacity-4"
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


def _resolve_auth_key(tenant_config: Path) -> tuple[str, str]:
    try:
        payload = read_json(tenant_config)
        item = (payload.get("tenants") or [])[0]
        if not isinstance(item, dict):
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


def _add_option(command: list[str], flag: str, value: Any) -> None:
    if value not in (None, ""):
        command.extend([flag, str(value)])


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
    auth_key, auth_key_env = _resolve_auth_key(tenant_path)
    redact = {auth_key} if auth_key else set()

    commit_artifact = _first_completed_commit_csv(formal_root)
    commit_csv = commit_artifact[0] if commit_artifact else None
    tenant_index = commit_artifact[1] if commit_artifact else ""

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
    recovery_statuses = [
        str(recovery.get("status") or INCONCLUSIVE),
        str(recovery_reconcile.get("status") or INCONCLUSIVE),
        str(cursor_reconcile.get("status") or INCONCLUSIVE),
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
    return [
        {
            "id": "O1",
            "name": OBJECTIVES[0][1],
            "status": "PASS" if capacity_ok else "INCONCLUSIVE",
            "reason": (
                "容量阶梯场景全部完成；结果表示压测窗口内可承载上限，不等同于业务 DAU"
                if capacity_ok
                else "未完成 capacity-2/4/8/16/32 容量阶梯"
            ),
            "evidence": "suite.runs[scenario=capacity-*]",
        },
        {
            "id": "O2",
            "name": OBJECTIVES[1][1],
            "status": "PASS" if suite.get("profile_name") else "INCONCLUSIVE",
            "reason": (
                "由 objective_suite 按 instance-profiles 配置逐 profile 执行"
                if suite.get("profile_name")
                else "未通过 profile-aware 入口执行"
            ),
            "evidence": "profile.json + prepare_command + suite.json",
        },
        {
            "id": "O3",
            "name": OBJECTIVES[2][1],
            "status": (
                (
                    FAIL
                    if FAIL in fault_cases
                    else INCONCLUSIVE
                    if not fault_has_search_observation or INCONCLUSIVE in fault_cases
                    else PASS
                )
                if fault_suite.get("cases")
                and fault_has_search_observation
                else status("Search P95 isolation ratio")
            ),
            "reason": (
                "已执行真实故障探针，并包含旁观租户 Search P95 观测"
                if fault_has_search_observation
                and fault_cases
                and all(item == PASS for item in fault_cases)
                else "故障探针包含失败，需要检查故障窗口与旁观租户 Search P95 数据"
                if fault_has_search_observation
                and FAIL in fault_cases
                else "故障套件已执行，但没有旁观租户 Search P95 证据，不能判定隔离性"
                if fault_suite.get("cases")
                else "需要 baseline 与单租户故障/压力窗口的有效 Search P95 成对数据"
            ),
            "evidence": (
                str(fault_suite.get("path") or "fault-suite.json")
                if fault_suite.get("cases")
                else "acceptance: Search P95 isolation ratio"
            ),
        },
        {
            "id": "O4",
            "name": OBJECTIVES[3][1],
            "status": status("Tenant fairness (Jain)"),
            "reason": "需要至少两个独立认证租户的稳态 Commit 吞吐样本",
            "evidence": "acceptance: Tenant fairness (Jain)",
        },
        {
            "id": "O5",
            "name": OBJECTIVES[4][1],
            "status": status("Search success rate"),
            "reason": "优先级场景必须同时产生有效 Search 与 Commit 洪泛证据",
            "evidence": "search-priority-blackbox + acceptance: Search success rate",
        },
        {
            "id": "O6",
            "name": OBJECTIVES[5][1],
            "status": (
                (
                    FAIL
                    if FAIL in recovery_statuses
                    else INCONCLUSIVE
                    if INCONCLUSIVE in recovery_statuses
                    else PASS
                )
                if recovery_configured
                else "INCONCLUSIVE"
            ),
            "reason": (
                "真实重启、Commit 终态、消息集合和 cursor 对账均通过"
                if recovery_configured and all(item == PASS for item in recovery_statuses)
                else "真实重启已执行，但 Commit 终态或消息/cursor 对账未全部通过"
                if recovery_configured
                else "未配置真实 PID/container 重启控制，不能证明崩溃恢复重放"
            ),
            "evidence": "recovery plan + cursor/message-set reconciliation",
        },
        {
            "id": "O7",
            "name": OBJECTIVES[6][1],
            "status": (
                str(
                    next(
                        (
                            item.get("status")
                            for item in blackbox.get("checks", [])
                            if item.get("name") == "metrics"
                        ),
                        status("B7 lane/fan-out metrics"),
                    )
                )
                if metrics_configured
                else "INCONCLUSIVE"
            ),
            "reason": (
                "已采集 /metrics 四元组及 fan-out 指标"
                if metrics_configured
                else "未启用服务端 /metrics 采集"
            ),
            "evidence": "metrics_samples.csv + acceptance: B7 lane/fan-out metrics",
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
                f"<td>{html.escape(str(objective.get('reason')))}</td>"
                f"<td><code>{html.escape(str(objective.get('evidence')))}</code></td>"
                "</tr>"
            )
    details = []
    for profile in result.get("profiles") or []:
        details.append(f"<h3>{html.escape(str(profile.get('name')))}</h3>")
        for key, label in (
            ("capability_probe", "能力探针"),
            ("blackbox_contract_probe", "黑盒契约探针"),
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run EchoMem objective acceptance suite")
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--profile", default="", help="只运行一个 profile；默认运行全部")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--quick", action="store_true", help="bounded smoke matrix")
    parser.add_argument("--timeout-s", type=float, default=7200.0)
    parser.add_argument("--skip-run", action="store_true", help="只根据已有 suite.json 生成总报告")
    args = parser.parse_args()

    profiles = load_profiles(args.profiles)
    if args.profile:
        profiles = [item for item in profiles if str(item["name"]) == args.profile]
    if not profiles:
        parser.error("没有匹配的 profile")

    args.out_dir.mkdir(parents=True, exist_ok=True)
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
                scenarios = QUICK_SCENARIOS if args.quick else ""
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
                    "complete",
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
                        "--duration-cap-s", "30",
                        "--barrier-count-cap", "16",
                    ]
                command_result["run"] = run_command(command, timeout_s=args.timeout_s)
                formal_root = profile_dir / "formal"
                candidates = []
                if (formal_root / "suite.json").is_file():
                    candidates.append(formal_root / "suite.json")
                candidates.extend(sorted(formal_root.glob("*/suite.json")))
                if candidates:
                    suite_path = candidates[-1]

        suite = read_json(suite_path)
        formal_root = profile_dir / "formal"
        probe_artifacts, probe_commands = _run_configured_probes(
            profile,
            profile_dir=profile_dir,
            profiles_path=args.profiles,
            formal_root=formal_root,
            timeout_s=args.timeout_s,
        )
        suite = {**suite, **probe_artifacts}
        command_result.update(probe_commands)

        output_profiles.append({
            **profile,
            "name": name,
            "suite": str(suite_path),
            **probe_artifacts,
            "command": command_result,
            "objectives": objective_statuses(
                {**suite, "profile_name": name},
                recovery_configured=bool(profile.get("commit_recovery")),
                metrics_configured=bool(profile.get("metrics_enabled", True)),
            ),
        })

    result = {"created_at": now(), "profiles": output_profiles, "objectives": OBJECTIVES}
    (args.out_dir / "objective-suite.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    render_report(result, args.out_dir / "objective-suite.html")
    print(args.out_dir / "objective-suite.html")
    return 0 if output_profiles else 2


if __name__ == "__main__":
    raise SystemExit(main())

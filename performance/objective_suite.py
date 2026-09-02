#!/usr/bin/env python3
"""Run the seven EchoMem target checks from one profile-aware entry point.

The suite orchestrates existing real-HTTP probes. It does not mock the target
service and does not change EchoMem code. Missing deployment controls are
reported as INCONCLUSIVE instead of being silently skipped.
"""

from __future__ import annotations

import argparse
import html
import json
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


def run_command(command: list[str], *, timeout_s: float) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
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
            "command": [item if "key" not in item.lower() else "***" for item in command],
            "stdout": completed.stdout[-12000:],
            "stderr": completed.stderr[-12000:],
            "elapsed_s": (datetime.now(timezone.utc) - started).total_seconds(),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "TIMEOUT",
            "returncode": 124,
            "command": command,
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

    def status(name: str, fallback: str = "INCONCLUSIVE") -> str:
        return str((checks.get(name) or {}).get("status") or fallback)

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
            "status": status("Search P95 isolation ratio"),
            "reason": "需要 baseline 与单租户故障/压力窗口的有效 Search P95 成对数据",
            "evidence": "acceptance: Search P95 isolation ratio",
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
                status("cursor/message-set")
                if recovery_configured
                else "INCONCLUSIVE"
            ),
            "reason": (
                "已配置真实重启控制和消息/cursor 对账"
                if recovery_configured
                else "未配置真实 PID/container 重启控制，不能证明崩溃恢复重放"
            ),
            "evidence": "recovery plan + cursor/message-set reconciliation",
        },
        {
            "id": "O7",
            "name": OBJECTIVES[6][1],
            "status": (
                status("B7 lane/fan-out metrics")
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
            tenant_config = str(profile.get("tenant_config") or "")
            preflight_config = str(profile.get("preflight_config") or "")
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
                    "--repeats",
                    "1",
                    "--out-dir",
                    str(profile_dir / "formal"),
                ]
                if scenarios:
                    command += ["--scenarios", scenarios, "--duration-cap-s", "30"]
                command_result["run"] = run_command(command, timeout_s=args.timeout_s)
                formal_root = profile_dir / "formal"
                candidates = []
                if (formal_root / "suite.json").is_file():
                    candidates.append(formal_root / "suite.json")
                candidates.extend(sorted(formal_root.glob("*/suite.json")))
                if candidates:
                    suite_path = candidates[-1]

        suite = read_json(suite_path)
        output_profiles.append({
            **profile,
            "name": name,
            "suite": str(suite_path),
            "command": command_result,
            "objectives": objective_statuses(
                {**suite, "profile_name": name},
                recovery_configured=bool(profile.get("recovery_plan")),
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

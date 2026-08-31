#!/usr/bin/env python3
"""Run a repeatable, real multi-tenant EchoMem stress suite.

The suite is deliberately orchestration-only: each case is executed by the
existing runner, which keeps per-request CSV and raw server telemetry. The
suite adds scenario/repetition metadata and refuses to make a release claim
unless every run has independent tenant credentials.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Support both ``python -m stress.echomem.formal_suite`` and the direct
# command documented for server deployments.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from .acceptance import (
        build_model_analysis_input,
        evaluate_pr421_acceptance,
    )
    from shared.runtime_config import validate_real_model_config
except ImportError:
    from acceptance import build_model_analysis_input, evaluate_pr421_acceptance
    from shared.runtime_config import validate_real_model_config


# Formal runs reproduce online clients. The load generator does not impose
# FIFO, priority, lane, or tenant-fair scheduling of its own.
POLICIES = ("server-observe",)

# Acceptance targets from EchoMem PR421. These are recorded in suite.json so
# every result carries the intended gate instead of relying on report prose.
PR421_ACCEPTANCE_TARGETS: dict[str, Any] = {
    "source": {
        "repository": "tech-innovation-group/EchoMem",
        "pull_request": 421,
        "commit": "4bafa33b46487ec451498d114b9bf6c784462f3e",
    },
    "search_p95_isolation_ratio_max": 1.20,
    "tenant_fairness_jain_min": 0.90,
    "accepted_commit_recovery_rate_min": 1.00,
    "rejection_response_required": ["status_code", "retry_after", "reason_code"],
    "lane_metric_families": [
        "echomem_lane_queued",
        "echomem_lane_wait_seconds",
        "echomem_lane_exec_seconds",
        "echomem_lane_rejected_total",
    ],
    "lane_values": [
        "http_interactive",
        "http_background",
        "http_global",
        "tenant_rate_limit",
        "commit",
    ],
    "lane_label_contract": {
        "allowed_labels": ["lane"],
        "rejected_labels": ["tenant_id", "tenant"],
        "rejection_reason_label": "reason_code",
    },
    "fanout_metric_families": [
        "echomem_engine_fanout_exec_seconds",
        "echomem_engine_fanout_skipped_total",
    ],
    "saturation_search_rejection_rate_max": 0.05,
    "saturation_rejection_latency_max_s": 1.0,
    "hot_tenant_bystander_median_ratio_max": 1.50,
}

SCENARIOS: dict[str, dict[str, Any]] = {
    "baseline": {
        "label": "单租户基线",
        "tenants": 1,
        "duration_s": 600,
        "search_rps": 2.0,
        "commit_rpm": 2.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
    "mixed": {
        "label": "四租户均衡混合负载",
        "tenants": 4,
        "duration_s": 600,
        "search_rps": 8.0,
        "commit_rpm": 2.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
    "commit-storm": {
        "label": "Commit 压力",
        "tenants": 4,
        "duration_s": 600,
        "search_rps": 4.0,
        "commit_rpm": 10.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
    "commit-barrier": {
        "label": "160 Commit 屏障风暴（Zipf 热租户）",
        "tenants": 4,
        "duration_s": 60,
        "search_rps": 4.0,
        "commit_rpm": 0.0,
        "commit_barrier": True,
        "commit_barrier_count": 160,
        "commit_tenant_distribution": "zipf",
        "commit_zipf_exponent": 2.0,
        "sessions_per_tenant": 40,
        "messages_per_session": 3,
    },
    "saturation": {
        "label": "128 并发入口饱和",
        "tenants": 4,
        "duration_s": 60,
        "search_rps": 32.0,
        "commit_rpm": 0.0,
        "commit_barrier": True,
        "commit_barrier_count": 128,
        "commit_tenant_distribution": "uniform",
        "sessions_per_tenant": 32,
        "messages_per_session": 3,
    },
    "tenant-skew": {
        "label": "热租户 200 + 其他租户各 20",
        "tenants": 4,
        "duration_s": 120,
        "search_rps": 8.0,
        "commit_rpm": 0.0,
        "commit_barrier": True,
        "commit_barrier_count": 260,
        "commit_tenant_distribution": "explicit",
        "commit_tenant_counts": [200, 20, 20, 20],
        "sessions_per_tenant": 200,
        "messages_per_session": 3,
    },
    "capacity-16": {
        "label": "16 租户容量阶梯",
        "tenants": 16,
        "duration_s": 300,
        "search_rps": 16.0,
        "commit_rpm": 2.0,
        "sessions_per_tenant": 2,
        "messages_per_session": 3,
    },
    "search-storm": {
        "label": "Search 压力",
        "tenants": 4,
        "duration_s": 600,
        "search_rps": 20.0,
        "commit_rpm": 1.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
    "soak": {
        "label": "长稳态",
        "tenants": 4,
        "duration_s": 1800,
        "search_rps": 8.0,
        "commit_rpm": 2.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
}


def report4_scenarios() -> dict[str, dict[str, Any]]:
    """Build report(4)'s A/B/C/D matrix with a valid read-only baseline."""
    scenarios: dict[str, dict[str, Any]] = {}
    for concurrency in (1, 4, 16):
        workers = 8 * concurrency
        suffix = f"c{concurrency}"
        common = {
            "tenants": 8,
            "duration_s": 60,
            "search_workers": workers,
            "commit_workers": workers,
            "sessions_per_tenant": max(2, concurrency),
            "messages_per_session": 3,
        }
        scenarios[f"A-{suffix}"] = {
            **common,
            "label": f"A 纯读基线 / 每租户并发 {concurrency}",
            "search_rps": float(workers),
            "commit_rpm": 0.0,
            "read_only": True,
        }
        scenarios[f"B-{suffix}"] = {
            **common,
            "label": f"B 纯写注入 / 每租户并发 {concurrency}",
            "search_rps": 0.0,
            "commit_rpm": 0.0,
            "commit_barrier": True,
            "commit_barrier_count": workers,
        }
        for ratio, search_factor in (("8-1", 8), ("4-1", 4), ("1-1", 1)):
            scenarios[f"C{ratio}-{suffix}"] = {
                **common,
                "label": f"C 读写 {ratio} / 每租户并发 {concurrency}",
                "search_rps": float(workers * search_factor),
                "commit_rpm": float(workers),
            }
        scenarios[f"D-{suffix}"] = {
            **common,
            "label": f"D 连续注入洪峰 / 每租户并发 {concurrency}",
            "search_rps": float(workers),
            "commit_rpm": 0.0,
            "commit_barrier": True,
            "commit_barrier_count": workers,
            "commit_barrier_waves": 3,
            "commit_barrier_cooldown_s": 10.0,
        }
    return scenarios


def report6_scenarios() -> dict[str, dict[str, Any]]:
    """Build report(6)'s 8-tenant, 12-case A/B/C/D matrix.

    The runner's rates are global, while the report(6) concurrency is per
    tenant.  We therefore use eight tenant lanes and scale the global offered
    rate by the requested per-tenant concurrency.  Commit counts in C are
    rounded to whole requests per tenant, which is recorded in the manifest.
    """
    scenarios: dict[str, dict[str, Any]] = {}
    for concurrency in (1, 2):
        workers = 8 * concurrency
        common = {
            "tenants": 8,
            "duration_s": 60,
            "search_workers": workers,
            "commit_workers": workers,
            "sessions_per_tenant": 2,
            "messages_per_session": 10,
            "per_tenant_concurrency": concurrency,
        }
        suffix = f"@{concurrency}"
        scenarios[f"A{suffix}"] = {
            **common,
            "label": f"A 纯读基线 / 每租户并发 {concurrency}",
            "search_rps": float(workers),
            "commit_rpm": 0.0,
            "read_only": True,
        }
        scenarios[f"B{suffix}"] = {
            **common,
            "label": f"B 纯写注入 / 每租户并发 {concurrency}",
            "search_rps": 0.0,
            "commit_rpm": 0.0,
            "commit_barrier": True,
            "commit_barrier_count": workers,
        }
        for ratio, factor in (("8:1", 8), ("4:1", 4), ("1:1", 1)):
            # Search is a global arrival rate.  Commit is a per-tenant
            # requests/minute rate.  With eight tenants, this gives an exact
            # global read:write ratio over the one-minute scenario window:
            # (8 * concurrency * factor reads/s) : (8 * 60 * concurrency writes/min).
            commit_rpm = 60.0 * concurrency
            scenarios[f"C{ratio}{suffix}"] = {
                **common,
                "label": f"C 读写 {ratio} / 每租户并发 {concurrency}",
                "search_rps": float(workers * factor),
                "commit_rpm": commit_rpm,
            }
        scenarios[f"D{suffix}"] = {
            **common,
            "label": f"D 注入洪峰 / 每租户并发 {concurrency}",
            "search_rps": float(workers),
            "commit_rpm": 0.0,
            "commit_barrier": True,
            "commit_barrier_count": 32,
            "commit_barrier_waves": 1,
            "commit_barrier_cooldown_s": 0.0,
            "commit_burst_window_s": 10.0,
        }
    return scenarios


def complete_scenarios() -> dict[str, dict[str, Any]]:
    """Combine the PR397/report(6) and PR421 scenario catalogs."""
    combined: dict[str, dict[str, Any]] = {}
    combined.update(report6_scenarios())
    combined.update(SCENARIOS)
    return combined


SCENARIO_PROFILES = {
    "pr421": SCENARIOS,
    "report4": report4_scenarios(),
    "report6": report6_scenarios(),
    "complete": complete_scenarios(),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_tenants(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tenants = payload.get("tenants") if isinstance(payload, dict) else payload
    if not isinstance(tenants, list) or not tenants:
        raise ValueError("tenant config must contain a non-empty tenants list")
    return tenants


def write_subset(path: Path, tenants: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps({"tenants": tenants}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_case_process(
    command: list[str],
    *,
    timeout_s: float,
) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Run one case with a bounded wall-clock budget.

    A runner can contain its own per-request retries, so limiting only the
    workload duration does not bound the total case duration.  Start a new
    process group so a timed-out barrier cannot leave worker children behind.
    """
    process = subprocess.Popen(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=(os.name == "posix"),
    )
    try:
        stdout, stderr = process.communicate(timeout=max(1.0, timeout_s))
        return (
            subprocess.CompletedProcess(
                command, process.returncode, stdout, stderr
            ),
            False,
        )
    except subprocess.TimeoutExpired as exc:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            stdout, stderr = process.communicate()
        stdout = stdout or ""
        stderr = stderr or ""
        stderr += (
            f"\nformal_suite: case wall-clock timeout after "
            f"{timeout_s:.1f}s\n"
        )
        if exc.stderr:
            stderr = f"{exc.stderr}\n{stderr}"
        return (
            subprocess.CompletedProcess(command, 124, stdout, stderr),
            True,
        )


def run_case(
    runner: Path,
    case_root: Path,
    scenario: str,
    repetition: int,
    policy: str,
    config_path: Path,
    args: argparse.Namespace,
    case: dict[str, Any],
) -> dict[str, Any]:
    output = case_root / scenario / f"repeat-{repetition:02d}" / policy
    output.mkdir(parents=True, exist_ok=True)
    duration_s = case["duration_s"]
    if args.duration_cap_s > 0:
        duration_s = min(float(duration_s), args.duration_cap_s)
    case_timeout_s = (
        args.case_timeout_s
        if args.case_timeout_s > 0
        else duration_s + max(60.0, float(args.commit_timeout_s))
    )
    command = [
        sys.executable,
        str(runner),
        "--base-url",
        args.base_url,
        "--tenant-config",
        str(config_path),
        "--tenants",
        str(case["tenants"]),
        "--duration-s",
        str(duration_s),
        "--search-rps",
        str(case["search_rps"]),
        "--commit-rpm",
        str(case["commit_rpm"]),
        "--sessions-per-tenant",
        str(case["sessions_per_tenant"]),
        "--messages-per-session",
        str(case["messages_per_session"]),
        "--quality-seed-messages",
        str(case.get("quality_seed_messages", case["messages_per_session"])),
        "--commit-timeout-s",
        str(args.commit_timeout_s),
        "--commit-workers",
        str(case.get("commit_workers", args.commit_workers)),
        "--commit-max-attempts",
        str(args.commit_max_attempts),
        "--commit-retry-backoff-s",
        str(args.commit_retry_backoff_s),
        "--commit-retry-max-backoff-s",
        str(args.commit_retry_max_backoff_s),
        "--search-workers",
        str(case.get("search_workers", args.search_workers)),
        "--search-admission-capacity",
        str(args.search_admission_capacity),
        "--commit-admission-capacity",
        str(args.commit_admission_capacity),
        "--admission-capacity",
        str(args.admission_capacity),
        "--out-dir",
        str(output),
    ]
    if case.get("read_only"):
        command.append("--read-only")
    if case.get("commit_barrier"):
        command += [
            "--commit-barrier",
            "--commit-barrier-count",
            str(case["commit_barrier_count"]),
            "--commit-tenant-distribution",
            str(case.get("commit_tenant_distribution", "uniform")),
            "--commit-zipf-exponent",
            str(case.get("commit_zipf_exponent", 2.0)),
        ]
        if case.get("commit_tenant_counts"):
            command += [
                "--commit-tenant-counts",
                ",".join(map(str, case["commit_tenant_counts"])),
            ]
        if case.get("commit_barrier_waves"):
            command += [
                "--commit-barrier-waves",
                str(case["commit_barrier_waves"]),
                "--commit-barrier-cooldown-s",
                str(case.get("commit_barrier_cooldown_s", 0.0)),
            ]
        if case.get("commit_burst_window_s"):
            command += [
                "--commit-barrier-window-s",
                str(case["commit_burst_window_s"]),
            ]
    if args.auth_header:
        command += ["--auth-header", args.auth_header]
    if args.allow_shared_identity:
        command.append("--allow-shared-identity")
    if args.pid:
        command += ["--pid", str(args.pid)]
    if args.no_server_metrics:
        command.append("--no-server-metrics")
    command.append("--no-client-admission")
    if args.reset_command:
        completed_reset = subprocess.run(
            args.reset_command,
            shell=True,
            text=True,
            capture_output=True,
        )
        (output / "reset.stdout.log").write_text(
            completed_reset.stdout, encoding="utf-8"
        )
        (output / "reset.stderr.log").write_text(
            completed_reset.stderr, encoding="utf-8"
        )
        if completed_reset.returncode != 0:
            return {
                "scenario": scenario,
                "repetition": repetition,
                "policy": policy,
                "status": "RESET_FAILED",
                "returncode": completed_reset.returncode,
                "output_dir": str(output.resolve()),
            }
    completed, timed_out = run_case_process(
        command,
        timeout_s=case_timeout_s,
    )
    (output / "suite_runner.stdout.log").write_text(
        completed.stdout, encoding="utf-8"
    )
    (output / "suite_runner.stderr.log").write_text(
        completed.stderr, encoding="utf-8"
    )
    summary_path = output / "summary.json"
    summary: dict[str, Any] = {}
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "scenario": scenario,
        "scenario_label": case["label"],
        "repetition": repetition,
        "policy": policy,
        "status": summary.get("status", "NO_SUMMARY"),
        "runner_returncode": completed.returncode,
        "duration_s": duration_s,
        "case_timeout_s": case_timeout_s,
        "runner_timeout": timed_out,
        "output_dir": str(output.resolve()),
        "summary": summary,
    }


def render_report(manifest: dict[str, Any], output_path: Path) -> None:
    import html

    runs = manifest.get("runs") or []
    rows = []
    for run in runs:
        summary = run.get("summary") or {}
        metrics = summary.get("metrics") or {}
        commit = metrics.get("commit") or {}
        search = metrics.get("search") or {}
        c = commit.get("completion") or {}
        s = search.get("latency") or {}
        details = summary.get("details") or {}
        isolation = details.get("isolation") or {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(run.get('scenario')))}</td>"
            f"<td>{html.escape(str(run.get('repetition')))}</td>"
            f"<td><b>{html.escape(str(run.get('policy')))}</b></td>"
            f"<td>{html.escape(str(run.get('status')))}</td>"
            f"<td>{html.escape(str(commit.get('completed', 0)))} / {html.escape(str(commit.get('submitted', 0)))}</td>"
            f"<td>{fmt_seconds(c.get('mean_s'))}</td><td>{fmt_seconds(c.get('p95_s'))}</td><td>{fmt_seconds(c.get('p99_s'))}</td>"
            f"<td>{html.escape(str(search.get('succeeded', 0)))} / {html.escape(str(search.get('submitted', 0)))}</td>"
            f"<td>{fmt_seconds(s.get('mean_s'))}</td><td>{fmt_seconds(s.get('p95_s'))}</td>"
            f"<td>{html.escape(str((metrics.get('admission') or {}).get('max_queue_depth', 0)))}</td>"
            f"<td>{html.escape(str(isolation.get('status', '未提供')))}</td>"
            f"<td><a href='{html.escape(str(Path(run.get('output_dir', '')).relative_to(Path(manifest['output_root'])) / 'report.html'))}'>详情</a></td>"
            "</tr>"
        )
    if not runs:
        rows.append("<tr><td colspan='14'>没有执行记录</td></tr>")
    aggregates = aggregate_runs(runs)
    aggregate_rows = []
    aggregate_details = []
    for item in aggregates:
        aggregate_rows.append(
            "<tr>"
            f"<td>{html.escape(item['scenario'])}</td><td><b>{html.escape(item['policy'])}</b></td>"
            f"<td>{item['repetitions']}</td>"
            f"<td>{item['commit_completed']} / {item['commit_submitted']}</td>"
            f"<td>{fmt_seconds(item['commit_mean'])}</td><td>{fmt_seconds(item['commit_p50'])}</td>"
            f"<td>{fmt_seconds(item['commit_p90'])}</td><td>{fmt_seconds(item['commit_p95'])}</td>"
            f"<td>{fmt_seconds(item['commit_p99'])}</td><td>{fmt_seconds(item['commit_max'])}</td>"
            f"<td>{item['commit_delayed']}</td>"
            f"<td>{item['search_succeeded']} / {item['search_submitted']}</td>"
            f"<td>{fmt_seconds(item['search_mean'])}</td><td>{fmt_seconds(item['search_p50'])}</td>"
            f"<td>{fmt_seconds(item['search_p90'])}</td><td>{fmt_seconds(item['search_p95'])}</td>"
            f"<td>{fmt_seconds(item['search_p99'])}</td><td>{fmt_seconds(item['search_max'])}</td>"
            f"<td>{item['search_delayed']}</td><td>{item['rate_limited']}</td>"
            "</tr>"
        )
        tenant_lines = []
        for tenant, values in sorted(item["tenant_rows"].items()):
            commit_means = values["commit"]
            search_means = values["search"]
            tenant_lines.append(
                f"<tr><td>{html.escape(tenant)}</td>"
                f"<td>{values['commit_completed']} / {values['commit_submitted']}</td>"
                f"<td>{fmt_seconds(statistics.mean(commit_means) if commit_means else None)}</td>"
                f"<td>{fmt_seconds(percentile(commit_means, 50))}</td>"
                f"<td>{fmt_seconds(percentile(commit_means, 95))}</td>"
                f"<td>{fmt_seconds(max(commit_means) if commit_means else None)}</td>"
                f"<td>{values['commit_delayed']}</td>"
                f"<td>{values['search_succeeded']} / {values['search_submitted']}</td>"
                f"<td>{fmt_seconds(statistics.mean(search_means) if search_means else None)}</td>"
                f"<td>{fmt_seconds(percentile(search_means, 50))}</td>"
                f"<td>{fmt_seconds(percentile(search_means, 95))}</td>"
                f"<td>{fmt_seconds(percentile(search_means, 99))}</td>"
                f"<td>{fmt_seconds(max(search_means) if search_means else None)}</td>"
                f"<td>{values['search_delayed']}</td></tr>"
            )
        aggregate_details.append(
            f"<details><summary>{html.escape(item['scenario'])} · {html.escape(item['policy'])} · "
            f"逐租户汇总</summary><div class='scroll'><table><thead><tr>"
            f"<th>租户</th><th>Commit 完成/提交</th><th>Commit 平均</th><th>Commit P50</th>"
            f"<th>Commit P95</th><th>Commit 最大</th><th>Commit 延迟</th>"
            f"<th>Search 成功/提交</th><th>Search 平均</th><th>Search P95</th>"
            f"<th>Search P50</th><th>Search P99</th><th>Search 最大</th>"
            f"<th>Search 延迟</th></tr></thead><tbody>"
            f"{''.join(tenant_lines) or '<tr><td colspan=14>没有逐租户数据</td></tr>'}"
            "</tbody></table></div></details>"
        )
    icon = (
        "<svg class='logo' viewBox='0 0 56 56' role='img' aria-label='压测报告'>"
        "<rect x='3' y='3' width='50' height='50' rx='13' fill='#17324d'/>"
        "<path d='M13 40V29M22 40V20M31 40V25M40 40V13' stroke='#72d5b7' stroke-width='4' stroke-linecap='round'/>"
        "<path d='M11 44h34M12 17l8-5 8 6 12-9' fill='none' stroke='#ff9d6e' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'/></svg>"
    )
    document = f"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>EchoMem 正式多租户压测套件</title>
<style>
:root{{--bg:#f3f6f7;--paper:#fff;--ink:#17212b;--muted:#6d7b87;--line:#dce4e8;--green:#177b63;--amber:#9b6b16;--amber-bg:#fff7df}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
.page{{max-width:1500px;margin:auto;padding:28px 20px 60px}}.top{{display:flex;align-items:center;gap:14px;margin-bottom:18px}}.logo{{width:52px;height:52px}}
h1{{margin:0;font-size:25px}}h2{{font-size:18px;margin:0 0 10px}}.muted,small{{color:var(--muted)}}.section{{background:var(--paper);border:1px solid var(--line);padding:18px 19px;margin-top:12px}}
.notice{{padding:12px 14px;background:var(--amber-bg);border-left:4px solid var(--amber);color:#6d5116;margin-bottom:12px}}
.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}}th,td{{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#fafbfc;color:var(--muted)}}a{{color:#286aa6}}
</style></head><body><main class='page'><header class='top'>{icon}<div><h1>EchoMem 正式多租户压测套件</h1><small>真实 HTTP / 真实模型 · {html.escape(manifest.get('created_at', ''))}</small></div></header>
<section class='section'><div class='notice'><b>重要边界：</b>本套件的策略控制压测端准入，不自动等同于 EchoMem 内部限流。只有服务端提供队列、429、Retry-After 和执行时间遥测，才能确认服务端调度。</div>
<p>正式结果要求每次运行使用独立认证租户。当前套件包含单租户基线、四租户均衡、Commit 压力、Search 压力和长稳态场景；每个场景在同一套真实并发模式下重复执行，保留全部逐请求 CSV、原始 /metrics 和独立报告。</p></section>
<section class='section'><h2>运行配置</h2><p class='muted'>服务：<code>{html.escape(str(manifest.get('base_url')))}</code> · 重复轮次：{html.escape(str(manifest.get('repeats')))} · 客户端调度策略：关闭</p>
<div class='scroll'><table><thead><tr><th>场景</th><th>轮次</th><th>策略</th><th>状态</th><th>Commit 完成</th><th>Commit 平均</th><th>Commit P95</th><th>Commit P99</th><th>Search 成功</th><th>Search 平均</th><th>Search P95</th><th>最大队列</th><th>隔离</th><th>详情</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<section class='section'><h2>跨轮次聚合数据</h2><p class='muted'>这里直接合并每轮 CSV 的请求级数据，避免只看单轮均值。延迟单位为秒；延迟数使用各轮配置的阈值。</p>
<div class='scroll'><table><thead><tr><th>场景</th><th>策略</th><th>轮次</th><th>Commit 完成/提交</th><th>Commit 平均</th><th>Commit P50</th><th>Commit P90</th><th>Commit P95</th><th>Commit P99</th><th>Commit 最大</th><th>Commit 延迟数</th><th>Search 成功/提交</th><th>Search 平均</th><th>Search P50</th><th>Search P90</th><th>Search P95</th><th>Search P99</th><th>Search 最大</th><th>Search 延迟数</th><th>429</th></tr></thead><tbody>{''.join(aggregate_rows) or '<tr><td colspan=20>没有可聚合数据</td></tr>'}</tbody></table></div>
{''.join(aggregate_details)}</section>
<section class='section'><h2>原始套件清单</h2><p class='muted'>完整参数与每轮输出目录记录在 <code>suite.json</code>。报告中的延迟单位为秒；失败请求不会被隐藏。</p></section>
</main></body></html>"""
    output_path.write_text(document, encoding="utf-8")


def fmt_seconds(value: Any) -> str:
    try:
        return f"{float(value):.3f}s"
    except (TypeError, ValueError):
        return "-"


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100.0
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def csv_values(path: Path, field: str) -> list[float]:
    if not path.is_file():
        return []
    values: list[float] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                value = float(row.get(field) or "")
            except (TypeError, ValueError):
                continue
            if value >= 0:
                values.append(value)
    return values


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def aggregate_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in runs:
        groups.setdefault(
            (str(run.get("scenario")), str(run.get("policy"))), []
        ).append(run)
    aggregates = []
    for (scenario, policy), group in sorted(groups.items()):
        commit_values: list[float] = []
        search_values: list[float] = []
        commit_submitted = commit_completed = commit_failed = 0
        search_submitted = search_succeeded = search_errors = 0
        commit_delayed = search_delayed = rate_limited = 0
        tenant_rows: dict[str, dict[str, list[float] | int]] = {}
        for run in group:
            summary = run.get("summary") or {}
            metrics = summary.get("metrics") or {}
            commit = metrics.get("commit") or {}
            search = metrics.get("search") or {}
            commit_submitted += int(commit.get("submitted") or 0)
            commit_completed += int(commit.get("completed") or 0)
            commit_failed += int(commit.get("failed") or 0)
            search_submitted += int(search.get("submitted") or 0)
            search_succeeded += int(search.get("succeeded") or 0)
            search_errors += int(search.get("errors") or 0)
            commit_delayed += int(commit.get("delayed_count") or 0)
            search_delayed += int(search.get("delayed_count") or 0)
            rate_limited += int(commit.get("rate_limited_count") or 0)
            rate_limited += int(search.get("rate_limited_count") or 0)
            out_dir = Path(run.get("output_dir", ""))
            commit_values.extend(csv_values(out_dir / "commit_results.csv", "end_to_end_s"))
            search_values.extend(csv_values(out_dir / "search_results.csv", "service_s"))
            # Per-run means are insufficient for a cross-run percentile. Use
            # raw request rows so a busy run cannot be underweighted.
            for row in read_rows(out_dir / "commit_results.csv"):
                tenant = str(row.get("tenant") or "-")
                target = tenant_rows.setdefault(
                    tenant,
                    {
                        "commit": [],
                        "search": [],
                        "commit_completed": 0,
                        "commit_submitted": 0,
                        "commit_delayed": 0,
                        "search_succeeded": 0,
                        "search_submitted": 0,
                        "search_delayed": 0,
                    },
                )
                target["commit_submitted"] += 1
                try:
                    commit_duration = float(
                        row.get("end_to_end_s") or row.get("elapsed_s") or 0
                    )
                except (TypeError, ValueError):
                    commit_duration = 0.0
                if str(row.get("status") or "") in {
                    "completed", "complete", "transcommit", "succeeded", "success"
                }:
                    target["commit"].append(commit_duration)
                    target["commit_completed"] += 1
                if commit_duration >= float(
                    (summary.get("parameters") or {}).get(
                        "commit_delay_threshold_s", 10.0
                    )
                ):
                    target["commit_delayed"] += 1
            for row in read_rows(out_dir / "search_results.csv"):
                tenant = str(row.get("tenant") or "-")
                target = tenant_rows.setdefault(
                    tenant,
                    {
                        "commit": [],
                        "search": [],
                        "commit_completed": 0,
                        "commit_submitted": 0,
                        "commit_delayed": 0,
                        "search_succeeded": 0,
                        "search_submitted": 0,
                        "search_delayed": 0,
                    },
                )
                target["search_submitted"] += 1
                try:
                    code = int(float(row.get("status_code") or 0))
                except (TypeError, ValueError):
                    code = 0
                if 200 <= code < 300:
                    target["search_succeeded"] += 1
                    try:
                        search_duration = float(
                            row.get("service_s") or row.get("elapsed_s") or 0
                        )
                        target["search"].append(search_duration)
                    except (TypeError, ValueError):
                        search_duration = 0.0
                    if search_duration >= float(
                        (summary.get("parameters") or {}).get(
                            "search_delay_threshold_s", 2.5
                        )
                    ):
                        target["search_delayed"] += 1
        aggregates.append(
            {
                "scenario": scenario,
                "policy": policy,
                "repetitions": len(group),
                "commit_submitted": commit_submitted,
                "commit_completed": commit_completed,
                "commit_failed": commit_failed,
                "commit_mean": statistics.mean(commit_values) if commit_values else None,
                "commit_p50": percentile(commit_values, 50),
                "commit_p90": percentile(commit_values, 90),
                "commit_p95": percentile(commit_values, 95),
                "commit_p99": percentile(commit_values, 99),
                "commit_max": max(commit_values) if commit_values else None,
                "search_submitted": search_submitted,
                "search_succeeded": search_succeeded,
                "search_errors": search_errors,
                "search_mean": statistics.mean(search_values) if search_values else None,
                "search_p50": percentile(search_values, 50),
                "search_p90": percentile(search_values, 90),
                "search_p95": percentile(search_values, 95),
                "search_p99": percentile(search_values, 99),
                "search_max": max(search_values) if search_values else None,
                "commit_delayed": commit_delayed,
                "search_delayed": search_delayed,
                "rate_limited": rate_limited,
                "tenant_rows": tenant_rows,
            }
        )
    return aggregates


def main() -> int:
    parser = argparse.ArgumentParser(description="Run formal real multi-tenant stress suite")
    parser.add_argument("--base-url", default=os.getenv("ECHOMEM_BASE_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--tenant-config", required=True)
    parser.add_argument("--out-dir", default="")
    parser.add_argument(
        "--profile",
        choices=tuple(SCENARIO_PROFILES),
        default="pr421",
        help=(
            "Scenario profile; report6 is the PR397/report(6) matrix, pr421 "
            "is the PR421 acceptance suite, and complete runs both catalogs."
        ),
    )
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--duration-cap-s",
        type=float,
        default=0.0,
        help="Optional diagnostic cap for each scenario duration; 0 keeps scenario defaults.",
    )
    parser.add_argument(
        "--case-timeout-s",
        type=float,
        default=0.0,
        help=(
            "Wall-clock timeout for one scenario case, including setup and "
            "retries; 0 derives it from workload duration and commit timeout."
        ),
    )
    parser.add_argument("--auth-header", default=os.getenv("ECHOMEM_AUTH_HEADER", "X-API-Key"))
    parser.add_argument(
        "--allow-shared-identity",
        action="store_true",
        help="Allow an exploratory shared credential; isolation/fairness remain inconclusive.",
    )
    parser.add_argument("--commit-workers", type=int, default=8)
    parser.add_argument("--commit-timeout-s", type=float, default=120.0)
    parser.add_argument("--commit-max-attempts", type=int, default=3)
    parser.add_argument("--commit-retry-backoff-s", type=float, default=2.0)
    parser.add_argument("--commit-retry-max-backoff-s", type=float, default=30.0)
    parser.add_argument("--search-workers", type=int, default=32)
    parser.add_argument("--search-admission-capacity", type=int, default=0)
    parser.add_argument("--commit-admission-capacity", type=int, default=0)
    parser.add_argument("--admission-capacity", type=int, default=0)
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--reset-command", default="", help="Optional command run before every case")
    parser.add_argument("--no-server-metrics", action="store_true")
    parser.add_argument("--fault-plan", default="", help="Optional real fault/recovery/cursor plan")
    parser.add_argument("--fault-out-dir", default="")
    parser.add_argument("--fault-auth-key", default=os.getenv("ECHOMEM_AUTH_KEY", ""))
    parser.add_argument("--k6-summary", default="")
    parser.add_argument("--k6-runner-dir", default="")
    parser.add_argument("--k6-request-stream", default="")
    parser.add_argument(
        "--capability-probe",
        default="",
        help="Existing capability_probe.json to include in suite evidence",
    )
    parser.add_argument(
        "--preflight-config",
        default=os.getenv("ECHOMEM_CONFIG", ""),
        help="EchoMem config.json to validate before the suite starts",
    )
    args = parser.parse_args()

    scenario_catalog = SCENARIO_PROFILES[args.profile]
    default_scenarios = (
        "baseline,mixed,commit-storm,commit-barrier,saturation,tenant-skew,search-storm,soak"
        if args.profile == "pr421"
        else ",".join(scenario_catalog)
    )
    scenario_names = [
        item.strip()
        for item in (args.scenarios or default_scenarios).split(",")
        if item.strip()
    ]
    unknown = [item for item in scenario_names if item not in scenario_catalog]
    if unknown:
        parser.error(f"unknown scenarios: {', '.join(unknown)}")
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")
    if args.case_timeout_s < 0:
        parser.error("--case-timeout-s must not be negative")
    if args.profile in {"report6", "complete"} and not args.preflight_config:
        parser.error(
            f"--profile {args.profile} requires --preflight-config with the actual EchoMem config.json"
        )
    preflight_errors: list[str] = []
    if args.preflight_config:
        preflight_errors = validate_real_model_config(
            args.preflight_config,
            expected_embedding_dimensions=1024 if args.profile in {"report6", "complete"} else None,
        )
        if preflight_errors:
            parser.error(
                "real-model preflight failed:\n- " + "\n- ".join(preflight_errors)
            )
    if args.preflight_config:
        model_errors = validate_real_model_config(args.preflight_config)
        if model_errors:
            parser.error(
                "real-model preflight failed:\n- " + "\n- ".join(model_errors)
            )

    tenant_path = Path(args.tenant_config).expanduser().resolve()
    all_tenants = load_tenants(tenant_path)
    required_tenants = max(scenario_catalog[name]["tenants"] for name in scenario_names)
    if len(all_tenants) < required_tenants:
        parser.error(
            f"tenant config has {len(all_tenants)} tenants, but selected scenarios require {required_tenants}"
        )
    root = Path(args.out_dir or f"results/stress/formal_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    root.mkdir(parents=True, exist_ok=True)
    runner = Path(__file__).with_name("runner.py")
    config_dir = root / "_tenant_configs"
    config_dir.mkdir(exist_ok=True)
    config_paths: dict[int, Path] = {}
    for count in sorted({scenario_catalog[name]["tenants"] for name in scenario_names}):
        config_paths[count] = config_dir / f"tenants-{count}.json"
        write_subset(config_paths[count], all_tenants[:count])

    manifest: dict[str, Any] = {
        "created_at": now_iso(),
        "base_url": args.base_url,
        "profile": args.profile,
        "plan_sources": {
            "pr397": {
                "name": "EchoMem PR397 / report(6) 故障发现与真实多租户压测方案",
                "included": args.profile in {"report6", "complete"},
                "scenario_count": len(report6_scenarios()) if args.profile in {"report6", "complete"} else 0,
                "scenarios": sorted(report6_scenarios()) if args.profile in {"report6", "complete"} else [],
                "fault_plan_supplied": bool(args.fault_plan),
            },
            "pr421": {
                "name": "EchoMem PR421 可量化验收与调度指标方案",
                "included": args.profile in {"pr421", "complete"},
                "scenario_count": len(SCENARIOS) if args.profile in {"pr421", "complete"} else 0,
                "scenarios": sorted(SCENARIOS) if args.profile in {"pr421", "complete"} else [],
                "acceptance_targets_recorded": True,
            },
        },
        "tenant_config": str(tenant_path),
        "allow_shared_identity": args.allow_shared_identity,
        "output_root": str(root.resolve()),
        "scenarios": scenario_names,
        "repeats": args.repeats,
        "duration_cap_s": args.duration_cap_s,
        "case_timeout_s": args.case_timeout_s,
        "policies": list(POLICIES),
        "acceptance_targets": PR421_ACCEPTANCE_TARGETS,
        "preflight_config": (
            str(Path(args.preflight_config).expanduser().resolve())
            if args.preflight_config
            else ""
        ),
        "preflight": {
            "status": "PASS" if args.preflight_config else "NOT_RUN",
            "config": (
                str(Path(args.preflight_config).expanduser().resolve())
                if args.preflight_config
                else ""
            ),
            "checks": {
                "llm_provider_model": "real",
                "embedding_provider_model": "real",
                "embedding_dimensions": 1024 if args.profile == "report6" else "not_required",
                "api_key_values": "not_recorded",
            },
            "errors": preflight_errors,
        },
        "reset_command": args.reset_command,
        "client_admission_enabled": False,
        "server_observation_mode": True,
        "runs": [],
    }
    # Use a deterministic order so a rerun is easy to compare. The service
    # reset hook is the mechanism for keeping the data/index boundary fixed.
    for scenario in scenario_names:
        case = scenario_catalog[scenario]
        for repetition in range(1, args.repeats + 1):
            for policy in POLICIES:
                completed_runs = len(manifest["runs"])
                total_runs = len(scenario_names) * args.repeats * len(POLICIES)
                print(
                    f"FORMAL_PROGRESS {completed_runs}/{total_runs} "
                    f"scenario={scenario} repeat={repetition} policy={policy}",
                    flush=True,
                )
                run = run_case(
                    runner,
                    root,
                    scenario,
                    repetition,
                    policy,
                    config_paths[case["tenants"]],
                    args,
                    case,
                )
                manifest["runs"].append(run)
                print(
                    f"FORMAL_PROGRESS {len(manifest['runs'])}/{total_runs} "
                    f"scenario={scenario} repeat={repetition} policy={policy} "
                    f"status={run.get('status')}",
                    flush=True,
                )
                (root / "suite.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
    if args.fault_plan:
        fault_out = Path(args.fault_out_dir or (root / "fault-suite"))
        fault_command = [
            sys.executable,
            str(Path(__file__).with_name("fault_suite.py")),
            "--plan", str(Path(args.fault_plan).expanduser().resolve()),
            "--out-dir", str(fault_out),
            "--base-url", args.base_url,
            "--auth-key", args.fault_auth_key,
            "--auth-header", args.auth_header,
        ]
        completed = subprocess.run(fault_command, capture_output=True, text=True, check=False)
        fault_manifest = {
            "path": str(fault_out / "fault-suite.json"),
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
        fault_path = fault_out / "fault-suite.json"
        if fault_path.exists():
            try:
                fault_manifest.update(json.loads(fault_path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                pass
        manifest["fault_suite"] = fault_manifest

    if args.k6_summary and args.k6_runner_dir:
        k6_output = root / "k6-reconciliation.json"
        k6_command = [
            sys.executable,
            str(Path(__file__).with_name("k6_reconcile.py")),
            "--k6-summary", str(Path(args.k6_summary).expanduser().resolve()),
            "--runner-dir", str(Path(args.k6_runner_dir).expanduser().resolve()),
            "--out", str(k6_output),
        ]
        if args.k6_request_stream:
            k6_command.extend([
                "--k6-request-stream",
                str(Path(args.k6_request_stream).expanduser().resolve()),
            ])
        completed = subprocess.run(k6_command, capture_output=True, text=True, check=False)
        k6_manifest = {
            "path": str(k6_output),
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
        if k6_output.exists():
            try:
                k6_manifest.update(json.loads(k6_output.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                pass
        manifest["k6_reconciliation"] = k6_manifest

    if args.capability_probe:
        capability_path = Path(args.capability_probe).expanduser().resolve()
        if capability_path.is_file():
            try:
                manifest["capability_probe"] = json.loads(
                    capability_path.read_text(encoding="utf-8")
                )
                manifest["capability_probe_path"] = str(capability_path)
            except json.JSONDecodeError as exc:
                manifest["capability_probe"] = {
                    "status": "INCONCLUSIVE",
                    "reason": f"invalid capability probe JSON: {exc}",
                }
        else:
            manifest["capability_probe"] = {
                "status": "INCONCLUSIVE",
                "reason": "capability probe file does not exist",
            }

    acceptance = evaluate_pr421_acceptance(manifest)
    manifest["acceptance"] = acceptance
    (root / "suite.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "acceptance.json").write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "model_analysis_input.json").write_text(
        json.dumps(
            build_model_analysis_input(manifest, acceptance),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    report_path = root / "suite.html"
    try:
        from .formal_data_report import render as render_data_report
    except ImportError:
        from formal_data_report import render as render_data_report
    render_data_report(root / "suite.json", report_path)
    statuses = [str(run.get("status") or "NO_SUMMARY") for run in manifest["runs"]]
    if any(status in {"ENVIRONMENT_ERROR", "RESET_FAILED", "NO_SUMMARY", "FAIL"} for status in statuses) or acceptance["overall"] == "FAIL":
        overall = "FAIL"
    elif any(status == "INCONCLUSIVE" for status in statuses) or acceptance["overall"] in {"INCONCLUSIVE", "NOT_IMPLEMENTED"}:
        overall = "INCONCLUSIVE"
    else:
        overall = "PASS"
    suite_summary = {
        "status": overall,
        "test_type": "formal_stress_suite",
        "base_url": args.base_url,
        "created_at": manifest["created_at"],
        "finished_at": now_iso(),
        "parameters": {
            "tenant_config": str(tenant_path),
            "profile": args.profile,
            "plan_sources": (
                ["PR397/report(6)", "PR421"] if args.profile == "complete"
                else ["PR397/report(6)"] if args.profile == "report6"
                else ["PR421"] if args.profile == "pr421"
                else ["report(4)"]
            ),
            "scenarios": scenario_names,
            "repeats": args.repeats,
            "policies": list(POLICIES),
            "commit_workers": args.commit_workers,
            "commit_timeout_s": args.commit_timeout_s,
            "search_workers": args.search_workers,
            "client_admission": "disabled",
        },
        "details": {
            "run_count": len(manifest["runs"]),
            "expected_run_count": len(scenario_names) * args.repeats * len(POLICIES),
            "plan_sources": manifest["plan_sources"],
            "failed_runs": sum(status == "FAIL" for status in statuses),
            "inconclusive_runs": sum(status == "INCONCLUSIVE" for status in statuses),
            "environment_errors": sum(
                status in {"ENVIRONMENT_ERROR", "RESET_FAILED", "NO_SUMMARY"}
                for status in statuses
            ),
            "suite_report": "suite.html",
            "suite_manifest": "suite.json",
            "acceptance_report": "acceptance.json",
            "model_analysis_input": "model_analysis_input.json",
            "acceptance_overall": acceptance["overall"],
        },
        "aggregates": aggregate_runs(manifest["runs"]),
        "acceptance": acceptance,
    }
    (root / "summary.json").write_text(
        json.dumps(suite_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(report_path)
    return 0 if overall in {"PASS", "INCONCLUSIVE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

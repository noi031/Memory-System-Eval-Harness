#!/usr/bin/env python3
"""可重复的真实多租户 EchoMem 压测验收套件。

套件只负责编排：每个 case 由 run_stress 子进程执行，并保留逐请求 CSV 与
原始服务端遥测；套件在其上叠加场景/轮次元数据，并把 run_stress 原生产物
推导成验收求值器消费的契约摘要（summary.json / commit_results.csv /
search_results.csv）。只有每次运行都使用独立租户凭据时才允许做出上线结论。
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

# 支持 ``python -m performance.formal_suite`` 与直接执行两种方式。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from performance.acceptance import (
    build_model_analysis_input,
    evaluate_pr421_acceptance,
)
from performance.perf_preflight import run_preflight


# 正式运行复现在线客户端。压测端不施加 FIFO、优先级、lane 或租户公平调度。
POLICIES = ("server-observe",)

# 正式套件的运行器。每个 case 作为独立子进程执行，产物落到 case 的 run/ 子目录。
RUNNER = Path(__file__).with_name("run_stress.py")

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
    "capacity-2": {
        "label": "2 租户容量阶梯",
        "tenants": 2,
        "duration_s": 180,
        "search_rps": 2.0,
        "commit_rpm": 2.0,
        "sessions_per_tenant": 2,
        "messages_per_session": 3,
    },
    "capacity-4": {
        "label": "4 租户容量阶梯",
        "tenants": 4,
        "duration_s": 180,
        "search_rps": 4.0,
        "commit_rpm": 2.0,
        "sessions_per_tenant": 2,
        "messages_per_session": 3,
    },
    "capacity-8": {
        "label": "8 租户容量阶梯",
        "tenants": 8,
        "duration_s": 180,
        "search_rps": 8.0,
        "commit_rpm": 2.0,
        "sessions_per_tenant": 2,
        "messages_per_session": 3,
    },
    "capacity-32": {
        "label": "32 租户容量阶梯",
        "tenants": 32,
        "duration_s": 300,
        "search_rps": 32.0,
        "commit_rpm": 2.0,
        "sessions_per_tenant": 2,
        "messages_per_session": 3,
    },
    "search-priority-blackbox": {
        "label": "Search/Commit 同时到达（服务端优先级黑盒）",
        "tenants": 4,
        "duration_s": 90,
        "search_rps": 16.0,
        "commit_rpm": 0.0,
        "search_workers": 32,
        "commit_workers": 32,
        "commit_barrier": True,
        "commit_barrier_count": 128,
        "commit_tenant_distribution": "uniform",
        "sessions_per_tenant": 32,
        "messages_per_session": 3,
        "blackbox_search_priority": True,
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


def _identity_is_independent(tenants: list[dict[str, Any]]) -> bool:
    """所有租户都能解析出非空 auth_key 且彼此不同，才算独立认证。"""
    keys: list[str] = []
    for tenant in tenants:
        key = str(tenant.get("auth_key") or "").strip()
        if not key:
            env_name = str(tenant.get("auth_key_env") or "").strip()
            key = os.environ.get(env_name, "").strip() if env_name else ""
        if not key:
            return False
        keys.append(key)
    return len(set(keys)) == len(keys)


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


def _read_requests_csv(path: Path) -> list[dict[str, str]]:
    """读取逐请求 CSV；文件缺失时返回空列表。"""
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _resolve_run_dir(run_dir: Path) -> Path:
    """定位 run_stress 实际产物目录。

    run_stress 会在 ``--out-dir`` 下再创建时间戳子目录，产物实际落在该
    子目录内；直接构造的产物（如测试夹具）则落在 ``run_dir`` 自身。两者
    都能被解析到同一份 summary.json 所在目录。
    """
    if (run_dir / "summary.json").is_file():
        return run_dir
    children = [
        child for child in run_dir.iterdir()
        if child.is_dir() and (child / "summary.json").is_file()
    ]
    return children[0] if len(children) == 1 else run_dir


def _ms_to_s(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number / 1000.0


def _bounded_label_violations(metrics_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """从 lane 指标样本提取违反 bounded-label 契约的 tenant 标签。"""
    violations: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in metrics_rows:
        metric = str(row.get("metric") or "")
        if not metric.startswith("echomem_lane_"):
            continue
        try:
            labels = json.loads(row.get("labels") or "{}")
        except json.JSONDecodeError:
            labels = {}
        if not isinstance(labels, dict):
            continue
        for label_key in ("tenant_id", "tenant"):
            value = labels.get(label_key)
            if value is None:
                continue
            key = (metric, label_key, str(value))
            if key not in seen:
                seen.add(key)
                violations.append(
                    {"metric": metric, "label": label_key, "value": str(value)}
                )
            break
        if len(violations) >= 5:
            break
    return violations


def _build_case_command(
    args: argparse.Namespace,
    case: dict[str, Any],
    config_path: Path,
    output: Path,
    duration_s: float,
    barrier_count_cap: int = 0,
) -> list[str]:
    """把 stress case 字典映射为 run_stress CLI 参数。

    barrier 场景按「洪峰窗口 / 多波 / 其余分布」映射到 D / H / S；无 barrier
    的定速率场景映射到 K。``blackbox_search_priority`` 等仅记录在 manifest
    的字段不映射 CLI。
    """
    per_tenant_conc = int(case.get("per_tenant_concurrency") or 1)
    # Barrier 场景会在 run_stress 内部另外准备精确数量的未提交会话。
    # ``sessions_per_tenant`` 只用于 warm-up；将它设置成 barrier 总数会在
    # 正式压测前额外提交数百个真实模型请求，并可能耗尽 case timeout。
    seed_sessions = int(case.get("sessions_per_tenant", 5))
    if case.get("commit_barrier"):
        seed_sessions = min(seed_sessions, 4)
    cmd = [
        sys.executable,
        str(RUNNER),
        "--echomem-url",
        args.base_url,
        "--tenants",
        str(case["tenants"]),
        "--duration-s",
        str(duration_s),
        "--concurrency-steps",
        str(per_tenant_conc),
        "--out-dir",
        str(output / "run"),
        "--seed-sessions-per-tenant",
        str(seed_sessions),
        "--messages-per-session",
        str(case.get("messages_per_session", 10)),
        "--commit-poll-timeout-s",
        str(args.commit_timeout_s),
        "--commit-retry-max",
        str(args.commit_max_attempts),
        "--commit-retry-backoff-s",
        str(args.commit_retry_backoff_s),
        "--barrier-prepare-concurrency",
        "4",
        "--barrier-wave-size",
        str(getattr(args, "barrier_wave_size", 32)),
    ]
    if getattr(args, "local_auth_mode", False):
        # EchoMem local auth resolves the configured default identity when no
        # X-Auth-Key is sent. The local workspace has no key registry, so
        # passing a synthetic tenant-config key would make every request 401.
        cmd += [
            "--auth-mode",
            "static",
            "--tenant-id",
            str(getattr(args, "local_tenant_id", "local")),
            "--user-id",
            str(getattr(args, "local_user_id", "local_user")),
        ]
    else:
        cmd += ["--tenant-config", str(config_path)]
    if getattr(args, "reuse_existing_data", False):
        cmd += ["--skip-seed"]
    if case.get("search_rps"):
        cmd += ["--mode", "fixed-rps", "--rps", str(case["search_rps"])]
    if case.get("commit_rpm"):
        cmd += ["--commit-rpm", str(case["commit_rpm"])]
    if args.preflight_config:
        cmd += ["--preflight-config", args.preflight_config]
    if args.no_server_metrics:
        cmd += ["--no-metrics"]
    if case.get("commit_barrier"):
        barrier_count = int(case.get("commit_barrier_count", 32))
        if barrier_count_cap > 0:
            barrier_count = min(barrier_count, barrier_count_cap)
        # 洪峰窗口（report6 D：waves 为 1 且存在 burst 窗口）→ D 场景。
        if case.get("commit_burst_window_s") and not (case.get("commit_barrier_waves") or 1) > 1:
            cmd += [
                "--scenarios", "D",
                "--burst-commits", str(barrier_count),
                "--burst-window-s", str(case["commit_burst_window_s"]),
            ]
        # 多波（report4 D：waves > 1）→ H 场景。
        elif (case.get("commit_barrier_waves") or 1) > 1:
            cmd += [
                "--scenarios", "H", "--commit-barrier",
                "--commit-barrier-count", str(barrier_count),
                "--commit-barrier-waves", str(case["commit_barrier_waves"]),
                "--commit-barrier-cooldown-s", str(case.get("commit_barrier_cooldown_s", 0.0)),
            ]
        # 其余 barrier（并发读 + 一次性 barrier）→ S 场景。
        else:
            cmd += [
                "--scenarios", "S", "--commit-barrier",
                "--commit-barrier-count", str(barrier_count),
                "--commit-tenant-distribution", str(case.get("commit_tenant_distribution", "uniform")),
            ]
            if case.get("commit_zipf_exponent"):
                cmd += ["--commit-zipf-exponent", str(case["commit_zipf_exponent"])]
            if case.get("commit_tenant_counts"):
                cmd += ["--commit-tenant-counts", ",".join(map(str, case["commit_tenant_counts"]))]
    else:
        cmd += ["--scenarios", "K"]
    return cmd


def _derive_case_summary(run_dir: Path, identity_independent: bool) -> dict[str, Any]:
    """把 run_stress 原生产物推导成 stress 契约摘要。

    契约字段（metrics.search / metrics.commit / metrics.fairness /
    metrics.per_tenant / details.*）供 acceptance 求值器与 data report
    消费；run_stress 原始 summary.json / requests.csv 保留在 run 目录内不动。
    """
    run_dir = _resolve_run_dir(run_dir)
    summary_path = run_dir / "summary.json"
    native: dict[str, Any] = {}
    if summary_path.is_file():
        try:
            native = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            native = {}
    rows = _read_requests_csv(run_dir / "requests.csv")
    reads = [row for row in rows if row.get("op") == "read"]
    ok_reads = [row for row in reads if row.get("status") == "ok"]
    commit_submits = [row for row in rows if row.get("op") == "commit_submit"]
    ok_commits = [row for row in commit_submits if row.get("status") == "ok"]
    commit_dones = [row for row in rows if row.get("op") == "commit_done"]
    ok_dones = [row for row in commit_dones if row.get("status") == "ok"]
    fail_dones = [row for row in commit_dones if row.get("status") == "error"]

    read_latencies = [
        value for value in (_ms_to_s(row.get("stage_ms")) for row in ok_reads)
        if value is not None
    ]
    search_quality = native.get("search_quality") or {}
    native_durability = native.get("commit_durability") or {}

    submitted = len(reads)
    succeeded = len(ok_reads)
    commit_submitted = len(commit_submits)
    completed = len(ok_dones)
    failed = len(fail_dones)
    commit_success_rate = native_durability.get("commit_success_rate")
    if commit_success_rate is None:
        commit_success_rate = completed / commit_submitted if commit_submitted else None

    completed_by_tenant: dict[str, int] = {}
    for row in ok_dones:
        tenant_idx = str(row.get("tenant_idx") or "")
        if tenant_idx:
            completed_by_tenant[tenant_idx] = completed_by_tenant.get(tenant_idx, 0) + 1

    per_tenant: dict[str, dict[str, Any]] = {}
    for tenant_idx in sorted({str(row.get("tenant_idx") or "") for row in rows}):
        if not tenant_idx:
            continue
        tenant_reads = [
            value for value in (
                _ms_to_s(row.get("stage_ms"))
                for row in reads
                if str(row.get("tenant_idx") or "") == tenant_idx
                and row.get("status") == "ok"
            )
            if value is not None
        ]
        tenant_ok_commits = [
            row for row in ok_commits if str(row.get("tenant_idx") or "") == tenant_idx
        ]
        tenant_done_stages = [
            value for value in (
                _ms_to_s(row.get("stage_ms"))
                for row in ok_dones
                if str(row.get("tenant_idx") or "") == tenant_idx
            )
            if value is not None
        ]
        if not tenant_ok_commits and not tenant_done_stages and not tenant_reads:
            continue
        commit_entry: dict[str, Any] = {}
        if tenant_ok_commits:
            commit_entry["submitted"] = len(tenant_ok_commits)
            commit_entry["completed"] = sum(
                1
                for row in ok_dones
                if str(row.get("tenant_idx") or "") == tenant_idx
            )
        if tenant_done_stages:
            commit_entry["completion"] = {
                "p50_s": round(percentile(tenant_done_stages, 50), 3)
            }
        search_entry: dict[str, Any] = {}
        if tenant_reads:
            search_entry = {
                "submitted": sum(
                    1
                    for row in reads
                    if str(row.get("tenant_idx") or "") == tenant_idx
                ),
                "succeeded": len(tenant_reads),
                "latency": {
                    "p50_s": round(percentile(tenant_reads, 50), 3),
                    "p95_s": round(percentile(tenant_reads, 95), 3),
                },
            }
        per_tenant[tenant_idx] = {
            "commit": commit_entry,
            "search": search_entry,
        }

    details: dict[str, Any] = {
        "identity_mode": "independent_auth_keys" if identity_independent else "shared",
        "quality_seed": [],
        "native_status": native.get("status"),
    }
    for key in (
        "degradation",
        "isolation",
        "search_quality",
        "commit_durability",
        "reconciliation",
        "resources",
    ):
        if isinstance(native.get(key), dict):
            details[key] = native[key]
    metrics_path = run_dir / "metrics_samples.csv"
    if metrics_path.is_file():
        metrics_rows = _read_requests_csv(metrics_path)
        if metrics_rows:
            families = (
                tuple(PR421_ACCEPTANCE_TARGETS["lane_metric_families"])
                + tuple(PR421_ACCEPTANCE_TARGETS["fanout_metric_families"])
            )
            observed = {
                str(row.get("metric") or "")
                for row in metrics_rows
                if row.get("metric")
            }
            # Prometheus histograms are exported as *_bucket, *_count and
            # *_sum samples. Treat any of those samples as evidence that the
            # corresponding metric family exists.
            for family in families:
                if any(
                    name == family
                    or name.startswith(f"{family}_")
                    for name in observed
                ):
                    observed.add(family)
            details["pr421_metric_coverage"] = {
                "present": {family: True for family in families if family in observed},
                "missing": [family for family in families if family not in observed],
                "bounded_label_violations": _bounded_label_violations(metrics_rows),
            }
            per_tenant_quartets: dict[str, dict[str, Any]] = {}
            for row in metrics_rows:
                metric_name = str(row.get("metric") or "")
                family = next(
                    (
                        candidate
                        for candidate in families
                        if metric_name == candidate
                        or metric_name.startswith(f"{candidate}_")
                    ),
                    None,
                )
                if family not in PR421_ACCEPTANCE_TARGETS["lane_metric_families"]:
                    continue
                try:
                    labels = json.loads(row.get("labels") or "{}")
                except json.JSONDecodeError:
                    labels = {}
                if not isinstance(labels, dict):
                    continue
                tenant = labels.get("tenant_id") or labels.get("tenant")
                if tenant in (None, ""):
                    continue
                short = {
                    "echomem_lane_queued": "queued",
                    "echomem_lane_wait_seconds": "wait",
                    "echomem_lane_exec_seconds": "exec",
                    "echomem_lane_rejected_total": "rejected",
                }[family]
                entry = per_tenant_quartets.setdefault(
                    str(tenant),
                    {
                        "queued": False,
                        "wait": False,
                        "exec": False,
                        "rejected": False,
                        "lanes": set(),
                        "per_lane": {},
                    },
                )
                entry[short] = True
                if labels.get("lane") not in (None, ""):
                    lane = str(labels["lane"])
                    entry["lanes"].add(lane)
                    lane_entry = entry["per_lane"].setdefault(
                        lane,
                        {
                            "queued": False,
                            "wait": False,
                            "exec": False,
                            "rejected": False,
                        },
                    )
                    lane_entry[short] = True
            for entry in per_tenant_quartets.values():
                entry["lanes"] = sorted(entry["lanes"])
            details["pr421_metric_coverage"]["per_tenant_quartets"] = per_tenant_quartets

    def _percentile(values: list[float], p: float) -> float | None:
        value = percentile(values, p)
        return round(value, 3) if value is not None else None

    return {
        "status": "completed" if str(native.get("status") or "") == "completed" else "NO_SUMMARY",
        "metrics": {
            "search": {
                "submitted": submitted,
                "succeeded": succeeded,
                "errors": submitted - succeeded,
                "success_rate": (succeeded / submitted) if submitted else None,
                "latency": {
                    "mean_s": (
                        round(statistics.mean(read_latencies), 3) if read_latencies else None
                    ),
                    "p50_s": _percentile(read_latencies, 50),
                    "p95_s": _percentile(read_latencies, 95),
                    "p99_s": _percentile(read_latencies, 99),
                },
                "rate_limited_count": sum(
                    1 for row in reads if row.get("error_type") == "http_4xx"
                ),
                "quality_asserted": int(search_quality.get("anchor_total") or 0),
                "quality_failures": int(search_quality.get("quality_failures") or 0),
            },
            "commit": {
                "submitted": commit_submitted,
                "completed": completed,
                "failed": failed,
                "success_rate": commit_success_rate,
                "rate_limited_count": sum(
                    1 for row in commit_submits if row.get("error_type") == "http_4xx"
                ),
            },
            "fairness": {
                "commit_completed_per_tenant": completed_by_tenant,
            },
            "per_tenant": per_tenant,
        },
        "details": details,
        "parameters": {
            "commit_delay_threshold_s": 10.0,
            "search_delay_threshold_s": 2.5,
        },
    }


def _write_case_csvs(output: Path, rows: list[dict[str, str]]) -> None:
    """把 run_stress 逐请求记录归一化为套件契约的两个 CSV。"""
    done_by_session: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.get("op") == "commit_done" and row.get("session_id"):
            done_by_session.setdefault(row["session_id"], row)

    commit_rows: list[dict[str, str]] = []
    for row in rows:
        if row.get("op") != "commit_submit":
            continue
        session_id = row.get("session_id") or ""
        done = done_by_session.get(session_id)
        if done is not None and done.get("status") == "ok":
            status = "completed"
            end_to_end = _ms_to_s(done.get("stage_ms"))
        elif done is not None:
            status = "failed"
            end_to_end = _ms_to_s(done.get("stage_ms"))
        else:
            status = "submitted"
            end_to_end = _ms_to_s(row.get("stage_ms"))
        commit_rows.append(
            {
                "tenant": row.get("tenant_idx") or "",
                "session_id": session_id,
                "archive_id": row.get("archive_id") or "",
                "status": status,
                "end_to_end_s": f"{end_to_end:.3f}" if end_to_end is not None else "",
                "queue_wait_s": "",
                "admission_wait_s": "",
                "admission_queue_depth": "",
                "request_id": session_id,
            }
        )
    with (output / "commit_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "tenant", "session_id", "status", "end_to_end_s",
                "archive_id",
                "queue_wait_s", "admission_wait_s", "admission_queue_depth", "request_id",
            ],
        )
        writer.writeheader()
        writer.writerows(commit_rows)

    search_rows: list[dict[str, str]] = []
    for row in rows:
        if row.get("op") != "read":
            continue
        if row.get("status") == "ok":
            status_code = "200"
        elif row.get("error_type") == "http_4xx" and (
            row.get("retry_after_s") or row.get("reason_code")
        ):
            status_code = "429"
        else:
            status_code = "500"
        service_s = _ms_to_s(row.get("stage_ms"))
        search_rows.append(
            {
                "tenant": row.get("tenant_idx") or "",
                "session_id": row.get("session_id") or "",
                "status_code": status_code,
                "service_s": f"{service_s:.3f}" if service_s is not None else "",
                "queue_wait_s": "",
                "request_id": row.get("session_id") or "",
                "retry_after_s": row.get("retry_after_s") or "",
                "reason_code": row.get("reason_code") or "",
            }
        )
    with (output / "search_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "tenant", "session_id", "status_code", "service_s",
                "queue_wait_s", "request_id", "retry_after_s", "reason_code",
            ],
        )
        writer.writeheader()
        writer.writerows(search_rows)


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
        else duration_s + max(
            60.0,
            float(args.commit_timeout_s),
            120.0 if case.get("commit_barrier") and not getattr(args, "barrier_count_cap", 0) else 0.0,
        )
    )
    barrier_count_cap = int(getattr(args, "barrier_count_cap", 0) or 0)
    command = _build_case_command(
        args, case, config_path, output, duration_s, barrier_count_cap
    )
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
                "scenario_label": case["label"],
                "repetition": repetition,
                "policy": policy,
                "status": "RESET_FAILED",
                "runner_returncode": completed_reset.returncode,
                "duration_s": duration_s,
                "case_timeout_s": case_timeout_s,
                "barrier_count_cap": barrier_count_cap,
                "output_dir": str(output.resolve()),
                "summary": {},
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
    run_dir = _resolve_run_dir(output / "run")
    rows = _read_requests_csv(run_dir / "requests.csv")
    _write_case_csvs(output, rows)
    derived = _derive_case_summary(run_dir, args.identity_independent)
    if timed_out:
        derived["status"] = "TIMEOUT"
    elif completed.returncode != 0:
        derived["status"] = "NO_SUMMARY"
    (output / "summary.json").write_text(
        json.dumps(derived, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "scenario": scenario,
        "scenario_label": case["label"],
        "repetition": repetition,
        "policy": policy,
        "status": (
            "TIMEOUT" if timed_out
            else "ENV_ERROR" if completed.returncode != 0 and not rows
            else "FAIL" if completed.returncode != 0
            else derived["status"]
        ),
        "runner_returncode": completed.returncode,
        "duration_s": duration_s,
        "case_timeout_s": case_timeout_s,
        "barrier_count_cap": barrier_count_cap,
        "runner_timeout": timed_out,
        "output_dir": str(output.resolve()),
        "summary": derived,
    }


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
    parser.add_argument(
        "--local-auth",
        action="store_true",
        help="Use the single local identity from config.json instead of tenant credentials.",
    )
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
    parser.add_argument(
        "--instance-profile",
        default="",
        help="实际生效的机器规格名称，例如 4U8G / 8U16G",
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
    parser.add_argument(
        "--allow-shared-identity",
        action="store_true",
        help="Allow an exploratory shared credential; isolation/fairness remain inconclusive.",
    )
    parser.add_argument("--commit-timeout-s", type=float, default=120.0)
    parser.add_argument("--commit-max-attempts", type=int, default=3)
    parser.add_argument("--commit-retry-backoff-s", type=float, default=2.0)
    parser.add_argument(
        "--barrier-wave-size",
        type=int,
        default=32,
        help="barrier Commit 最大同时在途数，默认 32",
    )
    parser.add_argument(
        "--barrier-count-cap",
        type=int,
        default=0,
        help=(
            "显式限制每个 barrier 场景的 Commit 数；仅用于 bounded/quick "
            "诊断，0 表示使用方案原始数量"
        ),
    )
    parser.add_argument("--reset-command", default="", help="Optional command run before every case")
    parser.add_argument("--no-server-metrics", action="store_true")
    parser.add_argument(
        "--reuse-existing-data",
        action="store_true",
        help="复用 tenant-config 对应租户的已有记忆，不重复注入真实模型",
    )
    parser.add_argument(
        "--preflight-config",
        default=os.getenv("ECHOMEM_CONFIG", ""),
        help="EchoMem config.json to validate before the suite starts",
    )
    args = parser.parse_args()

    scenario_catalog = SCENARIO_PROFILES[args.profile]
    default_scenarios = (
        "baseline,mixed,commit-storm,commit-barrier,saturation,tenant-skew,"
        "search-priority-blackbox,search-storm"
        if args.profile == "pr421"
        else ",".join(
            name for name in scenario_catalog
            if not (args.profile == "complete" and name == "soak")
        )
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
    if args.barrier_wave_size < 1:
        parser.error("--barrier-wave-size must be >= 1")
    if args.profile in {"report6", "complete"} and not args.preflight_config:
        parser.error(
            f"--profile {args.profile} requires --preflight-config with the actual EchoMem config.json"
        )
    preflight_result: dict[str, Any] | None = None
    if args.preflight_config:
        preflight_result = run_preflight(args.preflight_config, timeout_s=30.0)
        if not preflight_result["ok"]:
            parser.error(
                f"real-model preflight failed: {preflight_result['error']}"
            )

    tenant_path = Path(args.tenant_config).expanduser().resolve()
    all_tenants = load_tenants(tenant_path)
    required_tenants = max(scenario_catalog[name]["tenants"] for name in scenario_names)
    if len(all_tenants) < required_tenants:
        parser.error(
            f"tenant config has {len(all_tenants)} tenants, but selected scenarios require {required_tenants}"
        )
    args.identity_independent = _identity_is_independent(all_tenants)
    try:
        runtime_config = json.loads(
            Path(args.preflight_config).expanduser().read_text(encoding="utf-8")
        ) if args.preflight_config else {}
    except (OSError, json.JSONDecodeError):
        runtime_config = {}
    auth_config = runtime_config.get("auth") if isinstance(runtime_config, dict) else {}
    # Do not infer the wire authentication mode from config.json. A deployment
    # may keep a local workspace config while exposing API-key identities.
    args.local_auth_mode = bool(args.local_auth)
    args.local_tenant_id = (
        str(auth_config.get("default_tenant_id") or "local")
        if isinstance(auth_config, dict)
        else "local"
    )
    args.local_user_id = (
        str(auth_config.get("default_user_id") or "local_user")
        if isinstance(auth_config, dict)
        else "local_user"
    )
    root = Path(args.out_dir or f"results/performance/formal_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    root.mkdir(parents=True, exist_ok=True)
    runner = RUNNER
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
        "instance_profile": args.instance_profile,
        "plan_sources": {
            "pr397": {
                "name": "EchoMem PR397 / report(6) 故障发现与真实多租户压测方案",
                "included": args.profile in {"report6", "complete"},
                "scenario_count": len(report6_scenarios()) if args.profile in {"report6", "complete"} else 0,
                "scenarios": sorted(report6_scenarios()) if args.profile in {"report6", "complete"} else [],
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
        "preflight": (
            {
                **preflight_result,
                "config": str(Path(args.preflight_config).expanduser().resolve()),
            }
            if preflight_result is not None
            else {"status": "NOT_RUN", "config": "", "engines_checked": 0, "engines": [], "digest": ""}
        ),
        "reset_command": args.reset_command,
        "reuse_existing_data": args.reuse_existing_data,
        "client_admission_enabled": False,
        "server_observation_mode": True,
        "runs": [],
    }
    # 用确定性顺序执行，便于重跑对比；服务端重置钩子负责固定数据/索引边界。
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
    if any(
        status in {"ENVIRONMENT_ERROR", "ENV_ERROR", "RESET_FAILED", "NO_SUMMARY", "TIMEOUT", "FAIL"}
        for status in statuses
    ) or acceptance["overall"] == "FAIL":
        overall = "FAIL"
    elif any(status in {"INCONCLUSIVE", "NOT_IMPLEMENTED"} for status in statuses) or acceptance["overall"] in {"INCONCLUSIVE", "NOT_IMPLEMENTED"}:
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
            "instance_profile": args.instance_profile,
            "plan_sources": (
                ["PR397/report(6)", "PR421"] if args.profile == "complete"
                else ["PR397/report(6)"] if args.profile == "report6"
                else ["PR421"] if args.profile == "pr421"
                else ["report(4)"]
            ),
            "scenarios": scenario_names,
            "repeats": args.repeats,
            "policies": list(POLICIES),
            "commit_timeout_s": args.commit_timeout_s,
            "barrier_wave_size": args.barrier_wave_size,
        },
        "details": {
            "run_count": len(manifest["runs"]),
            "expected_run_count": len(scenario_names) * args.repeats * len(POLICIES),
            "plan_sources": manifest["plan_sources"],
            "failed_runs": sum(status == "FAIL" for status in statuses),
            "inconclusive_runs": sum(status == "INCONCLUSIVE" for status in statuses),
            "environment_errors": sum(
                status in {"ENVIRONMENT_ERROR", "ENV_ERROR", "RESET_FAILED", "NO_SUMMARY", "TIMEOUT"}
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

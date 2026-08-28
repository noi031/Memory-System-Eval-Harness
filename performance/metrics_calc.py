"""Pure functions for stress-test statistics.

No I/O here so every function is unit-testable in isolation. Percentile
conventions match `dynamic/metrics.py` and `scripts/compare_memory_backends.py`
(linear interpolation over sorted values).
"""

from __future__ import annotations

from typing import Any, Iterable

from performance.loadgen import RequestRecord


def percentile(sorted_vals: list[float], q: float) -> float | None:
    """Linear-interpolated percentile (q in [0, 1]) of a sorted list."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if q <= 0.0:
        return sorted_vals[0]
    if q >= 1.0:
        return sorted_vals[-1]
    pos = (n - 1) * q
    lower = int(pos)
    upper = lower + 1
    frac = pos - lower
    if upper >= n:
        return sorted_vals[lower]
    return sorted_vals[lower] + (sorted_vals[upper] - sorted_vals[lower]) * frac


def percentiles(sorted_vals: list[float], quantiles: Iterable[float]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for q in quantiles:
        label = f"p{int(round(q * 100))}"
        result[label] = percentile(sorted_vals, q)
    return result


def _op_stats(stages: list[float]) -> dict[str, Any]:
    """Summary over one operation's measured stage latencies (ms)."""
    if not stages:
        return {
            "count": 0,
            "avg_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "max_ms": None,
            "min_ms": None,
        }
    ordered = sorted(stages)
    ps = percentiles(ordered, (0.5, 0.95, 0.99))
    return {
        "count": len(stages),
        "avg_ms": round(sum(stages) / len(stages), 3),
        "p50_ms": ps["p50"],
        "p95_ms": ps["p95"],
        "p99_ms": ps["p99"],
        "max_ms": round(ordered[-1], 3),
        "min_ms": round(ordered[0], 3),
    }


def summarize_records(records: list[RequestRecord], *, wall_s: float) -> dict[str, Any]:
    """Summarize records grouped by (scene key, operation).

    ``wall_s`` is the scene wall-clock duration used to derive QPS. Errors
    are classified into timeout / http_4xx / http_5xx / connection / other.
    """
    groups: dict[tuple[str, str], list[float]] = {}
    errors: dict[tuple[str, str], dict[str, int]] = {}
    for rec in records:
        key = (rec.scene_key, rec.op)
        groups.setdefault(key, []).append(rec.stage_ms)
        if rec.error_type:
            counter = errors.setdefault(key, {})
            counter[rec.error_type] = counter.get(rec.error_type, 0) + 1

    summary: dict[str, dict[str, Any]] = {}
    for key, stages in groups.items():
        scene_key, op = key
        entry = _op_stats(stages)
        count = entry["count"]
        entry["qps"] = round(count / wall_s, 3) if wall_s > 0 else None
        err = errors.get(key, {})
        entry["errors_total"] = sum(err.values())
        entry["error_rate"] = round(sum(err.values()) / count, 5) if count else None
        entry["error_breakdown"] = err
        summary.setdefault(scene_key, {})[op] = entry
    return summary


def degradation_factor(
    baseline: dict[str, Any] | None,
    target: dict[str, Any] | None,
) -> dict[str, float | None]:
    """Read-latency degradation of target vs baseline for p50/p95/p99.

    A value of 1.0 means no degradation; ``None`` when either side lacks data.
    """
    result: dict[str, float | None] = {"p50": None, "p95": None, "p99": None}
    if not baseline or not target:
        return result
    for key in ("p50", "p95", "p99"):
        base = baseline.get(f"{key}_ms")
        tgt = target.get(f"{key}_ms")
        if base is not None and tgt is not None and base > 0:
            result[key] = round(tgt / base, 3)
    return result


def read_records_in_window(
    records: list[RequestRecord],
    t0_ms: float,
    t1_ms: float,
) -> list[RequestRecord]:
    """Read-op records whose completion timestamp falls inside [t0, t1]."""
    return [
        rec
        for rec in records
        if rec.op == "read" and rec.ts_ms is not None and t0_ms <= rec.ts_ms <= t1_ms
    ]


def consistency_summary(records: list[RequestRecord]) -> dict[str, Any]:
    """Write-then-read consistency window stats (records with op=consistent_check)."""
    stages = [rec.stage_ms for rec in records if rec.op == "consistent_check"]
    stats = _op_stats(stages)
    stats["timeouts"] = sum(
        1
        for rec in records
        if rec.op == "consistent_check" and rec.error_type in ("timeout", "consistency_timeout")
    )
    return stats


def burst_summary(
    burst_reads: list[RequestRecord],
    baseline_reads: list[RequestRecord],
) -> dict[str, Any]:
    """Compare burst-window read latency distribution against the scene baseline."""
    if not burst_reads or not baseline_reads:
        return {"count": 0, "degradation": {"p50": None, "p95": None, "p99": None}}
    burst_stats = _op_stats([rec.stage_ms for rec in burst_reads])
    baseline_stats = _op_stats([rec.stage_ms for rec in baseline_reads])
    return {
        "count": len(burst_reads),
        "burst_stats": burst_stats,
        "baseline_stats": baseline_stats,
        "degradation": degradation_factor(baseline_stats, burst_stats),
    }


def commit_completion_latency(records: list[RequestRecord]) -> dict[str, Any]:
    """Submit→completed 的异步完成等待耗时 (ms)，即 commit_done 轮询阶段。

    ``commit_done`` 的 stage_ms 是客户端从提交成功到观察为已完成的等待
    时长（见 loadgen.run_write_transaction），量化 commit 异步完成的实时性。
    """
    return _op_stats(
        [rec.stage_ms for rec in records if rec.op == "commit_done" and rec.status == "ok"]
    )


def degradation_measurements(summary: dict[str, Any]) -> dict[str, Any]:
    """写洪峰/混合场景 vs 同并发 A 基线的绝对延迟与相对倍率。

    每个对照形如 ``D@4_vs_A@4``，输出 baseline/flood 的 P50/P95/P99 绝对
    值、绝对差 (delta_ms) 与相对倍率 (ratio)：即「写洪峰时 search 延迟比
    基线高多少」的直接度量。数据来自 summary.scenes 的 read 统计与
    summary.degradation 的倍率。
    """
    scenes = summary.get("scenes") or {}
    degradation = summary.get("degradation") or {}
    result: dict[str, Any] = {}
    for key, factors in degradation.items():
        if "_vs_" not in key:
            continue
        target_key, base_key = key.split("_vs_", 1)
        target = ((scenes.get(target_key) or {}).get("ops") or {}).get("read") or {}
        baseline = ((scenes.get(base_key) or {}).get("ops") or {}).get("read") or {}
        entry: dict[str, Any] = {}
        for p in ("p50", "p95", "p99"):
            t_ms = target.get(f"{p}_ms")
            b_ms = baseline.get(f"{p}_ms")
            entry[f"baseline_{p}_ms"] = b_ms
            entry[f"flood_{p}_ms"] = t_ms
            entry[f"delta_{p}_ms"] = (
                round(float(t_ms) - float(b_ms), 3)
                if t_ms is not None and b_ms is not None
                else None
            )
            entry[f"ratio_{p}"] = factors.get(p)
        result[key] = entry
    return result


def fairness_measurements(fairness: dict[str, Any]) -> dict[str, Any]:
    """租户公平性量化：每个多租户场景的最快/最慢租户等待与差距。

    ``slowest_waits_extra_ms`` 即「最慢租户比最快租户多等的 P95 时长」，
    是公平性不满足时受困租户可能遭遇的最坏额外等待的直接度量。
    """
    scenes: dict[str, Any] = {}
    for scene_key, fair in (fairness or {}).items():
        rows: list[dict[str, Any]] = []
        for row in fair.get("tenants") or []:
            p95 = row.get("p95_ms")
            if p95 is None:
                continue
            rows.append(
                {
                    "tenant_idx": row.get("tenant_idx"),
                    "p95_ms": float(p95),
                    "p99_ms": (
                        float(row["p99_ms"]) if row.get("p99_ms") is not None else None
                    ),
                }
            )
        if len(rows) < 2:
            continue
        best = min(rows, key=lambda item: item["p95_ms"])
        worst = max(rows, key=lambda item: item["p95_ms"])
        scenes[scene_key] = {
            "tenant_count": len(rows),
            "p95_max_min_ratio": fair.get("p95_max_min_ratio"),
            "p95_cv": fair.get("p95_cv"),
            "fastest_tenant_idx": best["tenant_idx"],
            "fastest_tenant_p95_ms": best["p95_ms"],
            "slowest_tenant_idx": worst["tenant_idx"],
            "slowest_tenant_p95_ms": worst["p95_ms"],
            "slowest_tenant_p99_ms": worst["p99_ms"],
            "slowest_waits_extra_ms": round(worst["p95_ms"] - best["p95_ms"], 3),
        }
    if not scenes:
        return {"scenes": scenes}
    worst_key = max(scenes, key=lambda key: scenes[key]["p95_max_min_ratio"] or 0.0)
    return {"scenes": scenes, "worst_scene": worst_key, **scenes[worst_key]}


# ---------------------------------------------------------------------- #
#  Four feature guarantees: commit durability, tenant fairness,           #
#  memory trend, resource timeline                                        #
# ---------------------------------------------------------------------- #

# 租户公平性判定阈值：组间读 P95 max/min 比达到该值判不均衡。
FAIRNESS_MAX_MIN_RATIO = 3.0
# RSS 斜率泄漏判定阈值（MB/分钟）：超过即判疑似泄漏。
RSS_LEAK_SLOPE_MB_PER_MIN = 5.0


def _verdict(verdict: str, reason: str) -> dict[str, Any]:
    return {"verdict": verdict, "reason": reason}


def _merge_subs(subs: list[dict[str, Any]]) -> str:
    """Worst verdict of sub-checks: FAIL > INCONCLUSIVE > PASS."""
    if any(sub.get("verdict") == "FAIL" for sub in subs):
        return "FAIL"
    if any(sub.get("verdict") == "INCONCLUSIVE" for sub in subs):
        return "INCONCLUSIVE"
    return "PASS"


def commit_durability(records: list[RequestRecord]) -> dict[str, Any]:
    """Commit 成功保证：submit(202) 接受后必须最终 completed。

    A submit that the server accepted (status ok) is paired with its
    commit_done record by session_id. Any accepted commit that does not
    reach ``completed`` violates the guarantee. Poll timeouts are reported
    separately: they mean the observation window (--commit-poll-timeout-s)
    expired, not necessarily that the commit itself failed.
    """
    submit_by_session: dict[str, bool] = {}
    done_by_session: dict[str, str] = {}
    submit_errors: dict[str, int] = {}
    for rec in records:
        if rec.op == "commit_submit":
            if rec.status == "ok":
                submit_by_session[rec.session_id] = True
            else:
                submit_errors[rec.error_type] = submit_errors.get(rec.error_type, 0) + 1
        elif rec.op == "commit_done":
            done_by_session[rec.session_id] = rec.error_type or rec.status

    accepted = 0
    done_ok = 0
    done_failed = 0
    done_timeout = 0
    done_other = 0
    for session_id in submit_by_session:
        outcome = done_by_session.get(session_id)
        accepted += 1
        if outcome in ("ok", "completed"):
            done_ok += 1
        elif outcome in ("commit_failed", "failed"):
            done_failed += 1
        elif outcome in ("commit_timeout", "timeout"):
            done_timeout += 1
        else:
            done_other += 1
    total_accepts = accepted + len(submit_errors)
    return {
        "submit_ok_total": accepted,
        "submit_rejected_total": len(submit_errors),
        "submit_rejected_breakdown": submit_errors,
        "accepted_done_ok": done_ok,
        "accepted_done_failed": done_failed,
        "accepted_done_poll_timeout": done_timeout,
        "accepted_done_other": done_other,
        "commit_success_rate": (
            round(done_ok / accepted, 5) if accepted else None
        ),
        # 违反「commit 成功保证」的信号：202 已接受但最终失败
        "guarantee_violations": done_failed + done_other,
    }


def tenant_fairness(records: list[RequestRecord]) -> dict[str, Any]:
    """租户公平性：按场景×租户的读延迟与吞吐均衡度。

    For every scene, per-tenant read P50/P95 and QPS are compared. The
    ``p95_max_min_ratio`` is the spread across tenants; a ratio high
    enough to indicate one tenant starving others is reported.
    """
    per_scene: dict[str, dict[int, list[float]]] = {}
    for rec in records:
        if rec.op != "read":
            continue
        per_scene.setdefault(rec.scene_key, {}).setdefault(rec.tenant_idx, []).append(rec.stage_ms)

    result: dict[str, Any] = {}
    for scene_key, tenant_map in per_scene.items():
        rows: list[dict[str, Any]] = []
        for tenant_idx, stages in tenant_map.items():
            ordered = sorted(stages)
            rows.append(
                {
                    "tenant_idx": tenant_idx,
                    "count": len(stages),
                    "p50_ms": percentile(ordered, 0.5),
                    "p95_ms": percentile(ordered, 0.95),
                    "p99_ms": percentile(ordered, 0.99),
                }
            )
        if len(rows) < 2:
            result[scene_key] = {"tenants": rows, "p95_max_min_ratio": None, "balanced": True}
            continue
        p95s = [row["p95_ms"] or 0.0 for row in rows]
        positive = [v for v in p95s if v > 0]
        ratio = None
        if len(positive) >= 2:
            ratio = round(max(positive) / min(positive), 3)
        mean = sum(p95s) / len(p95s)
        variance = sum((v - mean) ** 2 for v in p95s) / len(p95s)
        result[scene_key] = {
            "tenants": rows,
            "p95_max_min_ratio": ratio,
            "p95_cv": round(variance**0.5 / mean, 3) if mean > 0 else None,
            "balanced": ratio is None or ratio < FAIRNESS_MAX_MIN_RATIO,
        }
    return result


def rss_trend_mb_per_min(series: list[tuple[float, float]]) -> dict[str, Any]:
    """Least-squares slope of RSS (bytes) over time, in MB per minute.

    Needs at least 4 samples across the observed window; fewer samples
    return an undecidable result. The slope together with the cooling
    settle delta distinguishes a slow leak from index-size growth.
    """
    n = len(series)
    if n < 4:
        return {"slope_mb_per_min": None, "r2": None, "samples": n}
    xs = [ts for ts, _ in series]
    ys = [value / 1024 / 1024 for _, value in series]  # MB
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    s_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    s_xx = sum((x - mean_x) ** 2 for x in xs)
    if s_xx <= 0:
        return {"slope_mb_per_min": None, "r2": None, "samples": n}
    slope = s_xy / s_xx  # MB per second
    ss_res = sum((y - (mean_y + slope * (x - mean_x))) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r2 = round(1 - ss_res / ss_tot, 4) if ss_tot > 0 else None
    return {
        "slope_mb_per_min": round(slope * 60, 3),
        "r2": r2,
        "samples": n,
    }


# ---------------------------------------------------------------------- #
#  Feature verdicts: the four EchoMem guarantees, evaluated per run       #
# ---------------------------------------------------------------------- #

FEATURE_LABELS: dict[str, str] = {
    "commit_guarantee": "特性1 commit 异步/成功保证/不阻塞检索",
    "tenant_fairness": "特性2 租户公平性",
    "memory_leak": "特性3 无内存泄漏",
    "resource_timeline": "特性4 资源利用率随时间变化图",
}


def evaluate_features(summary: dict[str, Any]) -> dict[str, Any]:
    """Evaluate all four feature guarantees from a finished run summary.

    Verdicts are PASS / FAIL / INCONCLUSIVE (data insufficient). The
    function is tolerant of missing fields so it can grade partial or
    degraded runs (e.g. --no-metrics, static single-tenant). Each verdict
    carries ``measurements`` with the quantified magnitude it is based on
    (degradation ratio + absolute delta, per-tenant wait spread, RSS growth
    slope and hour projection, CPU/RSS timeline extremes); the report
    renders these as the quantified-analysis section.
    """
    config = summary.get("config") or {}
    try:
        degradation_threshold = float(config.get("degradation_threshold", 2.0))
    except (TypeError, ValueError):
        degradation_threshold = 2.0

    features: dict[str, Any] = {}

    # -- 特性1a: commit 成功保证 -------------------------------------------
    durability = summary.get("commit_durability") or {}
    submitted = int(durability.get("submit_ok_total", 0) or 0)
    rejected = int(durability.get("submit_rejected_total", 0) or 0)
    violations = int(durability.get("guarantee_violations", 0) or 0)
    if submitted == 0 and rejected == 0:
        sub_durability = _verdict(
            "INCONCLUSIVE", "未运行写场景（B/C/D），无法评估 commit 成功保证"
        )
    elif violations > 0:
        sub_durability = _verdict(
            "FAIL",
            f"202 已接受的 commit 中 {violations} 个未最终 completed "
            f"(accepted_done_failed={durability.get('accepted_done_failed')}, "
            f"accepted_done_other={durability.get('accepted_done_other')})",
        )
    else:
        sub_durability = _verdict(
            "PASS",
            f"{submitted} 个已接受 commit 全部 completed (成功率 "
            f"{durability.get('commit_success_rate')})；提交阶段被拒绝 "
            f"{rejected} 次（不可重试因素，分类见 commit_durability）",
        )
    sub_durability["measurements"] = {
        "submitted_202": submitted,
        "submit_rejected": rejected,
        "accepted_done_ok": durability.get("accepted_done_ok"),
        "commit_success_rate": durability.get("commit_success_rate"),
        "violations": violations,
        "completion_latency_ms": (summary.get("commit_latency") or {}),
    }

    # -- 特性1b: search 优先级 / commit 不阻塞检索 ---------------------------
    degradation = summary.get("degradation") or {}
    d_cases = {
        key: value
        for key, value in degradation_measurements(summary).items()
        if key.startswith("D")
    }
    d_factors = [
        float(factors.get("p95"))
        for key, factors in degradation.items()
        if key.startswith("D") and factors.get("p95") is not None
    ]
    worst_ratio = max(d_factors) if d_factors else None
    worst_case = (
        max(d_cases, key=lambda key: (d_cases[key].get("ratio_p95") or 0.0))
        if d_cases
        else None
    )

    def _delta_text(case_key: str | None) -> str:
        case = d_cases.get(case_key) if case_key else None
        if not case or case.get("delta_p95_ms") is None:
            return ""
        return (
            f"（基线 P95 {case['baseline_p95_ms']}ms → 洪峰 {case['flood_p95_ms']}ms，"
            f"+{case['delta_p95_ms']}ms）"
        )

    if not d_factors:
        sub_priority = _verdict(
            "INCONCLUSIVE",
            "未运行注入洪峰场景（D）或缺少同并发档 A 基线，无法评估 search 优先级",
        )
    elif worst_ratio >= degradation_threshold:
        sub_priority = _verdict(
            "FAIL",
            f"{worst_case} 注入洪峰窗口读 P95 劣化 {worst_ratio}x"
            f"{_delta_text(worst_case)} ≥ 阈值 {degradation_threshold}x",
        )
    else:
        sub_priority = _verdict(
            "PASS",
            f"注入洪峰窗口读 P95 劣化 max={worst_ratio}x"
            f"{_delta_text(worst_case)} < 阈值 {degradation_threshold}x",
        )
    sub_priority["measurements"] = {
        "threshold_ratio": degradation_threshold,
        "worst_p95_ratio": worst_ratio,
        "worst_case": worst_case,
        "cases": d_cases,
    }
    features["commit_guarantee"] = {
        "verdict": _merge_subs([sub_durability, sub_priority]),
        "sub": {"durability": sub_durability, "retrieval_precedence": sub_priority},
        "measurements": {
            "durability": sub_durability["measurements"],
            "retrieval_precedence": sub_priority["measurements"],
        },
    }

    # -- 特性2: 租户公平性 ---------------------------------------------------
    fairness = summary.get("tenant_fairness") or {}
    fair_measurements = fairness_measurements(fairness)
    multi_tenant = [v for v in fairness.values() if len(v.get("tenants") or []) >= 2]
    if not fairness:
        features["tenant_fairness"] = _verdict("INCONCLUSIVE", "无按租户分组的读数据")
    elif not multi_tenant:
        features["tenant_fairness"] = _verdict(
            "INCONCLUSIVE",
            "单租户运行（如 --auth-mode static），无法评估租户间公平性",
        )
    elif any(not v.get("balanced", True) for v in multi_tenant):
        worst = max((v.get("p95_max_min_ratio") or 0 for v in multi_tenant), default=0.0)
        slow_p95 = fair_measurements.get("slowest_tenant_p95_ms")
        extra = fair_measurements.get("slowest_waits_extra_ms")
        wait_text = (
            f"；最慢租户 P95 {slow_p95}ms，比最快租户多等 {extra}ms"
            if slow_p95 is not None and extra is not None
            else ""
        )
        features["tenant_fairness"] = _verdict(
            "FAIL",
            f"存在租户间读 P95 max/min 比 ≥ {FAIRNESS_MAX_MIN_RATIO}x 的场景 "
            f"(最大 {worst}x){wait_text}",
        )
    else:
        worst = max(
            (v.get("p95_max_min_ratio") or 1.0 for v in multi_tenant), default=1.0
        )
        features["tenant_fairness"] = _verdict(
            "PASS",
            f"全部场景租户间读 P95 max/min 比最大 {worst}x "
            f"< {FAIRNESS_MAX_MIN_RATIO}x",
        )
    features["tenant_fairness"]["measurements"] = fair_measurements

    # -- 特性3: 无内存泄漏（RSS 时间趋势） ------------------------------------
    resources = summary.get("resources") or {}
    trend = resources.get("rss_trend") or {}
    slope = trend.get("slope_mb_per_min")
    unsettled = resources.get("rss_unsettled_mb")
    if slope is None:
        features["memory_leak"] = _verdict(
            "INCONCLUSIVE", "RSS 采样不足（<4 帧）或 /metrics 不可用，无法判定泄漏趋势"
        )
    elif slope >= RSS_LEAK_SLOPE_MB_PER_MIN:
        features["memory_leak"] = _verdict(
            "FAIL",
            f"RSS 上升斜率 {slope} MB/min ≥ 泄漏判定阈值 "
            f"{RSS_LEAK_SLOPE_MB_PER_MIN} MB/min"
            f"（预计每小时增长 {round(slope * 60, 1)} MB）",
        )
    else:
        settle_note = (
            f"冷却后未回落 {unsettled}MB"
            if unsettled is not None
            else "冷却后未回落量不可测（/metrics 采样缺失）"
        )
        features["memory_leak"] = _verdict(
            "PASS",
            f"RSS 上升斜率 {slope} MB/min < 泄漏判定阈值 "
            f"{RSS_LEAK_SLOPE_MB_PER_MIN} MB/min（{settle_note}）",
        )
    features["memory_leak"]["measurements"] = {
        "slope_mb_per_min": slope,
        "projected_growth_mb_per_hour": (
            round(slope * 60, 1) if slope is not None else None
        ),
        "rss_baseline_mb": resources.get("rss_baseline_mb"),
        "rss_peak_mb": resources.get("rss_peak_mb"),
        "rss_unsettled_mb": unsettled,
        "trend_r2": trend.get("r2"),
        "trend_samples": trend.get("samples"),
    }

    # -- 特性4: 资源利用率随时间变化图（报告内容完整性） -------------------------
    server = summary.get("server") or {}
    no_metrics = bool(config.get("no_metrics"))
    if no_metrics or server.get("metrics_available") is False:
        features["resource_timeline"] = _verdict(
            "INCONCLUSIVE",
            "未采集服务端 /metrics（--no-metrics 或抓取全部失败），报告不含资源时间线",
        )
    else:
        features["resource_timeline"] = _verdict(
            "PASS",
            "report.html 已包含 CPU/RSS/线程/commit 队列/inflight 随时间变化曲线 "
            "（metrics_samples.csv 含原始采样时序）",
        )
    features["resource_timeline"]["measurements"] = {
        "metrics_available": server.get("metrics_available"),
        "metrics_frames": resources.get("metrics_frames"),
        "cpu_util_mean_percent": resources.get("cpu_util_mean_percent"),
        "cpu_util_max_percent": resources.get("cpu_util_max_percent"),
        "rss_baseline_mb": resources.get("rss_baseline_mb"),
        "rss_peak_mb": resources.get("rss_peak_mb"),
        "threads_max": resources.get("threads_max"),
        "commit_queue_max": resources.get("commit_queue_max"),
    }

    verdicts = [entry["verdict"] for entry in features.values()]
    overall = "PASS"
    if any(v == "FAIL" for v in verdicts):
        overall = "FAIL"
    elif any(v == "INCONCLUSIVE" for v in verdicts):
        overall = "INCONCLUSIVE"
    return {"features": features, "overall": overall}
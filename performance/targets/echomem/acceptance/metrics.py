"""EchoMem 服务端指标（Prometheus /metrics）的命名与资源摘要。

观测采样器本体（``MetricsFrame`` / ``parse_prometheus_text`` /
``MetricsMonitor``）在 ``performance.monitor``，本模块只保留 EchoMem
指标名常量与把它们组合成场景资源摘要的 ``scene_resource_summary``。
"""

from __future__ import annotations

from typing import Any

from performance.monitor import MetricsMonitor

# Stable EchoMem metric names (see src/echomem/metrics/*).
RECALL_DURATION = "echomem_recall_duration_seconds"
HTTP_DURATION = "echomem_http_request_duration_seconds"
HTTP_INFLIGHT = "echomem_http_requests_inflight"
COMMIT_DURATION = "echomem_session_commit_duration_seconds"
COMMIT_QUEUE_DEPTH = "echomem_session_commit_queue_depth"
CPU_SECONDS = "echomem_process_cpu_seconds_total"
RESIDENT_MEMORY = "echomem_process_resident_memory_bytes"
PROCESS_THREADS = "echomem_process_threads"
PYTHON_THREADS = "echomem_python_threads"
OPEN_HANDLES = "echomem_process_open_handles"
RECALL_TOTAL = "echomem_recall_requests_total"
RECALL_ENGINE_CALLS = "echomem_recall_engine_calls_total"

HISTOGRAMS = frozenset({RECALL_DURATION, HTTP_DURATION, COMMIT_DURATION})
COUNTERS = frozenset({CPU_SECONDS, RECALL_TOTAL, RECALL_ENGINE_CALLS})

# PR421 B7 调度可观测性契约：lane 四元组 + fan-out 二元组，标签限定
# lane/engine（禁止 tenant_id/tenant 标签）。家族 → 短名。
LANE_METRIC_FAMILIES = {
    "echomem_lane_queued": "queued",
    "echomem_lane_wait_seconds": "wait",
    "echomem_lane_exec_seconds": "exec",
    "echomem_lane_rejected_total": "rejected",
}
FANOUT_METRIC_FAMILIES = {
    "echomem_engine_fanout_exec_seconds": "exec",
    "echomem_engine_fanout_skipped_total": "skipped",
}


def match_metric_family(name: str) -> str | None:
    """样本名 → B7 家族（直名或带 _bucket/_count/_sum 后缀）。"""
    for family in (*LANE_METRIC_FAMILIES, *FANOUT_METRIC_FAMILIES):
        if name == family or name.startswith(f"{family}_"):
            return family
    return None


def metric_coverage(monitor: MetricsMonitor, t0: float, t1: float) -> dict:
    """采样帧 → PR421 B7 指标覆盖证据。

    产出与探针侧 ``limit_failure.metrics_coverage`` 同构的
    present/missing/lane_quartets/fanout_engines，并附加
    ``bounded_label_violations``（lane 指标上出现的 tenant 标签）。
    """
    present: set[str] = set()
    lane_quartets: dict[str, dict[str, bool]] = {}
    fanout_engines: dict[str, dict[str, bool]] = {}
    violations: list[dict[str, str]] = []
    seen_violations: set[tuple[str, str, str]] = set()
    for frame in monitor.frames:
        if not (t0 <= frame.ts <= t1):
            continue
        for name, samples in frame.samples.items():
            family = match_metric_family(name)
            if family is None:
                continue
            present.add(family)
            if family in LANE_METRIC_FAMILIES:
                short = LANE_METRIC_FAMILIES[family]
                for labels, _value in samples:
                    lane = labels.get("lane")
                    if lane:
                        lane_quartets.setdefault(
                            str(lane),
                            {"queued": False, "wait": False, "exec": False, "rejected": False},
                        )[short] = True
                    for label_key in ("tenant_id", "tenant"):
                        value = labels.get(label_key)
                        if value is None:
                            continue
                        key = (family, label_key, str(value))
                        if key not in seen_violations:
                            seen_violations.add(key)
                            violations.append(
                                {"metric": family, "label": label_key, "value": str(value)}
                            )
                        break
            elif family in FANOUT_METRIC_FAMILIES:
                short = FANOUT_METRIC_FAMILIES[family]
                for labels, _value in samples:
                    engine = labels.get("engine")
                    if engine:
                        fanout_engines.setdefault(
                            str(engine), {"exec": False, "skipped": False}
                        )[short] = True
    families = {**LANE_METRIC_FAMILIES, **FANOUT_METRIC_FAMILIES}
    return {
        "present": {family: family in present for family in families},
        "missing": sorted(set(families) - present),
        "bounded_label_violations": violations[:5],
        "lane_quartets": lane_quartets,
        "fanout_engines": fanout_engines,
    }


def scene_resource_summary(logger: Any, monitor: MetricsMonitor, t0: float, t1: float) -> dict:
    """Resource snapshot for one scene window, tolerating missing metrics."""
    del logger  # reserved for future diagnostics of missing series
    cpu = monitor.cpu_utilization(CPU_SECONDS, t0, t1)
    rss_series = monitor.gauge_series(RESIDENT_MEMORY, t0, t1)
    return {
        "cpu_util_mean_fraction": cpu,
        "cpu_util_percent": round(cpu * 100, 2) if cpu is not None else None,
        "rss_max_bytes": (
            max(value for _, value in rss_series) if rss_series else None
        ),
        "threads_max": monitor.gauge_max(PROCESS_THREADS, t0, t1),
        "python_threads_max": monitor.gauge_max(PYTHON_THREADS, t0, t1),
        "handles_max": monitor.gauge_max(OPEN_HANDLES, t0, t1),
        "http_inflight_max": monitor.gauge_max(HTTP_INFLIGHT, t0, t1),
        "commit_queue_depth_max": monitor.gauge_max(COMMIT_QUEUE_DEPTH, t0, t1),
        "recall_duration": monitor.histogram_percentiles(RECALL_DURATION, t0, t1),
        "http_duration": monitor.histogram_percentiles(HTTP_DURATION, t0, t1),
        "commit_duration": monitor.histogram_percentiles(COMMIT_DURATION, t0, t1),
    }

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

"""服务端观测纯函数测试：``acceptance/metrics.py``（Prometheus /metrics 采样与分析）。

全部为纯函数/内存内测试，直接构造 ``MetricsFrame`` 注入
``MetricsMonitor.frames``，不启动后台线程、不依赖 conftest 的 mock 服务器。
"""

from __future__ import annotations

import pytest

from performance.targets.echomem.acceptance.metrics import (
    COMMIT_QUEUE_DEPTH,
    CPU_SECONDS,
    HTTP_INFLIGHT,
    PROCESS_THREADS,
    RESIDENT_MEMORY,
    MetricsFrame,
    MetricsMonitor,
    parse_prometheus_text,
    scene_resource_summary,
)

PROMETHEUS_SAMPLE = """\
# HELP a_counter Total counter.
# TYPE a_counter counter
a_counter 3
a_counter 5
# TYPE b_gauge gauge
b_gauge{label="x",other="y"} 1.5
m_bucket{le="0.1"} 2
m_bucket{le="0.5"} 4
m_bucket{le="+Inf"} 4
m_sum 1.2
m_count 4
# EOF
"""


def _monitor_with_frames(frames: list[MetricsFrame]) -> MetricsMonitor:
    monitor = MetricsMonitor("http://test", interval_s=1.0)
    monitor.frames = frames
    return monitor


def _cpu_frame(ts: float, user: float, system: float, rss: float) -> MetricsFrame:
    return MetricsFrame(
        ts=ts,
        samples={
            CPU_SECONDS: [
                ({"mode": "user"}, user),
                ({"mode": "system"}, system),
            ],
            RESIDENT_MEMORY: [({}, rss)],
        },
    )


# -- parse_prometheus_text --------------------------------------------------


def test_parse_basic() -> None:
    parsed = parse_prometheus_text(PROMETHEUS_SAMPLE)
    assert "a_counter" in parsed
    # 同 name+labels 的后续样本覆盖前者
    assert [value for _, value in parsed["a_counter"]] == [5.0]
    assert parsed["b_gauge"][0][0] == {"label": "x", "other": "y"}
    assert parsed["b_gauge"][0][1] == 1.5


def test_parse_histogram_keeps_buckets_and_aggregates() -> None:
    parsed = parse_prometheus_text(PROMETHEUS_SAMPLE)
    buckets = {labels.get("le"): value for labels, value in parsed["m_bucket"]}
    assert buckets == {"0.1": 2.0, "0.5": 4.0, "+Inf": 4.0}
    assert parsed["m_count"][0][1] == 4.0
    assert parsed["m_sum"][0][1] == 1.2


def test_parse_malformed_lines_skipped() -> None:
    parsed = parse_prometheus_text(
        "# TYPE x gauge\nnot_a_number 12 34\nok 1\nbad{label=\"a\" 2\n"
    )
    assert parsed == {"ok": [({}, 1.0)]}


# -- counter / gauge analytics ----------------------------------------------


def test_counter_delta_and_cpu_utilization_and_gauge_max() -> None:
    monitor = _monitor_with_frames(
        [
            _cpu_frame(1.0, 8.0, 2.0, 100.0),
            _cpu_frame(2.0, 12.0, 3.0, 130.0),
        ]
    )
    # 帧 t=1 的 cpu = user(8) + system(2) = 10，t=2 的 cpu = 12 + 3 = 15
    assert monitor.counter_delta(CPU_SECONDS, 0.0, 3.0) == 5.0
    assert monitor.cpu_utilization(0.0, 3.0) == pytest.approx(5.0 / 3.0, abs=1e-4)
    assert monitor.gauge_max(RESIDENT_MEMORY, 0.0, 3.0) == 130.0


def test_counter_delta_missing_window() -> None:
    monitor = _monitor_with_frames([_cpu_frame(5.0, 8.0, 2.0, 100.0)])
    assert monitor.counter_delta(CPU_SECONDS, 0.0, 3.0) is None
    assert monitor.cpu_utilization(0.0, 3.0) is None


def test_gauge_series() -> None:
    monitor = _monitor_with_frames(
        [_cpu_frame(1.0, 0.0, 0.0, 100.0), _cpu_frame(2.0, 0.0, 0.0, 130.0)]
    )
    assert monitor.gauge_series(RESIDENT_MEMORY, 1.5, 2.5) == [(2.0, 130.0)]


def test_cpu_utilization_series() -> None:
    monitor = MetricsMonitor("http://test")
    monitor.frames = [
        MetricsFrame(ts=0.0, samples={CPU_SECONDS: [({}, 0.0)]}),
        MetricsFrame(ts=1.0, samples={CPU_SECONDS: [({}, 1.0)]}),
        MetricsFrame(ts=2.0, samples={CPU_SECONDS: [({}, 1.0)]}),
    ]
    series = monitor.cpu_utilization_series(0.0, 2.0)
    assert series == [(1.0, 100.0), (2.0, 0.0)]


# -- histogram percentiles ---------------------------------------------------


def _histogram_frame() -> MetricsFrame:
    return MetricsFrame(
        ts=1.0,
        samples={
            "m_bucket": [
                ({"le": "0.1", "status": "ok"}, 10.0),
                ({"le": "0.5", "status": "ok"}, 30.0),
                ({"le": "1.0", "status": "ok"}, 40.0),
                ({"le": "+Inf", "status": "ok"}, 40.0),
            ],
            "m_sum": [({"status": "ok"}, 12.0)],
            "m_count": [({"status": "ok"}, 40.0)],
        },
    )


def test_histogram_percentiles() -> None:
    monitor = _monitor_with_frames([_histogram_frame()])
    result = monitor.histogram_percentiles("m", 0.0, 2.0)
    assert result["p50"] == pytest.approx(0.3)
    assert result["p95"] == pytest.approx(0.9)
    assert result["p99"] == pytest.approx(0.98)


def test_histogram_percentiles_missing_window() -> None:
    monitor = _monitor_with_frames([])
    assert monitor.histogram_percentiles("m", 0.0, 2.0) == {
        "p50": None,
        "p95": None,
        "p99": None,
    }


def test_histogram_percentiles_zero_total() -> None:
    frame = MetricsFrame(
        ts=1.0,
        samples={"m_bucket": [({"le": "0.1"}, 0.0), ({"le": "+Inf"}, 0.0)]},
    )
    monitor = _monitor_with_frames([frame])
    assert monitor.histogram_percentiles("m", 0.0, 2.0) == {
        "p50": None,
        "p95": None,
        "p99": None,
    }


# -- scene_resource_summary ---------------------------------------------------


def test_scene_resource_summary_tolerates_missing_metrics() -> None:
    monitor = _monitor_with_frames([])
    summary = scene_resource_summary(None, monitor, 0.0, 2.0)
    assert summary["cpu_util_mean_fraction"] is None
    assert summary["cpu_util_percent"] is None
    assert summary["rss_max_bytes"] is None
    assert summary["recall_duration"] == {"p50": None, "p95": None, "p99": None}


def test_scene_resource_summary_snapshot() -> None:
    frames = [
        MetricsFrame(
            ts=0.5,
            samples={
                CPU_SECONDS: [({"mode": "user"}, 8.0), ({"mode": "system"}, 2.0)],
                RESIDENT_MEMORY: [({}, 100.0)],
                PROCESS_THREADS: [({}, 12.0)],
                HTTP_INFLIGHT: [({}, 3.0)],
                COMMIT_QUEUE_DEPTH: [({}, 2.0)],
            },
        ),
        MetricsFrame(
            ts=1.5,
            samples={
                CPU_SECONDS: [({"mode": "user"}, 16.0), ({"mode": "system"}, 4.0)],
                RESIDENT_MEMORY: [({}, 130.0)],
                PROCESS_THREADS: [({}, 15.0)],
                HTTP_INFLIGHT: [({}, 5.0)],
                COMMIT_QUEUE_DEPTH: [({}, 4.0)],
            },
        ),
    ]
    monitor = _monitor_with_frames(frames)
    summary = scene_resource_summary(None, monitor, 0.0, 2.0)
    # cpu delta = (16+4) - (8+2) = 10，窗口墙钟 2.0
    assert summary["cpu_util_mean_fraction"] == pytest.approx(10.0 / 2.0)
    assert summary["cpu_util_percent"] == pytest.approx(500.0)
    assert summary["rss_max_bytes"] == 130.0
    assert summary["threads_max"] == 15.0
    assert summary["http_inflight_max"] == 5.0
    assert summary["commit_queue_depth_max"] == 4.0

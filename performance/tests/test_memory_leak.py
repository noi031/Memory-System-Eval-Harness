"""通用内存泄漏诊断模块单测。"""
import json
import tempfile
from pathlib import Path

from performance import memory_leak as ML

MB = 1024 * 1024


def _write_csv(path: Path, rows: list[tuple[float, str, str, float]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("ts,metric,labels,value\n")
        for ts, metric, labels, value in rows:
            handle.write(f"{ts},{metric},{json.dumps(labels)},{value}\n")
    return path


def test_rss_trend_slope_positive():
    series = [(i * 60.0, (100 + i * 2) * MB) for i in range(30)]
    out = ML.rss_trend_mb_per_min(series)
    assert out["slope_mb_per_min"] == 2.0  # 每 60s +2MB → 2 MB/min


def test_rss_trend_too_few_samples():
    assert ML.rss_trend_mb_per_min([(0.0, 100 * MB)] * 3)["slope_mb_per_min"] is None


def test_extract_rss_series_buckets():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_csv(
            Path(tmp) / "m.csv",
            [
                (0.0, "echomem_process_resident_memory_bytes", {}, 100 * MB),
                (0.5, "echomem_process_resident_memory_bytes", {}, 102 * MB),
                (1.0, "echomem_process_resident_memory_bytes", {}, 104 * MB),
                (0.0, "echomem_other", {}, 1),
            ],
        )
        pts, series = ML.extract_rss_series(path)
        assert len(pts) == 3  # 只取 resident memory
        assert len(series) == 1  # 30s 桶内均值


def test_evaluate_memory_leak_pass():
    out = ML.evaluate_memory_leak(
        {
            "rss_trend": {"slope_mb_per_min": 1.0, "r2": 0.5, "samples": 10},
            "window_s": 1200,
            "rss_baseline_mb": 500,
            "rss_peak_mb": 600,
        }
    )
    assert out["verdict"] == "PASS"


def test_evaluate_memory_leak_fail():
    out = ML.evaluate_memory_leak(
        {
            "rss_trend": {"slope_mb_per_min": 12.0, "r2": 0.8, "samples": 10},
            "window_s": 1200,
        }
    )
    assert out["verdict"] == "FAIL"


def test_evaluate_memory_leak_short_window_inconclusive():
    out = ML.evaluate_memory_leak(
        {"rss_trend": {"slope_mb_per_min": 99.0}, "window_s": 60}
    )
    assert out["verdict"] == "INCONCLUSIVE"


def test_diagnose_runs_aggregation():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good = root / "good"
        good.mkdir()
        _write_csv(
            good / "metrics_samples.csv",
            [(i * 60.0, "x_resident_memory_bytes", {}, (100 + i) * MB) for i in range(30)],
        )
        bad = root / "bad"
        bad.mkdir()
        _write_csv(
            bad / "metrics_samples.csv",
            [(i * 60.0, "x_resident_memory_bytes", {}, (100 + i * 15) * MB) for i in range(30)],
        )
        out = ML.diagnose_runs([{"output_dir": str(good)}, {"output_dir": str(bad)}])
        assert out["verdict"] == "FAIL"
        assert len(out["per_case"]) == 2


def test_diagnose_runs_no_data_inconclusive():
    out = ML.diagnose_runs([{"output_dir": "/nonexistent"}])
    assert out["verdict"] == "INCONCLUSIVE"

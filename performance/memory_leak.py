"""通用内存泄漏诊断：压测完成后从 metrics_samples.csv 判定 RSS 趋势。

与 target 无关：任何被测系统只要在 case 目录落盘了 monitor 格式的
``metrics_samples.csv``（见 :mod:`performance.monitor`）且服务端暴露
resident memory 指标（metric 名以 ``_resident_memory_bytes`` 结尾），
收尾阶段即可自动产出内存泄漏诊断。suite 收尾（
:func:`performance.suite._finalize_suite`）会调用 :func:`diagnose_runs`
把结果挂到 ``manifest["memory_leak"]``，各 target 报告自行渲染。
"""

from __future__ import annotations

import csv
import statistics
from pathlib import Path
from typing import Any

# RSS 斜率泄漏判定阈值（MB/分钟）：超过即判疑似泄漏（约 300MB/小时）。
RSS_LEAK_SLOPE_MB_PER_MIN = 5.0
# 泄漏判定的最小观测窗口（秒）：短于它时 RSS 斜率受启动预热/GC 主导，不可判定。
MIN_LEAK_WINDOW_S = 600.0
# 识别服务端 resident memory 指标的 metric 名后缀。
RSS_METRIC_SUFFIX = "_resident_memory_bytes"
# 斜率回归前的降采样分桶（秒）：平滑瞬时波动。
_BUCKET_S = 30.0
# 斜率回归所需最少样本数。
_MIN_SAMPLES = 4


def rss_trend_mb_per_min(series: list[tuple[float, float]]) -> dict[str, Any]:
    """Least-squares slope of RSS (bytes) over time, in MB per minute.

    Needs at least ``_MIN_SAMPLES`` samples across the observed window;
    fewer samples return an undecidable result. The slope together with the
    cooling settle delta distinguishes a slow leak from index-size growth.
    """
    n = len(series)
    if n < _MIN_SAMPLES:
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


def extract_rss_series(
    csv_path: Path,
    *,
    metric_suffix: str = RSS_METRIC_SUFFIX,
    bucket_s: float = _BUCKET_S,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]] | None:
    """从 metrics_samples.csv 提取 resident memory 序列，返回 (原始点, 分桶均值点)。

    无该指标或文件缺失时返回 None。分桶点用于斜率回归（去抖）。
    """
    if not Path(csv_path).is_file():
        return None
    pts: list[tuple[float, float]] = []
    with open(csv_path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["metric"].endswith(metric_suffix):
                try:
                    pts.append((float(row["ts"]), float(row["value"])))
                except ValueError:
                    continue
    if not pts:
        return None
    pts.sort()
    ts0 = pts[0][0]
    buckets: dict[int, list[float]] = {}
    for ts, value in pts:
        buckets.setdefault(int((ts - ts0) // bucket_s), []).append(value)
    series = [
        (ts0 + key * bucket_s, statistics.mean(values))
        for key, values in sorted(buckets.items())
    ]
    return pts, series


def compute_resources(case_dir: Path) -> dict[str, Any] | None:
    """从 case 目录的 metrics_samples.csv 计算 RSS 诊断资源；无数据返回 None。"""
    extracted = extract_rss_series(Path(case_dir) / "metrics_samples.csv")
    if extracted is None:
        return None
    pts, series = extracted
    trend = rss_trend_mb_per_min(series)
    window_s = pts[-1][0] - pts[0][0]
    return {
        "rss_trend": trend,
        "window_s": round(window_s, 1),
        "rss_baseline_mb": round(series[0][1] / 1e6, 1) if series else None,
        "rss_peak_mb": round(max(value for _, value in pts) / 1e6, 1),
    }


def evaluate_memory_leak(resources: dict[str, Any]) -> dict[str, Any]:
    """特性3：无内存泄漏判定（RSS 归一校正后斜率）。

    ``resources`` 含 rss_trend/window_s/rss_baseline_mb/rss_peak_mb，可选
    rss_normalized（扣除注入数据增长后的归一口径）与 rss_unsettled_mb。
    窗口短于 MIN_LEAK_WINDOW_S 时斜率不可靠（启动预热/GC 主导），判
    INCONCLUSIVE；无归一数据时回退原始斜率。
    """
    trend = resources.get("rss_trend") or {}
    normalized = resources.get("rss_normalized") or {}
    slope = (
        (normalized.get("net_trend") or {}).get("slope_mb_per_min")
        or trend.get("slope_mb_per_min")
    )
    unsettled = resources.get("rss_unsettled_mb")
    window_s = float(resources.get("window_s") or 0)
    if window_s and window_s < MIN_LEAK_WINDOW_S:
        verdict = _verdict(
            "INCONCLUSIVE",
            f"观测窗口 {window_s:.0f}s < {MIN_LEAK_WINDOW_S:.0f}s，"
            f"RSS 斜率受启动预热/GC 主导，不判泄漏",
        )
    elif slope is None:
        verdict = _verdict(
            "INCONCLUSIVE", "RSS 采样不足（<4 帧）或 /metrics 不可用，无法判定泄漏趋势"
        )
    elif slope >= RSS_LEAK_SLOPE_MB_PER_MIN:
        verdict = _verdict(
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
        verdict = _verdict(
            "PASS",
            f"RSS 上升斜率 {slope} MB/min < 泄漏判定阈值 "
            f"{RSS_LEAK_SLOPE_MB_PER_MIN} MB/min（{settle_note}）",
        )
    verdict["measurements"] = {
        "slope_mb_per_min": slope,
        "slope_source": "rss_net" if normalized.get("net_trend") else "rss_raw",
        "projected_growth_mb_per_hour": (
            round(slope * 60, 1) if slope is not None else None
        ),
        "window_s": window_s,
        "rss_baseline_mb": resources.get("rss_baseline_mb"),
        "rss_peak_mb": resources.get("rss_peak_mb"),
        "rss_unsettled_mb": unsettled,
        "rss_normalized": {
            "net_peak_mb": normalized.get("net_peak_mb"),
            "net_settled_mb": normalized.get("net_settled_mb"),
            "injected_mb": normalized.get("injected_mb"),
        },
        "trend_r2": trend.get("r2"),
        "trend_samples": trend.get("samples"),
    }
    return verdict


def _verdict(status: str, reason: str) -> dict[str, Any]:
    return {"verdict": status, "reason": reason}


def diagnose_case(case_dir: Path) -> dict[str, Any] | None:
    """单个 case 的内存泄漏诊断；无 RSS 数据返回 None。"""
    resources = compute_resources(case_dir)
    if resources is None:
        return None
    return {"case": str(Path(case_dir).name), **evaluate_memory_leak(resources)}


def diagnose_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """对一组已完成的 run（含 output_dir）聚合内存泄漏判定。

    任一 case 判 FAIL 则整体 FAIL；存在 INCONCLUSIVE（短窗口/缺 RSS）且无
    FAIL 则整体 INCONCLUSIVE；全部 case 都有可判数据且全 PASS 才判 PASS。
    缺 RSS 采样的 case 保留带原因的 INCONCLUSIVE 项，不丢弃。
    """
    per_case: list[dict[str, Any]] = []
    for run in runs:
        output_dir = run.get("output_dir")
        if not output_dir:
            continue
        diag = diagnose_case(Path(output_dir))
        if diag is None:
            per_case.append({
                "case": str(Path(output_dir).name),
                "verdict": "INCONCLUSIVE",
                "reason": "无 RSS 采样（/metrics 缺失或无 resident memory 指标）",
            })
        else:
            per_case.append(diag)
    verdicts = [item["verdict"] for item in per_case]
    if not verdicts:
        return {
            "verdict": "INCONCLUSIVE",
            "reason": "无 case 提供 RSS 采样（/metrics 缺失或无 resident memory 指标）",
            "per_case": [],
        }
    if any(v == "FAIL" for v in verdicts):
        verdict, reason = "FAIL", "存在窗口足够的 case 判定为内存泄漏"
    elif any(v == "INCONCLUSIVE" for v in verdicts):
        verdict, reason = "INCONCLUSIVE", "存在不可判定 case（短窗口或无 RSS 数据）"
    else:
        verdict, reason = "PASS", "全部 case RSS 斜率低于泄漏阈值"
    return {"verdict": verdict, "reason": reason, "per_case": per_case}

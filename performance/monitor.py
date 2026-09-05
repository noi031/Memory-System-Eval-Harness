"""服务端 Prometheus /metrics 观测采样器（系统无关）。

后台采样线程 GET ``<base_url>/metrics``，按 ``interval_s`` 间隔保帧；
派生 helper 从帧里算 counter 差值 / gauge 极值·序列 / histogram 分位。
抓取失败被容忍：采样线程继续跑并记录失败次数，不中断。

具体指标名由调用方（target 层）传入，本模块不认识任何被测系统。
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("performance.monitor")


@dataclass
class MetricsFrame:
    """Raw timestamped samples from one /metrics GET.

    ``samples`` maps metric name -> list of (labels_dict, value). Histogram
    series keep their ``_bucket``/``_sum``/``_count`` full names so the
    distribution survives parsing.
    """

    ts: float
    samples: dict[str, list[tuple[dict[str, str], float]]] = field(default_factory=dict)


def parse_prometheus_text(text: str) -> dict[str, list[tuple[dict[str, str], float]]]:
    """Parse a Prometheus text exposition into name -> [(labels, value)].

    COMMENT/TYPE/HELP lines are skipped; histogram series are kept under
    their full names (``<name>_bucket`` etc.) so later helpers can
    reconstruct bucket distributions. Multiple samples of the same
    name+labels collapse to the last occurrence.
    """
    samples: dict[str, list[tuple[dict[str, str], float]]] = defaultdict(list)
    seen: dict[tuple[str, str], int] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "EOF":
            break
        if "{" in line:
            name, _, rest = line.partition("{")
            labels_part, sep2, value_part = rest.partition("} ")
            if sep2 != "} ":
                continue  # malformed sample, skip
        else:
            # label-less samples: "<name> <value>"
            name, sep3, value_part = line.partition(" ")
            if not sep3:
                continue
            labels_part = ""
        meta = name
        if labels_part:
            meta = f"{name}{{{labels_part}}}"
        key = (name, meta)
        try:
            value = float(value_part)
        except ValueError:
            continue
        if key in seen:
            samples[name][seen[key]] = (_parse_labels(labels_part), value)
        else:
            seen[key] = len(samples[name])
            samples[name].append((_parse_labels(labels_part), value))
    return dict(samples)


def _parse_labels(labels_part: str) -> dict[str, str]:
    """Parse ``k="v",k2="v2"`` into a dict (empty part -> {})."""
    if not labels_part:
        return {}
    labels: dict[str, str] = {}
    inside = labels_part.strip()
    if inside.startswith("{") and inside.endswith("}"):
        inside = inside[1:-1]
    for token in inside.split(","):
        token = token.strip()
        if not token:
            continue
        key, _, raw = token.partition("=")
        labels[key.strip()] = raw.strip().strip('"')
    return labels


@dataclass
class MetricsMonitor:
    """Background /metrics sampler plus derived analytics."""

    base_url: str
    interval_s: float = 2.0
    timeout_s: float = 5.0

    frames: list[MetricsFrame] = field(default_factory=list)
    fetch_ok: int = 0
    fetch_failures: int = 0
    last_error: str = ""

    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="perf-metrics-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, join_s: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_s)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self.interval_s)

    def sample(self) -> MetricsFrame | None:
        url = f"{self.base_url}/metrics"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as resp:
                text = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.fetch_failures += 1
            self.last_error = str(exc)
            logger.warning("metrics fetch failed: %s", exc)
            return None
        try:
            frame = MetricsFrame(ts=time.time(), samples=parse_prometheus_text(text))
        except Exception as exc:  # keep the sampler alive on any parse issue
            self.fetch_failures += 1
            self.last_error = f"parse: {exc}"
            logger.warning("metrics parse failed: %s", exc)
            return None
        self.fetch_ok += 1
        self.frames.append(frame)
        return frame

    # -- frame helpers ------------------------------------------------------

    def _frame_at_or_before(self, ts: float) -> MetricsFrame | None:
        picked: MetricsFrame | None = None
        for frame in self.frames:
            if frame.ts <= ts:
                picked = frame
            else:
                break
        return picked

    def _value(self, frame: MetricsFrame, name: str) -> float:
        """Sum samples of *name* in one frame across label sets."""
        total = 0.0
        for _, value in frame.samples.get(name, []):
            total += value
        return total

    # -- derived analytics ---------------------------------------------------

    def _first_frame_in(self, t0: float, t1: float) -> MetricsFrame | None:
        for frame in self.frames:
            if t0 <= frame.ts <= t1:
                return frame
        return None

    def counter_delta(self, name: str, t0: float, t1: float) -> float | None:
        """(value at t1) - (value at t0) for a counter, summed across labels.

        Baseline is the earliest frame inside the window, falling back to
        the last frame before the window; end is the last frame at or
        before ``t1``. Either missing -> None.
        """
        after = self._frame_at_or_before(t1)
        if after is None:
            return None
        before = self._first_frame_in(t0, t1) or self._frame_at_or_before(t0)
        if before is None:
            return None
        return self._value(after, name) - self._value(before, name)

    def gauge_max(self, name: str, t0: float, t1: float) -> float | None:
        values = [self._value(f, name) for f in self.frames if t0 <= f.ts <= t1]
        return max(values) if values else None

    def gauge_series(self, name: str, t0: float, t1: float) -> list[tuple[float, float]]:
        return [
            (f.ts, self._value(f, name))
            for f in self.frames
            if t0 <= f.ts <= t1
        ]

    def cpu_utilization_series(
        self,
        name: str,
        t0: float,
        t1: float,
    ) -> list[tuple[float, float]]:
        """Per-frame CPU utilization (percent of one core) via frame deltas.

        Differences between consecutive frames divided by their wall-clock
        span; the first frame in the window has no delta and is skipped.
        """
        series: list[tuple[float, float]] = []
        prev_value: float | None = None
        prev_ts: float | None = None
        for frame in self.frames:
            if frame.ts < t0 or frame.ts > t1:
                continue
            value = self._value(frame, name)
            if prev_value is not None and prev_ts is not None and frame.ts > prev_ts:
                span = frame.ts - prev_ts
                if span > 0:
                    delta = value - prev_value
                    percent = max(0.0, delta) / span * 100.0
                    series.append((frame.ts, round(percent, 2)))
            prev_value, prev_ts = value, frame.ts
        return series

    def histogram_percentiles(
        self,
        name: str,
        t0: float,
        t1: float,
    ) -> dict[str, float | None]:
        """Estimate p50/p95/p99 (seconds) from the last frame in [t0, t1].

        Uses the cumulative bucket counts of the last frame (Prometheus
        histograms are cumulative), with the previous bucket's upper bound
        as the lower interpolation anchor.
        """
        frame = None
        for candidate in self.frames:
            if t0 <= candidate.ts <= t1:
                frame = candidate
        if frame is None:
            return {"p50": None, "p95": None, "p99": None}
        buckets = self._bucket_distribution(frame, name)
        if not buckets or buckets[-1][1] <= 0:
            return {"p50": None, "p95": None, "p99": None}
        total = buckets[-1][1]
        bounds = [b for b, _ in buckets]
        counts = [c for _, c in buckets]
        result: dict[str, float | None] = {}
        for label, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99)):
            result[label] = self._bucket_percentile(bounds, counts, total, q)
        return result

    @staticmethod
    def _bucket_distribution(
        frame: MetricsFrame,
        name: str,
    ) -> list[tuple[float, float]]:
        """Cumulative bucket (upper bound, count) pairs for a histogram.

        Buckets of the same upper bound across all label sets are merged
        (summed), which yields the global latency distribution of the
        metric regardless of its outcome/status labels.
        """
        prefix = f"{name}_bucket"
        entries = frame.samples.get(prefix, [])
        if not entries:
            return []
        merged: dict[float, float] = {}
        for labels, value in entries:
            le = labels.get("le", "")
            if le == "+Inf" or le == "":
                continue
            try:
                bound = float(le)
            except ValueError:
                continue
            merged[bound] = merged.get(bound, 0.0) + value
        ordered = sorted(merged.items())
        return [(bound, count) for bound, count in ordered]

    @staticmethod
    def _bucket_percentile(
        bounds: list[float],
        counts: list[float],
        total: float,
        q: float,
    ) -> float | None:
        if total <= 0:
            return None
        target = q * total
        lower_bound = 0.0
        lower_count = 0.0
        for bound, count in zip(bounds, counts):
            if count >= target:
                if count == lower_count:
                    return bound
                frac = (target - lower_count) / (count - lower_count)
                return lower_bound + (bound - lower_bound) * frac
            lower_bound = bound
            lower_count = count
        return bounds[-1]

    def cpu_utilization(self, name: str, t0: float, t1: float) -> float | None:
        """CPU seconds delta divided by wall time (fraction of one core)."""
        delta = self.counter_delta(name, t0, t1)
        wall = t1 - t0
        if delta is None or wall <= 0:
            return None
        return round(delta / wall, 4)

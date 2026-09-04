"""Pure statistics for load-test results.

Self-contained (no imports from the ``performance`` package) so the
general engine stays independently testable.  Field names intentionally
match the existing ``performance/`` harness records for later comparison.
"""

from __future__ import annotations

import math
from typing import Any, Iterable


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile.  ``p`` in ``(0, 100]``; empty input -> None."""

    if not values:
        return None
    ordered = sorted(values)
    if p <= 0 or p > 100:
        raise ValueError(f"percentile must be in (0, 100], got {p}")
    rank = max(1, math.ceil(p / 100.0 * len(ordered)))
    return ordered[rank - 1]


def latency_summary(values: list[float]) -> dict[str, float | None]:
    """Aggregate a latency series in milliseconds."""

    if not values:
        return {
            "count": 0,
            "mean": None,
            "p50": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "max": None,
        }
    mean = sum(values) / len(values)
    return {
        "count": len(values),
        "mean": _round2(mean),
        "p50": _round2(percentile(values, 50)),
        "p90": _round2(percentile(values, 90)),
        "p95": _round2(percentile(values, 95)),
        "p99": _round2(percentile(values, 99)),
        "max": _round2(max(values)),
    }


def histogram(values: Iterable[str]) -> dict[str, int]:
    """Count occurrences of categorical values (status codes, error types)."""

    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def rps(count: int, elapsed_s: float) -> float | None:
    """Requests per second; None when no wall time elapsed."""

    if elapsed_s <= 0 or count <= 0:
        return None
    return _round2(count / elapsed_s)


def _round2(value: float) -> float:
    return round(value, 2)


def top(hist: dict[str, int], limit: int = 3) -> dict[str, int]:
    """Top-N entries of a histogram by count."""

    return dict(sorted(hist.items(), key=lambda item: item[1], reverse=True)[:limit])


def as_json_safe(obj: Any) -> Any:
    """Recursively convert values that json cannot serialize (e.g. sets)."""

    if isinstance(obj, dict):
        return {str(k): as_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [as_json_safe(v) for v in obj]
    return obj

"""Scenario matrix for performance stress runs.

Scenarios:
    A  pure-read baseline (no writes, used as the comparison reference)
    B  pure-write injection (open -> add -> commit submit -> commit done)
    C  mixed read/write at configurable read:write ratios
    D  injection burst on top of sustained reads (detects write/read coupling)

``expand_matrix`` expands (concurrency steps x scenarios x mix ratios) into
an ordered list of :class:`SceneRun` during which server metrics stay running.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCENARIO_IDS = ("A", "B", "C", "D")

SCENARIO_NAMES: dict[str, str] = {
    "A": "pure-read baseline",
    "B": "pure-write injection",
    "C": "mixed read/write",
    "D": "injection burst over reads",
}


@dataclass(frozen=True)
class SceneRun:
    """One atomic stress step: one scenario at one concurrency step.

    ``per_tenant_conc`` is the number of worker threads per tenant; total
    worker threads = tenants * per_tenant_conc.
    """

    scene_id: str
    per_tenant_conc: int
    duration_s: float
    mix: tuple[int, int] | None = None  # (read, write) ratio, scene C only
    burst_commits: int = 0  # scene D only
    burst_window_s: float = 0.0  # scene D only

    @property
    def key(self) -> str:
        """Stable identifier used as the summary section key."""
        if self.scene_id == "C" and self.mix is not None:
            return f"C:{self.mix[0]}:{self.mix[1]}@{self.per_tenant_conc}"
        return f"{self.scene_id}@{self.per_tenant_conc}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "per_tenant_conc": self.per_tenant_conc,
            "duration_s": self.duration_s,
            "mix": f"{self.mix[0]}:{self.mix[1]}" if self.mix else None,
            "burst_commits": self.burst_commits,
            "burst_window_s": self.burst_window_s,
        }


def parse_mix_ratio(value: str) -> tuple[int, int]:
    """Parse a "READ:WRITE" ratio string into an int pair.

    ``"8:1"`` means 8 read operations per 1 write transaction.
    """
    left, _, right = value.partition(":")
    read = int(left.strip())
    write = int(right.strip())
    if read < 0 or write < 0 or read + write == 0:
        raise ValueError(f"invalid mix ratio '{value}': expected READ:WRITE")
    return (read, write)


def parse_mix_ratios(values: list[str]) -> list[tuple[int, int]]:
    return [parse_mix_ratio(value) for value in values]


def parse_concurrency_steps(value: str) -> list[int]:
    steps = [int(part.strip()) for part in str(value).split(",") if part.strip()]
    if not steps or any(step < 1 for step in steps):
        raise ValueError(f"invalid concurrency steps '{value}': positive ints expected")
    return steps


def expand_matrix(
    *,
    scenario_ids: list[str],
    concurrency_steps: list[int],
    mix_ratios: list[tuple[int, int]],
    duration_s: float,
    burst_commits: int,
    burst_window_s: float,
) -> list[SceneRun]:
    """Expand the scenario matrix into an ordered list of runs.

    Order: scenario-major, concurrency-minor (A@1, A@4, ... C:8:1@1, ...).
    """
    unknown = [sid for sid in scenario_ids if sid not in SCENARIO_IDS]
    if unknown:
        raise ValueError(f"unknown scenario ids: {', '.join(unknown)}")
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")

    runs: list[SceneRun] = []
    for sid in scenario_ids:
        for conc in concurrency_steps:
            if sid == "A":
                runs.append(SceneRun("A", conc, duration_s))
            elif sid == "B":
                runs.append(SceneRun("B", conc, duration_s))
            elif sid == "C":
                for mix in mix_ratios:
                    runs.append(SceneRun("C", conc, duration_s, mix=mix))
            elif sid == "D":
                runs.append(
                    SceneRun(
                        "D",
                        conc,
                        duration_s,
                        burst_commits=burst_commits,
                        burst_window_s=burst_window_s,
                    )
                )
    return runs
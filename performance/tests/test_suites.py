"""Observation load matrix (suites.py) unit tests.

The catalog is ``six_metric_observation_cases`` only: M2 fairness (4t/8t)
and M3 flood/baseline/heterogeneous cases. M1 runs through the T x U
capacity runner and M4/M5/M6 are probes, so neither contributes cases here.
"""

from __future__ import annotations

import pytest

from performance.engine import load_scene
from performance.profile import ArrivalSpec
from performance.targets.echomem.orchestrator.suites import (
    SCENES_DIR,
    QuickSpec,
    apply_quick,
    build_case_profile,
    six_metric_observation_cases,
)

REQUIRED_FIELDS = (
    "label", "scene", "tenants", "duration_s", "search_rps", "commit_rpm",
    "sessions_per_tenant", "messages_per_session",
)

# observation_run --quick / m6_only 使用的收敛规格。
_OBSERVATION_QUICK = QuickSpec(duration_cap_s=45, barrier_count_cap=8, include_seed=True)


def _by_label(cases):
    return {case["label"]: case for case in cases}


# -- catalog shape ------------------------------------------------------


def test_observation_catalog_six_cases():
    cases = six_metric_observation_cases()
    assert [case["label"] for case in cases] == [
        "m2-fairness-4t", "m2-fairness-8t", "m3-baseline",
        "m3-flood-uniform", "m3-flood-single-tenant", "m3-heterogeneous-tenants",
    ]


def test_observation_catalog_labels_unique():
    labels = [case["label"] for case in six_metric_observation_cases()]
    assert len(labels) == len(set(labels))


def test_observation_catalog_required_fields_present():
    for case in six_metric_observation_cases():
        for field in REQUIRED_FIELDS:
            assert field in case, f"{case['label']} missing {field}"


def test_observation_catalog_scenes_loadable():
    for case in six_metric_observation_cases():
        load_scene(SCENES_DIR / f"{case['scene']}.py")


def test_observation_quick_variant_bounds_duration_and_barrier():
    quick = _by_label(six_metric_observation_cases(quick=True))
    full = _by_label(six_metric_observation_cases())
    assert quick["m2-fairness-4t"]["duration_s"] == 45
    assert quick["m2-fairness-8t"]["duration_s"] == 45
    assert quick["m3-baseline"]["duration_s"] == 15
    assert quick["m3-flood-uniform"]["commit_barrier_count"] == 8
    assert quick["m3-flood-single-tenant"]["commit_tenant_counts"] == [8, 0, 0, 0]
    assert full["m2-fairness-4t"]["duration_s"] == 480
    assert full["m3-flood-uniform"]["commit_barrier_count"] == 64


# -- apply_quick --------------------------------------------------------


def test_apply_quick_returns_copy():
    case = _by_label(six_metric_observation_cases())["m3-flood-uniform"]
    quick = apply_quick(case, QuickSpec(barrier_count_cap=8))
    assert case["commit_barrier_count"] == 64
    assert quick["commit_barrier_count"] == 8
    assert quick["duration_s"] == 15.0


def test_apply_quick_caps_explicit_tenant_counts():
    case = _by_label(six_metric_observation_cases())["m3-flood-single-tenant"]
    quick = apply_quick(case, QuickSpec(barrier_count_cap=8))
    assert quick["commit_barrier_count"] == 8
    assert quick["commit_tenant_counts"] == [5, 1, 1, 1]
    assert sum(quick["commit_tenant_counts"]) == 8


def test_apply_quick_can_bound_commit_poll_timeout():
    case = {**_by_label(six_metric_observation_cases())["m3-flood-uniform"],
            "commit_poll_timeout_s": 180}
    assert apply_quick(case, QuickSpec())["commit_poll_timeout_s"] == 180
    bounded = apply_quick(case, QuickSpec(commit_poll_timeout_cap_s=45))
    assert bounded["commit_poll_timeout_s"] == 45


# -- build_case_profile -------------------------------------------------


def _profile(case, *, quick=None):
    return build_case_profile(
        case,
        base_url="http://127.0.0.1:8010/",
        tenant_count=1,
        auth_headers={"X-Auth-Key": "k1"},
        quick=quick,
    )


def test_build_m3_baseline_read_only():
    case = _by_label(six_metric_observation_cases())["m3-baseline"]
    profile = _profile(case)
    assert profile.target.base_url == "http://127.0.0.1:8010"
    assert profile.load.mix == {"read": 64, "write": 0}
    assert profile.load.arrival == {
        "read": ArrivalSpec(model="fixed_rps", rps=8.0),
    }
    assert "write" not in profile.load.arrival


def test_build_m3_flood_uniform_barrier_params():
    case = _by_label(six_metric_observation_cases())["m3-flood-uniform"]
    profile = _profile(case)
    params = profile.params
    assert params["barrier_count"] == 64
    assert params["barrier_distribution"] == "uniform"
    assert params["barrier_max_workers"] == 32
    assert "write" not in profile.load.arrival
    assert profile.load.mix == {"read": 64}


def test_build_m3_flood_single_tenant_explicit_counts():
    case = _by_label(six_metric_observation_cases())["m3-flood-single-tenant"]
    profile = _profile(case)
    assert profile.params["barrier_count"] == 64
    assert profile.params["barrier_distribution"] == "explicit"
    assert profile.params["commit_tenant_counts"] == [64, 0, 0, 0]


def test_build_m2_fairness_per_tenant_arrival():
    case = _by_label(six_metric_observation_cases())["m2-fairness-4t"]
    profile = _profile(case)
    assert profile.load.arrival["read"].scope == "per_tenant"
    assert profile.load.arrival["write"].scope == "per_tenant"
    assert profile.load.arrival["read"].rps == 1.0
    assert profile.load.arrival["write"].rps == pytest.approx(2.0 / 60.0)
    assert profile.load.arrival["write"].start_s == 30
    assert profile.load.arrival["read"].end_s == 300
    assert profile.load.mix == {"read": 64, "write": 64}


def test_build_m3_heterogeneous_tenant_weights():
    case = _by_label(six_metric_observation_cases())["m3-heterogeneous-tenants"]
    profile = _profile(case)
    assert profile.load.arrival["read"].tenant_weights == (8.0, 4.0, 2.0, 1.0)
    assert profile.load.arrival["write"].tenant_weights == (1.0, 2.0, 4.0, 8.0)


def test_build_quick_bounds_observation_case():
    case = _by_label(six_metric_observation_cases())["m3-flood-uniform"]
    profile = _profile(case, quick=_OBSERVATION_QUICK)
    assert profile.load.duration_s == 45.0
    assert profile.params["barrier_count"] == 8
    assert profile.params["barrier_max_workers"] == 8

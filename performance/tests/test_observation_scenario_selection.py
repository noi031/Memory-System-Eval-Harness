"""Load-scenario selection (--scenarios) for the observation entry point.

``--scenarios`` follows the generic layer's load/probe convention: the
catalog is exactly the six M2/M3 observation cases, an explicit selection
replaces the metric-derived default set, and M4/M6 dependency load windows
are appended automatically so their probes keep evidence windows.
"""

from __future__ import annotations

import argparse
import json

import pytest

from performance.targets.echomem import observation_run as module
from performance.targets.echomem.orchestrator.suites import six_metric_observation_cases


def _setup(tmp_path, monkeypatch, *, metrics, scenarios="", probes=""):
    tenants = tmp_path / "tenants.json"
    tenants.write_text('{"tenants": []}')
    profile = {"name": "4U8G", "base_url": "http://unused.invalid",
               "tenant_config": str(tenants), "resource_evidence": {}, "readiness": {}}
    profiles = tmp_path / "profiles.json"
    profiles.write_text(json.dumps({"profiles": [profile]}))
    captured = {}

    def suite(profile, **kwargs):
        captured["suite"] = kwargs
        return {"runs": []}

    monkeypatch.setattr(module, "_configure", lambda p, *a, **k: p)
    monkeypatch.setattr(module, "check_readiness", lambda p, **kw: {"ok": True})
    monkeypatch.setattr(module, "platform_snapshot", lambda: {"git_commit": "unit-test"})
    monkeypatch.setattr(module, "run_suite", suite)
    monkeypatch.setattr(module, "run_configured_probes",
                        lambda *a, **k: (captured.setdefault("probes_kwargs",
                                                             {"probes": k.get("probes")}), []))
    monkeypatch.setattr(module, "evaluate_observation",
                        lambda *a, **k: {"status": "MEASURED", "sampling_mode": "full", "metrics": {}})
    monkeypatch.setattr(module, "write_observation_report", lambda *a, **k: None)
    args = argparse.Namespace(profiles=profiles, profile="4U8G", env_file=None,
        out_dir=tmp_path / "out", metrics=metrics, quick=False, resume=False,
        timeout_s=600, scenarios=scenarios, probes=probes)
    return args, captured


# -- 校验 ----------------------------------------------------------------


def test_scenario_catalog_is_the_six_observation_cases():
    catalog = {str(case["label"]) for case in six_metric_observation_cases()}
    assert module._scenario_labels(",".join(sorted(catalog))) == sorted(catalog)


def test_scenario_labels_reject_unknown_labels():
    with pytest.raises(ValueError, match="scenarios must be"):
        module._scenario_labels("m3-baseline,soak")
    with pytest.raises(ValueError, match="scenarios must be"):
        module._scenario_labels("")


def test_scenario_labels_dedupe_and_preserve_order():
    assert module._scenario_labels("m3-baseline,m3-baseline,m3-flood-uniform") == [
        "m3-baseline", "m3-flood-uniform",
    ]


# -- run() 场景解析 -------------------------------------------------------


def test_run_derives_default_scenarios_from_metrics(tmp_path, monkeypatch):
    args, captured = _setup(tmp_path, monkeypatch, metrics="M2,M3")
    module.run(args)
    assert captured["suite"]["scenarios"] == [
        "m2-fairness-4t", "m2-fairness-8t",
        "m3-baseline", "m3-flood-uniform",
        "m3-flood-single-tenant", "m3-heterogeneous-tenants",
    ]


def test_run_explicit_scenarios_replace_metric_defaults(tmp_path, monkeypatch):
    args, captured = _setup(tmp_path, monkeypatch, metrics="M2,M3",
                            scenarios="m3-baseline")
    module.run(args)
    assert captured["suite"]["scenarios"] == ["m3-baseline"]


def test_run_m6_appends_flood_uniform_dependency(tmp_path, monkeypatch):
    args, captured = _setup(tmp_path, monkeypatch, metrics="M3,M6",
                            scenarios="m3-baseline")
    module.run(args)
    assert captured["suite"]["scenarios"] == ["m3-baseline", "m3-flood-uniform"]


def test_run_m4_appends_baseline_dependency(tmp_path, monkeypatch):
    args, captured = _setup(tmp_path, monkeypatch, metrics="M4",
                            scenarios="m3-flood-uniform")
    module.run(args)
    assert captured["suite"]["scenarios"] == ["m3-flood-uniform", "m3-baseline"]


def test_run_m6_without_metrics_deps_still_gets_load_window(tmp_path, monkeypatch):
    args, captured = _setup(tmp_path, monkeypatch, metrics="M6")
    module.run(args)
    assert captured["suite"]["scenarios"] == ["m3-baseline", "m3-flood-uniform"]


def test_run_passes_probe_selection_through(tmp_path, monkeypatch):
    args, captured = _setup(tmp_path, monkeypatch, metrics="M3,M6",
                            scenarios="m3-baseline", probes="nxn-isolation,capability")
    module.run(args)
    assert captured["probes_kwargs"]["probes"] == ["nxn-isolation", "capability"]

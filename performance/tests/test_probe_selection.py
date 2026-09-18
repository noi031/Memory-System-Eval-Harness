"""Probe selection (--probes) semantics for the probe orchestrator.

``probes=None`` keeps the configured-only behavior; an explicit selection
runs exactly the selected probes plus the metric-required ones
(fault-isolation / commit-recovery / tenant-observability), and a selected
probe without a profile section runs with its own parameter defaults. The
two ported legacy probes (nxn-isolation, disconnect-recovery) are wired with
safe default parameters (tenant config, first tenant identity, process id).
"""

from __future__ import annotations

import json

from performance.targets.echomem.orchestrator import probes as probes_module
from performance.targets.echomem.orchestrator.probes import PROBE_NAMES, run_configured_probes


def _tenants(tmp_path, count=4):
    path = tmp_path / "tenants.json"
    path.write_text(json.dumps({"tenants": [
        {"tenant_id": f"t{index}", "auth_key_env": "ECHOMEM_AUTH_KEY"}
        for index in range(count)
    ]}), encoding="utf-8")
    return path


def _run(profile, tenant_config, suite_dir, **kwargs):
    return run_configured_probes(
        profile,
        base_url="http://unused.invalid",
        suite_dir=suite_dir,
        auth_headers={},
        tenant_config=tenant_config,
        quick=False,
        timeout_s=30,
        **kwargs,
    )


# -- 注册表 ----------------------------------------------------------------


def test_probe_names_has_fourteen_entries():
    assert len(PROBE_NAMES) == 14
    assert all(name == name.replace("_", "-") and " " not in name for name in PROBE_NAMES)
    assert "nxn-isolation" in PROBE_NAMES and "disconnect-recovery" in PROBE_NAMES


# -- 选择语义 ----------------------------------------------------------------


def test_probes_none_runs_all_configured_blocks(tmp_path, monkeypatch):
    calls = []

    def fake_probe(params, *, probes_dir, scene, output, base_url, timeout_s, redact_values=None):
        calls.append((scene, dict(params)))
        return {"status": "PASS"}, {"status": "completed"}

    monkeypatch.setattr(probes_module, "run_configured_probe", fake_probe)
    tenants_path = _tenants(tmp_path)
    profile = {
        "tenant_config": str(tenants_path),
        "six_metrics_observation": True,
        "capability_probe": {"enabled": True},
        "invalid_input": {"enabled": True},
        "missing_cases": {"enabled": True},
        "concurrent_commit": {"enabled": True},
    }
    artifacts, commands = _run(profile, {"tenants": []}, tmp_path / "suite")
    scenes = {scene for scene, _ in calls}
    assert {"capability.py", "invalid_input.py", "missing_cases.py", "concurrent_commit.py"} <= scenes
    assert artifacts["capability_probe"]["status"] == "PASS"
    # 未配置的探针块不跑。
    assert not any(scene == "fault_isolation.py" for scene, _ in calls)


def test_probes_selection_filters_configured_blocks(tmp_path, monkeypatch):
    calls = []

    def fake_probe(params, *, probes_dir, scene, output, base_url, timeout_s, redact_values=None):
        calls.append(scene)
        return {"status": "PASS"}, {"status": "completed"}

    monkeypatch.setattr(probes_module, "run_configured_probe", fake_probe)
    tenants_path = _tenants(tmp_path)
    profile = {
        "tenant_config": str(tenants_path),
        "six_metrics_observation": True,
        "capability_probe": {"enabled": True},
        "missing_cases": {"enabled": True},
        "invalid_input": {"enabled": True},
    }
    artifacts, commands = _run(profile, {"tenants": []}, tmp_path / "suite",
                               probes=["capability"])
    assert calls == ["capability.py"]
    # 未选中且非指标必需的探针不产生命令记录（只有 capability 的执行命令）。
    assert [command["status"] for command in commands] == ["completed"]


def test_probes_selection_keeps_metric_required_fault_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOMEM_TEST_CONTROL_TOKEN", "unit-token")
    calls = []

    def fake_probe(params, *, probes_dir, scene, output, base_url, timeout_s, redact_values=None):
        calls.append(scene)
        return {"status": "PASS"}, {"status": "completed"}

    monkeypatch.setattr(probes_module, "run_configured_probe", fake_probe)
    tenants_path = _tenants(tmp_path)
    profile = {
        "tenant_config": str(tenants_path),
        "six_metrics_observation": True,
        "fairness_expectations": {"tenant_ids": ["t0", "t1"]},
        "fault_isolation": {"enabled": True, "queries": {"t0": {"id": "q", "query": "q"}}},
    }
    artifacts, commands = _run(profile, {"tenants": []}, tmp_path / "suite",
                               probes=["capability"])
    assert "fault_isolation.py" in calls
    assert artifacts["fault_isolation"]["expected_cases"] > 0


def test_probes_selection_skips_unselected_sweep_and_blackbox_commands(tmp_path, monkeypatch):
    calls = []

    def fake_probe(params, *, probes_dir, scene, output, base_url, timeout_s, redact_values=None):
        calls.append(scene)
        return {"status": "PASS"}, {"status": "completed"}

    monkeypatch.setattr(probes_module, "run_configured_probe", fake_probe)
    tenants_path = _tenants(tmp_path)
    profile = {"tenant_config": str(tenants_path), "six_metrics_observation": True}
    artifacts, commands = _run(profile, {"tenants": []}, tmp_path / "suite",
                               probes=["capability"])
    # 未选中的限流阶梯与黑盒契约：不产生 INCONCLUSIVE 命令记录，也不运行。
    assert calls == ["capability.py"]
    assert all(command["status"] == "completed" for command in commands)
    assert "limit_failure_sweep" not in artifacts


def test_probes_selection_runs_selected_without_config_using_defaults(tmp_path, monkeypatch):
    calls = []

    def fake_probe(params, *, probes_dir, scene, output, base_url, timeout_s, redact_values=None):
        calls.append((scene, dict(params)))
        return {"status": "PASS"}, {"status": "completed"}

    monkeypatch.setattr(probes_module, "run_configured_probe", fake_probe)
    tenants_path = _tenants(tmp_path)
    profile = {"tenant_config": str(tenants_path), "six_metrics_observation": True}
    artifacts, commands = _run(profile, {"tenants": []}, tmp_path / "suite",
                               probes=["capability"])
    scene, params = calls[0]
    assert scene == "capability.py"
    assert "session_id" not in params  # 无配置段 → 探针自身默认参数


# -- 移植探针接线 ------------------------------------------------------------


def test_nxn_isolation_wired_with_tenant_config(tmp_path, monkeypatch):
    calls = []

    def fake_probe(params, *, probes_dir, scene, output, base_url, timeout_s, redact_values=None):
        calls.append((scene, dict(params)))
        return {"status": "PASS"}, {"status": "completed"}

    monkeypatch.setattr(probes_module, "run_configured_probe", fake_probe)
    tenants_path = _tenants(tmp_path)
    profile = {"tenant_config": str(tenants_path), "six_metrics_observation": True,
               "nxn_isolation": {"markers_per_tenant": 3}}
    artifacts, commands = _run(profile, {"tenants": []}, tmp_path / "suite",
                               probes=["nxn-isolation"])
    assert calls[0][0] == "nxn_isolation.py"
    assert calls[0][1]["tenant_config"] == str(tenants_path)
    assert calls[0][1]["markers_per_tenant"] == 3
    assert artifacts["nxn_isolation"]["status"] == "PASS"


def test_disconnect_recovery_wired_with_first_tenant_and_pid(tmp_path, monkeypatch):
    calls = []

    def fake_probe(params, *, probes_dir, scene, output, base_url, timeout_s, redact_values=None):
        calls.append((scene, dict(params)))
        return {"status": "PASS"}, {"status": "completed"}

    monkeypatch.setattr(probes_module, "run_configured_probe", fake_probe)
    tenants_path = _tenants(tmp_path)
    profile = {
        "tenant_config": str(tenants_path),
        "six_metrics_observation": True,
        "resource_evidence": {"process_id": 4242},
        "disconnect_recovery": {"requests": 3},
    }
    artifacts, commands = _run(profile, {"tenants": [{"tenant_id": "t0"}]},
                               tmp_path / "suite", probes=["disconnect-recovery"])
    scene, params = calls[0]
    assert scene == "disconnect_recovery.py"
    assert params["tenant"] == "t0"
    assert params["pid"] == 4242
    assert params["requests"] == 3
    assert artifacts["disconnect_recovery"]["status"] == "PASS"

"""Local-process degradation: no Docker, no protected observation APIs.

Tests the two-tier readiness gate and the observation-run plumbing that
turns missing optional evidence into honest INCONCLUSIVE/BLOCKED instead of
aborting the whole run.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from performance.targets.echomem.acceptance.readiness import check_readiness
from performance.targets.echomem.observation_run import (
    _configure,
    _validate_stage_observability_config,
)
from performance.targets.echomem.orchestrator.probes import run_configured_probes


def _profile(tmp_path: Path) -> dict:
    tenants = tmp_path / "tenants.json"
    tenants.write_text(json.dumps({"tenants": [
        {"tenant_id": f"t{i}", "auth_key_env": "DEGRADE_AUTH_%d" % i}
        for i in range(8)
    ]}))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"runtime": {"log_level": "INFO"},
                                  "logging": {"format": "console"}}))
    return {
        "name": "Local", "base_url": "http://localhost:8010",
        "resource_container": "", "require_4u8g": False,
        "tenant_config": str(tenants), "preflight_config": str(config),
    }


# -- two-tier readiness -------------------------------------------------- #


def test_readiness_strict_true_still_blocks_optional_gates(tmp_path, monkeypatch):
    monkeypatch.delenv("ECHOMEM_TEST_CONTROL_TOKEN", raising=False)
    profile = {**_profile(tmp_path),
               "fault_isolation": {"enabled": True},
               "tenant_observability": {"enabled": True}}
    with patch("performance.targets.echomem.acceptance.readiness._get",
               return_value=(200, {"ready": True})):
        result = check_readiness(profile)
    assert result["ok"] is False  # fault/tenant gates missing → strict blocks
    assert "degraded" not in result


def test_readiness_strict_false_degrades_optional_gates(tmp_path, monkeypatch):
    monkeypatch.delenv("ECHOMEM_TEST_CONTROL_TOKEN", raising=False)
    profile = {**_profile(tmp_path),
               "fault_isolation": {"enabled": True},
               "tenant_observability": {"enabled": True}}
    # ready → 200, metrics → 404; fault/tenant 无 token 直接 BLOCKED。
    with patch("performance.targets.echomem.acceptance.readiness._get",
               side_effect=[(200, {"ready": True}), (404, {})]):
        result = check_readiness(profile, strict=False)
    assert result["ok"] is True  # hard gates (target-url, ready) pass
    assert set(result["degraded"]) == {"fault_isolation", "tenant_observability", "metrics"}


def test_readiness_strict_false_blocks_on_bad_url(tmp_path, monkeypatch):
    profile = {**_profile(tmp_path), "base_url": "ftp://bad"}
    with patch("performance.targets.echomem.acceptance.readiness._get",
               return_value=(200, {"ready": True})):
        result = check_readiness(profile, strict=False)
    assert result["ok"] is False


# -- stage observability gate -------------------------------------------- #


def test_stage_observability_allows_missing_container(tmp_path):
    # INFO/console logs are fine when there is no container to collect logs from.
    profile = {**_profile(tmp_path), "require_stage_observability": True}
    _validate_stage_observability_config(profile, ["M1", "M2", "M3"])


def test_stage_observability_still_requires_debug_json_with_container(tmp_path):
    profile = {**_profile(tmp_path), "require_stage_observability": True,
               "resource_container": "echomem"}
    with pytest.raises(ValueError, match="runtime.log_level=DEBUG"):
        _validate_stage_observability_config(profile, ["M1"])


# -- _configure on a fully degraded local profile ------------------------ #


def test_configure_accepts_local_process_without_optional_evidence(
    tmp_path, monkeypatch
):
    for index in range(8):
        monkeypatch.setenv("DEGRADE_AUTH_%d" % index, "unit-only-%d" % index)
    profile = _profile(tmp_path)
    readiness = {
        "ok": True,
        "resource_evidence": {"container": "", "container_id": None,
                              "image_id": None, "cpus": 0, "memory_bytes": 0,
                              "running": None, "resource_policy": "host-default"},
        "checks": [], "degraded": ["fault_isolation", "tenant_observability"],
    }
    with patch("performance.targets.echomem.observation_run.check_readiness",
               return_value=readiness), \
         patch("performance.targets.echomem.observation_run.run_preflight",
               return_value={"ok": True, "engines": [
                   {"kind": "llm", "model": "unit-llm", "status": "ok"},
                   {"kind": "embedding", "model": "unit-emb", "status": "ok"},
               ]}):
        result = _configure(profile, ["M1", "M2", "M3"], quick=True)
    assert result["fault_isolation"]["enabled"] is False
    assert result["tenant_observability"]["enabled"] is False
    assert result["commit_recovery"] is None  # M5/M6 not selected
    assert result["resource_evidence"]["resource_policy"] == "host-default"


def test_configure_m5_without_restart_control_does_not_require_container(
    tmp_path, monkeypatch
):
    for index in range(8):
        monkeypatch.setenv("DEGRADE_AUTH_%d" % index, "unit-only-%d" % index)
    profile = _profile(tmp_path)
    readiness = {
        "ok": True,
        "resource_evidence": {"container": "", "container_id": None,
                              "image_id": None, "cpus": 0, "memory_bytes": 0,
                              "running": None, "resource_policy": "host-default"},
        "checks": [], "degraded": [],
    }
    with patch("performance.targets.echomem.observation_run.check_readiness",
               return_value=readiness), \
         patch("performance.targets.echomem.observation_run.run_preflight",
               return_value={"ok": True, "engines": [
                   {"kind": "llm", "model": "unit-llm", "status": "ok"},
                   {"kind": "embedding", "model": "unit-emb", "status": "ok"},
               ]}):
        result = _configure(profile, ["M5"], quick=True)
    # No container/pid → no allow_container_restart demand; the probe itself
    # will record INCONCLUSIVE ("no container or pid configured").
    assert result["commit_recovery"]["enabled"] is True
    assert result["commit_recovery"].get("allow_container_restart") is not True


def test_configure_m5_with_container_still_requires_restart_opt_in(tmp_path, monkeypatch):
    for index in range(8):
        monkeypatch.setenv("DEGRADE_AUTH_%d" % index, "unit-only-%d" % index)
    profile = {**_profile(tmp_path), "resource_container": "dedicated-test"}
    readiness = {
        "ok": True,
        "resource_evidence": {"container": "dedicated-test",
                              "container_id": "id", "image_id": "img",
                              "cpus": 4, "memory_bytes": 8 * 1024**3,
                              "running": True, "resource_policy": "fixed-4u8g"},
        "checks": [], "degraded": [],
    }
    with patch("performance.targets.echomem.observation_run.check_readiness",
               return_value=readiness), \
         patch("performance.targets.echomem.observation_run.run_preflight",
               return_value={"ok": True, "engines": [
                   {"kind": "llm", "model": "unit-llm", "status": "ok"},
                   {"kind": "embedding", "model": "unit-emb", "status": "ok"},
               ]}):
        with pytest.raises(ValueError, match="allow_container_restart"):
            _configure(profile, ["M5"], quick=True)


# -- probe orchestration on degraded fault plane -------------------------- #


def test_probes_record_inconclusive_when_fault_plane_degraded(tmp_path, monkeypatch):
    tenants = tmp_path / "tenants.json"
    tenants.write_text(json.dumps({"tenants": [
        {"tenant_id": "stress-a", "auth_key": "unit-a"},
        {"tenant_id": "stress-b", "auth_key": "unit-b"},
        {"tenant_id": "stress-c", "auth_key": "unit-c"},
        {"tenant_id": "stress-d", "auth_key": "unit-d"},
    ]}))
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    profile = {
        "name": "Local", "base_url": "http://localhost:8010",
        "tenant_config": str(tenants), "six_metrics_observation": True,
        "readiness": {"ok": True, "degraded": ["fault_isolation"]},
        "fault_isolation": {"enabled": False},
        "tenant_observability": {"enabled": False},
    }
    artifacts, commands = run_configured_probes(
        profile, base_url="http://localhost:8010", suite_dir=suite_dir,
        auth_headers={},
        tenant_config=json.loads(tenants.read_text(encoding="utf-8")),
        quick=True,
    )
    assert "fault_isolation" not in artifacts
    assert any(
        c["status"] == "INCONCLUSIVE" and "Fault control plane unavailable" in c["reason"]
        for c in commands
    )
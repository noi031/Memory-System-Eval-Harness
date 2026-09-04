"""Probe orchestration (probes.py) unit tests.

Runs the probe subprocesses against the in-process mock EchoMem server;
probe scenes live under ``performance/targets/echomem/probes``.
"""

from __future__ import annotations

import json
from pathlib import Path

from performance.targets.echomem.orchestrator.probes import (
    _resolve_auth_key,
    _resolve_tenant_id,
    run_configured_probes,
)


def _tenant_config(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _tenants(tmp_path) -> Path:
    path = tmp_path / "tenants.json"
    path.write_text(
        json.dumps({"tenants": [{"tenant_id": "t1", "auth_key": "k1"}]}),
        encoding="utf-8",
    )
    return path


def _probe_scenes(commands) -> list[str]:
    """commands 里实际运行过的探针场景文件名。"""
    scenes = []
    for entry in commands:
        argv = entry.get("command")
        if not isinstance(argv, list) or "--scene" not in argv:
            continue
        scenes.append(Path(argv[argv.index("--scene") + 1]).name)
    return scenes


# -- 凭据/租户解析（dict 形态） ----------------------------------------


def test_resolve_auth_key_from_dict():
    config = {"tenants": [{"tenant_id": "t1", "auth_key": "k1"}]}
    assert _resolve_auth_key(config, "") == ("k1", "")
    assert _resolve_auth_key(config, "0") == ("k1", "")
    assert _resolve_auth_key(config, "t1") == ("k1", "")
    assert _resolve_auth_key(config, "9") == ("", "")
    assert _resolve_auth_key(config, "t2") == ("", "")
    assert _resolve_auth_key({}, "0") == ("", "")


def test_resolve_auth_key_env_fallback():
    config = {
        "tenants": [{"tenant_id": "t1", "auth_key_env": "TEST_ECHOMEM_AUTH_KEY"}]
    }
    assert _resolve_auth_key(config, "")[1] == "TEST_ECHOMEM_AUTH_KEY"


def test_resolve_tenant_id_from_dict():
    config = {"tenants": [{"tenant_id": "t1"}, {"id": "t2"}]}
    assert _resolve_tenant_id(config, "t2") == "t2"
    assert _resolve_tenant_id(config, "missing") == "t1"
    assert _resolve_tenant_id({}, "x") == "x"


# -- capability 探针 -----------------------------------------------------


def test_capability_probe_runs_against_mock(server, tmp_path):
    _, _, base_url = server
    tenants_path = _tenants(tmp_path)
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    profile = {
        "name": "p",
        "base_url": base_url,
        "tenant_config": str(tenants_path),
        "capability_probe": {"health_path": "/health", "metrics_path": "/metrics"},
    }
    artifacts, commands = run_configured_probes(
        profile,
        base_url=base_url,
        suite_dir=suite_dir,
        auth_headers={},
        tenant_config=_tenant_config(tenants_path),
        quick=False,
    )
    payload = artifacts["capability_probe"]
    assert payload["path"] == str(suite_dir / "capability-probe.json")
    assert (suite_dir / "capability-probe.json").is_file()
    assert payload["probe"] == "capability"
    assert payload["status"] == "INCONCLUSIVE"  # /metrics NOT_IMPLEMENTED + 未配置路径
    runs = [c for c in commands if isinstance(c.get("command"), list)]
    assert len(runs) == 1
    argv = runs[0]["command"]
    assert Path(argv[argv.index("--scene") + 1]).name == "capability.py"
    # INCONCLUSIVE 由 payload 保留，不被子进程失败掩盖
    assert runs[0]["status"] == "INCONCLUSIVE"
    # blackbox 缺 commit 证据 + sweep 未配置 → 两条 INCONCLUSIVE 标记
    markers = [c for c in commands if c.get("status") == "INCONCLUSIVE" and "command" not in c]
    assert len(markers) == 2


def test_unconfigured_probes_not_run(server, tmp_path):
    _, _, base_url = server
    tenants_path = _tenants(tmp_path)
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    profile = {"name": "p", "base_url": base_url, "tenant_config": str(tenants_path)}
    artifacts, commands = run_configured_probes(
        profile,
        base_url=base_url,
        suite_dir=suite_dir,
        auth_headers={},
        tenant_config=_tenant_config(tenants_path),
        quick=False,
    )
    assert artifacts == {}
    assert _probe_scenes(commands) == []
    for key in (
        "capability_probe", "blackbox_contract_probe", "missing_cases",
        "concurrent_commit", "fault_isolation", "limit_failure_sweep",
        "commit_recovery", "fault_suite",
    ):
        assert key not in artifacts
    # blackbox 无 commit 证据 + limit_failure_sweep 未配置 → INCONCLUSIVE 标记
    assert [c["status"] for c in commands] == ["INCONCLUSIVE", "INCONCLUSIVE"]


def test_missing_precondition_inconclusive(server, tmp_path):
    _, _, base_url = server
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    profile = {"name": "p", "base_url": base_url}
    artifacts, commands = run_configured_probes(
        profile,
        base_url=base_url,
        suite_dir=suite_dir,
        auth_headers={},
        tenant_config={},
        quick=False,
    )
    # 无 tenant 配置 + 无已完成 Commit → blackbox 记 INCONCLUSIVE，无制品
    assert "blackbox_contract_probe" not in artifacts
    assert any(
        c.get("status") == "INCONCLUSIVE" and "commit" in c.get("reason", "").lower()
        for c in commands
    )


# -- fault_plan ${BASE_URL} 替换 -----------------------------------------


def test_fault_plan_base_url_replacement(server, tmp_path):
    _, _, base_url = server
    tenants_path = _tenants(tmp_path)
    plan_path = tmp_path / "fault-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "faults": [
                    {
                        "kind": "llm-500",
                        "endpoint": "${BASE_URL}/fault/llm-500",
                        "action": "enable",
                        "timeout_s": 1,
                    }
                ],
                "recovery": {
                    "health_url": "${BASE_URL}/health",
                    "container": "c",
                    "wait_s": 0,
                    "poll_s": 1,
                },
                "cursor": {
                    "uri_template": "echo://sessions/{session}/current/commit_cursor.json"
                },
            }
        ),
        encoding="utf-8",
    )
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    profile = {
        "name": "p",
        "base_url": base_url,
        "tenant_config": str(tenants_path),
        "fault_plan": str(plan_path),
    }
    artifacts, commands = run_configured_probes(
        profile,
        base_url=base_url,
        suite_dir=suite_dir,
        auth_headers={},
        tenant_config=_tenant_config(tenants_path),
        quick=True,
    )
    resolved = suite_dir / "fault-plan.resolved.json"
    assert resolved.is_file()
    text = resolved.read_text(encoding="utf-8")
    assert "${BASE_URL}" not in text
    data = json.loads(text)
    assert data["faults"][0]["endpoint"] == f"{base_url}/fault/llm-500"
    assert data["recovery"]["health_url"] == f"{base_url}/health"
    # fault_suite 探针已运行并产出制品
    assert "fault_suite" in artifacts
    assert artifacts["fault_suite"]["path"] == str(
        suite_dir / "fault-suite" / "fault-suite.json"
    )
    assert _probe_scenes(commands) == ["fault_suite.py"]

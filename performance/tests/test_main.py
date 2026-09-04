"""Orchestrator CLI (main.py) unit tests."""

from __future__ import annotations

import json
import sys

import pytest

from performance.targets.echomem.orchestrator.main import (
    acquire_output_lock,
    load_env_file,
    load_profiles,
    main,
    run_command,
)

# -- load_profiles -------------------------------------------------------


def test_load_profiles_dict_wrapper(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps({"profiles": [{"name": "a"}, {"name": "b"}]}), encoding="utf-8"
    )
    assert [item["name"] for item in load_profiles(path)] == ["a", "b"]


def test_load_profiles_bare_list_filters(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps([{"name": "a"}, "junk", {}]), encoding="utf-8")
    assert [item["name"] for item in load_profiles(path)] == ["a"]


def test_load_profiles_empty_raises(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"profiles": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_profiles(path)


# -- load_env_file -------------------------------------------------------


def test_load_env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n"
        "FOO=bar\n"
        "export BAZ='qux quux'\n"
        "QUOTED=\"hello world\"\n"
        "EMPTY=\n"
        "NOT_A_VAR\n"
        "BAD-KEY=1\n",
        encoding="utf-8",
    )
    assert load_env_file(path) == {
        "FOO": "bar",
        "BAZ": "qux quux",
        "QUOTED": "hello world",
        "EMPTY": "",
    }


# -- acquire_output_lock -------------------------------------------------


def test_acquire_output_lock(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    first = acquire_output_lock(out)
    try:
        with pytest.raises(RuntimeError, match="already locked"):
            acquire_output_lock(out)
    finally:
        first.close()
    # 释放后可再获取
    second = acquire_output_lock(out)
    second.close()


# -- run_command ---------------------------------------------------------


def test_run_command_success():
    result = run_command([sys.executable, "-c", "print('hello')"], timeout_s=10)
    assert result["status"] == "PASS"
    assert result["returncode"] == 0
    assert "hello" in result["stdout"]


def test_run_command_nonzero_exit():
    result = run_command(
        [sys.executable, "-c", "import sys; sys.exit(3)"], timeout_s=10
    )
    assert result["status"] == "FAIL"
    assert result["returncode"] == 3


def test_run_command_timeout():
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=1
    )
    assert result["status"] == "TIMEOUT"
    assert result["returncode"] == 124


def test_run_command_redact():
    result = run_command(
        [sys.executable, "-c", "print('x')", "top-secret"],
        timeout_s=10,
        redact_values={"top-secret"},
    )
    assert result["status"] == "PASS"
    assert "top-secret" not in result["command"]
    assert "***configured***" in result["command"]


# -- 端到端 quick mock ---------------------------------------------------


def test_main_quick_mock(mock_server, tmp_path):
    _, _, base_url = mock_server()
    tenants_path = tmp_path / "tenants.json"
    tenants_path.write_text(
        json.dumps({"tenants": [{"tenant_id": "t1", "auth_key": "k1"}]}),
        encoding="utf-8",
    )
    profiles_path = tmp_path / "instance-profiles.json"
    profiles_path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "name": "4U8G",
                        "base_url": base_url,
                        "tenant_config": str(tenants_path),
                        "capability_probe": {
                            "health_path": "/health",
                            "metrics_path": "/metrics",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    rc = main(
        [
            "--profiles", str(profiles_path),
            "--profile", "4U8G",
            "--out-dir", str(out_dir),
            "--quick",
            "--scenarios", "baseline",
            "--quick-duration-cap-s", "1.5",
            "--quick-case-timeout-s", "30",
            "--timeout-s", "60",
        ]
    )
    assert rc == 0

    suite = json.loads((out_dir / "objective-suite.json").read_text(encoding="utf-8"))
    assert "profiles" in suite
    assert "objectives" in suite
    assert "instance_profiles" in suite
    assert "multi_spec_completed_count" in suite
    profile = suite["profiles"][0]
    assert profile["name"] == "4U8G"
    assert profile["profile_execution_status"] == "completed"
    assert profile["completed_runs"] == 1
    assert [objective["id"] for objective in profile["objectives"]] == [
        "O1", "O2", "O3", "O4", "O5", "O6", "O7",
    ]
    assert profile["capability_probe"]["path"] == str(
        out_dir / "4U8G" / "capability-probe.json"
    )

    html = (out_dir / "objective-suite.html").read_text(encoding="utf-8")
    for objective_id in ("O1", "O2", "O3", "O4", "O5", "O6", "O7"):
        assert objective_id in html
    assert "EchoMem 七项目标自动化验收" in html

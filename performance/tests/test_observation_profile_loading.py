"""Profile loading and resolution for the observation entry point.

``load_profiles`` reads the instance-profiles JSON in either shape and
filters unnamed entries; ``_resolve_profile`` expands ``${ENV:-default}``
placeholders and resolves file paths relative to the profiles-file directory.
Both were moved verbatim from the removed objective-suite entry.
"""

from __future__ import annotations

import json

import pytest

from performance.targets.echomem.observation_run import _resolve_profile, load_profiles


def _write(tmp_path, payload):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_profiles_accepts_object_and_bare_list(tmp_path):
    profiles = [{"name": "a", "base_url": "x"}, {"name": "b", "base_url": "y"}]
    assert load_profiles(_write(tmp_path, {"profiles": profiles})) == profiles
    assert load_profiles(_write(tmp_path, profiles)) == profiles


def test_load_profiles_filters_unnamed_entries(tmp_path):
    path = _write(tmp_path, {"profiles": [{"name": "a"}, {"no_name": 1}]})
    assert load_profiles(path) == [{"name": "a"}]


def test_load_profiles_rejects_empty_or_invalid_payload(tmp_path):
    with pytest.raises(ValueError, match="non-empty profiles list"):
        load_profiles(_write(tmp_path, {"profiles": []}))
    with pytest.raises(ValueError, match="non-empty profiles list"):
        load_profiles(_write(tmp_path, "not-a-list"))


def test_resolve_profile_expands_env_and_relative_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOMEM_UNIT_BASE_URL", "http://unit:8010")
    profile = {
        "name": "unit",
        "base_url": "${ECHOMEM_UNIT_BASE_URL}",
        "tenant_config": "tenants.json",
        "preflight_config": "preflight.json",
        "fault_plan": "plan.json",
    }
    profiles_path = tmp_path / "profiles.json"
    resolved = _resolve_profile(profile, profiles_path)
    assert resolved["base_url"] == "http://unit:8010"
    assert resolved["tenant_config"] == str((tmp_path / "tenants.json").resolve())
    assert resolved["preflight_config"] == str((tmp_path / "preflight.json").resolve())
    assert resolved["fault_plan"] == str((tmp_path / "plan.json").resolve())


def test_resolve_profile_env_default_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("ECHOMEM_MISSING_VAR", raising=False)
    profile = {"name": "unit", "base_url": "${ECHOMEM_MISSING_VAR:-http://fallback:8010}"}
    resolved = _resolve_profile(profile, tmp_path / "profiles.json")
    assert resolved["base_url"] == "http://fallback:8010"


def test_resolve_profile_empty_paths_stay_empty(tmp_path):
    profile = {"name": "unit", "tenant_config": "", "fault_plan": None}
    resolved = _resolve_profile(profile, tmp_path / "profiles.json")
    assert resolved["tenant_config"] == ""
    assert resolved["fault_plan"] == ""

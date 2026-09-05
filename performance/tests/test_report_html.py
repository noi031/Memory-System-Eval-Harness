"""Acceptance HTML report: rendering helpers, row normalization, grouping.

Pure data/render tests — no mock server needed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from performance.targets.echomem.acceptance.report_html import (
    OK_STATES,
    esc,
    float_value,
    group_runs,
    int_value,
    normalize_row,
    number,
    parse_timestamp,
    percent,
    percentile,
    policy_label,
    render,
    scenario_label,
    seconds,
    stats,
    status_badge,
    tenant_groups,
    timestamp_delta,
)
from performance.util import read_csv

# -- math helpers ----------------------------------------------------------


def test_percentile():
    assert percentile([], 50) is None
    assert percentile([1, 2, 3, 4, 5], 50) == 3.0
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([1, 2, 3, 4], 25) == pytest.approx(1.75)
    assert percentile([1, 2, 3, 4, 5], 0) == 1.0
    assert percentile([1, 2, 3, 4, 5], 100) == 5.0


def test_stats():
    s = stats([1, 2, 3])
    assert s["count"] == 3
    assert s["mean"] == 2.0
    assert s["min"] == 1.0
    assert s["p50"] == 2.0
    assert s["p90"] == pytest.approx(2.8)
    assert s["p95"] == pytest.approx(2.9)
    assert s["p99"] == pytest.approx(2.98)
    assert s["max"] == 3.0
    assert s["total"] == 6.0
    empty = stats([])
    assert empty["count"] == 0
    assert empty["total"] == 0.0
    assert all(v is None for k, v in empty.items() if k not in {"count", "total"})


# -- csv / value helpers ---------------------------------------------------


def test_read_csv(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    assert read_csv(path) == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
    assert read_csv(tmp_path / "missing.csv") == []


def test_float_and_int_value():
    assert float_value({"x": "3.5"}, "x") == 3.5
    assert float_value({"x": "abc"}, "x") is None
    assert float_value({"x": "-1"}, "x") is None  # negative rejected
    assert float_value({}, "x", "y") is None
    assert float_value({"x": "", "y": "2.0"}, "x", "y") == 2.0
    assert int_value({"x": "3.7"}, "x") == 3
    assert int_value({"x": "-2"}, "x") is None


def test_timestamp_helpers():
    parsed = parse_timestamp("2026-01-01T00:00:00Z")
    assert parsed is not None and parsed.tzinfo is not None
    assert parse_timestamp("") is None
    assert parse_timestamp("not-a-date") is None
    row = {"start": "2026-01-01T00:00:00+00:00", "end": "2026-01-01T00:00:02+00:00"}
    assert timestamp_delta(row, "start", "end") == 2.0
    reversed_row = {"start": "2026-01-01T00:00:02+00:00", "end": "2026-01-01T00:00:00+00:00"}
    assert timestamp_delta(reversed_row, "start", "end") is None  # negative rejected
    assert timestamp_delta({}, "start", "end") is None


def test_rendering_helpers():
    assert esc(None) == "-"
    assert esc("") == "-"
    assert esc("<b>") == "&lt;b&gt;"
    assert number(1.5, 3) == "1.500"
    assert number(None) == "-"
    assert seconds(1.5) == "1.500s"
    assert seconds(None) == "-"
    assert percent(0.5) == "50.00%"
    assert percent("x") == "-"


# -- labels ----------------------------------------------------------------


def test_policy_label():
    assert policy_label("server-observe") == "服务端观测（客户端调度关闭）"
    assert policy_label("fifo") == "FIFO"
    assert policy_label("search-priority") == "Search 优先"
    assert policy_label("dual-lane") == "双通道"
    assert policy_label("tenant-fair") == "租户公平"
    assert policy_label("dual-lane-tenant-fair") == "双通道 + 租户公平"
    assert policy_label("unknown-policy") == "unknown-policy"


def test_scenario_label():
    assert scenario_label("baseline") == "单租户基线"
    assert scenario_label("A@1") == "A 纯读基线 / 每租户并发 1"
    assert scenario_label("D@2") == "D 注入洪峰 / 每租户并发 2"
    assert scenario_label("unknown-scenario") == "unknown-scenario"


def test_status_badge():
    assert status_badge("PASS") == "<span class='badge pass'>PASS</span>"
    assert status_badge("INCONCLUSIVE") == "<span class='badge inconclusive'>INCONCLUSIVE</span>"
    assert status_badge("FAIL") == "<span class='badge fail'>FAIL</span>"
    assert status_badge("NOT_IMPLEMENTED") == "<span class='badge not-implemented'>NOT_IMPLEMENTED</span>"
    # underscores are normalized to hyphens for the CSS class
    assert status_badge("ENVIRONMENT_ERROR") == "<span class='badge environment-error'>ENVIRONMENT_ERROR</span>"
    assert status_badge(None) == "<span class='badge unknown'>-</span>"


def test_ok_states_contract():
    assert OK_STATES == {"completed", "complete", "transcommit", "succeeded", "success"}


# -- normalize_row ---------------------------------------------------------


def test_normalize_commit_branch():
    summary = {"parameters": {"commit_delay_threshold_s": 10.0}}
    n = normalize_row(
        {"tenant": "t1", "status": "completed", "status_code": "200", "end_to_end_s": "11.0"},
        "commit",
        summary,
    )
    assert n["operation"] == "commit"
    assert n["tenant"] == "t1"
    assert n["successful"] is True
    assert n["duration"] == 11.0
    assert n["delayed"] is True
    assert n["threshold"] == 10.0


def test_normalize_commit_default_threshold_and_failure():
    # default threshold 10.0, boundary inclusive
    assert normalize_row({"status": "completed", "end_to_end_s": "10.0"}, "commit", {})["delayed"] is True
    assert normalize_row({"status": "completed", "end_to_end_s": "9.9"}, "commit", {})["delayed"] is False
    n = normalize_row({"status": "pending", "end_to_end_s": "5.0"}, "commit", {"parameters": {}})
    assert n["successful"] is False
    assert n["status"] == "pending"


def test_normalize_search_branch():
    summary = {"parameters": {"search_delay_threshold_s": 2.5}}
    n = normalize_row({"status_code": "200", "service_s": "3.0"}, "search", summary)
    assert n["operation"] == "search"
    assert n["successful"] is True
    assert n["duration"] == 3.0
    assert n["delayed"] is True
    assert n["threshold"] == 2.5
    assert n["status"] == "200"
    assert n["status_code"] == 200


def test_normalize_search_threshold_and_failure():
    summary = {"parameters": {"search_delay_threshold_s": 2.5}}
    assert normalize_row({"status_code": "200", "service_s": "2.5"}, "search", summary)["delayed"] is True
    assert normalize_row({"status_code": "200", "service_s": "2.4"}, "search", summary)["delayed"] is False
    n = normalize_row({"status_code": "500", "service_s": "1.0"}, "search", summary)
    assert n["successful"] is False
    n2 = normalize_row({"error": "timeout", "service_s": "1.0"}, "search", summary)
    assert n2["successful"] is False
    assert n2["status"] == "timeout"
    assert n2["status_code"] == "-"


# -- grouping --------------------------------------------------------------


def _commit_row(**overrides) -> dict:
    row = {"tenant": "t1", "status": "completed", "status_code": "200", "end_to_end_s": "1.0"}
    row.update(overrides)
    return normalize_row(row, "commit", {"parameters": {}})


def _search_row(**overrides) -> dict:
    row = {"tenant": "t1", "status_code": "200", "service_s": "0.5"}
    row.update(overrides)
    return normalize_row(row, "search", {"parameters": {}})


def _loaded_run(scenario, policy, commits, searches, *, repetition=1, label=None) -> dict:
    return {
        "scenario": scenario,
        "scenario_label": label or scenario,
        "repetition": repetition,
        "policy": policy,
        "status": "completed",
        "summary": {"parameters": {}},
        "output_dir": Path("."),
        "commits": commits,
        "searches": searches,
    }


def test_group_runs_groups_by_scenario_and_policy():
    runs = [
        _loaded_run("baseline", "fifo", [_commit_row()], [], repetition=1),
        _loaded_run("baseline", "fifo", [_commit_row(tenant="t2")], [], repetition=2),
        _loaded_run("mixed", "tenant-fair", [], [_search_row()]),
    ]
    groups = group_runs(runs)
    assert [g["scenario"] for g in groups] == ["baseline", "mixed"]
    assert groups[0]["policy"] == "fifo"
    assert len(groups[0]["items"]) == 2
    assert len(groups[0]["commits"]) == 2
    assert len(groups[0]["commit_success"]) == 2
    assert groups[0]["tenants"] == ["t1", "t2"]
    assert groups[1]["policy"] == "tenant-fair"
    assert len(groups[1]["searches"]) == 1
    assert len(groups[1]["search_success"]) == 1


def test_tenant_groups_splits_by_tenant():
    group = group_runs([
        _loaded_run("s", "fifo", [_commit_row(tenant="a"), _commit_row(tenant="b")], [_search_row(tenant="a")]),
    ])[0]
    tenants = tenant_groups(group)
    assert [t["tenant"] for t in tenants] == ["a", "b"]
    assert tenants[0]["commit_submitted"] == 1
    assert tenants[0]["commit_completed"] == 1
    assert tenants[0]["search_submitted"] == 1
    assert tenants[1]["commit_submitted"] == 1
    assert tenants[1]["search_submitted"] == 0


# -- render ----------------------------------------------------------------


def _write_run_csvs(tmp_path: Path, name: str, *, commits: list[str], searches: list[str]) -> None:
    out = tmp_path / name
    out.mkdir()
    (out / "commit_results.csv").write_text(
        "tenant,status,end_to_end_s,status_code,queued_at\n" + "".join(commits),
        encoding="utf-8",
    )
    (out / "search_results.csv").write_text(
        "tenant,status_code,service_s,queued_at\n" + "".join(searches),
        encoding="utf-8",
    )


def _run_entry(name, *, status="completed", tenants=1, identity_mode=None) -> dict:
    details = {} if identity_mode is None else {"identity_mode": identity_mode}
    return {
        "scenario": "baseline",
        "scenario_label": "单租户基线",
        "policy": "fifo",
        "repetition": 1,
        "status": status,
        "output_dir": name,
        "summary": {"parameters": {"tenants": tenants}, "details": details},
    }


def _render(tmp_path, manifest: dict) -> str:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "report.html"
    render(manifest_path, output)
    return output.read_text(encoding="utf-8")


def test_render_pass_smoke(tmp_path):
    _write_run_csvs(
        tmp_path,
        "run1",
        commits=["t1,completed,1.0,200,2026-01-01T00:00:00+00:00\n"],
        searches=["t1,200,0.5,2026-01-01T00:00:01+00:00\n"],
    )
    text = _render(tmp_path, {"base_url": "http://target", "repeats": 1, "runs": [_run_entry("run1")]})
    assert "hero pass" in text
    assert "<strong>PASS</strong>" in text
    assert "单租户基线" in text
    assert "不能宣称已对齐 PR421" in text  # no acceptance data


@pytest.mark.parametrize("status", ["FAIL", "ENVIRONMENT_ERROR", "RESET_FAILED", "NO_SUMMARY"])
def test_render_fail_statuses(tmp_path, status):
    _write_run_csvs(tmp_path, "run1", commits=["t1,completed,1.0,200,2026-01-01T00:00:00+00:00\n"], searches=[])
    text = _render(tmp_path, {"runs": [_run_entry("run1", status=status)]})
    assert "hero fail" in text
    assert "<strong>FAIL</strong>" in text


def test_render_fail_wins_over_inconclusive(tmp_path):
    _write_run_csvs(tmp_path, "run1", commits=["t1,completed,1.0,200,2026-01-01T00:00:00+00:00\n"], searches=[])
    _write_run_csvs(tmp_path, "run2", commits=[], searches=["t1,200,0.5,2026-01-01T00:00:01+00:00\n"])
    text = _render(tmp_path, {
        "runs": [_run_entry("run1", status="FAIL"), _run_entry("run2", status="INCONCLUSIVE")],
    })
    assert "<strong>FAIL</strong>" in text


def test_render_inconclusive_over_pass(tmp_path):
    _write_run_csvs(tmp_path, "run1", commits=["t1,completed,1.0,200,2026-01-01T00:00:00+00:00\n"], searches=[])
    _write_run_csvs(tmp_path, "run2", commits=["t1,completed,1.0,200,2026-01-01T00:00:01+00:00\n"], searches=[])
    text = _render(tmp_path, {
        "runs": [_run_entry("run1", status="INCONCLUSIVE"), _run_entry("run2", status="completed")],
    })
    assert "hero inconclusive" in text
    assert "<strong>INCONCLUSIVE</strong>" in text


def test_render_acceptance_matrix_from_manifest(tmp_path):
    _write_run_csvs(tmp_path, "run1", commits=["t1,completed,1.0,200,2026-01-01T00:00:00+00:00\n"], searches=[])
    manifest = {
        "runs": [_run_entry("run1")],
        "acceptance": {
            "overall": "PASS",
            "checks": [
                {"name": "commit-p95", "status": "PASS", "target": "<=1s", "observed": "0.8s",
                 "reason": "", "evidence": "run1/commit_results.csv"},
            ],
            "review": {"reasonable_targets": ["a"], "missing_or_weak_targets": ["b"]},
            "pr28_review_resolution": [{"item": "P95", "status": "PASS", "evidence": "x"}],
        },
    }
    text = _render(tmp_path, manifest)
    assert "commit-p95" in text
    assert "badge pass" in text
    assert "PR28 检视意见闭环" in text
    assert "不能宣称已对齐 PR421" not in text


def test_render_acceptance_matrix_from_file(tmp_path):
    _write_run_csvs(tmp_path, "run1", commits=["t1,completed,1.0,200,2026-01-01T00:00:00+00:00\n"], searches=[])
    (tmp_path / "acceptance.json").write_text(json.dumps({
        "checks": [{"name": "search-p95", "status": "FAIL", "target": "x", "observed": "y"}],
    }), encoding="utf-8")
    text = _render(tmp_path, {"runs": [_run_entry("run1")]})
    assert "search-p95" in text
    assert "不能宣称已对齐 PR421" not in text


def test_render_independent_auth_evidence(tmp_path):
    _write_run_csvs(tmp_path, "run1", commits=["t1,completed,1.0,200,2026-01-01T00:00:00+00:00\n"], searches=[])
    text = _render(tmp_path, {
        "runs": [_run_entry("run1", tenants=2, identity_mode="independent_auth_keys")],
    })
    assert "本套件使用独立认证租户，可用于隔离结论。" in text
    text2 = _render(tmp_path, {"runs": [_run_entry("run1", tenants=2)]})
    assert "未能证明所有运行都使用独立认证租户，隔离结论不可用于上线。" in text2

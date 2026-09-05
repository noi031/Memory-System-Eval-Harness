"""通用套件层（suite.py）单元测试：records 汇总与单 case 执行。"""

from __future__ import annotations

import json

from performance.profile import LoadSpec, Profile, TargetSpec
from performance.records import RequestRecord
from performance.suite import run_case, summarize_case_records


def _record(**overrides):
    fields = {
        "scene": "scene_generic", "worker_id": 0, "tenant_idx": 0, "op": "read",
        "stage_ms": 0.0, "status": "ok", "error_type": "", "ts_ms": 0.0,
    }
    fields.update(overrides)
    return RequestRecord(**fields)


def _write_scene(tmp_path):
    """一个纯记录型通用场景：不依赖任何 target 协议。"""
    scene = tmp_path / "scene_generic.py"
    scene.write_text(
        "def read(ctx):\n"
        "    ctx.record(op='read', stage_ms=10.0, status='ok', query='q')\n"
        "\n"
        "def write(ctx):\n"
        "    ctx.record(op='commit_submit', stage_ms=5.0, status='ok', session_id='s')\n"
        "    ctx.record(op='commit_done', stage_ms=20.0, status='ok', session_id='s')\n"
        "\n"
        "tasks = {'read': read, 'write': write}\n",
        encoding="utf-8",
    )
    return scene


def _profile():
    return Profile(
        name="generic",
        target=TargetSpec(base_url="http://127.0.0.1:8010"),
        load=LoadSpec(workers=2, duration_s=0.5, mix={"read": 1, "write": 1}),
    )


# -- summarize_case_records ----------------------------------------------


def test_summarize_case_records_metrics_only():
    records = [
        _record(tenant_idx=0, op="read", stage_ms=100.0, query="PERFANCHOR-0-0-0"),
        _record(
            tenant_idx=1, op="read", stage_ms=200.0, status="error",
            error_type="http_4xx", retry_after_s=1.0,
        ),
        _record(tenant_idx=0, op="commit_submit", stage_ms=50.0, session_id="s1"),
        _record(tenant_idx=0, op="commit_done", stage_ms=1000.0, session_id="s1"),
    ]
    summary = summarize_case_records(records)
    # 通用层只产 metrics；details/parameters 由 target 侧扩展。
    assert set(summary) == {"metrics"}
    search = summary["metrics"]["search"]
    assert search["submitted"] == 2
    assert search["succeeded"] == 1
    assert search["errors"] == 1
    assert search["success_rate"] == 0.5
    assert search["rate_limited_count"] == 1
    # is_anchor=None → 无法判定锚点，quality_asserted 记 0。
    assert search["quality_asserted"] == 0
    assert search["quality_failures"] == 0
    assert search["latency"]["mean_s"] == 0.1
    assert search["latency"]["p50_s"] == 0.1
    commit = summary["metrics"]["commit"]
    assert commit["submitted"] == 1
    assert commit["completed"] == 1
    assert commit["failed"] == 0
    assert commit["success_rate"] == 1.0
    assert summary["metrics"]["fairness"]["commit_completed_per_tenant"] == {"0": 1}
    assert summary["metrics"]["per_tenant"]["0"]["commit"]["completed"] == 1


def test_summarize_case_records_custom_ops_and_anchor():
    records = [
        _record(op="query", stage_ms=100.0, query="ANCHOR-0"),
        _record(op="query", stage_ms=200.0, query="普通", quality_ok=False),
        _record(
            op="send", stage_ms=50.0, status="error", error_type="http_4xx",
            reason_code="rate_limit", session_id="s1",
        ),
        _record(op="finish", stage_ms=1000.0, session_id="s1"),
    ]
    summary = summarize_case_records(
        records,
        search_op="query",
        commit_submit_op="send",
        commit_done_op="finish",
        is_anchor=lambda q: q.startswith("ANCHOR"),
    )
    search = summary["metrics"]["search"]
    assert search["submitted"] == 2
    assert search["succeeded"] == 2
    assert search["quality_asserted"] == 1
    assert search["quality_failures"] == 1
    commit = summary["metrics"]["commit"]
    assert commit["submitted"] == 1
    assert commit["completed"] == 1
    assert commit["rate_limited_count"] == 1
    assert summary["metrics"]["per_tenant"]["0"]["commit"]["completed"] == 1


def test_summarize_empty_records():
    summary = summarize_case_records([])
    assert summary["metrics"]["search"]["submitted"] == 0
    assert summary["metrics"]["search"]["success_rate"] is None
    assert summary["metrics"]["search"]["latency"]["mean_s"] is None
    assert summary["metrics"]["commit"]["success_rate"] is None
    assert summary["metrics"]["per_tenant"] == {}


# -- run_case ------------------------------------------------------------


def test_run_case_writes_outputs_and_hooks(tmp_path):
    scene = _write_scene(tmp_path)
    case = {"label": "generic-case", "scene": "scene_generic"}
    case_dir = tmp_path / "out"
    evidence_counts: list[int] = []

    def _extended(records):
        summary = summarize_case_records(records)
        summary["details"] = {"custom": True}
        return summary

    run = run_case(
        case,
        _profile(),
        scene_path=scene,
        case_dir=case_dir,
        timeout_s=30.0,
        summarize=_extended,
        write_evidence=lambda out, records: evidence_counts.append(len(records)),
    )
    assert run["status"] == "completed"
    assert run["scenario"] == "generic-case"
    assert run["scene"] == "scene_generic"
    assert run["repetition"] == 1
    assert run["policy"] == "server-observe"
    assert run["runner_timeout"] is False
    assert run["output_dir"] == str(case_dir.resolve())
    for name in ("summary.json", "records.csv"):
        assert (case_dir / name).is_file(), name
    assert evidence_counts, "write_evidence should be invoked"
    summary = json.loads((case_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["details"] == {"custom": True}
    assert summary["metrics"]["search"]["submitted"] > 0
    assert summary["metrics"]["commit"]["completed"] > 0

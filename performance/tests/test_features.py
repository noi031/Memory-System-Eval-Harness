"""特征判定纯函数测试：``acceptance/features.py`` 与 13 特性一一对应。

全部为纯函数测试，用 ``performance.records.RequestRecord`` 构造
合成记录；不依赖 conftest 的 mock 服务器。
"""

from __future__ import annotations

import pytest

from performance.records import RequestRecord
from performance.targets.echomem.acceptance import features as F


def _rec(
    op: str,
    *,
    scene: str = "A@4",
    stage_ms: float = 10.0,
    status: str = "ok",
    error_type: str = "",
    ts_ms: float = 1000.0,
    tenant_idx: int = 0,
    http_status: int | None = None,
    session_id: str = "",
    archive_id: str = "",
    extra: str = "",
    retried: bool = False,
    retry_total_wait_ms: float = 0.0,
    retry_after_s: float | None = None,
    reason_code: str = "",
    content_bytes: int = 0,
    query: str = "",
    hit_count: int = 0,
    real_recall: bool = False,
    quality_ok: bool = True,
    degraded: bool = False,
) -> RequestRecord:
    return RequestRecord(
        scene=scene,
        worker_id=0,
        tenant_idx=tenant_idx,
        op=op,
        stage_ms=stage_ms,
        status=status,
        error_type=error_type,
        ts_ms=ts_ms,
        http_status=http_status,
        session_id=session_id,
        archive_id=archive_id,
        extra=extra,
        retried=retried,
        retry_total_wait_ms=retry_total_wait_ms,
        retry_after_s=retry_after_s,
        reason_code=reason_code,
        content_bytes=content_bytes,
        query=query,
        hit_count=hit_count,
        real_recall=real_recall,
        quality_ok=quality_ok,
        degraded=degraded,
    )


# -- 分位数 ---------------------------------------------------------------


def test_percentile_interpolation():
    assert F.percentile([], 0.5) is None
    assert F.percentile([1.0, 2.0, 3.0, 4.0], 0.0) == 1.0
    assert F.percentile([1.0, 2.0, 3.0, 4.0], 1.0) == 4.0
    assert F.percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert F.percentile([1.0, 2.0, 3.0, 4.0], 0.95) == pytest.approx(3.85)
    assert F.percentile([1.0, 2.0, 3.0, 4.0], 0.99) == pytest.approx(3.97)


def test_percentiles_labels():
    out = F.percentiles([1.0, 2.0, 3.0, 4.0], (0.5, 0.95, 0.99))
    assert out["p50"] == 2.5
    assert out["p95"] == pytest.approx(3.85)
    assert out["p99"] == pytest.approx(3.97)


# -- summarize_records（特性1~4 的统计底座） --------------------------------


def test_summarize_records_groups_scene_x_op():
    records = [
        _rec("read", scene="A@4", stage_ms=10.0),
        _rec("read", scene="A@4", stage_ms=20.0),
        _rec("add", scene="B@4", stage_ms=5.0),
        _rec("add", scene="B@4", stage_ms=5.0),
        _rec("read", scene="B@4", stage_ms=8.0),
    ]
    out = F.summarize_records(records, wall_s=2.0)
    assert set(out) == {"A@4", "B@4"}
    assert out["A@4"]["read"]["count"] == 2
    assert out["A@4"]["read"]["qps"] == 1.0
    assert out["B@4"]["add"]["count"] == 2
    assert out["B@4"]["read"]["count"] == 1


def test_summarize_records_error_classification():
    records = [
        _rec("read", scene="E@4", stage_ms=10.0),
        _rec("read", scene="E@4", stage_ms=11.0, status="error", error_type="timeout"),
        _rec(
            "read", scene="E@4", stage_ms=12.0, status="error",
            error_type="http_4xx", http_status=429,
        ),
        _rec(
            "read", scene="E@4", stage_ms=13.0, status="error",
            error_type="http_5xx", http_status=503,
        ),
        _rec("read", scene="E@4", stage_ms=14.0, status="error", error_type="connection"),
        _rec("read", scene="E@4", stage_ms=15.0, status="error", error_type="other"),
    ]
    out = F.summarize_records(records, wall_s=3.0)["E@4"]["read"]
    assert out["count"] == 6
    assert out["qps"] == 2.0
    assert out["errors_total"] == 5
    assert out["error_rate"] == round(5 / 6, 5)
    assert out["error_breakdown"] == {
        "timeout": 1,
        "http_4xx": 1,
        "http_5xx": 1,
        "connection": 1,
        "other": 1,
    }
    assert out["http_status_breakdown"] == {"429": 1, "503": 1}


def test_summarize_records_percentiles_and_qps():
    records = [_rec("read", scene="A@4", stage_ms=float(v)) for v in (1, 2, 3, 4)]
    out = F.summarize_records(records, wall_s=2.0)["A@4"]["read"]
    assert out["avg_ms"] == 2.5
    assert out["p50_ms"] == 2.5
    assert out["p95_ms"] == pytest.approx(3.85)
    assert out["p99_ms"] == pytest.approx(3.97)
    assert out["max_ms"] == 4.0
    assert out["min_ms"] == 1.0
    assert out["qps"] == 2.0


# -- 特性1: commit_durability ---------------------------------------------


def test_commit_durability_accepted_must_complete():
    records = [
        _rec("commit_submit", scene="B@4", session_id="s1"),
        _rec("commit_done", scene="B@4", session_id="s1"),
        _rec("commit_submit", scene="B@4", session_id="s2"),
        _rec(
            "commit_done", scene="B@4", session_id="s2",
            status="error", error_type="commit_failed",
        ),
        _rec("commit_submit", scene="B@4", session_id="s3"),
        _rec(
            "commit_done", scene="B@4", session_id="s3",
            status="error", error_type="commit_timeout",
        ),
        _rec("commit_submit", scene="B@4", session_id="s4"),  # 接受后从未轮询
    ]
    out = F.commit_durability(records)
    assert out["submit_ok_total"] == 4
    assert out["submit_rejected_total"] == 0
    assert out["accepted_done_ok"] == 1
    assert out["accepted_done_failed"] == 1
    assert out["accepted_done_poll_timeout"] == 1
    assert out["accepted_done_other"] == 1
    assert out["guarantee_violations"] == 2  # done_failed + done_other
    assert out["commit_success_rate"] == 0.25


def test_commit_durability_rejections_and_empty():
    records = [
        _rec(
            "commit_submit", scene="B@4", session_id="s1",
            status="error", error_type="http_4xx", http_status=429,
        ),
    ]
    out = F.commit_durability(records)
    assert out["submit_ok_total"] == 0
    assert out["submit_rejected_total"] == 1
    assert out["submit_rejected_breakdown"] == {"http_4xx": 1}
    assert out["commit_success_rate"] is None
    assert out["guarantee_violations"] == 0

    empty = F.commit_durability([])
    assert empty["submit_ok_total"] == 0
    assert empty["commit_success_rate"] is None
    assert empty["guarantee_violations"] == 0


# -- 特性2: tenant_fairness ------------------------------------------------


def test_tenant_fairness_ratio_and_balanced():
    records = [
        _rec("read", scene="A@4", tenant_idx=0, stage_ms=100.0),
        _rec("read", scene="A@4", tenant_idx=0, stage_ms=100.0),
        _rec("read", scene="A@4", tenant_idx=1, stage_ms=300.0),
        _rec("read", scene="A@4", tenant_idx=1, stage_ms=300.0),
    ]
    out = F.tenant_fairness(records)
    scene = out["A@4"]
    assert scene["p95_max_min_ratio"] == 3.0
    assert scene["balanced"] is False  # 3.0 < FAIRNESS_MAX_MIN_RATIO 为 False
    assert len(scene["tenants"]) == 2

    records = [
        _rec("read", scene="B@4", tenant_idx=0, stage_ms=100.0),
        _rec("read", scene="B@4", tenant_idx=1, stage_ms=120.0),
    ]
    out = F.tenant_fairness(records)
    assert out["B@4"]["p95_max_min_ratio"] == 1.2
    assert out["B@4"]["balanced"] is True


def test_tenant_fairness_single_tenant():
    records = [_rec("read", scene="C@4", tenant_idx=0, stage_ms=100.0)]
    out = F.tenant_fairness(records)
    assert out["C@4"]["p95_max_min_ratio"] is None
    assert out["C@4"]["balanced"] is True
    assert F.tenant_fairness([]) == {}


# -- 特性3: rss_trend_mb_per_min -------------------------------------------


def test_rss_trend_requires_4_samples():
    out = F.rss_trend_mb_per_min([])
    assert out == {"slope_mb_per_min": None, "r2": None, "samples": 0}
    mb = 1024 * 1024
    three = F.rss_trend_mb_per_min([(0.0, 100 * mb)] * 3)
    assert three["samples"] == 3 and three["slope_mb_per_min"] is None
    flat_ts = F.rss_trend_mb_per_min([(1.0, 100 * mb)] * 4)
    assert flat_ts["samples"] == 4 and flat_ts["slope_mb_per_min"] is None


def test_rss_trend_slope_mb_per_min():
    mb = 1024 * 1024
    series = [(0.0, 100 * mb), (60.0, 110 * mb), (120.0, 120 * mb), (180.0, 130 * mb)]
    out = F.rss_trend_mb_per_min(series)
    assert out["samples"] == 4
    assert out["slope_mb_per_min"] == 10.0  # 10 MB/60s -> 10 MB/min
    assert out["r2"] == 1.0


# -- merge_verdicts --------------------------------------------------------


def test_merge_verdicts_order():
    assert F.merge_verdicts(["env_error", "FAIL", "INCONCLUSIVE", "PASS"]) == "env_error"
    assert F.merge_verdicts(["FAIL", "INCONCLUSIVE", "PASS"]) == "FAIL"
    assert F.merge_verdicts(["INCONCLUSIVE", "PASS"]) == "INCONCLUSIVE"
    assert F.merge_verdicts(["PASS", "PASS"]) == "PASS"
    assert F.merge_verdicts(["PASS", "not_run"]) == "PASS"  # not_run 中性
    assert F.merge_verdicts([]) == "PASS"


# -- evaluate_features：13 特性全判定 ----------------------------------------


def _full_pass_summary() -> dict:
    return {
        "config": {"degradation_threshold": 2.0},
        "commit_durability": {
            "submit_ok_total": 2,
            "submit_rejected_total": 0,
            "accepted_done_ok": 2,
            "accepted_done_failed": 0,
            "accepted_done_poll_timeout": 0,
            "accepted_done_other": 0,
            "guarantee_violations": 0,
            "commit_success_rate": 1.0,
        },
        "commit_latency": {"count": 2, "p95_ms": 50.0},
        "scenes": {
            "A@4": {"ops": {"read": {"p50_ms": 10.0, "p95_ms": 20.0, "p99_ms": 30.0}}},
            "D@4": {"ops": {"read": {"p50_ms": 12.0, "p95_ms": 24.0, "p99_ms": 36.0}}},
        },
        "degradation": {"D@4_vs_A@4": {"p50": 1.2, "p95": 1.2, "p99": 1.2}},
        "tenant_fairness": {
            "multi": {
                "tenants": [
                    {
                        "tenant_idx": 0, "count": 5, "p50_ms": 90.0,
                        "p95_ms": 100.0, "p99_ms": 200.0,
                    },
                    {
                        "tenant_idx": 1, "count": 5, "p50_ms": 100.0,
                        "p95_ms": 120.0, "p99_ms": 240.0,
                    },
                ],
                "p95_max_min_ratio": 1.2,
                "p95_cv": 0.08,
                "balanced": True,
            }
        },
        "resources": {
            "rss_trend": {"slope_mb_per_min": 2.0, "r2": 0.9, "samples": 8},
            "rss_normalized": {},
            "rss_baseline_mb": 100.0,
            "rss_peak_mb": 200.0,
            "metrics_frames": 10,
            "cpu_util_mean_percent": 20.0,
            "cpu_util_max_percent": 40.0,
            "threads_max": 8,
            "commit_queue_max": 2,
        },
        "server": {"metrics_available": True},
        "write_retry": {
            "submit_total": 4, "final_ok": 4, "retry_exhausted_failures": 0,
        },
        "reconciliation": {
            "sessions": [{"session_id": "s1"}], "verdict": "PASS",
        },
        "search_quality": {"anchor_failures": 0, "total": 10},
        "isolation": {"D@4": {"verdict": "PASS", "reason": "ok"}},
        "error_type_validation": {"verdict": "PASS", "reason": "一致"},
        "fault_injection": {"verdict": "PASS", "reason": "恢复成功"},
        "preflight": {"ok": True, "engines_checked": 2},
        "isolation_probe": {
            "verdict": "PASS", "invalid_probe_count": 0, "probe_count": 2,
        },
        "saturation": {"verdict": "PASS", "reason": "全部带字段"},
        "hot_tenant": {"verdict": "PASS", "reason": "旁观公平"},
    }


def test_evaluate_features_full_summary_all_features():
    out = F.evaluate_features(_full_pass_summary())
    features = out["features"]
    assert set(features) == set(F.FEATURE_LABELS)
    assert features["commit_guarantee"]["verdict"] == "PASS"
    assert features["tenant_fairness"]["verdict"] == "PASS"
    assert features["memory_leak"]["verdict"] == "PASS"
    assert features["resource_timeline"]["verdict"] == "PASS"
    assert features["write_retry"]["verdict"] == "PASS"
    assert features["search_quality"]["verdict"] == "PASS"
    assert features["isolation_granularity"]["verdict"] == "PASS"
    assert features["error_type"]["verdict"] == "PASS"
    assert features["fault_injection"]["verdict"] == "PASS"
    assert features["fault_injection"]["evidence_type"] == "mock"
    assert features["preflight"]["verdict"] == "PASS"
    assert features["tenant_isolation"]["verdict"] == "INCONCLUSIVE"
    assert features["saturation_contract"]["verdict"] == "PASS"
    assert features["hot_tenant_fairness"]["verdict"] == "PASS"
    # 单次探针 PASS 不足以证明隔离 -> overall 至少 INCONCLUSIVE
    assert out["overall"] == "INCONCLUSIVE"
    assert out["verdict_layers"]["mock"] == {"fault_injection": "PASS"}
    assert len(out["verdict_layers"]["real"]) == 12
    # 判定携带量化证据
    assert features["commit_guarantee"]["measurements"]["durability"]["submitted_202"] == 2
    assert features["tenant_fairness"]["measurements"]["worst_scene"] == "multi"
    assert features["memory_leak"]["measurements"]["slope_mb_per_min"] == 2.0
    assert features["resource_timeline"]["measurements"]["metrics_available"] is True


def test_evaluate_features_empty_summary_tolerated():
    out = F.evaluate_features({})
    features = out["features"]
    assert set(features) == set(F.FEATURE_LABELS)
    assert out["overall"] == "INCONCLUSIVE"
    assert sum(1 for v in features.values() if v["verdict"] == "not_run") == 9
    assert features["commit_guarantee"]["verdict"] == "INCONCLUSIVE"
    assert features["tenant_fairness"]["verdict"] == "INCONCLUSIVE"
    assert features["memory_leak"]["verdict"] == "INCONCLUSIVE"
    assert features["resource_timeline"]["verdict"] == "PASS"
    for key, entry in features.items():
        assert "measurements" in entry, key


def test_evaluate_features_fail_branches():
    summary = {
        "config": {"degradation_threshold": 2.0},
        "commit_durability": {
            "submit_ok_total": 1,
            "submit_rejected_total": 0,
            "accepted_done_ok": 0,
            "accepted_done_failed": 1,
            "accepted_done_other": 0,
            "guarantee_violations": 1,
        },
        "scenes": {
            "A@4": {"ops": {"read": {"p95_ms": 20.0}}},
            "D@4": {"ops": {"read": {"p95_ms": 60.0}}},
        },
        "degradation": {"D@4_vs_A@4": {"p95": 3.0}},
        "tenant_fairness": {
            "multi": {
                "tenants": [
                    {"tenant_idx": 0, "count": 5, "p95_ms": 100.0, "p99_ms": 200.0},
                    {"tenant_idx": 1, "count": 5, "p95_ms": 400.0, "p99_ms": 800.0},
                ],
                "p95_max_min_ratio": 4.0,
                "p95_cv": 0.5,
                "balanced": False,
            }
        },
        "resources": {"rss_trend": {"slope_mb_per_min": 6.0, "r2": 0.9, "samples": 8}},
        "server": {"metrics_available": True},
        "write_retry": {"submit_total": 2, "final_ok": 1, "retry_exhausted_failures": 0},
        "reconciliation": {
            "sessions": [{"session_id": "s1", "verdict": "fail"}],
            "verdict": "FAIL",
            "reason": "缺失",
        },
        "search_quality": {"anchor_failures": 2, "total": 5},
        "isolation": {"D@4": {"verdict": "FAIL", "reason": "串扰"}},
        "error_type_validation": {"verdict": "FAIL", "reason": "错误类型不匹配"},
        "fault_injection": {"verdict": "FAIL", "reason": "未恢复"},
        "preflight": {"ok": False, "error": "key 缺失"},
        "isolation_probe": {"verdict": "FAIL", "invalid_probe_count": 1},
        "saturation": {"verdict": "FAIL", "reason": "缺 reason_code"},
        "hot_tenant": {"verdict": "FAIL", "reason": "旁观失衡"},
    }
    out = F.evaluate_features(summary)
    features = out["features"]
    assert features["commit_guarantee"]["verdict"] == "FAIL"
    assert features["tenant_fairness"]["verdict"] == "FAIL"
    assert features["memory_leak"]["verdict"] == "FAIL"
    assert features["write_retry"]["verdict"] == "FAIL"
    assert features["search_quality"]["verdict"] == "FAIL"
    assert features["isolation_granularity"]["verdict"] == "FAIL"
    assert features["error_type"]["verdict"] == "FAIL"
    assert features["fault_injection"]["verdict"] == "FAIL"
    assert features["preflight"]["verdict"] == "env_error"
    assert features["tenant_isolation"]["verdict"] == "FAIL"
    assert features["saturation_contract"]["verdict"] == "FAIL"
    assert features["hot_tenant_fairness"]["verdict"] == "FAIL"
    assert features["resource_timeline"]["verdict"] == "PASS"
    assert out["overall"] == "env_error"


# -- 特性12: saturation_summary --------------------------------------------


def test_saturation_summary_contract():
    records = [
        _rec("commit_submit", scene="S", status="ok"),
        _rec("read", scene="S", status="ok"),
        _rec(
            "commit_submit", scene="S", status="error", error_type="http_4xx",
            http_status=429, retry_after_s=1.0, reason_code="busy",
        ),
        _rec(
            "read", scene="S", status="error", error_type="http_4xx",
            http_status=429, retry_after_s=2.0, reason_code="quota",
        ),
    ]
    out = F.saturation_summary(records)
    assert out["rejected_total"] == 2
    assert out["total"] == 4
    assert out["rejection_rate"] == 0.5
    assert out["retry_after_present"] == 2
    assert out["reason_code_present"] == 2
    assert out["verdict"] == "PASS"
    assert out["rejection_p50_ms"] == 10.0


def test_saturation_summary_missing_fields_fail():
    records = [
        _rec(
            "commit_submit", scene="S", status="error", error_type="http_4xx",
            http_status=429, retry_after_s=1.0, reason_code="",
        ),
        _rec(
            "read", scene="S", status="error", error_type="http_4xx",
            http_status=429, retry_after_s=None, reason_code="busy",
        ),
    ]
    out = F.saturation_summary(records)
    assert out["rejected_total"] == 2
    assert out["retry_after_present"] == 1
    assert out["reason_code_present"] == 1
    assert out["verdict"] == "FAIL"


def test_saturation_summary_no_rejection_inconclusive():
    # 503（http_5xx）不是饱和拒绝样本：只有 http_4xx 计入拒绝集合
    records = [
        _rec("commit_submit", scene="S", status="ok"),
        _rec(
            "commit_submit", scene="S", status="error", error_type="http_5xx",
            http_status=503, retry_after_s=1.0, reason_code="overloaded",
        ),
    ]
    out = F.saturation_summary(records)
    assert out["rejected_total"] == 0
    assert out["verdict"] == "INCONCLUSIVE"


# -- 特性13: hot_tenant_summary ---------------------------------------------


def _hot_records(per_tenant: dict[int, list[float]]) -> list[RequestRecord]:
    return [
        _rec("commit_submit", scene="H", tenant_idx=t, stage_ms=s)
        for t, stages in per_tenant.items()
        for s in stages
    ]


def test_hot_tenant_summary_threshold():
    records = _hot_records(
        {0: [100.0] * 10, 1: [100.0], 2: [200.0], 3: [400.0]}
    )
    out = F.hot_tenant_summary(records)
    assert out["bystander_p50_ratio"] == 4.0
    assert out["verdict"] == "FAIL"
    assert "1.50" in out["reason"]


def test_hot_tenant_summary_pass():
    records = _hot_records(
        {0: [100.0] * 10, 1: [100.0], 2: [120.0], 3: [110.0]}
    )
    out = F.hot_tenant_summary(records)
    assert out["bystander_p50_ratio"] == 1.2
    assert out["verdict"] == "PASS"
    # 1.50 边界：ratio <= 1.50 判 PASS
    records = _hot_records({0: [100.0] * 10, 1: [100.0], 2: [150.0]})
    assert F.hot_tenant_summary(records)["verdict"] == "PASS"
    # 仅 1 个旁观租户 -> PASS
    records = _hot_records({0: [100.0] * 10, 1: [200.0]})
    assert F.hot_tenant_summary(records)["verdict"] == "PASS"


def test_hot_tenant_summary_inconclusive():
    assert F.hot_tenant_summary([])["verdict"] == "INCONCLUSIVE"
    # 各租户提交数均 >= 总提交数 1/4 -> 无旁观租户
    records = _hot_records(
        {0: [100.0], 1: [200.0], 2: [300.0], 3: [400.0]}
    )
    out = F.hot_tenant_summary(records)
    assert out["bystander_p50_ratio"] is None
    assert out["verdict"] == "INCONCLUSIVE"


# -- 特性6: search_quality_summary ------------------------------------------


def test_search_quality_summary_anchor_and_degraded():
    records = [
        _rec(
            "read", stage_ms=5.0, ts_ms=1000.0, query="PERFANCHOR-a",
            hit_count=3, quality_ok=True, real_recall=True,
        ),
        _rec(
            "read", stage_ms=1.0, ts_ms=1000.0, query="PERFANCHOR-b",
            hit_count=0, quality_ok=False, real_recall=False,
        ),
        _rec(
            "read", stage_ms=1.0, ts_ms=1000.0, query="PERFANCHOR-c",
            hit_count=0, quality_ok=True, real_recall=False, degraded=True,
        ),
        _rec(
            "read", stage_ms=2.0, ts_ms=1000.0, query="普通问题",
            hit_count=0, quality_ok=True, real_recall=False,
        ),
        # 洪峰窗口内：degraded 属容量产物，不参与质量判定
        _rec(
            "read", stage_ms=1.0, ts_ms=5000.0, query="PERFANCHOR-d",
            hit_count=0, quality_ok=False, real_recall=False,
        ),
    ]
    out = F.search_quality_summary(records, burst_windows=[(4000.0, 6000.0)])
    assert out["total"] == 4
    assert out["anchor_total"] == 3
    assert out["anchor_failures"] == 1
    assert out["anchor_degraded"] == 1
    assert out["degraded_total"] == 1
    assert out["ordinary_total"] == 1
    assert out["undetermined_real_recall"] == 1
    assert out["quality_failures"] == 1
    assert out["hit_count_p50"] == 3
    assert out["gated_read_stats"]["count"] == 1
    assert out["gated_read_stats"]["p50_ms"] == 5.0


def test_search_quality_summary_empty():
    out = F.search_quality_summary([])
    assert out["total"] == 0
    assert out["anchor_total"] == 0
    assert out["anchor_failures"] == 0
    assert out["gated_read_stats"]["count"] == 0


# -- 特性5 对账: reconcile_messages -------------------------------------------


def test_reconcile_messages_four_checks_pass():
    entry = {
        "session_id": "s1",
        "client_hashes": ["h1", "h2"],
        "server_hashes": ["h1", "h2"],
        "archive_status": "completed",
        "history_available": True,
        "archive_available": True,
        "atoms_available": True,
        "atom_source_turn_ids": ["m1", "m2"],
        "client_ids": ["m1", "m2"],
    }
    out = F.reconcile_messages([entry])
    assert out["verdict"] == "PASS"
    checks = out["sessions"][0]["checks"]
    assert [c["name"] for c in checks] == [
        "client_in_server",
        "server_no_duplicate",
        "archive_completed",
        "atom_no_dup_and_subset",
    ]
    assert all(c["ok"] is True for c in checks)
    assert out["sessions"][0]["verdict"] == "pass"


def test_reconcile_messages_detects_each_failure():
    base = {
        "session_id": "s1",
        "client_hashes": ["h1"],
        "server_hashes": ["h1"],
        "archive_status": "completed",
        "history_available": True,
        "archive_available": True,
        "atoms_available": True,
        "atom_source_turn_ids": ["m1"],
        "client_ids": ["m1"],
    }
    cases = [
        (dict(base, server_hashes=[]), "client_in_server"),
        (dict(base, server_hashes=["h1", "h1"]), "server_no_duplicate"),
        (dict(base, archive_status="pending"), "archive_completed"),
        (
            dict(base, atom_source_turn_ids=["m1", "m1"], client_ids=["m1"]),
            "atom_no_dup_and_subset",
        ),
    ]
    for entry, check_name in cases:
        out = F.reconcile_messages([entry])
        assert out["verdict"] == "FAIL", check_name
        check = next(
            c for c in out["sessions"][0]["checks"] if c["name"] == check_name
        )
        assert check["ok"] is False, check_name


def test_reconcile_messages_unavailable_and_empty():
    unavailable = {
        "session_id": "s1",
        "client_hashes": ["h1"],
        "server_hashes": [],
        "history_available": False,
        "archive_available": False,
        "atoms_available": False,
    }
    out = F.reconcile_messages([unavailable])
    assert out["verdict"] == "INCONCLUSIVE"
    assert "不可用" in out["reason"]
    assert F.reconcile_messages([])["verdict"] == "INCONCLUSIVE"


# -- 特性7: isolation_summary -------------------------------------------------


def test_isolation_summary_crosstalk():
    records = [
        _rec("read", scene="D@4", tenant_idx=0, stage_ms=100.0, ts_ms=500.0),
        _rec("read", scene="D@4", tenant_idx=1, stage_ms=130.0, ts_ms=500.0),
    ]
    out = F.isolation_summary(
        records, t0_ms=0.0, t1_ms=1000.0, burst_tenant_idx=0, baseline_p95=100.0
    )
    assert out["same_tenant_degradation"] == 1.0
    assert out["cross_tenant_degradation"] == 1.3
    assert out["verdict"] == "FAIL"  # 1.3 > 1.0 * 1.25（crosstalk_tolerance）
    assert "串扰" in out["reason"]


def test_isolation_summary_pass_and_threshold():
    records = [
        _rec("read", scene="D@4", tenant_idx=0, stage_ms=100.0, ts_ms=500.0),
        _rec("read", scene="D@4", tenant_idx=1, stage_ms=110.0, ts_ms=500.0),
    ]
    out = F.isolation_summary(
        records, t0_ms=0.0, t1_ms=1000.0, burst_tenant_idx=0, baseline_p95=100.0
    )
    assert out["verdict"] == "PASS"
    records = [
        _rec("read", scene="D@4", tenant_idx=0, stage_ms=100.0, ts_ms=500.0),
        _rec("read", scene="D@4", tenant_idx=1, stage_ms=300.0, ts_ms=500.0),
    ]
    out = F.isolation_summary(
        records, t0_ms=0.0, t1_ms=1000.0, burst_tenant_idx=0, baseline_p95=100.0,
        degradation_threshold=2.0,
    )
    assert out["verdict"] == "FAIL"
    assert "阈值" in out["reason"]


def test_isolation_summary_inconclusive():
    out = F.isolation_summary(
        [], t0_ms=0.0, t1_ms=1000.0, burst_tenant_idx=0, baseline_p95=100.0
    )
    assert out["verdict"] == "INCONCLUSIVE"
    records = [_rec("read", scene="D@4", tenant_idx=0, stage_ms=100.0, ts_ms=500.0)]
    out = F.isolation_summary(
        records, t0_ms=0.0, t1_ms=1000.0, burst_tenant_idx=0, baseline_p95=None
    )
    assert out["verdict"] == "INCONCLUSIVE"


# -- 特性5 重试: retry_summary -------------------------------------------------


def test_retry_summary():
    records = [
        _rec("commit_submit", scene="B@4", session_id="s1", stage_ms=10.0),
        _rec(
            "commit_submit", scene="B@4", session_id="s2", stage_ms=20.0,
            retried=True, retry_total_wait_ms=200.0,
            retry_after_s=1.0, reason_code="busy",
        ),
        _rec(
            "commit_submit", scene="B@4", session_id="s3", stage_ms=30.0,
            retried=True, retry_total_wait_ms=100.0,
            status="error", error_type="timeout",
        ),
    ]
    out = F.retry_summary(records)
    assert out["submit_total"] == 3
    assert out["retried_total"] == 2
    assert out["first_attempt_ok"] == 1
    assert out["first_attempt_rate"] == round(1 / 3, 5)
    assert out["final_ok"] == 2
    assert out["final_success_rate"] == round(2 / 3, 5)
    assert out["retry_exhausted_failures"] == 1
    assert out["retried_final_ok"] == 1
    assert out["retried_errors"] == {"timeout": 1}
    assert out["retry_wait_ms"]["count"] == 2
    assert out["retry_after_s"] == {"avg": 1.0, "max": 1.0}
    assert out["reason_codes"] == {"busy": 1}
    empty = F.retry_summary([])
    assert empty["submit_total"] == 0
    assert empty["reason_codes"] == {}
    assert empty["retry_after_s"] is None


def test_retry_summary_429_aggregate():
    records = [
        _rec(
            "commit_submit", scene="B@4", session_id="s1", retried=True,
            retry_after_s=0.5, reason_code="busy", retry_total_wait_ms=50.0,
        ),
        _rec(
            "commit_submit", scene="B@4", session_id="s2", retried=True,
            retry_after_s=1.5, reason_code="busy", retry_total_wait_ms=150.0,
        ),
    ]
    out = F.retry_summary(records)
    assert out["retry_after_s"] == {"avg": 1.0, "max": 1.5}
    assert out["reason_codes"] == {"busy": 2}


# -- 特性11: isolation_probe_summary ------------------------------------------


def test_isolation_probe_summary():
    records = [
        _rec(
            "isolation_probe", scene="I",
            extra='{"marker_found": true, "expected": true, "same_tenant": true}',
        ),
        _rec(
            "isolation_probe", scene="I",
            extra='{"marker_found": false, "expected": false, "same_tenant": false}',
        ),
    ]
    out = F.isolation_probe_summary(records)
    assert out["probe_count"] == 2
    assert out["expected_probe_count"] == 2
    assert out["invalid_probe_count"] == 0
    assert out["same_tenant_hit_rate"] == 1.0
    assert out["cross_tenant_false_positive_rate"] == 0.0
    assert out["verdict"] == "PASS"


def test_isolation_probe_summary_fail_and_inconclusive():
    records = [
        _rec(
            "isolation_probe", scene="I",
            extra='{"marker_found": true, "expected": false, "same_tenant": true}',
        ),
    ]
    out = F.isolation_probe_summary(records)
    assert out["invalid_probe_count"] == 1
    assert out["verdict"] == "FAIL"
    records = [
        _rec("isolation_probe", scene="I", status="error"),
        _rec(
            "isolation_probe", scene="I",
            extra='{"marker_found": true, "expected": true, "same_tenant": true}',
        ),
    ]
    assert F.isolation_probe_summary(records)["verdict"] == "INCONCLUSIVE"
    assert F.isolation_probe_summary([])["verdict"] == "INCONCLUSIVE"


# -- 特性9: fault_injection_summary --------------------------------------------


def test_fault_injection_summary():
    assert F.fault_injection_summary([])["verdict"] == "not_run"
    ok_seq = [
        {
            "stage": "timeout", "behavior": "timeout",
            "expected_error_type": "timeout", "observed_error_type": "timeout",
            "requests": 3, "hang": False, "recovered": True,
        },
        {
            "stage": "5xx", "behavior": "http_5xx",
            "expected_error_type": "http_5xx", "observed_error_type": "http_5xx",
            "requests": 2, "hang": False, "recovered": True,
        },
    ]
    out = F.fault_injection_summary(ok_seq)
    assert out["verdict"] == "PASS"
    assert all(stage["ok"] for stage in out["stages"])
    bad_seq = [
        {
            "stage": "timeout", "behavior": "timeout",
            "expected_error_type": "timeout", "observed_error_type": "http_5xx",
            "requests": 3, "hang": False, "recovered": False,
        },
    ]
    out = F.fault_injection_summary(bad_seq)
    assert out["verdict"] == "FAIL"


# -- 特性8: error_type_validation -----------------------------------------------


def test_error_type_validation():
    records = [
        _rec("commit_submit", scene="B@4", status="error", error_type="timeout"),
        _rec("read", scene="A@4", status="error", error_type="http_5xx"),
    ]
    out = F.error_type_validation(records)
    assert out["verdict"] == "PASS"
    assert out["observed_breakdown"] == {"timeout": 1, "http_5xx": 1}
    fault = {
        "stages": [
            {
                "stage": "s1", "expected_error_type": "timeout",
                "observed_error_type": "http_5xx", "ok": False,
            }
        ]
    }
    out = F.error_type_validation(records, fault)
    assert out["verdict"] == "FAIL"
    assert "错误类型不匹配" in out["reason"]
    assert F.error_type_validation([])["verdict"] == "not_run"


# -- 其余公共函数 ---------------------------------------------------------------


def test_read_records_in_window():
    records = [
        _rec("read", ts_ms=100.0),
        _rec("read", ts_ms=200.0),
        _rec("read", ts_ms=300.0),
        _rec("add", ts_ms=200.0),
    ]
    out = F.read_records_in_window(records, 150.0, 250.0)
    assert [r.ts_ms for r in out] == [200.0]
    assert F.read_records_in_window([], 0.0, 1.0) == []


def test_consistency_summary():
    records = [
        _rec("consistent_check", stage_ms=10.0),
        _rec("consistent_check", stage_ms=30.0, status="error", error_type="timeout"),
        _rec(
            "consistent_check", stage_ms=20.0,
            status="error", error_type="consistency_timeout",
        ),
        _rec("read", stage_ms=5.0),
    ]
    out = F.consistency_summary(records)
    assert out["count"] == 3
    assert out["timeouts"] == 2
    assert out["p50_ms"] == 20.0
    assert F.consistency_summary([])["count"] == 0


def test_commit_completion_latency():
    records = [
        _rec("commit_done", stage_ms=10.0),
        _rec("commit_done", stage_ms=30.0),
        _rec("commit_done", stage_ms=999.0, status="error", error_type="commit_timeout"),
    ]
    out = F.commit_completion_latency(records)
    assert out["count"] == 2
    assert out["p50_ms"] == 20.0
    assert out["max_ms"] == 30.0


def test_degradation_factor():
    baseline = {"p50_ms": 10.0, "p95_ms": 20.0, "p99_ms": 30.0}
    target = {"p50_ms": 15.0, "p95_ms": 40.0, "p99_ms": 60.0}
    assert F.degradation_factor(baseline, target) == {"p50": 1.5, "p95": 2.0, "p99": 2.0}
    assert F.degradation_factor(None, target) == {"p50": None, "p95": None, "p99": None}
    assert F.degradation_factor(baseline, {}) == {"p50": None, "p95": None, "p99": None}


def test_degradation_measurements():
    summary = {
        "scenes": {
            "A@4": {"ops": {"read": {"p50_ms": 10.0, "p95_ms": 20.0, "p99_ms": 30.0}}},
            "D@4": {"ops": {"read": {"p50_ms": 15.0, "p95_ms": 24.0, "p99_ms": 36.0}}},
        },
        "degradation": {
            "D@4_vs_A@4": {"p50": 1.5, "p95": 1.2, "p99": 1.2},
            "plain": {"p95": 9.9},  # 不含 _vs_ 的键被跳过
        },
    }
    out = F.degradation_measurements(summary)
    assert set(out) == {"D@4_vs_A@4"}
    entry = out["D@4_vs_A@4"]
    assert entry["baseline_p95_ms"] == 20.0
    assert entry["flood_p95_ms"] == 24.0
    assert entry["delta_p95_ms"] == 4.0
    assert entry["ratio_p95"] == 1.2
    assert entry["delta_p50_ms"] == 5.0


def test_fairness_measurements():
    fairness = {
        "A@4": {
            "tenants": [
                {"tenant_idx": 0, "p95_ms": 100.0, "p99_ms": 200.0},
                {"tenant_idx": 1, "p95_ms": 300.0, "p99_ms": 600.0},
                {"tenant_idx": 2, "p95_ms": None},
            ],
            "p95_max_min_ratio": 3.0,
            "p95_cv": 0.4,
        },
        "single": {
            "tenants": [{"tenant_idx": 0, "p95_ms": 100.0, "p99_ms": 200.0}],
        },
    }
    out = F.fairness_measurements(fairness)
    assert out["worst_scene"] == "A@4"
    scene = out["scenes"]["A@4"]
    assert scene["tenant_count"] == 2  # p95 为 None 的行被跳过
    assert scene["fastest_tenant_idx"] == 0
    assert scene["slowest_tenant_idx"] == 1
    assert scene["slowest_waits_extra_ms"] == 200.0
    assert "single" not in out["scenes"]
    assert F.fairness_measurements({}) == {"scenes": {}}


def test_burst_summary():
    burst = [_rec("read", stage_ms=40.0), _rec("read", stage_ms=60.0)]
    baseline = [_rec("read", stage_ms=10.0), _rec("read", stage_ms=30.0)]
    out = F.burst_summary(burst, baseline)
    assert out["count"] == 2
    assert out["degradation"]["p50"] == round(50.0 / 20.0, 3)
    assert F.burst_summary([], baseline) == {
        "count": 0,
        "degradation": {"p50": None, "p95": None, "p99": None},
    }


def test_rss_normalized_series():
    raw = [(0.0, 1000.0), (10.0, 1200.0), (20.0, 1400.0)]
    injected = [(5.0, 100.0), (15.0, 200.0)]
    out = F.rss_normalized_series(raw, injected)
    assert out == [(0.0, 1000.0), (10.0, 1100.0), (20.0, 1200.0)]
    assert F.rss_normalized_series([], injected) == []
    assert F.rss_normalized_series(raw, []) == []


def test_injected_bytes_series():
    records = [
        _rec("add", ts_ms=2000.0, content_bytes=20),
        _rec("add", ts_ms=1000.0, content_bytes=10),
        _rec("add", ts_ms=3000.0, content_bytes=30, status="error"),
    ]
    out = F.injected_bytes_series(records)
    assert out == [(1.0, 10.0), (2.0, 30.0)]
    assert F.injected_bytes_series([]) == []


# -- 常量与私有助手 -------------------------------------------------------------


def test_feature_labels_and_constants():
    assert F.FAIRNESS_MAX_MIN_RATIO == 3.0
    assert F.RSS_LEAK_SLOPE_MB_PER_MIN == 5.0
    assert F.VERDICT_ORDER == ("env_error", "fail", "inconclusive", "not_run", "pass")
    assert set(F.FEATURE_LABELS) == {
        "commit_guarantee",
        "tenant_fairness",
        "memory_leak",
        "resource_timeline",
        "write_retry",
        "search_quality",
        "isolation_granularity",
        "error_type",
        "fault_injection",
        "preflight",
        "tenant_isolation",
        "saturation_contract",
        "hot_tenant_fairness",
    }


def test_merge_subs_and_duplicates():
    assert F._merge_subs([{"verdict": "PASS"}, {"verdict": "INCONCLUSIVE"}]) == "INCONCLUSIVE"
    assert F._merge_subs([{"verdict": "PASS"}, {"verdict": "FAIL"}]) == "FAIL"
    assert F._merge_subs([{"verdict": "PASS"}, {"verdict": "PASS"}]) == "PASS"
    assert F._duplicates(["a", "b", "a", "c", "b", "a"]) == ["a", "b"]
    assert F._duplicates([]) == []

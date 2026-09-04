"""PR421 验收评估纯函数测试：``acceptance/evaluate.py`` 及 HTML 报告渲染。

全部为纯函数/临时目录测试：直接构造 manifest 注入
``evaluate_pr421_acceptance``，断言各 gate 的状态、观测值与理由；
HTML 部分用 ``tempfile`` 写临时 suite.json 后经 ``report_html.render``
渲染并检查关键片段，不依赖 conftest 的 mock 服务器。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from performance.targets.echomem.acceptance.evaluate import (
    FAIL,
    INCONCLUSIVE,
    NOT_IMPLEMENTED,  # noqa: F401
    PR28_REVIEW_RESOLUTION,
    build_model_analysis_input,
    evaluate_pr421_acceptance,
)
from performance.targets.echomem.acceptance.report_html import render


def test_missing_measurements_are_inconclusive_and_unavailable_are_explicit():
    result = evaluate_pr421_acceptance({"runs": []})
    assert INCONCLUSIVE == result["overall"]
    statuses = {item["status"] for item in result["checks"]}
    assert INCONCLUSIVE in statuses
    assert "INCONCLUSIVE" in statuses


def test_report6_quality_gate_rejects_empty_marker_results():
    manifest = {
        "runs": [{
            "scenario": "A@1",
            "summary": {
                "metrics": {
                    "search": {
                        "quality_asserted": 10,
                        "quality_failures": 2,
                    }
                },
                "details": {
                    "quality_seed": [
                        {"status": "completed"},
                    ]
                },
            },
        }]
    }
    result = evaluate_pr421_acceptance(manifest)
    quality = next(
        item for item in result["checks"]
        if item["name"] == "report(6) Search quality assertion"
    )
    assert "FAIL" == quality["status"]
    assert 2 == quality["observed"]["quality_failures"]


def test_model_input_is_secret_free_and_preserves_acceptance():
    manifest = {
        "base_url": "http://127.0.0.1:8010",
        "scenarios": ["saturation"],
        "repeats": 1,
        "client_admission_enabled": False,
        "server_observation_mode": True,
        "runs": [],
    }
    acceptance = evaluate_pr421_acceptance(manifest)
    payload = build_model_analysis_input(manifest, acceptance)
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "PR421" in encoded
    assert "api_key" not in encoded.lower()
    assert "NOT_IMPLEMENTED" in encoded


def test_html_renders_acceptance_matrix():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        manifest = {
            "base_url": "http://127.0.0.1:8010",
            "repeats": 1,
            "runs": [],
            "acceptance": {
                "overall": "INCONCLUSIVE",
                "checks": [
                    {
                        "name": "B7 lane/fan-out metrics",
                        "status": "INCONCLUSIVE",
                        "target": "6 metric families",
                        "observed": {"missing": ["lane_exec"]},
                        "reason": "缺失服务端指标",
                        "evidence": "details.pr421_metric_coverage",
                    }
                ],
                "review": {
                    "reasonable_targets": ["分离成功延迟与超时率"],
                    "missing_or_weak_targets": ["需要游标对账"],
                },
            },
        }
        path = root / "suite.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        output = root / "suite.html"
        render(path, output)
        document = output.read_text(encoding="utf-8")
        assert "PR421 验收矩阵" in document
        assert "需要游标对账" in document
        assert "INCONCLUSIVE" in document


def test_html_renders_review_resolution_when_present():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        acceptance = evaluate_pr421_acceptance({"runs": []})
        manifest = {"runs": [], "acceptance": acceptance}
        path = root / "suite.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        output = root / "suite.html"
        render(path, output)
        document = output.read_text(encoding="utf-8")
        assert "PR28 检视意见闭环" in document
        assert "Commit barrier and tenant distributions" in document
    assert "PARTIAL" in document


def test_saturation_without_rejections_does_not_claim_contract_pass():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        run_dir = root / "saturation" / "repeat-01" / "server-observe"
        run_dir.mkdir(parents=True)
        (run_dir / "search_results.csv").write_text(
            "status_code,end_to_end_s\n200,0.1\n200,0.2\n",
            encoding="utf-8",
        )
        result = evaluate_pr421_acceptance(
            {
                "runs": [
                    {
                        "scenario": "saturation",
                        "output_dir": str(run_dir),
                        "summary": {},
                    }
                ]
            }
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "Saturation rejection rate"
        )
        assert INCONCLUSIVE == check["status"]


def test_saturation_rejection_requires_reason_code():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        run_dir = root / "saturation" / "repeat-01" / "server-observe"
        run_dir.mkdir(parents=True)
        (run_dir / "search_results.csv").write_text(
            "status_code,end_to_end_s,retry_after_s,reason_code\n"
            "503,0.2,1,\n",
            encoding="utf-8",
        )
        result = evaluate_pr421_acceptance(
            {
                "runs": [{
                    "scenario": "saturation",
                    "output_dir": str(run_dir),
                    "summary": {},
                }]
            }
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "Saturation rejection rate"
        )
        assert FAIL == check["status"]


def test_saturation_counts_explicit_400_admission_rejection():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        run_dir = root / "saturation" / "repeat-01" / "server-observe"
        run_dir.mkdir(parents=True)
        (run_dir / "search_results.csv").write_text(
            "status_code,end_to_end_s,error_class,retry_after,reason_code,error_detail\n"
            "200,0.01,,,,\n"
            "400,0.02,admission_rejected,1,,too many recall requests in flight\n",
            encoding="utf-8",
        )
        result = evaluate_pr421_acceptance(
            {
                "runs": [{
                    "scenario": "saturation",
                    "output_dir": str(run_dir),
                    "summary": {},
                }]
            }
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "Saturation rejection rate"
        )
        assert FAIL == check["status"]
        assert 1 == check["observed"]["rejected"]
        assert {"400": 1} == check["observed"]["status_breakdown"]
        assert not check["observed"]["wire_status_complete"]
        assert check["observed"]["retry_after_complete"]


def test_report4_invalid_baseline_cannot_produce_degradation_pass():
    result = evaluate_pr421_acceptance(
        {
            "runs": [
                {
                    "scenario": "A-c4",
                    "summary": {
                        "metrics": {
                            "search": {
                                "success_rate": 0.8,
                                "latency": {"p95_s": 1.0},
                            }
                        }
                    },
                },
                {
                    "scenario": "D-c4",
                    "summary": {
                        "metrics": {
                            "search": {
                                "success_rate": 1.0,
                                "latency": {"p95_s": 1.1},
                            }
                        }
                    },
                },
            ]
        }
    )
    check = next(
        item for item in result["checks"]
        if item["name"] == "Search P95 isolation ratio"
    )
    assert INCONCLUSIVE == check["status"]
    assert "invalid_baselines" in check["observed"]


def test_metric_label_violation_is_not_a_coverage_pass():
    result = evaluate_pr421_acceptance(
        {
            "runs": [{
                "summary": {
                    "details": {
                        "pr421_metric_coverage": {
                            "present": {
                                "lane_queued": True,
                                "lane_wait": True,
                                "lane_exec": True,
                                "lane_rejected": True,
                                "engine_exec": True,
                                "engine_skipped": True,
                            },
                            "missing": [],
                            "bounded_label_violations": [{
                                "metric": "echomem_lane_queued",
                                "label": "tenant_id",
                                "value": "tenant-a",
                            }],
                        }
                    }
                }
            }]
        }
    )
    check = next(
        item for item in result["checks"]
        if item["name"] == "B7 lane/fan-out metrics"
    )
    assert INCONCLUSIVE == check["status"]


def test_bounded_lane_and_fanout_evidence_passes():
    lanes = (
        "recall_engine",
        "recall_intent_llm",
        "recall_query_embedding",
        "recall_rerank",
        "commit",
    )
    result = evaluate_pr421_acceptance(
        {
            "runs": [{
                "summary": {
                    "details": {
                        "pr421_metric_coverage": {
                            "present": {
                                "echomem_lane_queued": True,
                                "echomem_lane_wait_seconds": True,
                                "echomem_lane_exec_seconds": True,
                                "echomem_lane_rejected_total": True,
                                "echomem_engine_fanout_exec_seconds": True,
                                "echomem_engine_fanout_skipped_total": True,
                            },
                            "missing": [],
                            "bounded_label_violations": [],
                            "lane_quartets": {
                                lane: {
                                    "queued": True,
                                    "wait": True,
                                    "exec": True,
                                    "rejected": True,
                                }
                                for lane in lanes
                            },
                            "fanout_engines": {
                                "memory": {"exec": True, "skipped": True},
                            },
                        }
                    }
                }
            }]
        }
    )
    check = next(
        item for item in result["checks"]
        if item["name"] == "B7 lane/fan-out metrics"
    )
    assert "PASS" == check["status"]
    assert sorted(lanes) == check["observed"]["complete_lanes"]
    assert ["memory"] == check["observed"]["complete_fanout_engines"]


def test_bounded_lane_evidence_without_all_lanes_is_inconclusive():
    result = evaluate_pr421_acceptance(
        {
            "runs": [{
                "summary": {
                    "details": {
                        "pr421_metric_coverage": {
                            "present": {},
                            "missing": [],
                            "bounded_label_violations": [],
                            "lane_quartets": {
                                "commit": {
                                    "queued": True,
                                    "wait": True,
                                    "exec": True,
                                    "rejected": True,
                                }
                            },
                            "fanout_engines": {
                                "memory": {"exec": True, "skipped": True},
                            },
                        }
                    }
                }
            }]
        }
    )
    check = next(
        item for item in result["checks"]
        if item["name"] == "B7 lane/fan-out metrics"
    )
    assert INCONCLUSIVE == check["status"]
    assert "recall_engine" in check["observed"]["missing_lanes"]


def test_legacy_evidence_with_missing_metric_family_is_inconclusive():
    result = evaluate_pr421_acceptance(
        {
            "runs": [{
                "summary": {
                    "details": {
                        "pr421_metric_coverage": {
                            "present": {},
                            "missing": ["echomem_lane_wait_seconds"],
                            "bounded_label_violations": [],
                            "per_tenant_quartets": {
                                "a": {
                                    "queued": True,
                                    "wait": True,
                                    "exec": True,
                                    "rejected": True,
                                },
                                "b": {
                                    "queued": True,
                                    "wait": True,
                                    "exec": True,
                                    "rejected": True,
                                },
                            },
                        }
                    }
                }
            }]
        }
    )
    check = next(
        item for item in result["checks"]
        if item["name"] == "B7 lane/fan-out metrics"
    )
    assert INCONCLUSIVE == check["status"]


def test_fairness_uses_commit_completion_throughput():
    result = evaluate_pr421_acceptance(
        {
            "runs": [{
                "scenario": "tenant-skew",
                "summary": {
                    "metrics": {
                        "fairness": {
                            "commit_completed_per_tenant": {
                                "a": 10, "b": 10, "c": 10, "d": 10,
                            }
                        }
                    }
                }
            }]
        }
    )
    check = next(
        item for item in result["checks"]
        if item["name"] == "Tenant fairness (Jain)"
    )
    assert "PASS" == check["status"]
    assert 1.0 == check["observed"]


def test_review_resolution_is_explicit_and_model_visible():
    acceptance = evaluate_pr421_acceptance({"runs": []})
    statuses = {item["status"] for item in PR28_REVIEW_RESOLUTION}
    assert {"RESOLVED", "PARTIAL"} <= statuses
    assert PR28_REVIEW_RESOLUTION == acceptance["pr28_review_resolution"]
    model_input = build_model_analysis_input(
        {"scenarios": [], "repeats": 0},
        acceptance,
    )
    assert PR28_REVIEW_RESOLUTION == model_input["acceptance"]["pr28_review_resolution"]

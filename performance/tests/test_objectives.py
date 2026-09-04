"""目标映射测试：``acceptance/objectives.py``（运行套件数据 → O1-O7 判定）。

纯函数测试，直接构造套件字典驱动 ``scheduler_acceptance.evaluate``，
不依赖 conftest 的 mock 服务器。
"""

from __future__ import annotations

from typing import Any

from performance.targets.echomem.acceptance.objectives import (
    FAIL,
    INCONCLUSIVE,
    OBJECTIVES,
    PASS,
    acceptance_by_name,
    objective_statuses,
)


def _objective(suite: dict[str, Any], objective_id: str) -> dict[str, Any]:
    return next(item for item in objective_statuses(suite) if item["id"] == objective_id)


def _passing_suite() -> dict[str, Any]:
    """同时满足 O1/O3-O7 六项严格判定的真实证据套件。"""
    lanes = (
        "recall_engine",
        "recall_intent_llm",
        "recall_query_embedding",
        "recall_rerank",
        "commit",
    )
    return {
        "instance_profile": "4U8G",
        "capability_probe": {
            "checks": [{
                "name": "Prometheus B7 metrics",
                "present": {
                    "lane_queued": True,
                    "lane_wait": True,
                    "lane_exec": True,
                    "lane_rejected": True,
                },
            }]
        },
        "commit_recovery": {
            "status": PASS,
            "recovered": True,
            "replay_rate": 1.0,
            "message_reconciliation": {"status": PASS},
            "cursor_reconciliation": {"status": PASS},
            "idempotency_reconciliation": {"status": PASS},
        },
        "fault_suite": {
            "cases": [
                {"name": "kill-one-tenant", "execution": {"result": {"status": PASS}}},
            ],
            "tenant_fault_isolation": {
                "bystander_p95_degradation": 0.10,
                "fault_recovered": True,
            },
        },
        "runs": [
            {
                "scenario": "capacity-4",
                "status": "completed",
                "summary": {
                    "metrics": {
                        "search": {"submitted": 4, "success_rate": 1.0},
                        "commit": {"submitted": 0},
                    },
                    "details": {
                        "user_activity": {
                            "active_user_count": 4,
                            "hot_user_proxy": {"request_count": 4},
                        }
                    },
                },
            },
            {
                "scenario": "capacity-8",
                "status": "completed",
                "summary": {
                    "metrics": {
                        "search": {"submitted": 8, "success_rate": 0.5},
                        "commit": {"submitted": 0},
                    }
                },
            },
            {
                "scenario": "mixed",
                "status": "completed",
                "summary": {
                    "metrics": {
                        "fairness": {
                            "commit_completed_per_tenant": {"a": 2, "b": 2, "c": 1},
                        },
                        "per_tenant": {
                            tenant: {
                                "commit": {"submitted": count, "completed": count},
                                "search": {"submitted": 4, "latency": {"p95_s": 1.0}},
                            }
                            for tenant, count in {"a": 2, "b": 2, "c": 1}.items()
                        },
                    }
                },
            },
            {
                "scenario": "search-priority-blackbox",
                "status": "completed",
                "summary": {
                    "metrics": {
                        "search": {"latency": {"p95_s": 1.2}},
                        "commit": {"submitted": 32},
                    }
                },
            },
            {
                "status": "completed",
                "summary": {
                    "details": {
                        "pr421_metric_coverage": {
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
                },
            },
        ],
    }


def test_objective_statuses_returns_seven_objectives():
    objectives = objective_statuses({})
    assert [item["id"] for item in objectives] == ["O1", "O2", "O3", "O4", "O5", "O6", "O7"]
    assert [item["name"] for item in objectives] == [name for _, name in OBJECTIVES]
    # 空套件无任何证据：所有目标均为 INCONCLUSIVE
    assert all(item["status"] == INCONCLUSIVE for item in objectives)


def test_o2_passes_with_two_completed_profiles():
    suite = {
        "instance_profiles": [
            {"name": "4U8G", "status": "completed", "completed_runs": 2},
            {"name": "8U16G", "status": "passed", "completed_runs": 1},
        ]
    }
    o2 = _objective(suite, "O2")
    assert o2["status"] == PASS
    assert [item["name"] for item in o2["observed"]["completed_profiles"]] == ["4U8G", "8U16G"]
    assert o2["evidence"] == o2["observed"]["completed_profiles"]


def test_o2_inconclusive_with_fewer_than_two_completed_profiles():
    suite = {
        "instance_profiles": [
            {"name": "4U8G", "status": "completed", "completed_runs": 2},
            {"name": "8U16G", "status": "planned", "completed_runs": 0},
        ]
    }
    o2 = _objective(suite, "O2")
    assert o2["status"] == INCONCLUSIVE
    assert len(o2["observed"]["completed_profiles"]) == 1


def test_o2_ignores_profiles_without_completed_runs():
    suite = {
        "instance_profiles": [
            {"name": "4U8G", "status": "completed", "completed_runs": 0},
            {"name": "8U16G", "status": "completed"},
        ]
    }
    o2 = _objective(suite, "O2")
    assert o2["status"] == INCONCLUSIVE
    assert o2["observed"]["completed_profiles"] == []


def test_o1_o3_o7_mirror_scheduler_checks_by_name():
    objectives = objective_statuses(_passing_suite())
    by_id = {item["id"]: item for item in objectives}
    for objective_id, check_name in (
        ("O1", "DAU / 最大热用户容量"),
        ("O3", "单租户故障隔离"),
        ("O4", "Commit/Search 公平性 Jain"),
        ("O5", "Search 优先于 Commit"),
        ("O6", "Commit kill-9 恢复与重放"),
        ("O7", "分层/分租户调度可观测性"),
    ):
        item = by_id[objective_id]
        assert item["status"] == PASS
        assert item["evidence"] == f"scheduler_acceptance: {check_name}"
        assert item["reason"]
        assert isinstance(item["observed"], dict)
        assert item["owner"]
    # O1 的严格判定确实来自容量阶梯证据，而非默认值
    assert by_id["O1"]["observed"]["capacity_boundary_levels"] == [8]


def test_o5_failure_flows_through_from_scheduler():
    suite = {
        "runs": [{
            "scenario": "search-priority-blackbox",
            "status": "completed",
            "summary": {
                "metrics": {
                    "search": {"latency": {"p95_s": 5.01}},
                    "commit": {"submitted": 128},
                }
            },
        }]
    }
    o5 = _objective(suite, "O5")
    assert o5["status"] == FAIL


def test_recovery_backfills_message_set_reconciled_and_replay_verified():
    suite = {
        "commit_recovery": {
            "status": PASS,
            "recovered": True,
            "replay_rate": 1.0,
            "message_reconciliation": {"status": PASS},
            "cursor_reconciliation": {"status": PASS},
            "idempotency_reconciliation": {"status": PASS},
        }
    }
    o6 = _objective(suite, "O6")
    assert o6["status"] == PASS
    assert o6["observed"]["message_set_reconciled"] is True
    assert o6["observed"]["replay_verified"] is True


def test_recovery_message_reconciliation_failure_blocks_pass():
    suite = {
        "commit_recovery": {
            "status": PASS,
            "recovered": True,
            "replay_rate": 1.0,
            "message_reconciliation": {"status": FAIL},
            "cursor_reconciliation": {"status": PASS},
            "idempotency_reconciliation": {"status": PASS},
        }
    }
    o6 = _objective(suite, "O6")
    assert o6["status"] == INCONCLUSIVE
    assert o6["observed"]["message_set_reconciled"] is False


def test_o3_derives_from_strict_fault_check():
    suite = {
        "fault_suite": {
            "cases": [
                {"name": "kill-one-tenant", "execution": {"result": {"status": PASS}}},
            ],
            "tenant_fault_isolation": {
                "bystander_p95_degradation": 0.10,
                "fault_recovered": True,
            },
        }
    }
    o3 = _objective(suite, "O3")
    assert o3["status"] == PASS
    assert o3["evidence"] == "scheduler_acceptance: 单租户故障隔离"


def test_o3_fails_when_degradation_exceeds_target():
    suite = {
        "fault_suite": {
            "tenant_fault_isolation": {
                "bystander_p95_degradation": 0.35,
                "fault_recovered": True,
            },
        }
    }
    o3 = _objective(suite, "O3")
    assert o3["status"] == FAIL


def test_o3_inconclusive_without_fault_evidence():
    o3 = _objective({"runs": []}, "O3")
    assert o3["status"] == INCONCLUSIVE


def test_acceptance_by_name_maps_checks_by_name():
    suite = {
        "acceptance": {
            "checks": [
                {"name": "Tenant fairness (Jain)", "status": PASS, "observed": 1.0},
                {"name": "Search 优先于 Commit", "status": INCONCLUSIVE},
            ]
        }
    }
    by_name = acceptance_by_name(suite)
    assert set(by_name) == {"Tenant fairness (Jain)", "Search 优先于 Commit"}
    assert by_name["Search 优先于 Commit"]["status"] == INCONCLUSIVE


def test_acceptance_by_name_skips_unnamed_and_non_dict_entries():
    suite = {
        "acceptance": {
            "checks": [
                {"name": "A", "status": PASS},
                {"status": FAIL},  # 无 name，跳过
                "not-a-dict",  # 非字典，跳过
                {"name": "", "status": PASS},  # 空 name，跳过
            ]
        }
    }
    assert acceptance_by_name(suite) == {"A": {"name": "A", "status": PASS}}


def test_acceptance_by_name_missing_acceptance_returns_empty():
    assert acceptance_by_name({}) == {}

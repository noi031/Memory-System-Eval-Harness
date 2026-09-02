from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from performance.scheduler_acceptance import INCONCLUSIVE, PASS, _load, evaluate


class SchedulerAcceptanceTests(unittest.TestCase):
    def test_load_accepts_legacy_literal_newline_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text('{"status": "PASS"}\\n', encoding="utf-8")
            self.assertEqual({"status": "PASS"}, _load(path))

    def test_missing_specialized_evidence_is_inconclusive(self) -> None:
        result = evaluate({"runs": []})
        self.assertEqual(INCONCLUSIVE, result["overall"])
        self.assertEqual(7, len(result["checks"]))
        self.assertTrue(all(item["status"] == INCONCLUSIVE for item in result["checks"]))

    def test_priority_uses_blackbox_search_p95(self) -> None:
        result = evaluate(
            {
                "runs": [
                    {
                        "scenario": "search-priority-blackbox",
                        "status": "completed",
                        "summary": {
                            "metrics": {
                                "search": {"latency": {"p95_s": 1.2}},
                            }
                        },
                    }
                ]
            }
        )
        check = next(item for item in result["checks"] if item["name"] == "Search 优先于 Commit")
        self.assertEqual(INCONCLUSIVE, check["status"])

    def test_priority_requires_commit_flood_evidence(self) -> None:
        result = evaluate(
            {
                "runs": [
                    {
                        "scenario": "search-priority-blackbox",
                        "summary": {
                            "metrics": {
                                "search": {"latency": {"p95_s": 1.2}},
                                "commit": {"submitted": 32},
                            }
                        },
                        "status": "completed",
                    }
                ]
            }
        )
        check = next(item for item in result["checks"] if item["name"] == "Search 优先于 Commit")
        self.assertEqual(PASS, check["status"])

    def test_priority_does_not_accept_running_case(self) -> None:
        result = evaluate(
            {
                "runs": [
                    {
                        "scenario": "search-priority-blackbox",
                        "status": "running",
                        "summary": {
                            "metrics": {
                                "search": {"latency": {"p95_s": 1.2}},
                                "commit": {"submitted": 128},
                            }
                        },
                    }
                ]
            }
        )
        check = next(item for item in result["checks"] if item["name"] == "Search 优先于 Commit")
        self.assertEqual(INCONCLUSIVE, check["status"])

    def test_priority_fails_when_completed_case_exceeds_search_p95_target(self) -> None:
        result = evaluate(
            {
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
        )
        check = next(item for item in result["checks"] if item["name"] == "Search 优先于 Commit")
        self.assertEqual("FAIL", check["status"])

    def test_capacity_requires_successful_measurement(self) -> None:
        result = evaluate(
            {
                "instance_profile": "4U8G",
                "runs": [{
                    "scenario": "capacity-8",
                    "status": "completed",
                    "summary": {
                        "metrics": {
                            "search": {"submitted": 8, "success_rate": 0.8},
                            "commit": {"submitted": 8, "success_rate": 1.0},
                        }
                    },
                }],
            }
        )
        check = next(item for item in result["checks"] if item["name"] == "DAU / 最大热用户容量")
        self.assertEqual(INCONCLUSIVE, check["status"])

    def test_capacity_success_only_proves_lower_bound(self) -> None:
        result = evaluate(
            {
                "instance_profile": "4U8G",
                "runs": [{
                    "scenario": "capacity-4",
                    "status": "completed",
                    "summary": {
                        "metrics": {
                            "search": {"submitted": 4, "success_rate": 1.0},
                            "commit": {"submitted": 0},
                        }
                    },
                }],
            }
        )
        check = next(item for item in result["checks"] if item["name"] == "DAU / 最大热用户容量")
        self.assertEqual(INCONCLUSIVE, check["status"])
        self.assertEqual(
            "只有成功容量档位，缺少更高一档真实失败/超时边界；目前只能报告容量下界",
            check["reason"],
        )

    def test_capacity_passes_only_with_higher_failed_boundary(self) -> None:
        result = evaluate(
            {
                "instance_profile": "4U8G",
                "runs": [
                    {
                        "scenario": "capacity-4",
                        "status": "completed",
                        "summary": {
                            "metrics": {
                                "search": {"submitted": 4, "success_rate": 1.0},
                                "commit": {"submitted": 0},
                            }
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
                ],
            }
        )
        check = next(item for item in result["checks"] if item["name"] == "DAU / 最大热用户容量")
        self.assertEqual(PASS, check["status"])
        self.assertEqual([8], check["observed"]["capacity_boundary_levels"])

    def test_capacity_does_not_require_commit_success(self) -> None:
        result = evaluate(
            {
                "instance_profile": "4U8G",
                "runs": [
                    {
                        "scenario": "capacity-4",
                        "status": "completed",
                        "summary": {
                            "metrics": {
                                "search": {"submitted": 4, "success_rate": 1.0},
                                "commit": {"submitted": 2, "success_rate": 0.0},
                            }
                        },
                    },
                    {
                        "scenario": "capacity-8",
                        "status": "completed",
                        "summary": {
                            "metrics": {
                                "search": {"submitted": 0, "success_rate": None},
                                "commit": {"submitted": 0},
                            }
                        },
                    },
                ],
            }
        )
        check = next(item for item in result["checks"] if item["name"] == "DAU / 最大热用户容量")
        self.assertEqual(PASS, check["status"])
        self.assertEqual([4], check["observed"]["valid_capacity_levels"])

    def test_capacity_timeout_is_a_boundary_after_real_lower_level(self) -> None:
        result = evaluate(
            {
                "instance_profile": "4U8G",
                "runs": [
                    {
                        "scenario": "capacity-4",
                        "status": "completed",
                        "summary": {
                            "metrics": {
                                "search": {"submitted": 4, "success_rate": 1.0},
                            }
                        },
                    },
                    {
                        "scenario": "capacity-8",
                        "status": "TIMEOUT",
                        "runner_returncode": 124,
                        "case_timeout_s": 120.0,
                        "summary": {},
                    },
                ],
            }
        )
        check = next(item for item in result["checks"] if item["name"] == "DAU / 最大热用户容量")
        self.assertEqual(PASS, check["status"])
        self.assertEqual([8], check["observed"]["timeout_capacity_levels"])

    def test_multi_spec_needs_two_completed_profiles(self) -> None:
        result = evaluate(
            {
                "instance_profiles": [
                    {"name": "4U8G", "status": "completed"},
                    {"name": "8U16G", "status": "planned"},
                ]
            }
        )
        check = next(item for item in result["checks"] if item["name"] == "多规格实例调度配置")
        self.assertEqual(INCONCLUSIVE, check["status"])

    def test_legacy_commit_only_fairness_is_inconclusive(self) -> None:
        result = evaluate(
            {
                "acceptance": {
                    "checks": [{
                        "name": "Tenant fairness (Jain)",
                        "status": PASS,
                        "observed": 1.0,
                    }]
                }
            }
        )
        check = next(item for item in result["checks"] if item["name"] == "Commit/Search 公平性 Jain")
        self.assertEqual(INCONCLUSIVE, check["status"])

    def test_recovery_requires_real_evidence(self) -> None:
        result = evaluate(
            {"runs": []},
            recovery={"status": "PASS", "recovered": True, "replay_rate": 1.0},
        )
        check = next(item for item in result["checks"] if item["name"] == "Commit kill-9 恢复与重放")
        self.assertEqual(INCONCLUSIVE, check["status"])

    def test_recovery_fails_when_same_key_is_not_marked_replayed(self) -> None:
        result = evaluate(
            {"runs": []},
            recovery={
                "status": "INCONCLUSIVE",
                "recovered": True,
                "message_set_reconciled": True,
                "cursor_reconciliation": {"status": PASS},
                "idempotency_reconciliation": {"status": "FAIL"},
            },
        )
        check = next(item for item in result["checks"] if item["name"] == "Commit kill-9 恢复与重放")
        self.assertEqual("FAIL", check["status"])

    def test_recovery_requires_both_cursor_and_message_reconciliation(self) -> None:
        result = evaluate(
            {"runs": []},
            recovery={
                "status": PASS,
                "recovered": True,
                "replay_verified": True,
                "cursor_reconciliation": {"status": PASS},
                "message_set_reconciled": False,
                "idempotency_reconciliation": {"status": PASS},
            },
        )
        check = next(item for item in result["checks"] if item["name"] == "Commit kill-9 恢复与重放")
        self.assertEqual(INCONCLUSIVE, check["status"])

    def test_observability_requires_each_tenant_and_lane_quartet(self) -> None:
        lanes = ("http_interactive", "http_background", "http_global", "tenant_rate_limit", "commit")
        per_tenant = {
            tenant: {
                "per_lane": {
                    lane: {
                        "queued": True,
                        "wait": True,
                        "exec": True,
                        "rejected": True,
                    }
                    for lane in lanes
                }
            }
            for tenant in ("a", "b")
        }
        result = evaluate(
            {"runs": [{
                "summary": {
                    "details": {
                        "pr421_metric_coverage": {
                            "missing": [],
                            "per_tenant_quartets": per_tenant,
                        }
                    }
                },
                "status": "completed",
            }]},
            capability={
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
        )
        check = next(item for item in result["checks"] if item["name"] == "分层/分租户调度可观测性")
        self.assertEqual(PASS, check["status"])

    def test_fairness_does_not_use_search_priority_partial_commit_counts(self) -> None:
        result = evaluate(
            {
                "runs": [
                    {
                        "scenario": "search-priority-blackbox",
                        "status": "completed",
                        "summary": {
                            "metrics": {
                                "fairness": {
                                    "commit_completed_per_tenant": {
                                        "tenant-a": 2,
                                        "tenant-b": 0,
                                        "tenant-c": 0,
                                        "tenant-d": 0,
                                    }
                                }
                            }
                        },
                    }
                ]
            }
        )
        check = next(item for item in result["checks"] if item["name"] == "Commit/Search 公平性 Jain")
        self.assertEqual(INCONCLUSIVE, check["status"])

    def test_observability_accepts_pr421_bounded_lane_and_fanout_evidence(self) -> None:
        lanes = (
            "recall_engine",
            "recall_intent_llm",
            "recall_query_embedding",
            "recall_rerank",
            "commit",
        )
        result = evaluate(
            {"runs": [{
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
                "status": "completed",
            }]},
            capability={
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
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "分层/分租户调度可观测性"
        )
        self.assertEqual(PASS, check["status"])
        self.assertEqual(sorted(lanes), check["observed"]["complete_lanes"])
        self.assertEqual(["memory"], check["observed"]["complete_fanout_engines"])

    def test_observability_does_not_pass_partial_bounded_lane_evidence(self) -> None:
        result = evaluate(
            {"runs": [{
                "summary": {
                    "details": {
                        "pr421_metric_coverage": {
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
                },
                "status": "completed",
            }]},
            capability={
                "checks": [{
                    "name": "Prometheus B7 metrics",
                    "present": {},
                }]
            },
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "分层/分租户调度可观测性"
        )
        self.assertEqual(INCONCLUSIVE, check["status"])

    def test_observability_legacy_evidence_with_missing_family_is_inconclusive(self) -> None:
        lanes = (
            "http_interactive",
            "http_background",
            "http_global",
            "tenant_rate_limit",
            "commit",
        )
        result = evaluate(
            {"runs": [{
                "summary": {
                    "details": {
                        "pr421_metric_coverage": {
                            "missing": ["lane_wait"],
                            "bounded_label_violations": [],
                            "per_tenant_quartets": {
                                tenant: {
                                    "per_lane": {
                                        lane: {
                                            "queued": True,
                                            "wait": True,
                                            "exec": True,
                                            "rejected": True,
                                        }
                                        for lane in lanes
                                    }
                                }
                                for tenant in ("a", "b")
                            },
                        }
                    }
                },
                "status": "completed",
            }]},
            capability={
                "checks": [{
                    "name": "Prometheus B7 metrics",
                    "present": {},
                }]
            },
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "分层/分租户调度可观测性"
        )
        self.assertEqual(INCONCLUSIVE, check["status"])

    def test_fairness_can_be_derived_from_same_workload_run_summaries(self) -> None:
        result = evaluate(
            {
                "runs": [
                    {
                        "scenario": "mixed",
                        "status": "completed",
                        "summary": {
                            "metrics": {
                                "fairness": {
                                    "commit_completed_per_tenant": {
                                        "a": 2,
                                        "b": 2,
                                        "c": 1,
                                    },
                                },
                                "per_tenant": {
                                    "a": {
                                        "commit": {"completed": 2},
                                        "search": {"latency": {"p95_s": 1.0}},
                                    },
                                    "b": {
                                        "commit": {"completed": 2},
                                        "search": {"latency": {"p95_s": 1.0}},
                                    },
                                    "c": {
                                        "commit": {"completed": 1},
                                        "search": {"latency": {"p95_s": 1.0}},
                                    },
                                }
                            }
                        },
                    }
                ]
            }
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "Commit/Search 公平性 Jain"
        )
        self.assertEqual(PASS, check["status"])
        self.assertAlmostEqual(0.9259, check["observed"]["jain"], places=4)

    def test_fairness_prefers_broader_tenant_coverage_over_priority_order(self) -> None:
        def run(scenario: str, tenants: dict[str, int]) -> dict:
            return {
                "scenario": scenario,
                "status": "completed",
                "summary": {
                    "metrics": {
                        "fairness": {"commit_completed_per_tenant": tenants},
                        "per_tenant": {
                            tenant: {
                                "commit": {"completed": count},
                                "search": {"latency": {"p95_s": 1.0}},
                            }
                            for tenant, count in tenants.items()
                        },
                    }
                },
            }

        result = evaluate(
            {
                "runs": [
                    run("search-priority-blackbox", {"a": 2, "b": 2}),
                    run("tenant-skew", {"a": 2, "b": 2, "c": 2, "d": 2}),
                ]
            }
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "Commit/Search 公平性 Jain"
        )
        self.assertEqual(PASS, check["status"])
        self.assertEqual("tenant-skew", check["observed"]["scenario"])


if __name__ == "__main__":
    unittest.main()

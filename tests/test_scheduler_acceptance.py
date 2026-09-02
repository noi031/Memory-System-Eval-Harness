from __future__ import annotations

import unittest

from performance.scheduler_acceptance import INCONCLUSIVE, PASS, evaluate


class SchedulerAcceptanceTests(unittest.TestCase):
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

    def test_recovery_requires_real_evidence(self) -> None:
        result = evaluate(
            {"runs": []},
            recovery={"status": "PASS", "recovered": True, "replay_rate": 1.0},
        )
        check = next(item for item in result["checks"] if item["name"] == "Commit kill-9 恢复与重放")
        self.assertEqual(PASS, check["status"])


if __name__ == "__main__":
    unittest.main()

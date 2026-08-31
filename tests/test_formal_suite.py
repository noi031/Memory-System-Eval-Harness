from __future__ import annotations

import unittest
import json
from pathlib import Path

from stress.echomem.formal_suite import SCENARIOS, complete_scenarios, report6_scenarios


class Report6ScenarioTests(unittest.TestCase):
    def test_complete_contains_both_plan_catalogs(self) -> None:
        scenarios = complete_scenarios()

        self.assertEqual(set(report6_scenarios()) | set(SCENARIOS), set(scenarios))
        self.assertEqual(26, len(scenarios))

    def test_capacity_ladder_has_expected_points(self) -> None:
        scenarios = SCENARIOS

        self.assertEqual(
            {2, 4, 8, 16, 32},
            {scenarios[f"capacity-{count}"]["tenants"] for count in (2, 4, 8, 16, 32)},
        )
        self.assertTrue(
            all(
                scenarios[f"capacity-{count}"]["search_rps"] == count
                for count in (2, 4, 8, 16, 32)
            )
        )

    def test_search_priority_blackbox_is_server_contention_case(self) -> None:
        case = SCENARIOS["search-priority-blackbox"]

        self.assertTrue(case["blackbox_search_priority"])
        self.assertTrue(case["commit_barrier"])
        self.assertEqual(128, case["commit_barrier_count"])
        self.assertGreater(case["search_rps"], 0)
        self.assertEqual(0.0, case["commit_rpm"])

    def test_complete_capacity_points_are_executable(self) -> None:
        scenarios = complete_scenarios()

        for count in (2, 4, 8, 16, 32):
            self.assertIn(f"capacity-{count}", scenarios)
            self.assertEqual(count, scenarios[f"capacity-{count}"]["tenants"])

    def test_instance_profile_example_matches_available_machine_sizes(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "stress"
            / "echomem"
            / "instance-profiles.example.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        profiles = payload["profiles"]
        self.assertEqual(
            ["4U8G", "8U16G"],
            [item["name"] for item in profiles],
        )
        self.assertEqual(
            [
                ("4 vCPU", "8 GiB"),
                ("8 vCPU", "16 GiB"),
            ],
            [
                (item["resource_profile"]["cpu"], item["resource_profile"]["memory"])
                for item in profiles
            ],
        )

    def test_report6_contains_the_full_matrix(self) -> None:
        scenarios = report6_scenarios()

        self.assertEqual(12, len(scenarios))
        self.assertEqual(
            {
                "A@1", "A@2",
                "B@1", "B@2",
                "C8:1@1", "C8:1@2",
                "C4:1@1", "C4:1@2",
                "C1:1@1", "C1:1@2",
                "D@1", "D@2",
            },
            set(scenarios),
        )
        self.assertTrue(all(item["tenants"] == 8 for item in scenarios.values()))
        self.assertTrue(all(item["duration_s"] == 60 for item in scenarios.values()))
        self.assertTrue(
            all(item["sessions_per_tenant"] == 2 for item in scenarios.values())
        )
        self.assertTrue(
            all(item["messages_per_session"] == 10 for item in scenarios.values())
        )

    def test_report6_mixed_ratios_are_exact_over_one_minute(self) -> None:
        scenarios = report6_scenarios()

        for concurrency in (1, 2):
            for ratio, factor in (("8:1", 8), ("4:1", 4), ("1:1", 1)):
                item = scenarios[f"C{ratio}@{concurrency}"]
                search_per_minute = item["search_rps"] * 60
                commit_per_minute = item["tenants"] * item["commit_rpm"]
                self.assertEqual(
                    factor,
                    search_per_minute / commit_per_minute,
                )

    def test_report6_d_uses_32_commits_over_ten_seconds(self) -> None:
        scenarios = report6_scenarios()

        for concurrency in (1, 2):
            item = scenarios[f"D@{concurrency}"]
            self.assertEqual(32, item["commit_barrier_count"])
            self.assertEqual(10.0, item["commit_burst_window_s"])
            self.assertEqual(1, item["commit_barrier_waves"])


if __name__ == "__main__":
    unittest.main()

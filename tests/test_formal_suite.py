from __future__ import annotations

import unittest

from stress.echomem.formal_suite import report6_scenarios


class Report6ScenarioTests(unittest.TestCase):
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

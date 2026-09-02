from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from performance.objective_suite import (
    QUICK_SCENARIOS,
    load_profiles,
    objective_statuses,
    render_report,
)


class ObjectiveSuiteTests(unittest.TestCase):
    def test_load_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps({"profiles": [{"name": "4U8G"}]}), encoding="utf-8")
            self.assertEqual(["4U8G"], [item["name"] for item in load_profiles(path)])

    def test_quick_matrix_is_bounded_and_contains_priority(self) -> None:
        self.assertIn("search-priority-blackbox", QUICK_SCENARIOS)
        self.assertIn("capacity-2", QUICK_SCENARIOS)
        self.assertNotIn("soak", QUICK_SCENARIOS)

    def test_missing_evidence_stays_inconclusive(self) -> None:
        objectives = objective_statuses(
            {"profile_name": "4U8G", "runs": []},
            recovery_configured=False,
            metrics_configured=False,
        )
        self.assertTrue(all(item["status"] == "INCONCLUSIVE" for item in objectives if item["id"] != "O2"))
        self.assertEqual("PASS", next(item["status"] for item in objectives if item["id"] == "O2"))

    def test_render_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            render_report({"created_at": "now", "profiles": []}, path)
            self.assertTrue(path.is_file())
            self.assertIn("七项目标", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

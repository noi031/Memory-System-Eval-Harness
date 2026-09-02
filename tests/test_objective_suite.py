from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from performance.objective_suite import (
    QUICK_SCENARIOS,
    _first_completed_commit_csv,
    load_profiles,
    objective_statuses,
    run_command,
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

    def test_first_completed_commit_csv_finds_real_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "case" / "commit_results.csv"
            csv_path.parent.mkdir()
            csv_path.write_text(
                "tenant,session_id,archive_id,status\n0,s1,a1,completed\n",
                encoding="utf-8",
            )
            self.assertEqual((csv_path, "0"), _first_completed_commit_csv(root))

    def test_missing_evidence_stays_inconclusive(self) -> None:
        objectives = objective_statuses(
            {"profile_name": "4U8G", "runs": []},
            recovery_configured=False,
            metrics_configured=False,
        )
        self.assertTrue(all(item["status"] == "INCONCLUSIVE" for item in objectives if item["id"] != "O2"))
        self.assertEqual(
            "INCONCLUSIVE",
            next(item["status"] for item in objectives if item["id"] == "O2"),
        )

    def test_recovery_objective_requires_idempotency_evidence(self) -> None:
        suite = {
            "profile_name": "4U8G",
            "commit_recovery": {
                "status": "PASS",
                "message_reconciliation": {"status": "PASS"},
                "cursor_reconciliation": {"status": "PASS"},
            },
        }
        objectives = objective_statuses(
            suite,
            recovery_configured=True,
            metrics_configured=False,
        )
        self.assertEqual(
            "INCONCLUSIVE",
            next(item["status"] for item in objectives if item["id"] == "O6"),
        )

        suite["commit_recovery"]["idempotency_reconciliation"] = {"status": "PASS"}
        objectives = objective_statuses(
            suite,
            recovery_configured=True,
            metrics_configured=False,
        )
        self.assertEqual(
            "PASS",
            next(item["status"] for item in objectives if item["id"] == "O6"),
        )

    def test_render_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            render_report({"created_at": "now", "profiles": []}, path)
            self.assertTrue(path.is_file())
            self.assertIn("七项目标", path.read_text(encoding="utf-8"))

    def test_run_command_redacts_secret_values(self) -> None:
        result = run_command(
            ["python3", "-c", "print('ok')", "secret-value"],
            timeout_s=10,
            redact_values={"secret-value"},
        )
        self.assertEqual("PASS", result["status"])
        self.assertNotIn("secret-value", " ".join(result["command"]))


if __name__ == "__main__":
    unittest.main()

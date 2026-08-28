from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from stress.echomem.cursor_reconcile import reconcile
from stress.echomem.fault_injection import NOT_IMPLEMENTED, run_control
from stress.echomem.k6_reconcile import reconcile as reconcile_k6


class FailureToolTests(unittest.TestCase):
    def test_fault_control_without_real_control_is_not_implemented(self) -> None:
        class Args:
            command = ""
            endpoint = ""
            container = ""
            action = ""
            signal = "KILL"
            timeout_s = 1

        self.assertEqual(NOT_IMPLEMENTED, run_control(Args())["status"])

    def test_k6_reconcile_rejects_missing_runner_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "k6.json"
            summary.write_text(json.dumps({"real_http": True}), encoding="utf-8")
            result = reconcile_k6(summary, root / "run")
            self.assertEqual("INCONCLUSIVE", result["status"])

    def test_cursor_reconcile_compares_message_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commits = root / "commits.csv"
            with commits.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["status", "session_id", "archive_id", "message_ids"])
                writer.writeheader()
                writer.writerow({
                    "status": "completed",
                    "session_id": "s1",
                    "archive_id": "a1",
                    "message_ids": json.dumps(["m1", "m2"]),
                })

            class Args:
                commit_csv = commits
                cursor_url_template = ""
                auth_key = ""
                auth_header = "X-API-Key"
                timeout_s = 1

            result = reconcile(Args())
            self.assertEqual("NOT_IMPLEMENTED", result["status"])


if __name__ == "__main__":
    unittest.main()

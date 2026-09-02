from __future__ import annotations

import csv
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from performance.probes.cursor_reconcile import reconcile
from performance.probes.cursor_reconcile import values_from_payload
from performance.probes.capability_probe import classify_probe, run as run_capability
from performance.probes.capability_probe import request as capability_request
from performance.probes.blackbox_contract_probe import request as blackbox_request
from performance.probes.fault_injection import NOT_IMPLEMENTED, run_control


class FailureToolTests(unittest.TestCase):
    def test_metrics_probe_preserves_full_prometheus_payload(self) -> None:
        prefix = "# HELP old_metric " + ("x" * 5000)
        raw = prefix + "\nechomem_lane_queued 1\n"

        class Response:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return raw.encode()

        with patch("urllib.request.urlopen", return_value=Response()):
            capability = capability_request(
                "http://example.test", "/metrics", timeout_s=1, preserve_raw=True
            )
            blackbox = blackbox_request(
                "http://example.test", "/metrics", auth_key="",
                auth_header="X-Auth-Key", timeout_s=1, preserve_raw=True
            )

        self.assertIn("echomem_lane_queued", capability["payload"]["raw"])
        self.assertIn("echomem_lane_queued", blackbox["payload"]["raw"])

    def test_capability_probe_classifies_404_as_not_implemented_and_unconfigured_as_inconclusive(self) -> None:
        self.assertEqual(
            "NOT_IMPLEMENTED",
            classify_probe("optional", {"status_code": 404, "payload": {}})["status"],
        )
        class Args:
            base_url = "http://127.0.0.1:1"
            auth_key = ""
            auth_key_env = "MISSING_KEY"
            auth_header = "X-API-Key"
            health_path = "/health"
            metrics_path = "/metrics"
            cursor_path = ""
            operation_path = ""
            operation_keys = ["operation_id"]
            conflict_path = ""
            conflict_keys = ["version"]
            ttl_path = ""
            ttl_keys = ["ttl_seconds"]
            engine_path = ""
            engine_keys = ["status"]
            fault_path = ""
            fault_keys = ["status"]
            session_id = ""
            timeout_s = 0.01
        result = run_capability(Args())
        self.assertEqual("INCONCLUSIVE", result["status"])
        self.assertGreaterEqual(result["summary"]["inconclusive"], 6)

    def test_cursor_payload_extracts_nested_operation_and_archive(self) -> None:
        messages, archives, operations = values_from_payload({
            "result": {
                "message_set": {
                    "items": [{"message_id": "m1", "archive_id": "a1", "operation_id": "o1"}]
                }
            }
        })
        self.assertEqual({"m1"}, messages)
        self.assertEqual({"a1"}, archives)
        self.assertEqual({"o1"}, operations)

    def test_fault_control_without_real_control_is_inconclusive(self) -> None:
        class Args:
            command = ""
            endpoint = ""
            container = ""
            action = ""
            signal = "KILL"
            timeout_s = 1

        self.assertEqual("INCONCLUSIVE", run_control(Args())["status"])

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
                base_url = ""
                cursor_uri_template = "echo://sessions/{session}/current/commit_cursor.json"
                auth_key = ""
                auth_header = "X-API-Key"
                timeout_s = 1

            result = reconcile(Args())
            self.assertEqual("INCONCLUSIVE", result["status"])


if __name__ == "__main__":
    unittest.main()

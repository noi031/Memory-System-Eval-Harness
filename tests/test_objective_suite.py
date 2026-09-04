from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from performance.targets.echomem.probes.limit_failure import (
    auth_key,
    classify_response,
    discover_sessions,
    error_class,
    load_tenants,
    metrics_coverage,
    response_error_detail,
)


class ObjectiveSuiteTests(unittest.TestCase):
    def test_limit_probe_accepts_explicit_auth_key(self) -> None:
        self.assertEqual(
            "key-a",
            auth_key({"auth_key": "key-a", "auth_key_env": "MISSING_KEY"}),
        )

    def test_limit_probe_loads_explicit_tenant_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tenants.json"
            path.write_text(
                json.dumps(
                    {"tenants": [{"tenant_id": "a", "user_id": "u", "auth_key": "key-a"}]}
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                [{"tenant_id": "a", "user_id": "u", "auth_key_env": "", "auth_key": "key-a"}],
                load_tenants(path),
            )

    def test_limit_probe_discovers_sessions_from_numeric_tenant_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "run" / "requests.csv"
            csv_path.parent.mkdir()
            csv_path.write_text(
                "tenant,session_id,op,status\n"
                "0,session-a,open,ok\n"
                "1,session-b,open,ok\n",
                encoding="utf-8",
            )
            tenants = [
                {"tenant_id": "tenant-a"},
                {"tenant_id": "tenant-b"},
            ]
            self.assertEqual(
                {
                    "tenant-a": "session-a",
                    "tenant-b": "session-b",
                },
                discover_sessions(root, tenants),
            )

    def test_limit_probe_discovers_sessions_from_tenant_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "run" / "requests.csv"
            csv_path.parent.mkdir()
            csv_path.write_text(
                "tenant,session_id,op,status\n"
                "tenant-a,session-a,open,ok\n"
                "tenant-b,session-b,open,ok\n",
                encoding="utf-8",
            )
            tenants = [
                {"tenant_id": "tenant-a"},
                {"tenant_id": "tenant-b"},
            ]
            self.assertEqual(
                {
                    "tenant-a": "session-a",
                    "tenant-b": "session-b",
                },
                discover_sessions(root, tenants),
            )

    def test_limit_probe_preserves_error_class_and_detail(self) -> None:
        self.assertEqual("request_or_admission_4xx", error_class(400))
        self.assertEqual("server_error", error_class(503))
        self.assertEqual("transport_error", error_class(None))
        self.assertEqual(
            "invalid session",
            response_error_detail(json.dumps({"detail": "invalid session"})),
        )
        self.assertEqual(
            "admission_rejected",
            classify_response(400, "", "too many recall requests in flight"),
        )
        self.assertEqual(
            "request_or_admission_4xx",
            classify_response(400, "", "invalid session"),
        )

    def test_metrics_coverage_uses_real_samples_not_help_lines(self) -> None:
        raw = (
            "# HELP echomem_lane_wait_seconds wait\n"
            "# TYPE echomem_lane_wait_seconds histogram\n"
            'echomem_lane_wait_seconds_bucket{lane="recall_engine",le="1"} 1\n'
            'echomem_lane_rejected_total{lane="recall_engine",reason_code="queue_full"} 1\n'
            'echomem_engine_fanout_exec_seconds_count{engine="atomic_engine"} 1\n'
        )
        coverage = metrics_coverage(raw)
        self.assertIn("echomem_lane_wait_seconds", coverage["present"])
        self.assertIn("echomem_lane_rejected_total", coverage["present"])
        self.assertIn("echomem_engine_fanout_exec_seconds", coverage["present"])
        self.assertFalse(coverage["present"]["echomem_lane_queued"])


if __name__ == "__main__":
    unittest.main()

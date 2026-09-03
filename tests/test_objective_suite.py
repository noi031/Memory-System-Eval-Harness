from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from performance.objective_suite import (
    QUICK_SCENARIOS,
    _first_completed_commit_csv,
    _resolve_auth_key,
    _acquire_output_lock,
    _materialize_fault_plan,
    _preserve_probe_status,
    _resolve_tenant_id,
    _formal_run_counts,
    load_env_file,
    load_profiles,
    objective_statuses,
    run_command,
    render_report,
)
from performance.probes.limit_failure_probe import (
    auth_key,
    classify_response,
    discover_sessions,
    error_class,
    load_tenants,
    metrics_coverage,
    response_error_detail,
)
from performance.formal_suite import SCENARIOS


class ObjectiveSuiteTests(unittest.TestCase):
    def test_load_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps({"profiles": [{"name": "4U8G"}]}), encoding="utf-8")
            self.assertEqual(["4U8G"], [item["name"] for item in load_profiles(path)])

    def test_load_env_file_accepts_export_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.env"
            path.write_text(
                "# credentials\nexport MODEL_KEY='secret-value'\nEMPTY=\n",
                encoding="utf-8",
            )
            self.assertEqual(
                {"MODEL_KEY": "secret-value", "EMPTY": ""},
                load_env_file(path),
            )

    def test_resolve_tenant_id_falls_back_when_profile_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tenants.json"
            path.write_text(
                json.dumps(
                    {
                        "tenants": [
                            {"tenant_id": "tenant-live-a"},
                            {"tenant_id": "tenant-live-b"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                "tenant-live-a",
                _resolve_tenant_id(path, "stress-a"),
            )

    def test_quick_matrix_is_bounded_and_contains_priority(self) -> None:
        self.assertIn("search-priority-blackbox", QUICK_SCENARIOS)
        self.assertIn("capacity-2", QUICK_SCENARIOS)
        self.assertIn("capacity-8", QUICK_SCENARIOS)
        self.assertIn("fairness-bounded", QUICK_SCENARIOS)
        self.assertIn("tenant-skew", QUICK_SCENARIOS)
        self.assertNotIn("A@1", QUICK_SCENARIOS)
        self.assertNotIn("B@1", QUICK_SCENARIOS)
        self.assertNotIn("D@1", QUICK_SCENARIOS)
        self.assertNotIn("soak", QUICK_SCENARIOS)
        self.assertEqual(0.0, SCENARIOS["capacity-2"]["quick_commit_rpm"])
        self.assertEqual(0.0, SCENARIOS["capacity-4"]["quick_commit_rpm"])

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

    def test_resolve_auth_key_follows_numeric_tenant_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tenants.json"
            path.write_text(
                json.dumps(
                    {
                        "tenants": [
                            {"tenant_id": "a", "auth_key": "key-a"},
                            {"tenant_id": "b", "auth_key": "key-b"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(("key-b", ""), _resolve_auth_key(path, "1"))

    def test_resolve_auth_key_follows_tenant_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tenants.json"
            path.write_text(
                json.dumps(
                    {
                        "tenants": [
                            {"tenant_id": "a", "auth_key": "key-a"},
                            {"tenant_id": "b", "auth_key": "key-b"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(("key-b", ""), _resolve_auth_key(path, "b"))

    def test_missing_evidence_stays_inconclusive(self) -> None:
        objectives = objective_statuses(
            {"profile_name": "4U8G", "runs": []},
            recovery_configured=False,
            metrics_configured=False,
        )
        self.assertEqual(6, len(objectives))
        self.assertTrue(all(item["status"] == "INCONCLUSIVE" for item in objectives))

    def test_probe_inconclusive_is_not_reported_as_process_failure(self) -> None:
        execution = {"status": "FAIL", "returncode": 2}
        self.assertEqual(
            "INCONCLUSIVE",
            _preserve_probe_status(execution, {"status": "INCONCLUSIVE"})["status"],
        )

    def test_profile_can_enable_blackbox_probes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "profiles.json"
            path.write_text(
                json.dumps({
                    "profiles": [{
                        "name": "4U8G",
                        "missing_cases": {"enabled": True, "max_tenants": 1},
                        "concurrent_commit": {
                            "enabled": True, "concurrency": 4, "timeout_s": 120
                        },
                    }]
                }),
                encoding="utf-8",
            )
            profile = load_profiles(path)[0]
            self.assertTrue(profile["missing_cases"]["enabled"])
            self.assertEqual(4, profile["concurrent_commit"]["concurrency"])

    def test_profile_can_request_real_quick_seed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(
                json.dumps({
                    "profiles": [{
                        "name": "4U8G",
                        "quick_include_seed": False,
                    }]
                }),
                encoding="utf-8",
            )
            profile = load_profiles(path)[0]
            self.assertFalse(profile["quick_include_seed"])

    def test_recovery_profile_requires_accepted_202(self) -> None:
        profile_path = (
            Path(__file__).parents[1]
            / "performance"
            / "instance-profile-4u8g.audit.server.example.json"
        )
        profile = load_profiles(profile_path)[0]
        self.assertTrue(profile["commit_recovery"]["require_accepted_202"])

    def test_probe_pass_failure_status_is_preserved(self) -> None:
        execution = {"status": "FAIL", "returncode": 2}
        self.assertEqual(
            "FAIL",
            _preserve_probe_status(execution, {"status": "PASS"})["status"],
        )

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
            next(item["status"] for item in objectives if item["id"] == "O5"),
        )

        suite["commit_recovery"].update(
            {
                "accepted_202": True,
                "recovered": True,
                "commit_terminal": [{"state": "completed"}],
                "order_reconciliation": {"status": "PASS"},
                "idempotency_reconciliation": {"status": "INCONCLUSIVE"},
                "idempotency_replay": {
                    "same_archive": True,
                    "replayed": False,
                },
            }
        )
        objectives = objective_statuses(
            suite,
            recovery_configured=True,
            metrics_configured=False,
        )
        self.assertEqual(
            "PASS",
            next(item["status"] for item in objectives if item["id"] == "O5"),
        )

    def test_render_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            render_report({"created_at": "now", "profiles": []}, path)
            self.assertTrue(path.is_file())
            self.assertIn("六项 4U8G 目标", path.read_text(encoding="utf-8"))

    def test_single_profile_does_not_pass_multi_spec(self) -> None:
        objectives = objective_statuses(
            {
                "instance_profiles": [{
                    "name": "4U8G",
                    "status": "completed",
                    "completed_runs": 1,
                }],
                "runs": [],
            },
            recovery_configured=False,
            metrics_configured=False,
        )
        self.assertEqual(6, len(objectives))
        self.assertTrue(all(item["status"] == "INCONCLUSIVE" for item in objectives))

    def test_only_completed_formal_runs_count_as_completed_profile_evidence(self) -> None:
        suite = {
            "runs": [
                {
                    "status": "TIMEOUT",
                    "summary": {
                        "metrics": {
                            "search": {"submitted": 10},
                            "commit": {"submitted": 2},
                        }
                    },
                },
                {
                    "status": "completed",
                    "summary": {
                        "metrics": {
                            "search": {"submitted": 10},
                            "commit": {"submitted": 2},
                        }
                    },
                },
            ]
        }
        self.assertEqual((1, 2), _formal_run_counts(suite))

    def test_run_command_redacts_secret_values(self) -> None:
        result = run_command(
            ["python3", "-c", "print('ok')", "secret-value"],
            timeout_s=10,
            redact_values={"secret-value"},
        )
        self.assertEqual("PASS", result["status"])
        self.assertNotIn("secret-value", " ".join(result["command"]))

    def test_materialize_fault_plan_uses_selected_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fault-plan.json"
            output = root / "run" / "fault-plan.resolved.json"
            source.write_text(
                json.dumps({
                    "faults": [{"endpoint": "${BASE_URL}/fault/llm-500"}],
                    "recovery": {"health_url": "${BASE_URL}/health"},
                }),
                encoding="utf-8",
            )
            _materialize_fault_plan(
                source,
                base_url="http://127.0.0.1:18187/",
                output_path=output,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                "http://127.0.0.1:18187/fault/llm-500",
                payload["faults"][0]["endpoint"],
            )
            self.assertEqual(
                "http://127.0.0.1:18187/health",
                payload["recovery"]["health_url"],
            )

    def test_skip_run_can_use_explicit_suite_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = root / "suite.json"
            suite.write_text("{}", encoding="utf-8")
            profiles = root / "profiles.json"
            profiles.write_text(
                json.dumps({"profiles": [{"name": "4U8G"}]}),
                encoding="utf-8",
            )
            self.assertTrue(suite.is_file())
            self.assertEqual("4U8G", load_profiles(profiles)[0]["name"])

    def test_output_lock_rejects_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _acquire_output_lock(root)
            try:
                with self.assertRaisesRegex(RuntimeError, "already locked"):
                    _acquire_output_lock(root)
            finally:
                first.close()

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

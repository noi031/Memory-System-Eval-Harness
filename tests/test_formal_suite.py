from __future__ import annotations

import argparse
import csv
import json
import tempfile
import unittest
from pathlib import Path

from performance.formal_suite import (
    SCENARIOS,
    _build_case_command,
    _derive_case_summary,
    _write_case_csvs,
    complete_scenarios,
    report6_scenarios,
)


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
            / "performance"
            / "instance-profiles.example.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        profiles = payload["profiles"]
        self.assertEqual(
            ["4U8G", "8U16G", "16U32G", "32U", "64G"],
            [item["name"] for item in profiles],
        )
        self.assertEqual(
            [
                ("4 vCPU", "8 GiB"),
                ("8 vCPU", "16 GiB"),
                ("16 vCPU", "32 GiB"),
                ("32 vCPU", "32 GiB"),
                ("8 vCPU", "64 GiB"),
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


class FormalSuiteAdapterTests(unittest.TestCase):
    """run_stress 适配层：case→CLI 映射与契约推导。"""

    @staticmethod
    def _args(**overrides) -> argparse.Namespace:
        args = argparse.Namespace(
            base_url="http://127.0.0.1:8010",
            commit_timeout_s=120.0,
            commit_max_attempts=3,
            commit_retry_backoff_s=2.0,
            barrier_wave_size=32,
            instance_profile="4U8G",
            preflight_config="",
            no_server_metrics=False,
        )
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def _command(self, case: dict, output: Path) -> list:
        return _build_case_command(
            self._args(),
            case,
            output.parent / "tenants.json",
            output,
            60.0,
        )

    def _flag_value(self, command: list, flag: str) -> str:
        self.assertIn(flag, command)
        return command[command.index(flag) + 1]

    def test_build_case_command_maps_rate_based_case_to_K(self) -> None:
        case = {
            "tenants": 4,
            "search_rps": 8.0,
            "commit_rpm": 2.0,
            "sessions_per_tenant": 4,
            "messages_per_session": 3,
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            command = self._command(case, output)
            self.assertEqual("K", self._flag_value(command, "--scenarios"))
            self.assertEqual("fixed-rps", self._flag_value(command, "--mode"))
            self.assertEqual("8.0", self._flag_value(command, "--rps"))
            self.assertEqual("2.0", self._flag_value(command, "--commit-rpm"))
            self.assertEqual("4", self._flag_value(command, "--tenants"))
            self.assertEqual(str(output / "run"), self._flag_value(command, "--out-dir"))

    def test_build_case_command_can_reuse_existing_data_without_seed(self) -> None:
        case = {
            "tenants": 4,
            "search_rps": 8.0,
            "commit_rpm": 0.0,
            "sessions_per_tenant": 4,
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            command = _build_case_command(
                self._args(reuse_existing_data=True),
                case,
                output.parent / "tenants.json",
                output,
                15.0,
            )
            self.assertIn("--skip-seed", command)

    def test_build_case_command_maps_zipf_barrier_to_S(self) -> None:
        case = {
            "tenants": 4,
            "search_rps": 4.0,
            "commit_barrier": True,
            "commit_barrier_count": 160,
            "commit_tenant_distribution": "zipf",
            "commit_zipf_exponent": 2.0,
            "sessions_per_tenant": 40,
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            command = self._command(case, output)
            self.assertEqual("S", self._flag_value(command, "--scenarios"))
            self.assertEqual("4", self._flag_value(command, "--seed-sessions-per-tenant"))
            self.assertEqual("4", self._flag_value(command, "--barrier-prepare-concurrency"))
            self.assertEqual("160", self._flag_value(command, "--commit-barrier-count"))
            self.assertEqual("zipf", self._flag_value(command, "--commit-tenant-distribution"))
            self.assertEqual("2.0", self._flag_value(command, "--commit-zipf-exponent"))
            self.assertEqual("fixed-rps", self._flag_value(command, "--mode"))

    def test_build_case_command_maps_explicit_skew_to_S(self) -> None:
        case = {
            "tenants": 4,
            "commit_barrier": True,
            "commit_barrier_count": 260,
            "commit_tenant_distribution": "explicit",
            "commit_tenant_counts": [200, 20, 20, 20],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            command = self._command(case, output)
            self.assertEqual("S", self._flag_value(command, "--scenarios"))
            self.assertEqual(
                "200,20,20,20",
                self._flag_value(command, "--commit-tenant-counts"),
            )

    def test_build_case_command_maps_multi_wave_to_H(self) -> None:
        case = {
            "tenants": 4,
            "commit_barrier": True,
            "commit_barrier_count": 128,
            "commit_barrier_waves": 3,
            "commit_barrier_cooldown_s": 10.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            command = self._command(case, output)
            self.assertEqual("H", self._flag_value(command, "--scenarios"))
            self.assertEqual("3", self._flag_value(command, "--commit-barrier-waves"))
            self.assertEqual("10.0", self._flag_value(command, "--commit-barrier-cooldown-s"))

    def test_build_case_command_maps_burst_window_to_D(self) -> None:
        case = {
            "tenants": 4,
            "commit_barrier": True,
            "commit_barrier_count": 32,
            "commit_barrier_waves": 1,
            "commit_barrier_cooldown_s": 0.0,
            "commit_burst_window_s": 10.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            command = self._command(case, output)
            self.assertEqual("D", self._flag_value(command, "--scenarios"))
            self.assertEqual("32", self._flag_value(command, "--burst-commits"))
            self.assertEqual("10.0", self._flag_value(command, "--burst-window-s"))

    @staticmethod
    def _write_request_rows(run_dir: Path, rows: list[dict[str, str]]) -> None:
        headers = [
            "scene", "step_conc", "tenant_idx", "op", "stage_ms", "status",
            "error_type", "ts_ms", "session_id", "extra", "retry_count", "retried",
            "retry_total_wait_ms", "final_success", "retry_after_s", "reason_code",
            "message_id", "content_hash", "content_bytes", "hit_count", "real_recall",
            "quality_ok", "degraded", "query",
        ]
        with (run_dir / "requests.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                data = dict.fromkeys(headers, "")
                data.update(row)
                writer.writerow(data)

    def test_derive_case_summary_builds_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            run_dir.mkdir(parents=True)
            (run_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "commit_durability": {"commit_success_rate": 1.0},
                        "search_quality": {"anchor_total": 10, "quality_failures": 0},
                    }
                ),
                encoding="utf-8",
            )
            self._write_request_rows(
                run_dir,
                [
                    {"tenant_idx": "0", "op": "read", "stage_ms": "100.0", "status": "ok", "session_id": "ses-1"},
                    {"tenant_idx": "0", "op": "read", "stage_ms": "300.0", "status": "ok", "session_id": "ses-2"},
                    {"tenant_idx": "0", "op": "read", "stage_ms": "500.0", "status": "error", "error_type": "timeout", "session_id": "ses-3"},
                    {"tenant_idx": "0", "op": "commit_submit", "stage_ms": "50.0", "status": "ok", "session_id": "ses-1"},
                    {"tenant_idx": "0", "op": "commit_submit", "stage_ms": "60.0", "status": "ok", "session_id": "ses-2"},
                    {"tenant_idx": "0", "op": "commit_done", "stage_ms": "2000.0", "status": "ok", "session_id": "ses-1"},
                    {"tenant_idx": "0", "op": "commit_done", "stage_ms": "4000.0", "status": "ok", "session_id": "ses-2"},
                ],
            )
            with (run_dir / "metrics_samples.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["ts", "metric", "labels", "value"])
                writer.writerow(["1.0", "echomem_lane_queued", json.dumps({"lane": "http_interactive"}), "1"])
                writer.writerow(
                    [
                        "1.0",
                        "echomem_lane_exec_seconds",
                        json.dumps({"lane": "http_interactive", "tenant_id": "tenant-a"}),
                        "0.5",
                    ]
                )
                writer.writerow(["1.0", "other_metric", "{}", "1"])

            derived = _derive_case_summary(run_dir, identity_independent=True)
            self.assertEqual("completed", derived["status"])
            search = derived["metrics"]["search"]
            self.assertEqual(3, search["submitted"])
            self.assertEqual(2, search["succeeded"])
            self.assertEqual(1, search["errors"])
            self.assertAlmostEqual(2 / 3, search["success_rate"])
            self.assertEqual(0.2, search["latency"]["mean_s"])
            self.assertEqual(0.2, search["latency"]["p50_s"])
            self.assertEqual(0.29, search["latency"]["p95_s"])
            self.assertEqual(0.298, search["latency"]["p99_s"])
            self.assertEqual(0, search["rate_limited_count"])
            self.assertEqual(10, search["quality_asserted"])
            self.assertEqual(0, search["quality_failures"])
            commit = derived["metrics"]["commit"]
            self.assertEqual(2, commit["submitted"])
            self.assertEqual(2, commit["completed"])
            self.assertEqual(0, commit["failed"])
            self.assertEqual(1.0, commit["success_rate"])
            self.assertEqual(
                {"0": 2},
                derived["metrics"]["fairness"]["commit_completed_per_tenant"],
            )
            self.assertEqual(
                {"0": {"commit": {"submitted": 2, "completion": {"p50_s": 3.0}}}},
                derived["metrics"]["per_tenant"],
            )
            self.assertEqual("independent_auth_keys", derived["details"]["identity_mode"])
            coverage = derived["details"]["pr421_metric_coverage"]
            self.assertTrue(coverage["present"]["echomem_lane_queued"])
            self.assertTrue(coverage["present"]["echomem_lane_exec_seconds"])
            self.assertIn("echomem_lane_wait_seconds", coverage["missing"])
            self.assertIn("echomem_engine_fanout_skipped_total", coverage["missing"])
            self.assertEqual(
                [
                    {
                        "metric": "echomem_lane_exec_seconds",
                        "label": "tenant_id",
                        "value": "tenant-a",
                    }
                ],
                coverage["bounded_label_violations"],
            )

    def test_derive_case_summary_without_metrics_omits_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            run_dir.mkdir(parents=True)
            (run_dir / "summary.json").write_text(
                json.dumps({"status": "completed"}),
                encoding="utf-8",
            )
            self._write_request_rows(run_dir, [])
            derived = _derive_case_summary(run_dir, identity_independent=False)
            self.assertNotIn("pr421_metric_coverage", derived["details"])
            self.assertEqual("shared", derived["details"]["identity_mode"])

    def test_write_case_csvs_normalizes_requests(self) -> None:
        rows = [
            {"tenant_idx": "0", "op": "commit_submit", "session_id": "s1", "status": "ok", "stage_ms": "50.0", "error_type": "", "retry_after_s": "", "reason_code": ""},
            {"tenant_idx": "0", "op": "commit_done", "session_id": "s1", "status": "ok", "stage_ms": "2000.0", "error_type": "", "retry_after_s": "", "reason_code": ""},
            {"tenant_idx": "1", "op": "commit_submit", "session_id": "s2", "status": "ok", "stage_ms": "60.0", "error_type": "", "retry_after_s": "", "reason_code": ""},
            {"tenant_idx": "1", "op": "commit_done", "session_id": "s2", "status": "error", "stage_ms": "3000.0", "error_type": "commit_failed", "retry_after_s": "", "reason_code": ""},
            {"tenant_idx": "2", "op": "commit_submit", "session_id": "s3", "status": "ok", "stage_ms": "70.0", "error_type": "", "retry_after_s": "", "reason_code": ""},
            {"tenant_idx": "0", "op": "read", "session_id": "", "status": "ok", "stage_ms": "100.0", "error_type": "", "retry_after_s": "", "reason_code": ""},
            {"tenant_idx": "1", "op": "read", "session_id": "", "status": "error", "stage_ms": "200.0", "error_type": "http_4xx", "retry_after_s": "1.0", "reason_code": "lane_full"},
            {"tenant_idx": "2", "op": "read", "session_id": "", "status": "error", "stage_ms": "300.0", "error_type": "timeout", "retry_after_s": "", "reason_code": ""},
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            _write_case_csvs(output, rows)
            with (output / "commit_results.csv").open(newline="", encoding="utf-8") as handle:
                commit_rows = list(csv.DictReader(handle))
            self.assertEqual(
                [
                    "tenant", "session_id", "status", "end_to_end_s", "queue_wait_s",
                    "admission_wait_s", "admission_queue_depth", "request_id",
                ],
                list(commit_rows[0].keys()),
            )
            statuses = {row["session_id"]: row["status"] for row in commit_rows}
            self.assertEqual("completed", statuses["s1"])
            self.assertEqual("failed", statuses["s2"])
            self.assertEqual("submitted", statuses["s3"])
            self.assertEqual(
                "2.000",
                next(row["end_to_end_s"] for row in commit_rows if row["session_id"] == "s1"),
            )
            self.assertEqual("s1", commit_rows[0]["request_id"])
            with (output / "search_results.csv").open(newline="", encoding="utf-8") as handle:
                search_rows = list(csv.DictReader(handle))
            self.assertEqual(
                [
                    "tenant", "session_id", "status_code", "service_s", "queue_wait_s",
                    "request_id", "retry_after_s", "reason_code",
                ],
                list(search_rows[0].keys()),
            )
            self.assertEqual(["200", "429", "500"], [row["status_code"] for row in search_rows])
            self.assertEqual("1.0", search_rows[1]["retry_after_s"])
            self.assertEqual("lane_full", search_rows[1]["reason_code"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.locomo.blackbox import observed_metrics, write_artifacts
from benchmarks.locomo.compare import compare_runs, write_report


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class LocomoBlackboxMetricsTests(unittest.TestCase):
    def test_uses_only_observed_status_latency_and_usage(self) -> None:
        qa_rows = [
            {
                "question_id": "q1",
                "category": "1",
                "retrieval_status": "ok",
                "answer_status": "ok",
                "model_status": "ok",
                "health_status": "ok",
                "retrieval_count": "2",
                "model_retry_count": "1",
                "end_to_end_ms": "1000",
                "retrieval_latency_ms": "200",
                "injection_total_ms": "250",
                "llm_total_ms": "700",
                "answer_prompt_tokens": "100",
                "answer_completion_tokens": "20",
                "answer_total_tokens": "120",
            },
            {
                "question_id": "q2",
                "category": "1",
                "retrieval_status": "empty",
                "answer_status": "ok",
                "model_status": "ok",
                "health_status": "retrieval_empty",
                "retrieval_count": "0",
                "model_retry_count": "0",
                "end_to_end_ms": "3000",
                "retrieval_latency_ms": "400",
                "injection_total_ms": "450",
                "llm_total_ms": "2400",
                "answer_prompt_tokens": "80",
                "answer_completion_tokens": "20",
                "answer_total_tokens": "100",
            },
        ]
        judge_rows = [
            {
                "question_id": "q1",
                "verdict": "CORRECT",
                "judge_prompt_tokens": "30",
                "judge_completion_tokens": "5",
                "judge_total_tokens": "35",
            },
            {
                "question_id": "q2",
                "verdict": "WRONG",
                "judge_prompt_tokens": "30",
                "judge_completion_tokens": "5",
                "judge_total_tokens": "35",
            },
        ]
        import_rows = [{
            "status": "completed",
            "message_count": "10",
            "submitted_messages": "10",
        }]
        run = {
            "qa_parallelism": 2,
            "run_started_at": "2026-07-28T00:00:00+00:00",
            "run_finished_at": "2026-07-28T00:00:04+00:00",
        }

        metrics = observed_metrics(qa_rows, judge_rows, import_rows, run)

        self.assertEqual(0.5, metrics["accuracy"])
        self.assertEqual(0.5, metrics["request_success_rate"])
        self.assertEqual(0.5, metrics["empty_retrieval_rate"])
        self.assertEqual(290, metrics["visible_model_total_tokens"])
        self.assertEqual(220, metrics["tokens_per_correct"])
        self.assertEqual(2.0, metrics["end_to_end_s"]["avg"])
        self.assertEqual(0.5, metrics["qa_throughput_qps"])
        self.assertIsNone(metrics["internal_memory_injection_tokens"])

    def test_writes_json_and_markdown_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            snapshot = write_artifacts(
                qa_rows=[],
                judge_rows=[],
                import_rows=[],
                run_observation={},
                output_dir=output_dir,
            )

            self.assertEqual("strict_observed", snapshot["mode"])
            self.assertTrue(
                (output_dir / "strict_blackbox_metrics.json").is_file()
            )
            self.assertTrue(
                (output_dir / "strict_blackbox_report.md").is_file()
            )


class LocomoComparisonTests(unittest.TestCase):
    def test_reports_improvements_and_regressions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = root / "left"
            right = root / "right"
            output = root / "report"
            left.mkdir()
            right.mkdir()
            qa_rows = [
                {
                    "question_id": "q1",
                    "category": "1",
                    "question": "Q1",
                    "answer": "A1",
                    "response": "R1",
                    "retrieval_count": "1",
                },
                {
                    "question_id": "q2",
                    "category": "2",
                    "question": "Q2",
                    "answer": "A2",
                    "response": "R2",
                    "retrieval_count": "1",
                },
            ]
            _write_csv(left / "qa_results.csv", qa_rows)
            _write_csv(right / "qa_results.csv", qa_rows)
            _write_csv(left / "judge_results.csv", [
                {"question_id": "q1", "verdict": "WRONG"},
                {"question_id": "q2", "verdict": "CORRECT"},
            ])
            _write_csv(right / "judge_results.csv", [
                {"question_id": "q1", "verdict": "CORRECT"},
                {"question_id": "q2", "verdict": "WRONG"},
            ])

            report = compare_runs(left, right)
            written = write_report(left, right, output)

            self.assertEqual(1, report["transition_counts"]["improved"])
            self.assertEqual(1, report["transition_counts"]["regressed"])
            self.assertEqual(
                "unknown",
                report["compatibility"]["status"],
            )
            self.assertTrue(Path(written["artifacts"]["json"]).is_file())
            payload = json.loads(
                (output / "comparison.json").read_text(encoding="utf-8")
            )
            self.assertEqual(2, payload["question_count"])
            self.assertIn(
                "score deltas below are descriptive only",
                (output / "comparison.md").read_text(encoding="utf-8"),
            )

    def test_flags_memory_provenance_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = root / "left"
            right = root / "right"
            left.mkdir()
            right.mkdir()
            qa_rows = [{
                "question_id": "q1",
                "category": "1",
                "question": "Q1",
                "answer": "A1",
                "response": "R1",
                "retrieval_count": "1",
            }]
            judge_rows = [{"question_id": "q1", "verdict": "CORRECT"}]
            for run_dir in (left, right):
                _write_csv(run_dir / "qa_results.csv", qa_rows)
                _write_csv(run_dir / "judge_results.csv", judge_rows)
                (run_dir / "summary.json").write_text(
                    json.dumps({
                        "qa_profile": "test-best",
                        "tool_protocol_sha256": ["tool-hash"],
                    }),
                    encoding="utf-8",
                )
                (run_dir / "config.json").write_text(
                    json.dumps({
                        "config": {
                            "account": "tenant",
                            "llm_base_url": "https://answer.test/v1",
                            "llm_model": "model",
                        }
                    }),
                    encoding="utf-8",
                )
                (run_dir / "qa_resume_manifest.json").write_text(
                    json.dumps({
                        "memory_identity": {"account": "tenant"},
                        "answer_model": {
                            "base_url": "https://answer.test/v1",
                            "model": "model",
                        },
                        "qa": {"profile": "test-best"},
                        "qa_contract": {"sha256": "qa-hash"},
                    }),
                    encoding="utf-8",
                )
                (run_dir / "judge_resume_manifest.json").write_text(
                    json.dumps({
                        "judge": {
                            "base_url": "https://judge.test/v1",
                            "model": "judge",
                            "prompt_sha256": "judge-hash",
                        }
                    }),
                    encoding="utf-8",
                )
            (left / "memory_provenance.json").write_text(
                json.dumps({
                    "dataset_sha256": "dataset",
                    "session_uris": ["echo://session/left"],
                }),
                encoding="utf-8",
            )
            (right / "memory_provenance.json").write_text(
                json.dumps({
                    "dataset_sha256": "dataset",
                    "session_uris": ["echo://session/right"],
                }),
                encoding="utf-8",
            )

            report = compare_runs(left, right)

            self.assertFalse(report["compatibility"]["comparable"])
            self.assertEqual(
                ["memory_session_uris"],
                report["compatibility"]["mismatches"],
            )


if __name__ == "__main__":
    unittest.main()

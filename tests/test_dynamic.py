from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dynamic.run_eval import _build_v2_quality_report, build_parser, validate_dynamic_args


class DynamicConfigTests(unittest.TestCase):
    def test_quality_report_does_not_turn_evaluator_error_into_zero_score(self) -> None:
        report = _build_v2_quality_report(
            [{
                "round_id": "q1",
                "query": "question",
                "reply": "answer",
                "quality_score": None,
                "quality_error": "judge timeout",
            }],
            {},
        )

        self.assertIsNone(report["summary"]["avg_quality_score"])
        self.assertEqual("judge timeout", report["results"][0]["quality_error"])

    def test_replay_preflight_accepts_complete_local_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            evaluator = root / "evaluator.yaml"
            dataset.write_text("[]\n", encoding="utf-8")
            evaluator.write_text("dimensions: []\n", encoding="utf-8")
            args = build_parser().parse_args([
                "--dataset", str(dataset),
                "--evaluator-config", str(evaluator),
                "--llm-base-url", "https://example.test/v1",
                "--llm-api-key", "secret",
                "--password", "password",
            ])

            self.assertEqual([], validate_dynamic_args(args))

    def test_generate_preflight_reports_missing_simulator_before_login(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evaluator = Path(directory) / "evaluator.yaml"
            evaluator.write_text("dimensions: []\n", encoding="utf-8")
            args = build_parser().parse_args([
                "--evaluator-config", str(evaluator),
                "--user-simulator-config", str(Path(directory) / "missing.yaml"),
                "--llm-base-url", "https://example.test/v1",
                "--llm-api-key", "secret",
            ])

            errors = validate_dynamic_args(args)
            self.assertTrue(any("user simulator config not found" in error for error in errors))

    def test_hidden_check_flag_is_accepted_by_unified_entrypoint(self) -> None:
        args = build_parser().parse_args(["--check"])
        self.assertTrue(args.check)


if __name__ == "__main__":
    unittest.main()

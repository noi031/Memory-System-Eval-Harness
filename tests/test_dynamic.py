from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from shared.eval_base import build_config_from_args, resolve_llm_credentials, validate_eval_config
from run_eval import build_dynamic_parser as build_parser, validate_dynamic_args
from dynamic.artifacts import build_v2_quality_report as _build_v2_quality_report


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
                "--dataset-path", str(dataset),
                "--evaluator-config", str(evaluator),
                "--llm-base-url", "https://example.test/v1",
                "--llm-api-key", "secret",
                "--password", "password",
            ])
            resolve_llm_credentials(args)
            config = build_config_from_args(args)
            validate_eval_config(config)
            self.assertEqual([], validate_dynamic_args(args))

    def test_replay_uses_dataset_path_not_selector(self) -> None:
        """回归：根入口 --dataset dynamic --dataset-path <file> 必须把真实路径传给加载器。

        分发表把固定选择器 'dynamic' 放在 args.dataset，真实文件在 args.dataset_path；
        run_replay_mode 的两个加载分支都必须读取 dataset_path，而不是 args.dataset。
        """
        import logging
        import types
        from unittest import mock

        import dynamic.workflows as wf

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            dataset.write_text("{}", encoding="utf-8")
            args = build_parser().parse_args([
                "--dataset-path", str(dataset),
                "--evaluator-config", str(root / "evaluator.yaml"),
                "--llm-base-url", "https://example.test/v1",
                "--llm-api-key", "secret",
            ])
            args.dataset = "dynamic"  # 分发表设置的固定选择器

            captured: dict[str, str] = {}
            run_stub = types.SimpleNamespace(logger=logging.getLogger("test-dynamic"))
            with mock.patch.object(
                wf, "_load_v2_dataset",
                side_effect=lambda path: captured.setdefault("path", str(path))
                or {"background_memories": [], "dataset_queries": []},
            ), mock.patch.object(wf, "_run_replay_v2_mode", return_value=None):
                wf.run_replay_mode(
                    args,
                    run=run_stub,
                    agent_plugin=None,
                    llm=None,
                )

            self.assertEqual(str(dataset), captured.get("path"))

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

    def test_validate_dynamic_args_ignores_plugin_specific_params(self) -> None:
        """validate_dynamic_args must not validate plugin-specific parameters.

        Plugin-specific validation is delegated to the plugin's setup() method,
        not performed by dynamic run_eval.
        """
        with tempfile.TemporaryDirectory() as directory:
            evaluator = Path(directory) / "evaluator.yaml"
            evaluator.write_text("dimensions: []\n", encoding="utf-8")
            dataset = Path(directory) / "dataset.json"
            dataset.write_text("[]\n", encoding="utf-8")
            args = build_parser().parse_args([
                "--dataset-path", str(dataset),
                "--evaluator-config", str(evaluator),
                "--llm-base-url", "https://example.test/v1",
                "--llm-api-key", "secret",
            ])
            errors = validate_dynamic_args(args)
            error_text = " ".join(errors).lower()
            self.assertNotIn("echoagent", error_text)
            self.assertNotIn("username", error_text)
            self.assertNotIn("password", error_text)

    def test_validate_dynamic_args_does_not_check_llm_params(self) -> None:
        """LLM credential validation is handled by validate_eval_config, not validate_dynamic_args."""
        with tempfile.TemporaryDirectory() as directory:
            evaluator = Path(directory) / "evaluator.yaml"
            evaluator.write_text("dimensions: []\n", encoding="utf-8")
            dataset = Path(directory) / "dataset.json"
            dataset.write_text("[]\n", encoding="utf-8")
            args = build_parser().parse_args([
                "--dataset-path", str(dataset),
                "--evaluator-config", str(evaluator),
            ])
            errors = validate_dynamic_args(args)
            self.assertFalse(any("LLM" in e for e in errors))

            config = build_config_from_args(args)
            with self.assertRaises(ValueError) as ctx:
                validate_eval_config(config)
            self.assertIn("LLM API key", str(ctx.exception))



if __name__ == "__main__":
    unittest.main()

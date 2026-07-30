from __future__ import annotations

import unittest

from shared.eval_base import EvalConfig, validate_eval_config


class EvalConfigValidationTests(unittest.TestCase):
    def test_rejects_invalid_numeric_limits(self) -> None:
        config = EvalConfig(
            llm_base_url="https://example.test/v1",
            llm_api_key="secret",
            question_limit=-1,
            commit_poll_interval_s=0,
        )

        with self.assertRaisesRegex(ValueError, "commit poll interval.*questions"):
            validate_eval_config(config)


if __name__ == "__main__":
    unittest.main()

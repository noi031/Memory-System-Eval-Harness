from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shared.runtime_config import apply_cli_runtime_overrides, prepare_runtime_environment


class RuntimeConfigTests(unittest.TestCase):
    def test_cli_runtime_options_override_environment_for_preflight(self) -> None:
        with patch.dict(os.environ, {"ECHOMEM_BASE_URL": "http://old"}, clear=True):
            apply_cli_runtime_overrides([
                "--echomem-url=http://new",
                "--llm-base-url", "https://model.test/v1",
                "--llm-api-key", "secret",
            ])

            self.assertEqual("http://new", os.environ["ECHOMEM_BASE_URL"])
            self.assertEqual("https://model.test/v1", os.environ["LLM_BASE_URL"])
            self.assertEqual("secret", os.environ["LLM_API_KEY"])

    def test_discovers_workspace_model_and_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (root / ".env").write_text(
                f"ECHOMEM_WORKSPACE={workspace}\nECHOMEM_BASE_URL=http://127.0.0.1:9999\n",
                encoding="utf-8",
            )
            (workspace / "config.json").write_text(
                json.dumps({
                    "model": {
                        "llm": {
                            "api_base": "https://example.test/v1",
                            "model": "test-model",
                            "api_key": "model-secret",
                        }
                    }
                }),
                encoding="utf-8",
            )
            (workspace / ".echomem_http_auth_keys.json").write_text(
                json.dumps({"keys": [{"key": "ek_test"}]}),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                prepare_runtime_environment(root)
                self.assertEqual("https://example.test/v1", os.environ["LLM_BASE_URL"])
                self.assertEqual("test-model", os.environ["LLM_MODEL"])
                self.assertEqual("model-secret", os.environ["LLM_API_KEY"])
                self.assertEqual("ek_test", os.environ["ECHOMEM_AUTH_KEY"])
                self.assertEqual("model-secret", os.environ["JUDGE_TOKEN"])


if __name__ == "__main__":
    unittest.main()

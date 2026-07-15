#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import (
    model_preflight_from_payload,
    resolve_echomemory_runtime_env,
    resolve_echomemory_workspace_model_config,
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="echomemory-workspace-model-") as temp_dir:
        workspace = Path(temp_dir)
        secret = "workspace-secret-test"
        (workspace / "config.json").write_text(
            json.dumps(
                {
                    "model": {
                        "llm": {
                            "provider": "openai_compatible",
                            "api_base": "https://provider.example/v1",
                            "api_key": secret,
                            "model": "workspace-model",
                        },
                        "embedding": {
                            "provider": "openai_compatible",
                            "api_base": "https://embedding.example/v1",
                            "api_key": "embedding-secret-test",
                            "model": "workspace-embedding",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        payload = {
            "backend": "echomemory",
            "memoryBackend": "echomemory",
            "workspace": str(workspace),
            "account": "workspace-model-smoke",
        }
        model_config = resolve_echomemory_workspace_model_config(payload)
        assert model_config["chat_token"] == secret
        assert model_config["chat_base"] == "https://provider.example/v1"
        assert model_config["chat_model"] == "workspace-model"
        empty_model_env = {
            "ANSWER_TOKEN": "",
            "DASHSCOPE_API_KEY": "",
            "DASHSCOPE_BASE_URL": "",
            "ECHOMEM_API_KEY": "",
            "ECHOMEM_CHAT_API_KEY": "",
            "ECHOMEM_CHAT_BASE_URL": "",
            "ECHOMEM_CHAT_MODEL": "",
            "JUDGE_TOKEN": "",
            "LOCOMO_ANSWER_TOKEN": "",
            "LOCOMO_JUDGE_TOKEN": "",
            "OPENAI_API_KEY": "",
        }
        with patch.dict(os.environ, empty_model_env):
            runtime = resolve_echomemory_runtime_env(payload, Path("/missing/openviking.json"))
            assert runtime["chat_token"] == secret
            assert runtime["chat_base"] == "https://provider.example/v1"
            assert runtime["chat_model"] == "workspace-model"

            # Avoid an external request while proving the public preflight only exposes token_set.
            import server

            original_probe = server.openai_compatible_chat_preflight
            server.openai_compatible_chat_preflight = lambda base_url, model, token, timeout_s=45: {
                "ok": bool(token),
                "status": "ok",
                "base_url": base_url,
                "model": model,
            }
            try:
                result = model_preflight_from_payload({**payload, "role": "agent"})
            finally:
                server.openai_compatible_chat_preflight = original_probe
        assert result["ok"] is True
        assert result["token_set"] is True
        assert secret not in json.dumps(result)
    print("echomemory workspace model smoke passed")


if __name__ == "__main__":
    main()

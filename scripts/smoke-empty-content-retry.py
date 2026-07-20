#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import echomemory_memory_qa as qa


async def run_smoke() -> None:
    args = argparse.Namespace(
        answer_base_url="http://example.test/v1",
        answer_model="test-model",
        answer_token="test-token",
        timeout_s=1,
    )
    previous = {
        "answer": "",
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
        "model_retry_count": 0,
        "iteration": 1,
        "tools_used": [],
    }
    recovered = {
        "answer": "recovered",
        "prompt_tokens": 7,
        "completion_tokens": 2,
        "total_tokens": 9,
        "model_retry_count": 0,
        "model_error_kind": "",
    }

    mocked_call = AsyncMock(return_value=(recovered, 12.5))
    with patch.object(qa, "timed_call_openai_async", mocked_call):
        result, retry_ms, attempts = await qa.retry_empty_answer_once(
            args,
            [{"role": "user", "content": "test"}],
            previous,
        )

    mocked_call.assert_awaited_once()
    assert mocked_call.await_args.args[-1] == 0
    assert attempts == 1
    assert retry_ms == 12.5
    assert result["answer"] == "recovered"
    assert result["model_retry_count"] == 1
    assert result["prompt_tokens"] == 17
    assert result["completion_tokens"] == 6
    assert result["total_tokens"] == 23
    assert result["empty_content_retry_used"] is True
    print("empty-content retry smoke passed")


if __name__ == "__main__":
    asyncio.run(run_smoke())

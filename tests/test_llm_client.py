from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from shared.llm_client import LLMClient, LLMResponse, chat_with_repair


class JudgeFailureTests(unittest.TestCase):
    def test_judge_raises_on_transport_error(self) -> None:
        client = LLMClient("https://example.test/v1", "secret")
        client.chat = lambda messages: LLMResponse("", 0, 0, 0, "timeout")  # type: ignore[method-assign]

        with self.assertRaisesRegex(RuntimeError, "judge call failed"):
            client.judge("system", "user")


class ChatContentTests(unittest.TestCase):
    """chat() must not treat an empty visible completion as a success."""

    @staticmethod
    def _response(payload: dict) -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = json.dumps(payload).encode("utf-8")
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp

    def test_empty_content_with_content_filter_returns_error(self) -> None:
        resp = self._response({
            "choices": [{
                "message": {"role": "assistant", "content": ""},
                "finish_reason": "content_filter",
            }],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 0,
                "total_tokens": 5,
            },
        })
        client = LLMClient(
            "https://example.test/v1",
            "secret",
            max_retries=2,
            retry_backoff_s=0.01,
        )
        with (
            patch("urllib.request.urlopen", return_value=resp),
            patch("time.sleep"),
        ):
            result = client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual("", result.content)
        self.assertIn("empty completion", result.error)
        self.assertIn("content_filter", result.error)

    def test_empty_content_falls_back_to_reasoning_content(self) -> None:
        resp = self._response({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "thinking...",
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 10,
                "total_tokens": 15,
            },
        })
        client = LLMClient("https://example.test/v1", "secret", max_retries=1)
        with patch("urllib.request.urlopen", return_value=resp):
            result = client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual("thinking...", result.content)
        self.assertEqual("", result.error)

    def test_temperature_override_is_sent(self) -> None:
        resp = self._response({
            "choices": [{
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
            "usage": {},
        })
        client = LLMClient("https://example.test/v1", "secret", max_retries=1)
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            client.chat(
                [{"role": "user", "content": "hi"}],
                temperature=0.3,
            )
        req = mock_open.call_args.args[0]
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(0.3, payload["temperature"])


class ChatWithToolsContentTests(unittest.TestCase):
    """chat_with_tools() must not treat an empty, tool-less completion as OK."""

    @staticmethod
    def _response(payload: dict) -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = json.dumps(payload).encode("utf-8")
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp

    def test_empty_content_with_no_tool_call_returns_error(self) -> None:
        resp = self._response({
            "choices": [{
                "message": {"role": "assistant", "content": ""},
                "finish_reason": "content_filter",
            }],
            "usage": {},
        })
        client = LLMClient(
            "https://example.test/v1",
            "secret",
            max_retries=2,
            retry_backoff_s=0.01,
        )
        with (
            patch("urllib.request.urlopen", return_value=resp),
            patch("time.sleep"),
        ):
            result = client.chat_with_tools(
                [{"role": "user", "content": "hi"}],
                tools=[{}],
            )
        self.assertEqual("", result.content)
        self.assertEqual([], result.tool_calls)
        self.assertIn("empty completion", result.error)

    def test_tool_call_with_empty_content_is_success(self) -> None:
        resp = self._response({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {},
        })
        client = LLMClient("https://example.test/v1", "secret", max_retries=1)
        with patch("urllib.request.urlopen", return_value=resp):
            result = client.chat_with_tools(
                [{"role": "user", "content": "hi"}],
                tools=[{}],
            )
        self.assertEqual(1, len(result.tool_calls))
        self.assertEqual("", result.error)

    def test_empty_content_falls_back_to_reasoning_content(self) -> None:
        resp = self._response({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "thinking...",
                },
                "finish_reason": "stop",
            }],
            "usage": {},
        })
        client = LLMClient("https://example.test/v1", "secret", max_retries=1)
        with patch("urllib.request.urlopen", return_value=resp):
            result = client.chat_with_tools(
                [{"role": "user", "content": "hi"}],
                tools=[{}],
            )
        self.assertEqual("thinking...", result.content)
        self.assertEqual("", result.error)


class ChatWithRepairTests(unittest.TestCase):
    """chat_with_repair() retries unusable output with a corrective prompt."""

    @staticmethod
    def _fake(responses: list[LLMResponse]):
        class _FakeChat:
            def __init__(self, responses):
                self.responses = list(responses)
                self.calls = []

            def chat(self, messages, *, temperature=None):
                self.calls.append((messages, temperature))
                return self.responses.pop(0)

        return _FakeChat(responses)

    def test_retries_unusable_output_with_repair_prompt(self) -> None:
        def _parse(value: str) -> bool:
            if value == "yes":
                return True
            raise ValueError("unusable")

        llm = self._fake([
            LLMResponse("", 0, 0, 0.0),
            LLMResponse("yes", 1, 1, 0.1),
        ])
        with patch("time.sleep"):
            result = chat_with_repair(
                llm,
                "sys",
                "user prompt",
                repair_prompt=" Answer yes/no only.",
                parse=_parse,
            )
        self.assertTrue(result)
        self.assertEqual(2, len(llm.calls))
        # First attempt uses the client default; retry bumps temperature and
        # appends the corrective instruction.
        self.assertIsNone(llm.calls[0][1])
        self.assertEqual(0.3, llm.calls[1][1])
        self.assertIn("Answer yes/no only.", llm.calls[1][0][1]["content"])

    def test_raises_last_error_after_exhausting_attempts(self) -> None:
        def _parse(_value: str) -> bool:
            raise ValueError("unusable")

        llm = self._fake([
            LLMResponse("", 0, 0, 0.0, error="empty completion"),
            LLMResponse("maybe", 1, 1, 0.1),
            LLMResponse("maybe", 1, 1, 0.1),
        ])
        with patch("time.sleep"):
            with self.assertRaisesRegex(ValueError, "unusable"):
                chat_with_repair(
                    llm,
                    "sys",
                    "user prompt",
                    repair_prompt=" Answer yes/no only.",
                    parse=_parse,
                )

    def test_raises_transport_error_after_exhausting_attempts(self) -> None:
        def _parse(_value: str) -> bool:
            raise ValueError("unusable")

        llm = self._fake([
            LLMResponse("", 0, 0, 0.0, error="empty completion"),
            LLMResponse("", 0, 0, 0.0, error="empty completion"),
            LLMResponse("", 0, 0, 0.0, error="empty completion"),
        ])
        with patch("time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "empty completion"):
                chat_with_repair(
                    llm,
                    "sys",
                    "user prompt",
                    repair_prompt=" Answer yes/no only.",
                    parse=_parse,
                )


if __name__ == "__main__":
    unittest.main()

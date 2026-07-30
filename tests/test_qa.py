from __future__ import annotations

import unittest

from plugins.bare_llm.plugin import BareLLMPlugin, _SYSTEM_PROMPT
from shared.llm_client import LLMResponse
from shared.qa import QAResult


class _RecordingLLM:
    """Fake LLM that records the messages it receives."""

    def __init__(self, response_text="answer", error=None):
        self._response_text = response_text
        self._error = error
        self.timeout_s = None
        self.messages = None

    def chat(self, messages, *, timeout_s=None):
        self.timeout_s = timeout_s
        self.messages = messages
        return LLMResponse(
            content=self._response_text,
            prompt_tokens=10,
            completion_tokens=2,
            elapsed_s=0.5,
            error=self._error,
        )


def _make_plugin(llm: _RecordingLLM) -> BareLLMPlugin:
    plugin = BareLLMPlugin()
    plugin._llm = llm  # type: ignore[attr-defined]
    plugin._session_count = 0  # type: ignore[attr-defined]
    return plugin


class SendMessageTests(unittest.TestCase):
    def test_does_not_retrieve_memory(self) -> None:
        """bare_llm must not retrieve memory; memory_items is always empty."""
        llm = _RecordingLLM()
        plugin = _make_plugin(llm)

        resp = plugin.send_message("s1", "What is 2+2?")

        self.assertEqual([], resp.memory_items)

    def test_calls_llm_with_system_and_question(self) -> None:
        llm = _RecordingLLM(response_text="42")
        plugin = _make_plugin(llm)

        resp = plugin.send_message("s1", "What is the answer?")

        self.assertEqual("42", resp.text)
        self.assertEqual("system", llm.messages[0]["role"])
        self.assertEqual(_SYSTEM_PROMPT, llm.messages[0]["content"])
        self.assertEqual("user", llm.messages[1]["role"])
        self.assertIn("What is the answer?", llm.messages[1]["content"])

    def test_propagates_llm_error(self) -> None:
        llm = _RecordingLLM(error="api timeout")
        plugin = _make_plugin(llm)

        resp = plugin.send_message("s1", "question")

        self.assertEqual("api timeout", resp.error)


class CSVRowContractTests(unittest.TestCase):
    def test_csv_row_exposes_strict_blackbox_contract(self) -> None:
        result = QAResult(
            "q1",
            "question",
            "answer",
            "response",
            retrieval_items=[{"uri": "echo://item", "content": "memory"}],
            elapsed_s=1.5,
            prompt_tokens=10,
            completion_tokens=2,
            retrieval_latency_s=0.2,
            orchestration_latency_s=0.1,
            llm_latency_s=1.0,
            model_retry_count=1,
            model_usage_observed=True,
        )

        row = result.to_csv_row()

        self.assertEqual("ok", row["health_status"])
        self.assertEqual("1500.0", row["end_to_end_ms"])
        self.assertEqual("300.0", row["injection_total_ms"])
        self.assertEqual("12", row["answer_total_tokens"])
        self.assertEqual("blackbox", row["evidence_policy"])
        self.assertEqual("false", row["qa_memory_writeback_enabled"])


if __name__ == "__main__":
    unittest.main()

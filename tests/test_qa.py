from __future__ import annotations

import time
import unittest

from shared.llm_client import LLMResponse
from shared.qa import QAResult, answer_one_question, build_qa_prompt


class _SlowEchoMem:
    def search(self, *args, timeout_s=None, **kwargs):
        self.timeout_s = timeout_s
        time.sleep(0.02)
        raise TimeoutError("retrieval timed out")


class _RecordingLLM:
    def chat(self, messages, *, timeout_s=None):
        self.timeout_s = timeout_s
        return LLMResponse("", 0, 0, 0.0, error="answer timed out")


class QuestionTimeoutTests(unittest.TestCase):
    def test_includes_benchmark_query_time_in_prompt(self) -> None:
        messages = build_qa_prompt(
            "What happened?",
            [],
            question_time="2023-01-19",
        )

        self.assertIn("Current date: 2023-01-19", messages[-1]["content"])

    def test_uses_one_deadline_for_retrieval_and_answer(self) -> None:
        echomem = _SlowEchoMem()
        llm = _RecordingLLM()

        result = answer_one_question(
            echomem=echomem,
            llm=llm,
            question_id="q1",
            question="question",
            answer="answer",
            question_timeout_s=0.1,
        )

        self.assertLessEqual(echomem.timeout_s, 0.1)
        self.assertLess(llm.timeout_s, echomem.timeout_s)
        self.assertEqual("retrieval timed out", result.retrieval_error)
        self.assertEqual("answer timed out", result.llm_error)

    def test_skips_answer_when_retrieval_exhausts_deadline(self) -> None:
        echomem = _SlowEchoMem()
        llm = _RecordingLLM()

        result = answer_one_question(
            echomem=echomem,
            llm=llm,
            question_id="q1",
            question="question",
            answer="answer",
            question_timeout_s=0.001,
        )

        self.assertFalse(hasattr(llm, "timeout_s"))
        self.assertIn("question deadline exceeded", result.llm_error)

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

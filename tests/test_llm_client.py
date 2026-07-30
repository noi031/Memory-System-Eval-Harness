from __future__ import annotations

import unittest

from shared.llm_client import LLMClient, LLMResponse


class JudgeFailureTests(unittest.TestCase):
    def test_judge_raises_on_transport_error(self) -> None:
        client = LLMClient("https://example.test/v1", "secret")
        client.chat = lambda messages: LLMResponse("", 0, 0, 0, "timeout")  # type: ignore[method-assign]

        with self.assertRaisesRegex(RuntimeError, "judge call failed"):
            client.judge("system", "user")


if __name__ == "__main__":
    unittest.main()

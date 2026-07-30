"""Bare LLM plugin: pure system prompt + user query baseline.

No memory retrieval. Each call is stateless -- no retrieval loop, no
prefill, no session management. ``send_message`` issues a plain LLM call
with a fixed system prompt.
"""

from __future__ import annotations

import argparse
import time

from plugins.base import AgentDescriptor, AgentPlugin, AgentResponse
from shared.eval_base import add_llm_args, add_qa_args
from shared.llm_client import LLMClient
from backends.memory_types import NullMemoryClient

_SYSTEM_PROMPT = "You are a helpful assistant."


class BareLLMPlugin(AgentPlugin):
    """Bare LLM baseline: system prompt + user query, no memory retrieval.

    Each call is stateless -- no retrieval loop, no prefill, no session
    management. ``send_message`` issues a plain LLM call with the same
    fixed system prompt.
    """

    descriptor = AgentDescriptor(
        id="bare_llm",
        name="Bare LLM",
        description="Stateless LLM; no agent framework, no memory retrieval.",
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        add_llm_args(parser)
        add_qa_args(parser)

    def setup(self, config: dict) -> None:
        self._llm = LLMClient(
            base_url=config.get("llm_base_url", ""),
            api_key=config.get("llm_api_key", ""),
            model=config.get("llm_model", "doubao-seed-2.0-pro"),
            temperature=config.get("llm_temperature", 0.7),
            max_tokens=config.get("llm_max_tokens", 2048),
            timeout_s=config.get("llm_timeout_s", 120.0),
            max_retries=config.get("llm_retries", 3),
        )
        self._session_count = 0
        # NullMemoryClient so benchmark runners that set agent_plugin.memory_client
        # work without conditional branches. bare_llm does not use it.
        self.memory_client = NullMemoryClient()

    def create_session(self, title: str = "") -> str:
        self._session_count += 1
        return f"bare_llm_session_{self._session_count}"

    @property
    def supports_typing_simulation(self) -> bool:
        return False

    def send_message(
        self,
        session_id: str,
        message: str,
        context_path: str = "/",
        *,
        extra: dict | None = None,
    ) -> AgentResponse:
        start = time.monotonic()
        resp = self._llm.chat([
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ])
        elapsed_s = time.monotonic() - start
        elapsed_ms = elapsed_s * 1000

        return AgentResponse(
            text=resp.content,
            ttft_ms=round(elapsed_ms, 1),
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            cached_tokens=0,
            prefetch_committed=False,
            memory_items=[],
            error=resp.error or None,
            extra={"elapsed_s": elapsed_s},
        )

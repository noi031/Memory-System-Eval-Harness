"""Bare LLM plugin: baseline agent with system prompt + memory/query assembly.

In benchmark QA mode, ``run_qa`` uses the single-turn retrieve-then-generate
flow defined in ``agents/bare_llm/qa.py``: search the memory client (provided
by the memory plugin), assemble a system+memory+question prompt, and call
the LLM once.  When the memory plugin is ``none``, this is truly memoryless;
when it is ``echomemory``, it becomes a retrieval-augmented baseline.

In dynamic mode, ``send_message`` issues a stateless LLM call with no
background context.  Memory injection (if needed) is handled by the memory
plugin, not by this plugin.
"""

from __future__ import annotations

import argparse
import time
from typing import Any

from agents.base import AgentDescriptor, AgentPlugin, AgentResponse
from agents.bare_llm.qa import run_concurrent_qa
from memories import MemoryClient
from shared.llm_client import LLMClient
from shared.qa import QAResult

_SYSTEM_PROMPT = "You are a helpful assistant."


class BareLLMPlugin(AgentPlugin):
    """Bare LLM baseline: system prompt + memory/query assembly.

    Each call is stateless -- no retrieval loop, no prefill, no session
    management. In benchmark mode, ``run_qa`` runs the single-turn RAG flow
    from ``agents/bare_llm/qa.py``. In dynamic mode, ``send_message`` issues
    a plain LLM call with no background context.
    """

    descriptor = AgentDescriptor(
        id="bare_llm",
        name="Bare LLM",
        description="Stateless LLM; no agent framework, no memory retrieval.",
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """LLM args are declared by the benchmark's build_parser via add_llm_args."""
        pass

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

    def run_qa(
        self,
        tasks: list[dict[str, Any]],
        memory_client: MemoryClient,
        llm: LLMClient,
        *,
        concurrency: int,
        question_timeout_s: float,
        progress_callback=None,
    ) -> list[QAResult]:
        return run_concurrent_qa(
            tasks,
            memory_client,
            llm,
            concurrency=concurrency,
            question_timeout_s=question_timeout_s,
            progress_callback=progress_callback,
        )

    def create_session(self, title: str = "") -> str:
        self._session_count += 1
        return f"bare_llm_session_{self._session_count}"

    @property
    def supports_typing_simulation(self) -> bool:
        return False

    def send_message(
        self, session_id: str, message: str, context_path: str = "/"
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

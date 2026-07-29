"""Bare LLM plugin: no memory system, memories go into the system prompt.

Used as a baseline to compare agents with vs without a memory system.
"""

from __future__ import annotations

import argparse
import logging
import time
import uuid

from agents.base import AgentPlugin, AgentResponse
from shared.llm_client import LLMClient
from shared.eval_base import add_llm_args

logger = logging.getLogger("agent.bare_llm")

_SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer the user's question based on the "
    "background information provided below. If the answer is not in the "
    "background information, say you don't know.\n\n"
    "Background information:\n{memories}"
)


class BareLLMPlugin(AgentPlugin):
    """Bare LLM without a memory system.

    Background memories are concatenated into the system prompt. There is
    no retrieval, no prefill, and no session management -- each call is
    stateless except for the shared background context.
    """

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        add_llm_args(parser)

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
        self._memories_by_session: dict[str, list[dict]] = {}
        self._session_count = 0

    def inject_memories(self, memories: list[dict], session_id: str = "") -> str:
        sid = session_id or f"bare_llm_{uuid.uuid4().hex[:8]}"
        self._memories_by_session[sid] = [m for m in memories if m.get("text")]
        logger.info("存储 %d 条背景记忆到 session %s (拼入 system prompt)",
                    len(self._memories_by_session[sid]), sid)
        return sid

    def create_session(self, title: str = "") -> str:
        self._session_count += 1
        return f"bare_llm_session_{self._session_count}"

    @property
    def supports_typing_simulation(self) -> bool:
        return False

    def send_message(
        self, session_id: str, message: str, context_path: str = "/"
    ) -> AgentResponse:
        memories = self._memories_by_session.get(session_id, [])
        memories_text = "\n".join(
            f"- {m.get('text', '')}" for m in memories
        )
        system_prompt = _SYSTEM_PROMPT.format(memories=memories_text or "N/A")

        start = time.monotonic()
        resp = self._llm.chat([
            {"role": "system", "content": system_prompt},
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

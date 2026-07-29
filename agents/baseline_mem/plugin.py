"""Baseline memory plugin: EchoMem retrieval + LLM generation.

Encapsulates the "search EchoMem -> build prompt -> call LLM" flow.
This is the default agent for benchmark evaluation -- a baseline agent
with memory but no agent framework overhead.
"""

from __future__ import annotations

import argparse
import logging
import time
import uuid

from agents.base import AgentPlugin, AgentResponse
from shared.echomem_client import EchoMemClient, SearchResult
from shared.llm_client import LLMClient
from shared.eval_base import add_echomem_args, add_llm_args

logger = logging.getLogger("agent.baseline_mem")

_QA_SYSTEM_PROMPT = (
    "You are a helpful assistant with access to the user's personal memories. "
    "Answer questions based on the provided memory context. "
    "If the memories do not contain the answer, say you don't know. "
    "Keep your answer concise."
)


def build_qa_prompt(
    question: str,
    memory_items: list[SearchResult],
    memory_budget_chars: int = 8000,
) -> list[dict[str, str]]:
    """Build the message list for the LLM: system + memory block + question."""
    memory_parts: list[str] = []
    total_chars = 0
    for item in memory_items:
        text = item.content or ""
        if not text:
            continue
        if total_chars + len(text) > memory_budget_chars:
            remaining = memory_budget_chars - total_chars
            if remaining > 100:
                memory_parts.append(text[:remaining] + "...")
            break
        memory_parts.append(text)
        total_chars += len(text)

    memory_block = "\n---\n".join(memory_parts) if memory_parts else "(no relevant memories found)"
    user_content = f"Memory Context:\n{memory_block}\n\nQuestion: {question}\n\nAnswer:"

    return [
        {"role": "system", "content": _QA_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


class BaselineMemPlugin(AgentPlugin):
    """Baseline agent+memory: EchoMem retrieval + LLM generation.

    Memory injection goes directly to EchoMem (open/add/commit/poll).
    QA does EchoMem search -> build prompt -> LLM chat, identical to the
    previous answer_one_question() flow.

    Thread-safe: send_message() makes stateless HTTP calls, so it can
    be called concurrently from multiple threads.
    """

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        add_echomem_args(parser)
        add_llm_args(parser)
        g = parser.add_argument_group("baseline_mem")
        g.add_argument("--top-k", type=int, default=10, help="检索条数")
        g.add_argument("--memory-budget-chars", type=int, default=8000,
                       help="记忆注入 prompt 的字符上限")
        g.add_argument("--commit-timeout-s", type=float, default=0.0,
                       help="commit 轮询超时 (0=无限)")
        g.add_argument("--commit-poll-interval-s", type=float, default=2.0,
                       help="commit 轮询间隔")

    def setup(self, config: dict) -> None:
        self._echomem = EchoMemClient(
            base_url=config.get("echomem_url", "http://127.0.0.1:8010"),
            auth_key=config.get("echomem_auth_key", ""),
            account=config.get("account", "default"),
            user_id=config.get("user_id", "default"),
            agent_id=config.get("agent_id", "default"),
            workspace=config.get("workspace", ""),
            timeout_s=60.0,
            max_retries=3,
        )
        self._llm = LLMClient(
            base_url=config.get("llm_base_url", ""),
            api_key=config.get("llm_api_key", ""),
            model=config.get("llm_model", "doubao-seed-2.0-pro"),
            temperature=config.get("llm_temperature", 0.7),
            max_tokens=config.get("llm_max_tokens", 2048),
            timeout_s=config.get("llm_timeout_s", 120.0),
            max_retries=config.get("llm_retries", 3),
        )
        self._top_k = config.get("top_k", 10)
        self._memory_budget_chars = config.get("memory_budget_chars", 8000)
        self._agent_id = config.get("agent_id", "default")
        self._commit_timeout_s = config.get("commit_timeout_s", 0.0)
        self._commit_poll_interval_s = config.get("commit_poll_interval_s", 2.0)

    def inject_memories(self, memories: list[dict], session_id: str = "") -> str:
        """Inject memories into EchoMem via open/add/commit/poll.

        Memory dict fields (all optional except text/content):
          - text or content: message content
          - created_at or time: timestamp
          - role: message role (default "user")
          - role_id or speaker: speaker identifier
        """
        events = [m for m in memories if m.get("text") or m.get("content")]
        if not events:
            logger.warning("无背景记忆, 跳过注入")
            return session_id

        if session_id and self._echomem.has_archives(session_id):
            logger.info("session %s 已有 archive, 跳过注入", session_id)
            return session_id

        title = f"baseline_mem-{session_id or uuid.uuid4().hex[:8]}"
        sid = self._echomem.open_session(title=title, session_id=session_id)

        for event in events:
            text = event.get("text") or event.get("content", "")
            if not text:
                continue
            created_at = event.get("created_at") or event.get("time", "")
            role = event.get("role", "user")
            role_id = event.get("role_id") or event.get("speaker", "")
            try:
                self._echomem.add_message(
                    sid, role, text,
                    created_at=created_at,
                    role_id=role_id,
                )
            except Exception as exc:
                logger.warning("注入失败: %s", exc)

        archive_id = self._echomem.commit_session(sid)
        commit_result = self._echomem.poll_commit(
            sid, archive_id,
            timeout_s=self._commit_timeout_s,
            poll_interval_s=self._commit_poll_interval_s,
        )
        logger.info(
            "注入完成: %s (%.1fs, %d polls)",
            commit_result.status, commit_result.elapsed_s, commit_result.polls,
        )
        if commit_result.status not in ("completed",):
            raise RuntimeError(
                f"记忆注入失败: status={commit_result.status} "
                f"error={commit_result.error} (session={sid})"
            )
        return sid

    def create_session(self, title: str = "") -> str:
        return self._echomem.open_session(title=title or "baseline_mem_qa")

    @property
    def supports_typing_simulation(self) -> bool:
        return False

    def send_message(
        self, session_id: str, message: str, context_path: str = "/"
    ) -> AgentResponse:
        """Search EchoMem, build prompt, call LLM -- same as answer_one_question()."""
        start = time.monotonic()

        # 1. Retrieve memories
        retrieval_error = ""
        items = []
        try:
            items = self._echomem.search(
                message, top_k=self._top_k,
                session_id=session_id, agent_id=self._agent_id,
            )
        except Exception as e:
            retrieval_error = str(e)
            logger.warning("Retrieval failed: %s", e)

        # 2. Build prompt
        messages = build_qa_prompt(message, items, self._memory_budget_chars)

        # 3. Call LLM
        resp = self._llm.chat(messages)

        elapsed_s = time.monotonic() - start

        return AgentResponse(
            text=resp.content,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            memory_items=[
                {"uri": r.uri, "score": r.score, "content": r.content[:500], "type": r.memory_type}
                for r in items
            ],
            error=resp.error or None,
            extra={
                "retrieval_error": retrieval_error,
                "elapsed_s": elapsed_s,
            },
        )

    def teardown(self) -> None:
        pass

"""EchoAgent plugin: wraps EchoAgentClient + EchoMemClient into AgentPlugin.

Refactored from dynamic/run_eval.py. All agent-specific HTTP logic lives
here; the evaluation flow calls only AgentPlugin methods.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from typing import Any

from tqdm import tqdm

from agents.base import AgentPlugin, AgentResponse, TypingResult
from agents.echo_agent.client import EchoAgentClient
from shared.echomem_client import EchoMemClient
from shared.eval_base import add_echomem_args

logger = logging.getLogger("agent.echo_agent")


class EchoAgentPlugin(AgentPlugin):
    """EchoAgent + EchoMem plugin for dynamic evaluation.

    Memory injection goes directly to EchoMem (bypassing EchoAgent);
    QA goes through the full EchoAgent pipeline (prefill + SSE streaming).
    """

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        add_echomem_args(parser)
        g = parser.add_argument_group("EchoAgent")
        g.add_argument("--echoagent-url", default="http://127.0.0.1:31020",
                       help="EchoAgent 后端地址")
        g.add_argument("--username", default="test_user",
                       help="EchoAgent 登录用户名")
        g.add_argument("--password", default="",
                       help="EchoAgent 登录密码")
        g.add_argument("--memory-engine-endpoint",
                       default="http://127.0.0.1:31030",
                       help="echoagent 插件地址 (31030)")
        g.add_argument("--commit-timeout-s", type=float, default=0.0,
                       help="注入 commit 轮询超时 (0=无限)")
        g.add_argument("--commit-poll-interval-s", type=float, default=2.0,
                       help="注入 commit 轮询间隔")

    def setup(self, config: dict) -> None:
        echoagent_url = config.get("echoagent_url", "http://127.0.0.1:31020")
        username = config.get("username", "test_user")
        password = config.get("password", "")
        self._memory_engine_endpoint = config.get(
            "memory_engine_endpoint",
            "http://127.0.0.1:31030",
        )
        self._commit_timeout_s = config.get("commit_timeout_s", 0.0)
        self._commit_poll_interval_s = config.get("commit_poll_interval_s", 2.0)

        # Login to EchoAgent
        self.client = EchoAgentClient(echoagent_url, username, password)
        print(f"登录 EchoAgent ({echoagent_url})...")
        self.client.login()
        logger.info("登录成功 (user=%s, uuid=%s)", username, self.client.user_uuid)

        # Dynamic eval QA goes through EchoAgent -> echoagent plugin, which
        # uses agent_id="echoagent". Injection must use the same agent_id.
        self._agent_id = config.get("agent_id", "")
        if not self._agent_id or self._agent_id == "default":
            self._agent_id = "echoagent"

        # Resolve auth_key so injection uses the same identity as retrieval
        auth_key = config.get("echomem_auth_key", "")
        if not auth_key:
            try:
                auth_key = self.client.get_memory_auth_key(self._memory_engine_endpoint)
            except Exception as e:
                logger.warning("解析 auth_key 失败: %s — 注入将不携带身份", e)
                auth_key = ""
        self._auth_key = auth_key
        config["echomem_auth_key"] = auth_key
        logger.info("agent_id=%s, auth_key=%s", self._agent_id, "已设置" if auth_key else "未设置")

        # EchoMem client for direct memory injection
        self._echomem = EchoMemClient(
            base_url=config.get("echomem_url", "http://127.0.0.1:8010"),
            auth_key=auth_key,
            account=config.get("account", "default"),
            user_id=config.get("user_id", "default"),
            agent_id=self._agent_id,
            workspace=config.get("workspace", ""),
            timeout_s=60.0,
            max_retries=3,
        )

        # Typing state (reset per round)
        self._pending_turn_id = ""
        self._typing_committed = False
        self._typing_memory_items: list[dict] = []

    def inject_memories(self, memories: list[dict], session_id: str = "") -> str:
        """Inject background memories directly into EchoMem.

        If session_id is provided and that session already has archives,
        injection is skipped (replay optimization).
        """
        events = [m for m in memories if m.get("text")]
        if not events:
            logger.warning("无背景记忆, 跳过注入")
            return session_id

        # Skip if session already has archives
        if session_id and self._echomem.has_archives(session_id):
            logger.info("session %s 已有 archive, 跳过注入", session_id)
            return session_id

        title = f"inject-{session_id or uuid.uuid4().hex[:8]}"
        inject_session = self._echomem.open_session(title=title, session_id=session_id)

        for event in tqdm(events, desc="注入记忆", unit="mem", leave=False):
            try:
                self._echomem.add_message(inject_session, "user", event["text"])
            except Exception as exc:
                logger.warning("注入失败: %s", exc)

        archive_id = self._echomem.commit_session(inject_session)
        commit_result = self._echomem.poll_commit(
            inject_session,
            archive_id,
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
                f"error={commit_result.error} (session={inject_session})"
            )
        return inject_session

    def create_session(self, title: str = "") -> str:
        return self.client.create_session(title, self._memory_engine_endpoint)

    @property
    def supports_typing_simulation(self) -> bool:
        return True

    def simulate_typing(
        self,
        session_id: str,
        context_path: str,
        text: str,
        speed_ms: int = 200,
        jitter_ms: int = 20,
    ) -> TypingResult | None:
        """Simulate typing to trigger prefill.

        speed_ms < 50: fast mode -- single tick + finalize, no per-char delay.
        """
        import random

        # Reset typing state for this round
        self._pending_turn_id = ""
        self._typing_committed = False
        self._typing_memory_items = []

        client_turn_id = uuid.uuid4().hex[:12]
        committed = False

        if speed_ms < 50:
            tick_result = self.client.prefetch_tick(
                session_id, context_path, client_turn_id, 1, text,
            )
            if tick_result is None:
                return None
            time.sleep(0.5)
            finalize_result = self.client.prefetch_finalize(
                session_id, context_path, client_turn_id, text,
            )
            if finalize_result is not None:
                fin_data = finalize_result.get("data", finalize_result)
                committed = bool(fin_data.get("accepted"))
            # Store state for send_message
            self._pending_turn_id = client_turn_id
            self._typing_committed = committed
            return TypingResult(committed=committed)

        for i in range(1, len(text) + 1):
            draft = text[:i]
            tick_result = self.client.prefetch_tick(
                session_id, context_path, client_turn_id, i, draft,
            )
            if tick_result is None:
                return None
            tick_data = tick_result.get("data", tick_result)
            if not tick_data.get("accepted") and i == 1:
                self._pending_turn_id = client_turn_id
                self._typing_committed = False
                return TypingResult(committed=False)
            delay = speed_ms + random.randint(-jitter_ms, jitter_ms)
            time.sleep(max(10, delay) / 1000.0)

        finalize_result = self.client.prefetch_finalize(
            session_id, context_path, client_turn_id, text,
        )
        memory_items: list[dict] = []
        if finalize_result is not None:
            fin_data = finalize_result.get("data", finalize_result)
            committed = bool(fin_data.get("accepted"))
            memory_items = fin_data.get("memoryItems") or []

        # Store state for send_message
        self._pending_turn_id = client_turn_id
        self._typing_committed = committed
        self._typing_memory_items = memory_items
        return TypingResult(committed=committed, memory_items=memory_items)

    def send_message(
        self, session_id: str, message: str, context_path: str = "/"
    ) -> AgentResponse:
        """Send message to EchoAgent and stream the reply.

        Uses the prefill client_turn_id from the last simulate_typing call
        (if any), then clears the typing state.
        """
        # Capture and clear typing state
        pending_turn_id = self._pending_turn_id
        committed = self._typing_committed
        memory_items = list(self._typing_memory_items)
        self._pending_turn_id = ""
        self._typing_committed = False
        self._typing_memory_items = []

        try:
            msg_result = self.client.send_message(
                session_id, context_path, message, pending_turn_id,
            )
            msg_data = msg_result.get("data", msg_result)
            if msg_data.get("error"):
                return AgentResponse(
                    error=f"send failed: {msg_data.get('error')} {msg_data.get('message', '')}",
                    prefetch_committed=committed,
                    memory_items=memory_items,
                )

            # Extract seq for streaming
            messages_list = msg_data.get("messages") or []
            seq = 0
            for m in reversed(messages_list):
                if m.get("status") in ("generating", "completed"):
                    seq = m.get("seq", 0)
                    break
            if not seq and messages_list:
                seq = messages_list[-1].get("seq", 0)
            if not seq:
                seq = msg_data.get("latestContextSeq") or 0

            # Stream reply
            reply_result = self.client.stream_reply(session_id, context_path, seq)

        except Exception as exc:
            logger.error("发送/接收失败: %s", exc)
            return AgentResponse(
                error=str(exc),
                prefetch_committed=committed,
                memory_items=memory_items,
            )

        reply = reply_result.get("reply") or ""
        ttft = reply_result.get("ttft_ms")
        done = reply_result.get("done_event") or {}
        cached_tokens = int(done.get("cachedTokens") or done.get("cached_tokens") or 0)
        prompt_tokens = int(done.get("promptTokens") or done.get("prompt_tokens") or 0)

        return AgentResponse(
            text=reply,
            ttft_ms=round(ttft, 1) if ttft is not None else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            cached_tokens=cached_tokens,
            prefetch_committed=committed,
            memory_items=memory_items,
            error=reply_result.get("error"),
        )

    def teardown(self) -> None:
        pass

"""EchoMem MCP agent plugin: LLM uses MCP tools to retrieve memories.

The LLM is given EchoMem's MCP tools (memory_query, read, list, glob) as
OpenAI function-calling definitions.  It decides when to search memory and
which URIs to read, mimicking how a real agent would interact with a
memory system through the MCP protocol.

Three configurable parameters control behavior:
- --tool-calling / --no-tool-calling: enable LLM tool calling via MCP
- --search-in-tools / --no-search-in-tools: include memory_query in tool defs
- --manual-search / --no-manual-search: pre-fetch memory before LLM turn

Memory injection is handled before QA starts.  The MCP server must be running
(EchoMem config ``mcp.enabled=true``).
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any

from plugins.base import AgentDescriptor, AgentPlugin, AgentResponse
from plugins.echomem_mcp.mcp_client import McpClient
from plugins.echomem_mcp.runtime import MCP_TOOLS, _SYSTEM_PROMPT
from backends.echomem.client import EchoMemClient, _PENDING_CLEANUPS
from shared.eval_base import add_llm_args, add_qa_args
from shared.llm_client import LLMClient
from backends.memory_args import add_memory_backend_args
from backends.memory_format import format_memory_section

logger = logging.getLogger("eval.echomem_mcp")


class EchoMemMCPPlugin(AgentPlugin):
    """Agent that uses EchoMem MCP tools for memory retrieval.

    Behavior is controlled by three flags (all default to True):
    - tool_calling: whether to present tools to the LLM
    - search_in_tools: whether memory_query is in the tool list
    - manual_search: whether to pre-fetch memories before the LLM turn
    """

    descriptor = AgentDescriptor(
        id="echomem_mcp",
        name="EchoMem MCP Agent",
        description="LLM agent that retrieves memories via EchoMem MCP tools.",
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        add_llm_args(parser)
        add_qa_args(parser)
        add_memory_backend_args(parser)
        g = parser.add_argument_group("echomem-mcp")
        g.add_argument(
            "--mcp-url",
            default="http://127.0.0.1:8001",
            help="EchoMem MCP server URL (default: http://127.0.0.1:8001)",
        )
        g.add_argument(
            "--mcp-auth-key",
            default="",
            help="X-Auth-Key for MCP server (falls back to --echomem-auth-key if empty)",
        )
        g.add_argument(
            "--mcp-max-iterations",
            type=int,
            default=10,
            help="Maximum tool-call iterations per question (default: 10)",
        )
        g.add_argument(
            "--tool-calling",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Enable LLM tool calling via MCP (default: enabled)",
        )
        g.add_argument(
            "--search-in-tools",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Include memory_query in tool definitions (default: enabled)",
        )
        g.add_argument(
            "--manual-search",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Pre-fetch memory search before each LLM turn (default: enabled)",
        )

    def setup(self, config: dict) -> None:
        self._mcp_url = config.get("mcp_url", "http://127.0.0.1:8001")
        self._auth_key = config.get("mcp_auth_key", "") or config.get("echomem_auth_key", "")
        self._max_iterations = config.get("mcp_max_iterations", 10)
        self._tool_calling = config.get("tool_calling", True)
        self._search_in_tools = config.get("search_in_tools", True)
        self._manual_search = config.get("manual_search", True)
        self._top_k = config.get("top_k", 25)
        self._memory_budget_chars = config.get("memory_budget_chars", 0)
        self._question_timeout_s = float(config.get("question_timeout_s", 120.0))

        # Create LLM client
        self._llm = LLMClient(
            base_url=config.get("llm_base_url", ""),
            api_key=config.get("llm_api_key", ""),
            model=config.get("llm_model", "doubao-seed-2.0-pro"),
            temperature=config.get("llm_temperature", 0.7),
            max_tokens=config.get("llm_max_tokens", 2048),
            timeout_s=config.get("llm_timeout_s", 120.0),
            max_retries=config.get("llm_retries", 3),
        )

        # Create EchoMemClient for memory injection
        self._commit_timeout_s = float(config.get("commit_timeout_s", 0.0))
        self._commit_poll_interval_s = float(config.get("commit_poll_interval_s", 2.0))

        self.memory_client = EchoMemClient(
            base_url=config.get("echomem_url", "http://127.0.0.1:8010"),
            auth_key=config.get("echomem_auth_key", ""),
            account=config.get("account", "default"),
            user_id=config.get("user_id", "default"),
            agent_id=config.get("agent_id", "default"),
            workspace=config.get("workspace", ""),
            timeout_s=float(config.get("timeout_s", 60.0)),
            max_retries=int(config.get("max_retries", 3)),
        )

        # Identity isolation: skip when benchmark_name/run_id are absent
        # (test/validation scenarios) or when --reuse-memory-account is set.
        benchmark_name = config.get("benchmark_name", "")
        run_id = config.get("run_id", "")
        reuse = config.get("reuse_memory_account", False)
        keep = config.get("keep_memory_account", False)

        if benchmark_name and run_id and not reuse:
            label = f"eval-{benchmark_name}-{run_id}"[:120]
            self.memory_client.provision_isolated_identity(label)
            if not keep:
                _PENDING_CLEANUPS.append(self.memory_client)

    def inject_memories(
        self,
        memories: list[dict],
        *,
        backend: str = "echomem",
        session_id: str = "",
    ) -> str:
        if not session_id:
            session_id = self.memory_client.open_session(title="inject")
        for mem in memories:
            text = str(mem.get("text") or "")
            if text:
                self.memory_client.add_message(
                    session_id,
                    "user",
                    text,
                    created_at=str(mem.get("time") or ""),
                )
        archive_id = self.memory_client.commit_session(session_id)
        commit = self.memory_client.poll_commit(
            session_id,
            archive_id,
            timeout_s=self._commit_timeout_s,
            poll_interval_s=self._commit_poll_interval_s,
        )
        if commit.status != "completed":
            raise RuntimeError(
                f"memory injection failed: status={commit.status} error={commit.error}"
            )
        return session_id

    def create_session(self, title: str = "") -> str:
        self._session_count = getattr(self, "_session_count", 0) + 1
        return f"echomem_mcp_session_{self._session_count}"

    def send_message(
        self,
        session_id: str,
        message: str,
        context_path: str = "/",
        *,
        extra: dict | None = None,
    ) -> AgentResponse:
        extra = extra or {}
        question_time = extra.get("question_time", "")
        start = time.monotonic()
        deadline = start + self._question_timeout_s if self._question_timeout_s > 0 else None

        def remaining() -> float | None:
            if deadline is None:
                return None
            return max(0.001, deadline - time.monotonic())

        # Build messages
        time_context = f"Current date: {question_time}.\n\n" if str(question_time).strip() else ""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"{time_context}{message}"},
        ]

        retrieval_items: list[dict[str, Any]] = []
        retrieval_latency_s = 0.0

        # Phase A: Manual pre-fetch search
        if self._manual_search:
            try:
                t0 = time.monotonic()
                results = self.memory_client.search(message, top_k=self._top_k, timeout_s=remaining())
                retrieval_latency_s = time.monotonic() - t0
                memory_text = format_memory_section(results, self._memory_budget_chars)
                if memory_text:
                    messages.insert(1, {"role": "user", "content": memory_text})
                retrieval_items = [r.to_dict() for r in results]
            except Exception as e:
                logger.warning("Manual search failed: %s", e)

        # Phase B: Build tool list
        if self._tool_calling:
            tools = [
                t for t in MCP_TOOLS
                if not (t["function"]["name"] == "memory_query" and not self._search_in_tools)
            ]
        else:
            tools = []

        # Phase C: Tool-call loop or single call
        tool_call_count = 0
        iterations = 0
        total_prompt = 0
        total_completion = 0
        response_text = ""
        llm_error = ""

        if tools:
            mcp: McpClient | None = None
            try:
                mcp = McpClient(self._mcp_url, auth_key=self._auth_key)
                mcp.initialize(timeout_s=remaining())
            except Exception as e:
                logger.warning("MCP initialize failed: %s", e)
                mcp = None

            if mcp is not None:
                try:
                    for iteration in range(1, self._max_iterations + 1):
                        iterations = iteration
                        rem = remaining()
                        if rem is not None and rem <= 0:
                            llm_error = f"question deadline exceeded after {self._question_timeout_s:g}s"
                            break

                        resp = self._llm.chat_with_tools(messages, tools, timeout_s=rem)
                        total_prompt += resp.prompt_tokens
                        total_completion += resp.completion_tokens

                        if resp.error:
                            llm_error = resp.error
                            break

                        if not resp.tool_calls:
                            response_text = resp.content
                            break

                        messages.append({
                            "role": "assistant",
                            "content": resp.content or "",
                            "tool_calls": resp.tool_calls,
                        })

                        for tc in resp.tool_calls:
                            func = tc.get("function", {})
                            name = func.get("name", "")
                            try:
                                args = json.loads(func.get("arguments", "{}"))
                            except json.JSONDecodeError:
                                args = {}

                            try:
                                result_text = mcp.call_tool(name, args, timeout_s=remaining())
                                tool_call_count += 1
                                if name == "memory_query":
                                    retrieval_items.append({
                                        "tool": name,
                                        "query": args.get("query", ""),
                                        "result": result_text[:2000],
                                    })
                            except Exception as e:
                                result_text = f"Error calling {name}: {e}"
                                logger.warning("Tool %s failed: %s", name, e)

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": result_text,
                            })
                    else:
                        # Max iterations - force final answer without tools
                        messages.append({
                            "role": "user",
                            "content": "You have reached the tool-use iteration limit. Do not call any more tools. Answer the question directly now.",
                        })
                        resp = self._llm.chat_with_tools(messages, [], timeout_s=remaining())
                        total_prompt += resp.prompt_tokens
                        total_completion += resp.completion_tokens
                        response_text = resp.content
                        llm_error = resp.error or ""
                finally:
                    try:
                        mcp.close()
                    except Exception:
                        pass
            else:
                # MCP init failed - fall back to single LLM call
                resp = self._llm.chat(messages, timeout_s=remaining())
                total_prompt = resp.prompt_tokens
                total_completion = resp.completion_tokens
                response_text = resp.content
                llm_error = resp.error or ""
        else:
            # No tool calling - single LLM call
            resp = self._llm.chat(messages, timeout_s=remaining())
            total_prompt = resp.prompt_tokens
            total_completion = resp.completion_tokens
            response_text = resp.content
            llm_error = resp.error or ""

        elapsed = time.monotonic() - start
        return AgentResponse(
            text=response_text,
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            memory_items=retrieval_items,
            error=llm_error or None,
            extra={
                "tool_call_count": tool_call_count,
                "iterations": iterations,
                "qa_profile": "echomem_mcp",
                "elapsed_s": elapsed,
                "retrieval_latency_s": retrieval_latency_s,
                "llm_latency_s": elapsed,
            },
        )

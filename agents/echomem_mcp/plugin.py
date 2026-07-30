"""EchoMem MCP agent plugin: LLM uses MCP tools to retrieve memories.

The LLM is given EchoMem's MCP tools (memory_query, read, list, glob) as
OpenAI function-calling definitions.  It decides when to search memory and
which URIs to read, mimicking how a real agent would interact with a
memory system through the MCP protocol.

Memory injection is handled by the memory plugin (e.g. echomemory) before
QA starts.  This plugin does not use the ``memory_client`` parameter in
``run_qa`` -- the LLM retrieves memories by calling MCP tools, not by
direct HTTP search.

The MCP server must be running (EchoMem config ``mcp.enabled=true``).
"""

from __future__ import annotations

import argparse
from typing import Any, Callable

from agents.base import AgentDescriptor, AgentPlugin
from agents.echomem_mcp.mcp_client import McpClient
from agents.echomem_mcp.runtime import run_concurrent_mcp_qa
from memories import MemoryClient
from shared.llm_client import LLMClient
from shared.qa import QAResult


class EchoMemMCPPlugin(AgentPlugin):
    """Agent that uses EchoMem MCP tools for memory retrieval.

    In benchmark QA mode, ``run_qa`` runs a tool-call loop: the LLM is
    presented with MCP tools and decides when to search memory.  Each tool
    call is forwarded to the EchoMem MCP server via JSON-RPC over HTTP.
    """

    descriptor = AgentDescriptor(
        id="echomem_mcp",
        name="EchoMem MCP Agent",
        description="LLM agent that retrieves memories via EchoMem MCP tools.",
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
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

    def setup(self, config: dict) -> None:
        self._mcp_url = config.get("mcp_url", "http://127.0.0.1:8001")
        self._auth_key = config.get("mcp_auth_key", "") or config.get("echomem_auth_key", "")
        self._max_iterations = config.get("mcp_max_iterations", 10)

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
        # memory_client is not used: the LLM retrieves memories by calling
        # MCP tools, which are forwarded to the EchoMem MCP server.
        mcp_factory: Callable[[], McpClient] = lambda: McpClient(
            self._mcp_url, auth_key=self._auth_key
        )
        return run_concurrent_mcp_qa(
            tasks,
            mcp_factory,
            llm,
            concurrency=concurrency,
            question_timeout_s=question_timeout_s,
            max_iterations=self._max_iterations,
            progress_callback=progress_callback,
        )

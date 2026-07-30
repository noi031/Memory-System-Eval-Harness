"""OpenViking tool-calling agent plugin.

The LLM is given the memory backend's operations (memory_search, memory_read,
memory_list, memory_glob) as OpenAI function-calling definitions. It decides
when to search memory and which URIs to read, mimicking how a real agent
interacts with a memory system through tool calls.

Unlike echomem_mcp (which connects to EchoMem's MCP server directly via JSON-RPC),
this plugin uses the ``memory_client`` parameter in ``run_qa`` to execute tool
calls. The MemoryClient protocol abstracts over the concrete backend -- when
paired with the ``openviking`` memory plugin, the client is OpenVikingClient
and tool calls hit the OpenViking REST API. When paired with ``echomemory``,
the same tools hit EchoMem's HTTP endpoints.

Memory injection (writing background memories) is handled by the memory plugin
before QA starts. This plugin only handles retrieval via tool calls.
"""

from __future__ import annotations

import argparse
from typing import Any

from agents.base import AgentDescriptor, AgentPlugin
from agents.openviking_mcp.runtime import run_concurrent_tool_qa
from memories import MemoryClient
from shared.llm_client import LLMClient
from shared.qa import QAResult


class OpenVikingMCPPlugin(AgentPlugin):
    """Agent that uses memory tool calls for retrieval.

    In benchmark QA mode, ``run_qa`` runs a tool-call loop: the LLM is
    presented with memory tools and decides when to search. Each tool call
    is executed via the MemoryClient protocol (search / fs_read / fs_list /
    fs_glob).
    """

    descriptor = AgentDescriptor(
        id="openviking_mcp",
        name="OpenViking Tool Agent",
        description="LLM agent that retrieves memories via tool calls.",
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        g = parser.add_argument_group("openviking-mcp")
        g.add_argument(
            "--ov-max-iterations",
            type=int,
            default=10,
            help="Maximum tool-call iterations per question (default: 10)",
        )
        g.add_argument(
            "--ov-search-limit",
            type=int,
            default=8,
            help="Maximum results per memory_search call (default: 8)",
        )

    def setup(self, config: dict) -> None:
        self._max_iterations = config.get("ov_max_iterations", 10)
        self._search_limit = config.get("ov_search_limit", 8)

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
        return run_concurrent_tool_qa(
            tasks,
            memory_client,
            llm,
            concurrency=concurrency,
            question_timeout_s=question_timeout_s,
            max_iterations=self._max_iterations,
            search_limit=self._search_limit,
            progress_callback=progress_callback,
        )

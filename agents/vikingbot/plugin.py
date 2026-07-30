"""Plugin entrypoint for the VikingBot agent.

VikingBot is a tool-call agent that searches EchoMemory via OpenAI-
compatible tool definitions, iterates until it finds the answer, and
returns a sanitized final response. It is the default agent for the
LoCoMo benchmark.

In the dual-plugin architecture, VikingBot receives a MemoryClient
(created by the memory plugin) and an LLMClient (created by the
benchmark runner) at run_qa() call time. It does not create its own
clients.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from agents.base import AgentDescriptor, AgentPlugin
from memories import MemoryClient
from shared.llm_client import LLMClient
from shared.qa import QAResult

from .runtime import run_concurrent_vikingbot_qa

_DEFAULT_WORKSPACE = Path(__file__).resolve().parent / "bootstrap"


class VikingBotPlugin(AgentPlugin):
    """VikingBot: iterative tool-call agent with EchoMemory tools."""

    descriptor = AgentDescriptor(
        id="vikingbot",
        name="VikingBot",
        description="VikingBot prompt, memory tools, and iterative tool-call runtime.",
        capabilities=(
            "memory_search",
            "memory_read_many",
            "memory_list",
            "memory_grep",
            "memory_glob",
            "openai_tool_loop",
            "workspace_bootstrap",
        ),
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        g = parser.add_argument_group("VikingBot")
        g.add_argument(
            "--vikingbot-workspace",
            default=os.getenv("VIKINGBOT_WORKSPACE", str(_DEFAULT_WORKSPACE)),
            help="Workspace supplying the SOUL.md and TOOLS.md bootstrap",
        )
        g.add_argument("--tool-search-limit", type=int, default=None)
        g.add_argument("--user-memory-budget-chars", type=int, default=None)
        g.add_argument("--agent-memory-budget-chars", type=int, default=None)
        g.add_argument("--max-iterations", type=int, default=None)
        g.add_argument("--initial-min-score", type=float, default=None)
        g.add_argument("--tool-min-score", type=float, default=None)
        g.add_argument("--tool-search-pool-multiplier", type=int, default=None)
        g.add_argument(
            "--tool-set",
            choices=[
                "search_read",
                "vikingbot_native_safe",
                "vikingbot_echo_native",
            ],
            default=None,
        )
        g.add_argument(
            "--tools",
            action=argparse.BooleanOptionalAction,
            default=True,
            help=(
                "Expose the profile's memory tools to the answer model; "
                "--no-tools keeps the same profile prompt and initial memory "
                "injection but performs a single model turn"
            ),
        )

    def setup(self, config: dict) -> None:
        pass

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
        return run_concurrent_vikingbot_qa(
            tasks,
            memory_client,
            llm,
            concurrency=concurrency,
            question_timeout_s=question_timeout_s,
            progress_callback=progress_callback,
        )

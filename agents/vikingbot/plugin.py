"""Plugin entrypoint for the VikingBot agent."""

from __future__ import annotations

from typing import Any

from agents.base import AgentDescriptor
from backends import MemoryClient
from shared.llm_client import LLMClient
from shared.qa import QAResult

from .runtime import run_concurrent_vikingbot_qa


class VikingBotPlugin:
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

    def run_qa(
        self,
        tasks: list[dict[str, Any]],
        echomem: MemoryClient,
        llm: LLMClient,
        *,
        concurrency: int,
        question_timeout_s: float,
        progress_callback=None,
    ) -> list[QAResult]:
        return run_concurrent_vikingbot_qa(
            tasks,
            echomem,
            llm,
            concurrency=concurrency,
            question_timeout_s=question_timeout_s,
            progress_callback=progress_callback,
        )


PLUGIN = VikingBotPlugin()

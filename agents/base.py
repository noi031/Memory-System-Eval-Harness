"""Agent plugin interface for the evaluation harness.

Each agent under test implements AgentPlugin. The harness supports two
evaluation modes:

- Benchmark QA: the harness calls run_qa() with a batch of tasks.
- Dynamic simulation: the harness calls the step-based methods
  (create_session -> simulate_typing -> send_message).

Only setup() is required. Step-based methods and run_qa() raise
NotImplementedError by default; each plugin overrides the ones it supports.

Memory injection (writing background memories into a memory backend) is
NOT an agent operation -- it is handled entirely by the memory plugin's
client (open_session / add_message / commit / poll_commit). The agent
plugin never participates in memory injection.
"""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from memories import MemoryClient
from shared.llm_client import LLMClient
from shared.qa import QAResult


@dataclass
class TypingResult:
    """Result of typing simulation (prefill).

    Attributes:
        committed: whether the agent accepted the prefill.
        memory_items: memory items recalled during prefill, if any.
    """

    committed: bool = False
    memory_items: list[dict] = field(default_factory=list)


@dataclass
class AgentResponse:
    """Standardized response from an agent.

    Standard fields cover what the evaluation flow needs (text, latency,
    tokens, recalled memories). Agent-specific metrics go in ``extra``.
    """

    text: str = ""
    ttft_ms: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    prefetch_committed: bool = False
    memory_items: list[dict] = field(default_factory=list)
    error: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AgentDescriptor:
    """Metadata describing an agent plugin's identity and capabilities."""

    id: str
    name: str
    description: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)


class AgentPlugin(ABC):
    """Abstract agent interface for evaluation.

    Each agent under test implements this interface. The harness calls only
    these methods; it never touches agent-specific HTTP APIs directly.

    Benchmark QA flow:
        setup(config) -> run_qa(tasks, memory_client, llm, ...)

    Dynamic simulation flow:
        setup(config) -> create_session ->
        simulate_typing -> send_message -> teardown

    Memory injection is NOT part of this interface. The harness injects
    background memories directly through the memory plugin's client
    (open_session / add_message / commit / poll_commit), bypassing the
    agent entirely.
    """

    @abstractmethod
    def setup(self, config: dict) -> None:
        """Initialize the agent client.

        config is a flat dict of all CLI args. Each plugin reads the
        fields it cares about and ignores the rest.
        """

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Add plugin-specific CLI arguments to *parser*.

        Each plugin declares the arguments it needs (e.g. EchoMem URL,
        LLM credentials, agent-specific endpoints). The runner calls
        this before ``parse_args()`` so that ``--help`` shows the
        plugin's args. The default implementation adds nothing.
        """

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
        """Run benchmark QA tasks with the agent.

        Default implementation raises NotImplementedError. Plugins that
        support benchmark QA override this.
        """
        raise NotImplementedError

    def create_session(self, title: str = "") -> str:
        """Create a QA session. Returns session_id."""
        raise NotImplementedError

    def send_message(
        self, session_id: str, message: str, context_path: str = "/"
    ) -> AgentResponse:
        """Send a message and receive the agent's response.

        If simulate_typing was called beforehand, the plugin internally
        links the prefill to this message (the caller does not need to
        pass any prefill identifier).
        """
        raise NotImplementedError

    @property
    def supports_typing_simulation(self) -> bool:
        """Whether this agent supports typing simulation (prefill)."""
        return False

    def simulate_typing(
        self,
        session_id: str,
        context_path: str,
        text: str,
        speed_ms: int = 200,
        jitter_ms: int = 20,
    ) -> TypingResult | None:
        """Simulate typing to trigger prefill.

        Returns None if the agent does not support typing simulation.
        The default implementation always returns None.
        """
        return None

    def teardown(self) -> None:
        """Release resources. Default: no-op."""
        pass

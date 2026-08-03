"""Agent plugin interface for the evaluation harness.

Each agent under test implements AgentPlugin. The harness calls the
step-based methods for both benchmark and dynamic evaluation:

    setup(config) -> inject_memories(memories) ->
    (create_session -> [simulate_typing] -> send_message)* -> getlog -> teardown

Only setup() is required. Other methods raise NotImplementedError by
default; each plugin overrides the ones it supports.

Memory injection (writing background memories into a memory backend) is
part of this interface. Plugins that support it override inject_memories();
the default is a no-op that returns session_id unchanged.
"""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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

    Evaluation lifecycle:
        setup(config) -> inject_memories(memories) ->
        (create_session -> [simulate_typing] -> send_message)* -> getlog -> teardown

    Memory injection is part of this interface. Plugins that support it
    override inject_memories(); the default is a no-op returning session_id.
    """

    @abstractmethod
    def setup(self, config: dict) -> None:
        """Initialize the agent client and memory backend.

        config is a flat dict of all CLI args. Each plugin reads the
        fields it cares about and ignores the rest. Plugins that support
        memory injection create self.memory_client here.
        """

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Add plugin-specific CLI arguments to *parser*.

        Each plugin declares the arguments it needs (e.g. EchoMem URL,
        LLM credentials, agent-specific endpoints). The runner calls
        this before ``parse_args()`` so that ``--help`` shows the
        plugin's args. The default implementation adds nothing.
        """

    def inject_memories(
        self,
        memories: list[dict],
        *,
        backend: str = "echomem",
        session_id: str = "",
    ) -> str:
        """Inject all memories into the specified backend.

        Opens a session (or reuses session_id), adds each memory as a
        user message, commits, and polls until extraction completes.
        Returns the session_id used.

        Each dict has at least "text"; "time" (if present) is passed as
        created_at. The *backend* parameter selects which memory backend
        to use ("echomem" or "openviking"). Plugins that support multiple
        backends use this to select; single-backend plugins ignore it.

        Default is a no-op returning session_id unchanged. Plugins that
        support memory injection override this.
        """
        return session_id

    def create_session(self, title: str = "") -> str:
        """Create a QA session. Returns session_id."""
        raise NotImplementedError

    def send_message(
        self,
        session_id: str,
        message: str,
        context_path: str = "/",
        *,
        extra: dict | None = None,
    ) -> AgentResponse:
        """Send a message and receive the agent's response.

        If simulate_typing was called beforehand, the plugin internally
        links the prefill to this message (the caller does not need to
        pass any prefill identifier).

        The *extra* dict carries benchmark-specific context (question_time,
        question_id, sample_id, category, answer, etc.) that plugins may
        use. Dynamic mode callers omit it; plugins must tolerate None.
        """
        raise NotImplementedError

    @property
    def qa_profile(self) -> str:
        """The QA profile this plugin uses (e.g. 'vikingboat0411').

        Defaults to the plugin's id; plugins that resolve a specific
        profile in setup() override this.
        """
        return self.descriptor.id

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

    @abstractmethod
    def getlog(self) -> str:
        """Fetch agent/memory backend logs and return as a JSON string.

        Called by the evaluation runner at the end of a run. The runner
        saves the returned JSON string to the result directory.
        """

    def teardown(self) -> None:
        """Release resources. Default: no-op."""
        pass

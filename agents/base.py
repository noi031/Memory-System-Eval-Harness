"""Agent plugin abstract interface for the evaluation harness.

Each agent under test implements AgentPlugin. The evaluation flow calls
only these methods; it never touches agent-specific HTTP APIs directly.
"""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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


class AgentPlugin(ABC):
    """Abstract agent interface for evaluation.

    Each agent under test implements this interface. The evaluation flow
    (generate / replay) calls only these methods:

    1. setup(config) -- initialize (login, resolve credentials, etc.)
    2. inject_memories(memories) -- inject background memories
    3. create_session(title) -- create a QA session
    4. simulate_typing(...) -- optional typing simulation (prefill)
    5. send_message(session_id, message) -- send query, receive response
    6. teardown() -- cleanup
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

    @abstractmethod
    def inject_memories(self, memories: list[dict], session_id: str = "") -> str:
        """Inject background memories into the agent's memory backend.

        memories: list of {"id": str, "text": str, "source_round"?: int}.
        If session_id is provided, the plugin may reuse that session and
        skip injection if it already contains data.
        Returns an identifier for the injection context (e.g. session_id).
        """

    @abstractmethod
    def create_session(self, title: str = "") -> str:
        """Create a QA session. Returns session_id."""

    @abstractmethod
    def send_message(
        self, session_id: str, message: str, context_path: str = "/"
    ) -> AgentResponse:
        """Send a message and receive the agent's response.

        If simulate_typing was called beforehand, the plugin internally
        links the prefill to this message (the caller does not need to
        pass any prefill identifier).
        """

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

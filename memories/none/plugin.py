"""No-op memory plugin for evaluations that test agents without a memory system.

Design intent: the default memory plugin when --memory-plugin is not
specified. setup() creates a NullMemoryClient that implements the full
MemoryClient protocol with no-op methods, so benchmark code that calls
memory_client.search(...) etc. works without conditional branches.
inject_memories() returns an empty session_id (nothing to inject).
teardown() is a no-op.
"""

from __future__ import annotations

from typing import Any

from memories.base import (
    MemoryCapability,
    MemoryDescriptor,
    MemoryPlugin,
    SearchResult,
)


class NullMemoryClient:
    """MemoryClient implementation that does nothing and returns empty results."""

    account = "default"
    user_id = "default"
    agent_id = "default"
    auth_key = ""

    def health(self) -> dict[str, Any]:
        return {"status": "ok"}

    def open_session(self, title: str = "") -> str:
        return ""

    def add_message(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}

    def commit_session(self, session_id: str, keep_recent_count: int = 0) -> str:
        return ""

    def poll_commit(self, session_id: str, archive_id: str, timeout_s: float = 600.0, poll_interval_s: float = 2.0):
        from memories.base import CommitResult
        return CommitResult(session_id, archive_id, "completed", 0.0, 0)

    def has_archives(self, session_id: str) -> bool:
        return False

    def search(
        self,
        query: str,
        top_k: int = 10,
        session_id: str = "",
        agent_id: str = "",
        timeout_s: float | None = None,
    ) -> list[SearchResult]:
        return []

    def fs_read(self, uri: str, *, timeout_s: float | None = None) -> str:
        return ""

    def fs_list(self, uri: str, *, recursive: bool = False, timeout_s: float | None = None) -> list[dict[str, Any]]:
        return []

    def fs_glob(self, pattern: str, *, timeout_s: float | None = None) -> list[dict[str, Any]]:
        return []

    def close(self) -> None:
        pass

    def __enter__(self) -> "NullMemoryClient":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class NoneMemoryPlugin(MemoryPlugin):
    descriptor = MemoryDescriptor(
        id="none",
        name="None",
        status="active",
        description="No memory system -- all memory operations are no-ops.",
        capabilities=(),
    )

    def setup(self, config: dict) -> None:
        self.client = NullMemoryClient()

    def inject_memories(self, memories: list[dict], session_id: str = "") -> str:
        return session_id

    def teardown(self) -> None:
        pass


PLUGIN = NoneMemoryPlugin()

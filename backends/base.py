"""Common contracts for memory backend plugins."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .types import SearchResult


@dataclass(frozen=True)
class BackendCapability:
    name: str
    description: str


@dataclass(frozen=True)
class BackendDescriptor:
    id: str
    name: str
    status: str
    description: str
    capabilities: tuple[BackendCapability, ...] = field(default_factory=tuple)

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["capabilities"] = [asdict(item) for item in self.capabilities]
        return payload


@dataclass(frozen=True)
class BackendConfig:
    base_url: str
    api_key: str = ""
    account: str = "default"
    user_id: str = "default"
    agent_id: str = "default"
    workspace: str = ""
    timeout_s: float = 60.0
    max_retries: int = 3


class MemoryBackendPlugin(Protocol):
    descriptor: BackendDescriptor

    def create_client(self, config: BackendConfig):
        """Create a backend client for an evaluation run."""


class MemoryClient(Protocol):
    def search(
        self,
        query: str,
        top_k: int = 10,
        session_id: str = "",
        agent_id: str = "",
        timeout_s: float | None = None,
    ) -> list[SearchResult]:
        """Retrieve memory relevant to a query."""

    def fs_read(self, uri: str, *, timeout_s: float | None = None) -> str:
        """Read full content for a backend memory URI."""

    def fs_list(
        self,
        uri: str,
        *,
        recursive: bool = False,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """List public backend filesystem entries."""

    def fs_glob(
        self,
        pattern: str,
        *,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """Find public backend filesystem entries by glob pattern."""


REQUIRED_CAPABILITIES = (
    "session_write",
    "commit_session",
    "relevant_memory",
    "content_read",
)


def validate_backend(plugin: MemoryBackendPlugin) -> dict[str, Any]:
    capabilities = tuple(item.name for item in plugin.descriptor.capabilities)
    missing = tuple(name for name in REQUIRED_CAPABILITIES if name not in capabilities)
    return {
        "backend_id": plugin.descriptor.id,
        "status": "ok" if not missing else "fail",
        "ok": not missing,
        "required_capabilities": REQUIRED_CAPABILITIES,
        "capabilities": capabilities,
        "missing_required_capabilities": missing,
        "has_create_client": callable(getattr(plugin, "create_client", None)),
    }

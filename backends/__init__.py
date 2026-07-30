"""Memory backend plugins and client registry."""

from .base import (
    BackendCapability,
    BackendConfig,
    BackendDescriptor,
    MemoryClient,
    MemoryBackendPlugin,
    validate_backend,
)
from .registry import (
    available_backends,
    create_backend_client,
    get_backend_plugin,
)
from .types import CommitResult, SearchResult

__all__ = [
    "BackendCapability",
    "BackendConfig",
    "BackendDescriptor",
    "CommitResult",
    "MemoryClient",
    "MemoryBackendPlugin",
    "SearchResult",
    "available_backends",
    "create_backend_client",
    "get_backend_plugin",
    "validate_backend",
]

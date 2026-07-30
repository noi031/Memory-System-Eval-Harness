"""EchoMemory backend."""

from .client import EchoMemClient, SearchResult
from .plugin import PLUGIN, EchoMemoryPlugin

__all__ = [
    "EchoMemClient",
    "EchoMemoryPlugin",
    "PLUGIN",
    "SearchResult",
]

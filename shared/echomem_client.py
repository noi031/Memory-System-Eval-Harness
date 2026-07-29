"""Compatibility imports for the EchoMemory backend client.

New code should import from ``backends.echomemory``. This module remains while
external callers transition to the backend registry.
"""

from backends.echomemory.client import CommitResult, EchoMemClient, SearchResult

__all__ = ["CommitResult", "EchoMemClient", "SearchResult"]

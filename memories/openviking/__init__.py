"""OpenViking backend."""

from .client import OpenVikingClient, SearchResult
from .plugin import PLUGIN, OpenVikingPlugin

__all__ = [
    "OpenVikingClient",
    "OpenVikingPlugin",
    "PLUGIN",
    "SearchResult",
]

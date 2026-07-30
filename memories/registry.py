"""Memory plugin registry: dynamic loading by name.

Each plugin lives in ``memories/<name>/plugin.py`` and exports a subclass of
MemoryPlugin. New plugins are added by creating a directory -- no need to
modify this file.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from .base import MemoryPlugin, validate_memory

logger = logging.getLogger("memory_registry")

_KNOWN_PLUGINS = ("echomemory", "openviking", "none")


def get_plugin_class(name: str) -> type[MemoryPlugin]:
    """Return the MemoryPlugin subclass for *name* without instantiating."""
    module = importlib.import_module(f"memories.{name}.plugin")
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, MemoryPlugin)
            and attr is not MemoryPlugin
        ):
            return attr
    raise ValueError(f"memories.{name}.plugin 中未找到 MemoryPlugin 子类")


def load_memory_plugin(name: str, config: dict) -> MemoryPlugin:
    """Load a memory plugin by name and call setup."""
    plugin_cls = get_plugin_class(name)
    plugin = plugin_cls()
    plugin.setup(config)
    logger.info("loaded memory plugin: %s (%s)", name, plugin_cls.__name__)
    return plugin


def get_memory_plugin(memory_id: str) -> MemoryPlugin:
    """Return a memory plugin instance by stable id."""
    return load_memory_plugin(memory_id, {})


def available_memories() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for memory_id in _KNOWN_PLUGINS:
        plugin = get_memory_plugin(memory_id)
        row = plugin.descriptor.public()
        row["contract"] = validate_memory(plugin)
        rows.append(row)
    return rows

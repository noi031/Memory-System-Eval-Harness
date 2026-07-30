"""Agent plugin registry: dynamic loading by name.

Each plugin lives in ``agents/<name>/plugin.py`` and exports a subclass of
AgentPlugin. New plugins are added by creating a directory -- no need to
modify this file.
"""

from __future__ import annotations

import importlib
import logging

from agents.base import AgentPlugin, BenchmarkAgentPlugin

logger = logging.getLogger("agent_registry")


def get_plugin_class(name: str) -> type[AgentPlugin]:
    """Return the AgentPlugin subclass for *name* without instantiating."""
    module = importlib.import_module(f"agents.{name}.plugin")
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, AgentPlugin)
            and attr is not AgentPlugin
        ):
            return attr
    raise ValueError(f"agents.{name}.plugin 中未找到 AgentPlugin 子类")


def load_agent_plugin(name: str, config: dict) -> AgentPlugin:
    """Load an agent plugin by name and call setup.

    Searches ``agents.<name>.plugin`` for a class that is a subclass of
    AgentPlugin (but not AgentPlugin itself). Instantiates it and calls
    ``setup(config)``.
    """
    plugin_cls = get_plugin_class(name)
    plugin = plugin_cls()
    plugin.setup(config)
    logger.info("loaded agent plugin: %s (%s)", name, plugin_cls.__name__)
    return plugin


def get_agent_plugin(agent_id: str) -> BenchmarkAgentPlugin:
    """Return a benchmark-native agent implementation by stable id."""
    normalized = str(agent_id or "").strip().lower()
    if normalized == "vikingbot":
        from agents.vikingbot.plugin import PLUGIN

        return PLUGIN
    raise ValueError(f"unknown benchmark agent plugin: {agent_id}")

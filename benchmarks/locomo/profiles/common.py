"""Shared LoCoMo VikingBot profile configuration."""

from __future__ import annotations

import os
from pathlib import Path


AGENT_PLUGIN = "vikingbot"
VIKINGBOT_WORKSPACE = (
    Path(__file__).resolve().parents[3] / "agents" / "vikingbot" / "bootstrap"
)


def default_vikingbot_workspace() -> str:
    return os.getenv("VIKINGBOT_WORKSPACE", str(VIKINGBOT_WORKSPACE))

"""Reproducible LoCoMo VikingBot profiles migrated from the v2 workbench."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


HISTORICAL_PROFILE = "vikingbot-historical-75"
HISTORICAL_REFERENCE = "2026-07-17 EchoMemory conv-30 run: 61/81 (75.31%)"
HISTORICAL_PROMPT_COMMIT = "1f027927d2557dc67948499f9cb975bb664219df"
HISTORICAL_PROFILE_COMMIT = "c6bf307243866d02117bc71d05803a3770c5fb1c"
HISTORICAL_SOURCE = {
    "repository": "memory-benchmark-workbench",
    "prompt_commit": HISTORICAL_PROMPT_COMMIT,
    "prompt_path": "scripts/openviking_memory_qa.py",
    "profile_commit": HISTORICAL_PROFILE_COMMIT,
    "profile_path": "scripts/echomemory_evaluation_profiles.py",
    "adaptation": (
        "Prompt layout and iterative loop preserved; OpenViking tool names "
        "and transport were mapped to read-only EchoMemory HTTP tools."
    ),
}
AGENT_PLUGIN = "vikingbot"
HISTORICAL_WORKSPACE = (
    Path(__file__).resolve().parents[3] / "agents" / "vikingbot" / "bootstrap"
)
HISTORICAL_SETTINGS: dict[str, Any] = {
    "top_k": 25,
    "initial_min_score": 0.0,
    "memory_budget_chars": 6000,
    "user_memory_budget_chars": 4000,
    "agent_memory_budget_chars": 2000,
    "tool_search_limit": 25,
    "tool_min_score": 0.0,
    "tool_search_pool_multiplier": 4,
    "tool_set": "search_read",
    "max_iterations": 50,
    "question_timeout_s": 600.0,
    "llm_max_tokens": 1024,
    "llm_retries": 5,
    "initial_tool_prefetch": False,
    "fallback_to_one_shot": False,
    "tool_names": ("memory_search", "memory_read_many"),
    "agent_plugin": AGENT_PLUGIN,
}


def default_vikingbot_workspace() -> str:
    return os.getenv("VIKINGBOT_WORKSPACE", str(HISTORICAL_WORKSPACE))

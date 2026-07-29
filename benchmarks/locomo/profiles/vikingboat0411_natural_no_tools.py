"""Natural one-shot baseline derived from VikingBot v0.4.11."""

from __future__ import annotations

from typing import Any

from .vikingboat0411 import VIKINGBOAT_0411_SETTINGS


VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE = (
    "vikingboat0411-natural-no-tools"
)
VIKINGBOAT_0411_NATURAL_NO_TOOLS_REFERENCE = (
    "VikingBot v0.4.11 natural no-tool baseline with EchoMemory initial "
    "retrieval; no score claim"
)
VIKINGBOAT_0411_NATURAL_NO_TOOLS_SOURCE = {
    "repository": "openviking",
    "version": "v0.4.11",
    "source_root": "/Users/chx/Code/openviking/versions/v0.4.11",
    "prompt_path": "bot/vikingbot/agent/context.py",
    "adaptation": (
        "Keeps the VikingBot identity, question envelope, initial top-25 "
        "EchoMemory retrieval, and generation settings. Removes memory-tool "
        "instructions and URI-only entries, exposes no tools, and asks for "
        "a direct answer using only complete injected memory excerpts."
    ),
}
VIKINGBOAT_0411_NATURAL_NO_TOOLS_SETTINGS: dict[str, Any] = dict(
    VIKINGBOAT_0411_SETTINGS
)

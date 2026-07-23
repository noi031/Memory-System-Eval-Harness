#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for candidate in (ROOT, SCRIPTS):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

from echomemory_qa_prompting import build_vikingbot_agent_aligned_messages
from echomemory_qa_tools import echomemory_search_tool_definition


def messages_for(tool_set: str, **overrides: object) -> list[dict[str, str]]:
    values: dict[str, object] = {
        "vikingboat_tool_loop": True,
        "tool_set": tool_set,
        "vikingbot_channel": "cli",
        "user_id": "user",
        "prompt_context_mode": "vikingbot_aligned",
        "prompt_system_mode": "vikingbot_aligned",
        "session_context_mode": "single",
        "current_time_mode": "runtime",
    }
    values.update(overrides)
    args = argparse.Namespace(**values)
    job = SimpleNamespace(
        question="When did the remembered event happen?",
        query_time="2026-07-17",
    )
    return build_vikingbot_agent_aligned_messages(
        args,
        job,
        "A remembered event happened on 2023-01-20.",
        "",
        True,
    )


def main() -> None:
    search_read_messages = messages_for("search_read")
    combined = "\n".join(message["content"] for message in search_read_messages)
    assert len(search_read_messages) == 3
    assert "EchoMemory" in combined
    assert "memory_search" in combined
    assert "memory_read_many" in combined
    assert "OpenViking" not in combined
    assert "openviking_" not in combined
    assert "memory_grep" not in combined
    assert "preserve exact names, dates, and values" not in combined
    assert "Stop when the evidence is sufficient" not in combined
    assert "Evaluation alignment" not in combined
    assert "Focused evidence" not in combined
    assert "high-signal lines" not in combined
    assert "For questions about the user's remembered facts" in combined
    assert "A previous empty search result does not prove" in combined
    assert "Reply in the same language as the user's query" in combined
    assert "Group chat session" not in combined
    assert search_read_messages[-1]["content"] == (
        "Current date: 2026-07-17. Answer the question directly: "
        "When did the remembered event happen?"
    )
    search_properties = echomemory_search_tool_definition()["function"]["parameters"]["properties"]
    assert list(search_properties) == ["query"]
    legacy_search_properties = echomemory_search_tool_definition(
        argparse.Namespace(search_tool_target_uri_schema=True)
    )["function"]["parameters"]["properties"]
    assert list(legacy_search_properties) == ["query", "target_uri"]

    group_messages = messages_for("search_read", session_context_mode="group")
    group_combined = "\n".join(message["content"] for message in group_messages)
    assert "Group chat session" in group_combined
    assert "Evaluation alignment" not in group_combined

    question_time_messages = messages_for("search_read", current_time_mode="question_time")
    assert "## Current Time: 2026-07-17" in question_time_messages[1]["content"]
    assert "Group chat session" not in question_time_messages[1]["content"]

    legacy_system_messages = messages_for("search_read", prompt_system_mode="legacy_eval")
    legacy_system_combined = "\n".join(message["content"] for message in legacy_system_messages)
    assert "Evaluation alignment" in legacy_system_combined
    assert "preserve exact names, dates, and values" in legacy_system_combined
    assert "Group chat session" not in legacy_system_combined
    assert "## Current Time: 2026-07-17" not in legacy_system_messages[1]["content"]

    legacy_bundle_messages = messages_for("search_read", prompt_context_mode="legacy_eval")
    legacy_bundle_combined = "\n".join(message["content"] for message in legacy_bundle_messages)
    assert "Evaluation alignment" in legacy_bundle_combined
    assert "Group chat session" in legacy_bundle_combined
    assert "## Current Time: 2026-07-17" in legacy_bundle_messages[1]["content"]

    search_only_messages = messages_for("search_only")
    search_only_combined = "\n".join(message["content"] for message in search_only_messages)
    assert "memory_search" in search_only_combined
    assert "memory_read_many" not in search_only_combined

    full_tool_messages = messages_for("vikingboat_default")
    full_combined = "\n".join(message["content"] for message in full_tool_messages)
    assert "memory_search" in full_combined
    assert "memory_read_many" in full_combined
    assert "memory_grep" not in full_combined
    assert "memory_list" not in full_combined
    assert "memory_glob" not in full_combined

    print("EchoMemory agent prompt smoke passed")


if __name__ == "__main__":
    main()

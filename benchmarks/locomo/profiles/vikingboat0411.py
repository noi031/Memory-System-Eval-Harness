"""VikingBot v0.4.11 agent behavior adapted to EchoMemory tools."""

from __future__ import annotations

from typing import Any


VIKINGBOAT_0411_PROFILE = "vikingboat0411"
VIKINGBOAT_0411_REFERENCE = (
    "VikingBot v0.4.11 prompt and loop with EchoMemory read-only tools; "
    "no score claim"
)
VIKINGBOAT_0411_SOURCE = {
    "repository": "openviking",
    "version": "v0.4.11",
    "source_root": "/Users/chx/Code/openviking/versions/v0.4.11",
    "driver_path": "benchmark/locomo/vikingbot/run_eval.py",
    "prompt_path": "bot/vikingbot/agent/context.py",
    "bootstrap_paths": ["bot/workspace/SOUL.md", "bot/workspace/TOOLS.md"],
    "tools_path": "bot/vikingbot/agent/tools/ov_file.py",
    "loop_path": "bot/vikingbot/agent/loop.py",
    "adaptation": (
        "The VikingBot prompt, retrieval workflow, question envelope, and "
        "loop are retained. OpenViking tools are replaced model-side and "
        "runtime-side by EchoMemory read-only memory_* tools."
    ),
}
VIKINGBOAT_0411_SETTINGS: dict[str, Any] = {
    "top_k": 25,
    "initial_min_score": 0.1,
    "memory_budget_chars": 4000,
    "user_memory_budget_chars": 4000,
    "agent_memory_budget_chars": 2000,
    "tool_search_limit": 25,
    "tool_min_score": 0.35,
    "tool_search_pool_multiplier": 1,
    "tool_set": "vikingbot_echo_native",
    "max_iterations": 50,
    "question_timeout_s": 600.0,
    "llm_max_tokens": 4096,
    "llm_retries": 5,
    "answer_temperature": 0.7,
    "omit_answer_temperature": False,
    "initial_retrieval_query_mode": "vikingbot_prompt",
    "retrieval_uri_dedup": False,
    "tool_query_dedup_scope": "none",
    "search_tool_target_uri_schema": True,
    "session_context_mode": "single",
    "current_time_mode": "runtime",
    "tool_names": (
        "memory_search",
        "memory_read_many",
        "memory_list",
        "memory_grep",
        "memory_glob",
    ),
    "agent_plugin": "vikingbot",
}

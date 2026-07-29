"""VikingBot alignment settings from the committed v2 branch head."""

from __future__ import annotations

from typing import Any


V2_ALIGNED_PROFILE = "vikingbot-v2-head"
V2_ALIGNED_COMMIT = "a146a246c2fcce128229d19e05c87228affd829d"
V2_ALIGNED_REFERENCE = (
    "Committed v2 VikingBoat alignment profile; no score claim"
)
V2_ALIGNED_SOURCE = {
    "repository": "memory-benchmark-workbench",
    "commit": V2_ALIGNED_COMMIT,
    "settings_path": "memory/vikingboat_alignment.py",
    "prompt_path": "scripts/echomemory_qa_prompting.py",
    "tools_path": "scripts/echomemory_qa_tools.py",
    "loop_path": "scripts/echomemory_memory_qa.py",
    "adaptation": (
        "EchoMemory-only read tools retained; SDK and local workspace "
        "fallbacks are excluded in favor of public HTTP endpoints."
    ),
}
V2_ALIGNED_SETTINGS: dict[str, Any] = {
    "top_k": 30,
    "initial_min_score": 0.1,
    "memory_budget_chars": 6000,
    "user_memory_budget_chars": 4000,
    "agent_memory_budget_chars": 2000,
    "tool_search_limit": 20,
    "tool_min_score": 0.35,
    "tool_search_pool_multiplier": 1,
    "tool_set": "vikingbot_native_safe",
    "max_iterations": 50,
    "question_timeout_s": 600.0,
    "llm_max_tokens": 1024,
    "llm_retries": 5,
    "initial_tool_prefetch": False,
    "fallback_to_one_shot": False,
    "tool_names": (
        "memory_search",
        "memory_read_many",
        "memory_list",
        "memory_grep",
        "memory_glob",
    ),
    "agent_plugin": "vikingbot",
}

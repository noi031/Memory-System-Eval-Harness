"""LoCoMo test-best profile from the committed v2 evaluation profiles."""

from __future__ import annotations

from typing import Any


TEST_BEST_PROFILE = "test-best"
TEST_BEST_COMMIT = "c6bf307243866d02117bc71d05803a3770c5fb1c"
TEST_BEST_REFERENCE = (
    "v2 PR125-memory reference: 69/81 (85.19%); "
    "same head_clean memory historical run: 56/81 (69.14%)"
)
TEST_BEST_SOURCE = {
    "repository": "memory-benchmark-workbench",
    "commit": TEST_BEST_COMMIT,
    "profile_path": "scripts/echomemory_evaluation_profiles.py",
    "prompt_path": "scripts/echomemory_qa_prompting.py",
    "tools_path": "scripts/echomemory_qa_tools.py",
    "loop_path": "scripts/echomemory_memory_qa.py",
    "adaptation": (
        "Committed test-best prompt and loop settings retained; EchoMemory "
        "access uses the current read-only HTTP backend tools. The 69/81 "
        "reference used a different PR125 memory workspace and is not a "
        "head_clean reproduction target."
    ),
}
TEST_BEST_SETTINGS: dict[str, Any] = {
    "top_k": 25,
    "initial_min_score": 0.0,
    "memory_budget_chars": 6000,
    "user_memory_budget_chars": 4000,
    "agent_memory_budget_chars": 2000,
    "tool_search_limit": 20,
    "tool_min_score": 0.0,
    "tool_search_pool_multiplier": 1,
    "tool_set": "vikingbot_native_safe",
    "max_iterations": 50,
    "question_timeout_s": 600.0,
    "llm_max_tokens": 1024,
    "llm_retries": 5,
    "answer_temperature": 0.7,
    "omit_answer_temperature": False,
    "initial_retrieval_query_mode": "vikingbot_prompt",
    "retrieval_uri_dedup": False,
    "tool_query_dedup_scope": "turn",
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

"""LoCoMo profile for the actual 63/81 head_clean reference run."""

from __future__ import annotations

from typing import Any


LEGACY_77_PROFILE = "legacy-77"
LEGACY_77_REFERENCE = (
    "2026-07-13 head_clean conv-30 run: 63/81 (77.78%)"
)
LEGACY_77_SOURCE = {
    "repository": "memory-benchmark-workbench",
    "reference_run": (
        "runs/head_clean_top25_http_messages_full_20260713"
    ),
    "reference_memory": {
        "workspace": "/Users/chx/echomem_eval_matrix_20260712/head_clean",
        "account": "locomo-conv30-headclean-matrix-20260712",
        "session_count": 19,
    },
    "prompt_artifact": (
        "echomemory_memory_qa_results.csv:prompt_preview"
    ),
    "prompt_sha256": (
        "de7c842ef1c25867638ae7da5c66e2a0ec0db1f8207ec31c4b5c5240bc84ff02"
    ),
    "source_commit": "cee1251219165014bb1bb4055c4b4406bc6e3e30",
    "source_paths": [
        "scripts/echomemory_memory_qa.py",
        "scripts/echomemory_qa_prompting.py",
        "scripts/echomemory_qa_tools.py",
    ],
    "adaptation": (
        "The exact persisted system-prompt text and run settings are "
        "vendored. HTTP URI handling includes later correctness fixes."
    ),
}
LEGACY_77_SETTINGS: dict[str, Any] = {
    "top_k": 25,
    "initial_min_score": 0.0,
    "memory_budget_chars": 6000,
    "user_memory_budget_chars": 4000,
    "agent_memory_budget_chars": 2000,
    "tool_search_limit": 25,
    "tool_min_score": 0.0,
    "tool_search_pool_multiplier": 1,
    "tool_set": "vikingbot_native_safe",
    "max_iterations": 50,
    "question_timeout_s": 600.0,
    "llm_max_tokens": 1024,
    "llm_retries": 5,
    "answer_temperature": 0.7,
    "omit_answer_temperature": True,
    "initial_retrieval_query_mode": "question_only",
    "retrieval_uri_dedup": False,
    "tool_query_dedup_scope": "none",
    "search_tool_target_uri_schema": True,
    "session_context_mode": "group",
    "current_time_mode": "question_time",
    "tool_names": (
        "memory_search",
        "memory_read_many",
        "memory_list",
        "memory_grep",
        "memory_glob",
    ),
    "agent_plugin": "vikingbot",
}

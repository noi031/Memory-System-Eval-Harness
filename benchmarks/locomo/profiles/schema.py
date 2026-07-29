"""Typed and validated LoCoMo QA profile definitions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


_REQUIRED_FIELDS = {
    "top_k",
    "initial_min_score",
    "memory_budget_chars",
    "user_memory_budget_chars",
    "agent_memory_budget_chars",
    "tool_search_limit",
    "tool_min_score",
    "tool_search_pool_multiplier",
    "tool_set",
    "max_iterations",
    "question_timeout_s",
    "llm_max_tokens",
    "llm_retries",
    "tool_names",
    "agent_plugin",
}


@dataclass(frozen=True)
class ProfileSettings:
    top_k: int
    initial_min_score: float
    memory_budget_chars: int
    user_memory_budget_chars: int
    agent_memory_budget_chars: int
    tool_search_limit: int
    tool_min_score: float
    tool_search_pool_multiplier: int
    tool_set: str
    max_iterations: int
    question_timeout_s: float
    llm_max_tokens: int
    llm_retries: int
    tool_names: tuple[str, ...]
    agent_plugin: str
    answer_temperature: float = 0.7
    omit_answer_temperature: bool = True
    initial_retrieval_query_mode: str = "question_only"
    retrieval_uri_dedup: bool = True
    tool_query_dedup_scope: str = "question"
    search_tool_target_uri_schema: bool = False
    session_context_mode: str = ""
    current_time_mode: str = ""
    initial_tool_prefetch: bool = False
    fallback_to_one_shot: bool = False

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ProfileSettings":
        unknown = set(values) - set(cls.__dataclass_fields__)
        missing = _REQUIRED_FIELDS - set(values)
        if unknown or missing:
            details = []
            if unknown:
                details.append(f"unknown fields: {sorted(unknown)}")
            if missing:
                details.append(f"missing fields: {sorted(missing)}")
            raise ValueError("invalid LoCoMo profile settings: " + "; ".join(details))
        settings = cls(**dict(values))
        settings.validate()
        return settings

    def validate(self) -> None:
        positive = {
            "top_k": self.top_k,
            "memory_budget_chars": self.memory_budget_chars,
            "user_memory_budget_chars": self.user_memory_budget_chars,
            "agent_memory_budget_chars": self.agent_memory_budget_chars,
            "tool_search_limit": self.tool_search_limit,
            "tool_search_pool_multiplier": self.tool_search_pool_multiplier,
            "max_iterations": self.max_iterations,
            "llm_max_tokens": self.llm_max_tokens,
        }
        invalid_positive = [
            name for name, value in positive.items() if int(value) < 1
        ]
        if invalid_positive:
            raise ValueError(
                "LoCoMo profile values must be >= 1: "
                + ", ".join(invalid_positive)
            )
        if self.question_timeout_s < 0:
            raise ValueError("question_timeout_s must be >= 0")
        if self.llm_retries < 0:
            raise ValueError("llm_retries must be >= 0")
        if self.initial_min_score < 0 or self.tool_min_score < 0:
            raise ValueError("profile score thresholds must be >= 0")
        if self.initial_retrieval_query_mode not in {
            "question_only",
            "vikingbot_prompt",
        }:
            raise ValueError(
                "initial_retrieval_query_mode must be question_only or "
                "vikingbot_prompt"
            )
        if self.tool_query_dedup_scope not in {"none", "turn", "question"}:
            raise ValueError(
                "tool_query_dedup_scope must be none, turn, or question"
            )
        if self.session_context_mode not in {"", "single", "group"}:
            raise ValueError("session_context_mode must be single or group")
        if self.current_time_mode not in {"", "runtime", "question_time"}:
            raise ValueError(
                "current_time_mode must be runtime or question_time"
            )
        if not self.tool_names:
            raise ValueError("tool_names must not be empty")
        if not self.agent_plugin.strip():
            raise ValueError("agent_plugin must not be empty")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    reference: str
    source: Mapping[str, Any]
    settings: ProfileSettings

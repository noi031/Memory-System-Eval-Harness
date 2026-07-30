"""Plugin entrypoint for the VikingBot agent.

VikingBot is a tool-call agent that searches memory via OpenAI-compatible
tool definitions, iterates until it finds the answer, and returns a
sanitized final response. It is the default agent for the LoCoMo benchmark.

VikingBot creates its own MemoryClient and LLMClient in setup().
send_message() is a thin wrapper around answer_one_vikingbot_question()
that adapts the QAResult into an AgentResponse.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from plugins.base import AgentDescriptor, AgentResponse, AgentPlugin
from backends.echomem.client import EchoMemClient, _PENDING_CLEANUPS
from backends.openviking.client import OpenVikingClient
from backends.memory_args import add_memory_backend_args
from shared.eval_base import add_llm_args, add_qa_args
from shared.llm_client import LLMClient

from .runtime import answer_one_vikingbot_question

_DEFAULT_WORKSPACE = Path(__file__).resolve().parent / "bootstrap"


class VikingBotPlugin(AgentPlugin):
    """VikingBot: iterative tool-call agent with EchoMemory tools."""

    descriptor = AgentDescriptor(
        id="vikingbot",
        name="VikingBot",
        description="VikingBot prompt, memory tools, and iterative tool-call runtime.",
        capabilities=(
            "memory_search",
            "memory_read_many",
            "memory_list",
            "memory_grep",
            "memory_glob",
            "openai_tool_loop",
            "workspace_bootstrap",
        ),
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        add_llm_args(parser)
        add_qa_args(parser)
        add_memory_backend_args(parser, with_backend_choice=True)
        g = parser.add_argument_group("VikingBot")
        g.add_argument(
            "--vikingbot-workspace",
            default=os.getenv("VIKINGBOT_WORKSPACE", str(_DEFAULT_WORKSPACE)),
            help="Workspace supplying the SOUL.md and TOOLS.md bootstrap",
        )
        g.add_argument("--tool-search-limit", type=int, default=None)
        g.add_argument("--user-memory-budget-chars", type=int, default=None)
        g.add_argument("--agent-memory-budget-chars", type=int, default=None)
        g.add_argument("--max-iterations", type=int, default=None)
        g.add_argument("--initial-min-score", type=float, default=None)
        g.add_argument("--tool-min-score", type=float, default=None)
        g.add_argument("--tool-search-pool-multiplier", type=int, default=None)
        g.add_argument(
            "--tool-set",
            choices=[
                "search_read",
                "vikingbot_native_safe",
                "vikingbot_echo_native",
            ],
            default=None,
        )
        g.add_argument(
            "--tools",
            action=argparse.BooleanOptionalAction,
            default=True,
            help=(
                "Expose the profile's memory tools to the answer model; "
                "--no-tools keeps the same profile prompt and initial memory "
                "injection but performs a single model turn"
            ),
        )

    def setup(self, config: dict) -> None:
        self._memory_backend = config.get("memory_backend", "echomem")
        self._commit_timeout_s = float(config.get("commit_timeout_s", 0.0))
        self._commit_poll_interval_s = float(config.get("commit_poll_interval_s", 2.0))

        auth_key = config.get("echomem_auth_key", "")

        if self._memory_backend == "openviking":
            self.memory_client = OpenVikingClient(
                base_url=config.get("echomem_url", "http://127.0.0.1:19080"),
                api_key=auth_key,
                account=config.get("account", "default"),
                user_id=config.get("user_id", "default"),
                agent_id=config.get("agent_id", "default"),
                workspace=config.get("workspace", ""),
                timeout_s=float(config.get("timeout_s", 60.0)),
                max_retries=int(config.get("max_retries", 3)),
            )
        else:
            self.memory_client = EchoMemClient(
                base_url=config.get("echomem_url", "http://127.0.0.1:8010"),
                auth_key=auth_key,
                account=config.get("account", "default"),
                user_id=config.get("user_id", "default"),
                agent_id=config.get("agent_id", "default"),
                workspace=config.get("workspace", ""),
                timeout_s=float(config.get("timeout_s", 60.0)),
                max_retries=int(config.get("max_retries", 3)),
            )

        # Identity isolation
        benchmark_name = config.get("benchmark_name", "")
        run_id = config.get("run_id", "")
        reuse = config.get("reuse_memory_account", False)
        keep = config.get("keep_memory_account", False)

        if benchmark_name and run_id and not reuse:
            label = f"eval-{benchmark_name}-{run_id}"[:120]
            self.memory_client.provision_isolated_identity(label)
            if not keep and self._memory_backend == "echomem":
                _PENDING_CLEANUPS.append(self.memory_client)

        # LLM client for the tool-call loop
        self._llm = LLMClient(
            base_url=config.get("llm_base_url", ""),
            api_key=config.get("llm_api_key", ""),
            model=config.get("llm_model", "doubao-seed-2.0-pro"),
            temperature=config.get("llm_temperature", 0.7),
            max_tokens=config.get("llm_max_tokens", 2048),
            timeout_s=config.get("llm_timeout_s", 120.0),
            max_retries=config.get("llm_retries", 3),
        )

        # VikingBot-specific defaults (CLI args, may be None → function defaults)
        self._top_k = config.get("top_k") or 25
        self._tool_search_limit = config.get("tool_search_limit")
        self._user_memory_budget_chars = config.get("user_memory_budget_chars")
        self._agent_memory_budget_chars = config.get("agent_memory_budget_chars")
        self._max_iterations = config.get("max_iterations")
        self._initial_min_score = config.get("initial_min_score")
        self._tool_min_score = config.get("tool_min_score")
        self._tool_search_pool_multiplier = config.get("tool_search_pool_multiplier")
        self._tool_set = config.get("tool_set")
        self._tools_enabled = config.get("tools", True)
        self._vikingbot_workspace = config.get("vikingbot_workspace", "")
        self._question_timeout_s = float(config.get("question_timeout_s", 600.0))
        self._qa_profile = config.get("qa_profile", "vikingbot")
        # Params without CLI args (set by benchmark QA profiles in task dict)
        self._answer_temperature = config.get("answer_temperature")
        self._omit_answer_temperature = config.get("omit_answer_temperature")
        self._initial_retrieval_query_mode = config.get("initial_retrieval_query_mode")
        self._tool_query_dedup_scope = config.get("tool_query_dedup_scope")
        self._retrieval_uri_dedup = config.get("retrieval_uri_dedup")
        self._search_tool_target_uri_schema = config.get("search_tool_target_uri_schema")

    def inject_memories(
        self,
        memories: list[dict],
        *,
        backend: str = "",
        session_id: str = "",
    ) -> str:
        if not session_id:
            session_id = self.memory_client.open_session(title="inject")
        for mem in memories:
            text = str(mem.get("text") or "")
            if text:
                self.memory_client.add_message(
                    session_id,
                    "user",
                    text,
                    created_at=str(mem.get("time") or ""),
                )
        archive_id = self.memory_client.commit_session(session_id)
        commit = self.memory_client.poll_commit(
            session_id,
            archive_id,
            timeout_s=self._commit_timeout_s,
            poll_interval_s=self._commit_poll_interval_s,
        )
        if commit.status != "completed":
            raise RuntimeError(
                f"memory injection failed: status={commit.status} error={commit.error}"
            )
        return session_id

    def create_session(self, title: str = "") -> str:
        return self.memory_client.open_session(title=title or "qa")

    def send_message(
        self,
        session_id: str,
        message: str,
        context_path: str = "/",
        *,
        extra: dict | None = None,
    ) -> AgentResponse:
        """Send a question and receive the agent's response.

        For benchmark mode, *extra* carries the full task dict (built by
        the benchmark's build_qa_tasks). For dynamic mode, *extra* is None
        and the defaults stored in setup() are used.
        """
        extra = extra or {}

        # Required fields
        kwargs: dict[str, Any] = dict(
            question_id=extra.get("question_id", ""),
            question=extra.get("question", message),
            answer=extra.get("answer", ""),
            question_time=extra.get("question_time", ""),
            sample_id=extra.get("sample_id", ""),
            category=extra.get("category", ""),
        )

        # Optional fields: prefer extra (task dict), fall back to setup defaults.
        # None values are omitted so answer_one_vikingbot_question's own
        # defaults apply. profile_source is exempt because None is valid.
        optional: dict[str, Any] = {
            "top_k": extra.get("top_k", self._top_k),
            "tool_search_limit": extra.get("tool_search_limit", self._tool_search_limit),
            "tool_search_pool_multiplier": extra.get(
                "tool_search_pool_multiplier", self._tool_search_pool_multiplier,
            ),
            "initial_min_score": extra.get("initial_min_score", self._initial_min_score),
            "tool_min_score": extra.get("tool_min_score", self._tool_min_score),
            "tool_set": extra.get("tool_set", self._tool_set),
            "user_memory_budget_chars": extra.get(
                "user_memory_budget_chars", self._user_memory_budget_chars,
            ),
            "agent_memory_budget_chars": extra.get(
                "agent_memory_budget_chars", self._agent_memory_budget_chars,
            ),
            "max_iterations": extra.get("max_iterations", self._max_iterations),
            "question_timeout_s": extra.get(
                "question_timeout_s", self._question_timeout_s,
            ),
            "vikingbot_workspace": extra.get(
                "vikingbot_workspace", self._vikingbot_workspace,
            ),
            "qa_profile": extra.get("qa_profile", self._qa_profile),
            "answer_temperature": extra.get(
                "answer_temperature", self._answer_temperature,
            ),
            "omit_answer_temperature": extra.get(
                "omit_answer_temperature", self._omit_answer_temperature,
            ),
            "initial_retrieval_query_mode": extra.get(
                "initial_retrieval_query_mode", self._initial_retrieval_query_mode,
            ),
            "tool_query_dedup_scope": extra.get(
                "tool_query_dedup_scope", self._tool_query_dedup_scope,
            ),
            "retrieval_uri_dedup": extra.get(
                "retrieval_uri_dedup", self._retrieval_uri_dedup,
            ),
            "search_tool_target_uri_schema": extra.get(
                "search_tool_target_uri_schema", self._search_tool_target_uri_schema,
            ),
            "tools_enabled": extra.get("tools_enabled", self._tools_enabled),
            "system_prompt_append": extra.get("system_prompt_append", ""),
            "system_prompt_append_sha256": extra.get(
                "system_prompt_append_sha256", "",
            ),
            "system_prompt_append_source": extra.get(
                "system_prompt_append_source", "",
            ),
            "profile_source": extra.get("profile_source"),
        }
        for key, val in optional.items():
            if val is not None or key == "profile_source":
                kwargs[key] = val

        qa = answer_one_vikingbot_question(self.memory_client, self._llm, **kwargs)

        return AgentResponse(
            text=qa.response,
            prompt_tokens=qa.prompt_tokens,
            completion_tokens=qa.completion_tokens,
            memory_items=qa.retrieval_items,
            error=qa.llm_error or None,
            extra={
                "elapsed_s": qa.elapsed_s,
                "tool_call_count": qa.tool_call_count,
                "iterations": qa.iterations,
                "qa_profile": qa.qa_profile,
                "retrieval_latency_s": qa.retrieval_latency_s,
                "llm_latency_s": qa.llm_latency_s,
                "retrieval_error": qa.retrieval_error,
                "trace": qa.trace,
            },
        )

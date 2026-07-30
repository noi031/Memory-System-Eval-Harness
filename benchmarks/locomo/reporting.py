"""LoCoMo run summary construction."""

from __future__ import annotations

from benchmarks.locomo.import_memory import ImportReport
from benchmarks.locomo.judge import JudgeReport
from benchmarks.locomo.profiles import (
    profile_reference,
    profile_source,
)
from benchmarks.locomo.qa import QAOptions
from shared.qa import QAResult


def build_summary(
    *,
    dataset_path: str,
    sample_filter: str,
    total_samples: int,
    total_questions: int,
    import_report: ImportReport,
    reuse_existing_memory: bool,
    qa_results: list[QAResult],
    judge_report: JudgeReport,
    qa_options: QAOptions,
    session_mode: str,
    evaluation_identity: dict[str, str],
) -> dict:
    qa_errors = sum(1 for result in qa_results if result.llm_error)
    retrieval_errors = sum(
        1 for result in qa_results if result.retrieval_error
    )
    served_models = sorted({
        str(
            iteration.get("model_response", {}).get("response_model") or ""
        )
        for result in qa_results
        for iteration in result.trace.get("iterations", [])
        if str(
            iteration.get("model_response", {}).get("response_model") or ""
        ).strip()
    })
    tool_protocol_hashes = sorted({
        str(result.trace.get("tool_protocol", {}).get("sha256") or "")
        for result in qa_results
        if str(result.trace.get("tool_protocol", {}).get("sha256") or "").strip()
    })
    return {
        "status": (
            "failed"
            if import_report.incomplete
            or qa_errors
            or retrieval_errors
            or judge_report.errors
            else "completed"
        ),
        "benchmark": "locomo",
        "dataset": dataset_path,
        "sample_filter": sample_filter,
        "total_samples": total_samples,
        "total_questions": total_questions,
        "import_ok": import_report.completed,
        "import_total": import_report.total,
        "incomplete_imports": import_report.incomplete,
        "memory_source": "existing" if reuse_existing_memory else "injected",
        "qa_count": len(qa_results),
        "qa_errors": qa_errors,
        "retrieval_errors": retrieval_errors,
        "judge_correct": judge_report.correct,
        "judge_wrong": judge_report.wrong,
        "judge_errors": judge_report.errors,
        "judge_graded": judge_report.graded,
        "accuracy": round(judge_report.accuracy, 4),
        "avg_qa_elapsed_s": round(
            sum(result.elapsed_s for result in qa_results)
            / max(len(qa_results), 1),
            2,
        ),
        "total_prompt_tokens": sum(
            result.prompt_tokens for result in qa_results
        ),
        "total_completion_tokens": sum(
            result.completion_tokens for result in qa_results
        ),
        "qa_profile": qa_options.profile,
        "tools_enabled": qa_options.tools_enabled,
        "search_enabled": qa_options.search_enabled,
        "qa_profile_reference": profile_reference(qa_options.profile),
        "qa_profile_source": profile_source(qa_options.profile),
        "qa_prompt_append": {
            "enabled": bool(qa_options.system_prompt_append),
            "source": qa_options.system_prompt_append_source,
            "sha256": qa_options.system_prompt_append_sha256,
        },
        "tool_call_total": sum(
            result.tool_call_count for result in qa_results
        ),
        "avg_iterations": round(
            sum(result.iterations for result in qa_results)
            / max(len(qa_results), 1),
            2,
        ),
        "top_k": qa_options.top_k,
        "memory_budget_chars": qa_options.memory_budget_chars,
        "tool_search_limit": qa_options.tool_search_limit,
        "initial_min_score": qa_options.initial_min_score,
        "tool_min_score": qa_options.tool_min_score,
        "tool_search_pool_multiplier": (
            qa_options.tool_search_pool_multiplier
        ),
        "tool_set": qa_options.tool_set,
        "user_memory_budget_chars": qa_options.user_memory_budget_chars,
        "agent_memory_budget_chars": qa_options.agent_memory_budget_chars,
        "max_iterations": qa_options.max_iterations,
        "answer_temperature": qa_options.answer_temperature,
        "omit_answer_temperature": qa_options.omit_answer_temperature,
        "initial_retrieval_query_mode": (
            qa_options.initial_retrieval_query_mode
        ),
        "tool_query_dedup_scope": qa_options.tool_query_dedup_scope,
        "retrieval_uri_dedup": qa_options.retrieval_uri_dedup,
        "search_tool_target_uri_schema": (
            qa_options.search_tool_target_uri_schema
        ),
        "checkpoint_interval": qa_options.checkpoint_interval,
        "session_mode": session_mode,
        "retrieval_scope": "session" if session_mode == "single" else "account",
        "memory_identity": evaluation_identity,
        "served_model_ids": served_models,
        "tool_protocol_sha256": tool_protocol_hashes,
    }

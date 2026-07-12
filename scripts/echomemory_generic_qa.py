#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import re
import subprocess
import sys
import time
import traceback
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import benchmark_adapter
from echomemory_common import (
    DEFAULT_ECHOMEM_ROOT,
    detect_echomem_layout,
    echomem_transport_mode,
    ensure_echomem_imports,
    load_workspace_trace_token_rows,
    load_workspace_token_rows,
    open_echomem_sdk,
    sdk_ctx_kwargs,
    summarize_token_rows,
    workspace_token_usage_summary,
    write_echomem_config,
    write_json,
)
from echomemory_locomo_import import (
    import_one_session,
    materialize_hotpotqa_retrieval_projection,
    token_estimate,
)
from echomemory_memory_qa import (
    ECHOMEMORY_BACKEND_ROUTE,
    VIKINGBOT_ALIGNED_PROMPT_MODES,
    answer_question,
    classify_model_error,
    csv_fieldnames,
    hotpotqa_disable_answer_tooling,
    normalize_echomemory_tool_set,
    normalize_retrieval_mode,
    token_usage_json,
)
from memory.vikingboat_alignment import (
    VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
    VIKINGBOT_ALIGNMENT_PROFILE,
    VIKINGBOT_INITIAL_MIN_SCORE,
    VIKINGBOT_INITIAL_SEARCH_LIMIT,
    VIKINGBOT_MAX_ITERATIONS,
    VIKINGBOT_TOOL_MIN_SCORE,
    VIKINGBOT_TOOL_SEARCH_LIMIT,
    VIKINGBOT_TOOL_SET,
    VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
    alignment_metadata,
)
from openviking_generic_qa import (
    import_record_from_row,
    load_existing_csv,
    row_key,
    run_judge,
    run_official_eval,
    should_retry_row,
    official_metric_summary,
)
from echomemory_wait_and_eval import (
    build_workspace_snapshot,
    expected_session_count,
    require_memory_ready_or_exit,
    run_and_log,
    snapshot_ready,
    wait_for_async_memory_stability,
    write_status,
)

EVAL_RECALL_ROUTERS = tuple(
    item.strip()
    for item in str(os.environ.get("ECHOMEM_DEVELOP_EVAL_RECALL_ROUTERS") or "llm").split(",")
    if item.strip()
)


def safe_slug(value: Any, limit: int = 72) -> str:
    text = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip()).strip("-._")
    return (text or "sample")[:limit]


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return [part.strip() for part in text.split(",") if part.strip()]
    return []


def snapshot_has_summary_ready(snapshot: dict[str, Any], expected_sessions_total: int) -> bool:
    session_count = int(snapshot.get("session_count") or 0)
    abstract_count = int(snapshot.get("abstract_count") or 0)
    overview_count = int(snapshot.get("overview_count") or 0)
    vector_count = int(snapshot.get("vector_count") or 0)
    target_session_id = str(snapshot.get("target_session_id") or "").strip()
    enough_sessions = session_count >= max(1, expected_sessions_total or session_count)
    if target_session_id:
        return bool(
            enough_sessions
            and session_count > 0
            and abstract_count == session_count
            and overview_count == session_count
        )
    return bool(
        enough_sessions
        and session_count > 0
        and abstract_count == session_count
        and overview_count == session_count
        and vector_count > 0
    )


def read_dataset(path: Path) -> Any:
    return benchmark_adapter.read_dataset(path)


def selected_question_ids(args: argparse.Namespace) -> set[str]:
    return {item.strip() for item in str(getattr(args, "questions", "") or "").split(",") if item.strip()}


def job_matches_question_filter(job: benchmark_adapter.Job, question_filter: set[str]) -> bool:
    if not question_filter:
        return True
    candidates = {
        str(job.question_id or "").strip(),
        str(job.native_question_id or "").strip(),
        str(job.sample_id or "").strip(),
    }
    return bool(question_filter.intersection(candidates))


def iter_job_plans(args: argparse.Namespace):
    limit = args.count or None
    question_filter = selected_question_ids(args)
    emitted = 0
    if args.dataset_format == "locomo":
        data = read_dataset(args.dataset_path)
        jobs, plans = benchmark_adapter.locomo_jobs(data, limit, args.sample, question_filter or None)
        for job, plan in zip(jobs, plans):
            emitted += 1
            yield emitted, job, plan
        return
    for raw_index, raw in benchmark_adapter.iter_payload_from_path(args.dataset_path):
        if args.dataset_format == "longmemeval":
            built = benchmark_adapter.longmemeval_job_plan(raw, raw_index, args.sample)
        elif args.dataset_format == "hotpotqa":
            built = benchmark_adapter.hotpotqa_job_plan(raw, raw_index, args.sample)
        else:
            built = benchmark_adapter.generic_job_plan(args.dataset_format, raw, raw_index, args.sample)
        if built is None:
            continue
        job, plan = built
        if not job_matches_question_filter(job, question_filter):
            continue
        emitted += 1
        yield emitted, job, plan
        if limit and emitted >= limit:
            return


def iter_hotpotqa_raw_job_plans(args: argparse.Namespace):
    limit = args.count or None
    question_filter = selected_question_ids(args)
    emitted = 0
    for raw_index, raw in benchmark_adapter.iter_payload_from_path(args.dataset_path):
        built = benchmark_adapter.hotpotqa_job_plan(raw, raw_index, args.sample)
        if built is None:
            continue
        job, plan = built
        if not job_matches_question_filter(job, question_filter):
            continue
        emitted += 1
        yield emitted, raw_index, raw, job, plan
        if limit and emitted >= limit:
            return


def planned_job_count(args: argparse.Namespace) -> int | None:
    question_count = len(selected_question_ids(args))
    if question_count:
        return question_count
    if args.count:
        return args.count
    if args.sample not in ("", "all"):
        return None
    return benchmark_adapter.count_payload_items_from_path(args.dataset_path)


def sample_identity(args: argparse.Namespace, sample_id: str) -> tuple[str, str]:
    if args.identity_mode == "fixed":
        return args.user_id or "default", args.agent_id or "default"
    base = safe_slug(args.namespace, 40)
    sample = safe_slug(sample_id, 64)
    return f"{safe_slug(args.user_prefix)}-{base}-{sample}", f"{safe_slug(args.agent_prefix)}-{base}-{sample}"


def sample_account(args: argparse.Namespace, sample_id: str) -> str:
    if args.identity_mode == "fixed":
        return args.account or "default"
    account_base = safe_slug(args.account or "default", 24)
    namespace_base = safe_slug(args.namespace or "run", 24)
    sample_base = safe_slug(sample_id, 40)
    return f"{account_base}-{namespace_base}-{sample_base}"


def sample_scope(args: argparse.Namespace, sample_id: str) -> tuple[str, str, str]:
    account = sample_account(args, sample_id)
    user_id, agent_id = sample_identity(args, sample_id)
    return account, user_id, agent_id


def account_from_memory_uri(uri: Any) -> str:
    text = str(uri or "").strip()
    match = re.match(r"^echo://([^/]+)/", text)
    return str(match.group(1) or "").strip() if match else ""


def record_account(record: dict[str, Any], default_account: str) -> str:
    if not isinstance(record, dict):
        return default_account or "default"
    explicit = str(record.get("account") or "").strip()
    if explicit:
        return explicit
    parsed = account_from_memory_uri(record.get("memory_uri"))
    return parsed or (default_account or "default")


def fast_wait_timeout_seconds(args: argparse.Namespace, *, strict_ready_required: bool) -> int:
    requested = max(0, int(getattr(args, "stabilize_timeout_seconds", 0) or 0))
    if requested <= 0:
        return 0
    dataset_format = str(getattr(args, "dataset_format", "") or getattr(args, "format", "") or "").strip().lower()
    if strict_ready_required:
        return min(requested, 120)
    if dataset_format == "longmemeval":
        # LongMemEval should not wait for every late artifact, but opening QA
        # after only ~45s is too early for the current async atom/vector path.
        # Give each sample enough time to become retrieval-ready without
        # reverting to the heavier full strict-ready gate.
        return min(requested, 180)
    return min(requested, 45)


def late_ready_grace_check(
    *,
    workspace: Path,
    account: str,
    sample: str,
    target_session_id: str = "",
    expected_sessions_total: int,
    grace_seconds: int,
    poll_seconds: int,
) -> dict[str, Any]:
    deadline = time.time() + max(0, int(grace_seconds))
    last_snapshot: dict[str, Any] = {}
    while True:
        snapshot = build_workspace_snapshot(workspace, account, sample, target_session_id=target_session_id)
        last_snapshot = snapshot
        if snapshot_ready(snapshot, expected_sessions_total):
            return {
                "ready": True,
                "timed_out": False,
                "snapshot": snapshot,
                "grace_seconds": max(0, int(grace_seconds)),
            }
        if time.time() >= deadline:
            return {
                "ready": False,
                "timed_out": True,
                "snapshot": last_snapshot,
                "grace_seconds": max(0, int(grace_seconds)),
            }
        time.sleep(max(5, int(poll_seconds)))


def normalized_created_at(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y/%m/%d (%a) %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    return ""


def hotpotqa_context_pairs(context: Any) -> list[tuple[str, list[str]]]:
    helper = getattr(benchmark_adapter, "_hotpot_context_pairs", None)
    if callable(helper):
        return helper(context)
    return []


def hotpotqa_global_sentence_messages(
    args: argparse.Namespace,
    selected: list[tuple[int, int, Any, benchmark_adapter.Job, dict[str, Any]]],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for question_no, _raw_index, raw, job, _plan in selected:
        item = raw if isinstance(raw, dict) else {"context": raw}
        for doc_index, (title, sentences) in enumerate(hotpotqa_context_pairs(item.get("context")), 1):
            doc_title = str(title or f"document_{doc_index}").strip()
            for sent_id, sentence in enumerate(sentences):
                sentence_text = str(sentence or "").strip()
                if not sentence_text:
                    continue
                content = (
                    "[benchmark memory]\n"
                    "dataset_format: hotpotqa\n"
                    "corpus_mode: global_sentence_corpus\n"
                    f"namespace: {args.namespace}\n"
                    f"sample_id: {job.sample_id}\n"
                    f"source_question_id: {job.question_id}\n"
                    f"source_question_no: {question_no}\n"
                    f"hotpotqa_title: {doc_title}\n"
                    f"hotpotqa_sent_id: {sent_id}\n"
                    f"hotpotqa_doc_index: {doc_index}\n"
                    f"title: {doc_title}\n\n"
                    f"{doc_title}: {sentence_text}"
                )
                messages.append(
                    {
                        "role": "user",
                        "content": content,
                        "created_at": "",
                        "role_id": "hotpotqa_sentence",
                        "speaker": "hotpotqa_sentence",
                        "dia_id": f"{job.sample_id}:doc:{doc_index}:sent:{sent_id}",
                    }
                )
    rnd = random.Random(int(getattr(args, "random_seed", 30) or 30))
    rnd.shuffle(messages)
    return messages


def hotpotqa_global_sentence_records(
    args: argparse.Namespace,
    selected: list[tuple[int, int, Any, benchmark_adapter.Job, dict[str, Any]]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for question_no, _raw_index, raw, job, _plan in selected:
        item = raw if isinstance(raw, dict) else {"context": raw}
        for doc_index, (title, sentences) in enumerate(hotpotqa_context_pairs(item.get("context")), 1):
            doc_title = str(title or f"document_{doc_index}").strip()
            for sent_id, sentence in enumerate(sentences):
                sentence_text = str(sentence or "").strip()
                if not sentence_text:
                    continue
                records.append(
                    {
                        "title": doc_title,
                        "text": f"{doc_title}: {sentence_text}",
                        "sample_id": job.sample_id,
                        "source_question_id": job.question_id,
                        "source_question_no": question_no,
                        "hotpotqa_title": doc_title,
                        "hotpotqa_sent_id": sent_id,
                        "hotpotqa_doc_index": doc_index,
                        "sentence_text": sentence_text,
                    }
                )
    rnd = random.Random(int(getattr(args, "random_seed", 30) or 30))
    rnd.shuffle(records)
    return records


async def import_hotpotqa_global_sentence_projection(
    args: argparse.Namespace,
    sdk: Any,
    requested_session_id: str,
    messages: list[dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    started = time.time()
    context = sdk_ctx_kwargs(sdk, args.account, args.user_id, args.agent_id, requested_session_id)
    created = await sdk.create_session(title=label, ctx=context)
    actual_session_id = str(created.get("session_id") or requested_session_id)
    projection = await materialize_hotpotqa_retrieval_projection(
        args,
        sdk,
        actual_session_id,
        messages,
        expected_last_message_id=f"hotpotqa-global-sentence:{len(messages)}",
    )
    elapsed = round(time.time() - started, 4)
    complete = bool(projection)
    print(
        "[hotpotqa-global-fast-import] "
        + json.dumps(
            {
                "label": label,
                "session_id": actual_session_id,
                "messages": len(messages),
                "complete": complete,
                "elapsed_s": elapsed,
                "projection": projection,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return {
        "session_id": actual_session_id,
        "requested_session_id": requested_session_id,
        "expected_messages": len(messages),
        "submitted_messages": len(messages) if complete else 0,
        "live_message_count_before_commit": 0,
        "pending_message_count_after_commit": 0,
        "live_complete_before_commit": complete,
        "archive_complete_after_commit": complete,
        "atom_memory_complete_after_commit": complete,
        "retrieval_ready_after_commit": complete,
        "cursor_complete_after_commit": complete,
        "qa_ready_after_commit": complete,
        "pending_async_memory_after_commit": False,
        "last_added_message_id": f"hotpotqa-global-sentence:{len(messages)}",
        "commit_keep_recent_count": 0,
        "session_commit_skipped": True,
        "atom_flush": {
            "available": True,
            "complete": complete,
            "deferred": False,
            "skipped": True,
            "elapsed_s": 0.0,
            "attempts": [],
            "atom_pipeline_index": len(messages) - 1,
            "expected_atom_pipeline_index": len(messages) - 1,
            "atom_last_extracted_turn_id": f"hotpotqa-global-sentence:{len(messages)}",
            "atom_last_extracted_turn_id_ok": complete,
            "artifacts_ready": complete,
        },
        "commit_artifacts": {
            "complete": complete,
            "retrieval_ready": complete,
            "cursor_complete": complete,
            "commit_index": len(messages) - 1,
            "expected_commit_index": len(messages) - 1,
            "atom_pipeline_index": len(messages) - 1,
            "expected_atom_pipeline_index": len(messages) - 1,
            "stored_message_count": len(messages),
            "hotpotqa_projection": projection,
        },
        "create_response": created,
        "commit_response": {
            "task_id": f"hotpotqa-global-projection-{actual_session_id}",
            "status": "completed" if complete else "failed",
            "elapsed_s": elapsed,
        },
        "integrity": "complete" if complete else "failed",
        "integrity_stage": "qa_ready" if complete else "projection_failed",
        "memory_injection_time_s": elapsed,
        "import_elapsed_s": elapsed,
        "error": "" if complete else "hotpotqa projection import failed",
    }


def import_messages_from_plan(dataset_format: str, sample_id: str, namespace: str, plan: dict[str, Any]) -> list[dict[str, Any]]:
    documents = list(plan.get("memory_documents") or [])
    if documents:
        messages: list[dict[str, Any]] = []
        for index, doc in enumerate(documents, 1):
            time_text = str(doc.get("time") or "").strip()
            created_at = normalized_created_at(time_text)
            title = str(doc.get("title") or doc.get("doc_id") or f"document_{index}").strip()
            text = str(doc.get("text") or "").strip()
            if not text:
                continue
            if dataset_format == "longmemeval" and "Conversation turns:" in text:
                current_session_id = title or f"session_{index}"
                for raw_line in text.splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("source_dataset:") or line.startswith("session_id:") or line.startswith("time:"):
                        continue
                    if line == "Conversation turns:":
                        continue
                    if not line.startswith("turn_"):
                        continue
                    prefix, _, content = line.partition(":")
                    turn_label = prefix.strip()
                    role = turn_label.split()[-1] if " " in turn_label else "message"
                    content = content.strip()
                    if not content:
                        continue
                    message_text = (
                        f"[benchmark memory]\n"
                        f"dataset_format: {dataset_format}\n"
                        f"sample_id: {sample_id}\n"
                        f"namespace: {namespace}\n"
                        f"document_index: {index}\n"
                        f"title: {title or '-'}\n"
                        f"time: {time_text or '-'}\n"
                        f"session_id: {current_session_id}\n\n"
                        f"[session_date={time_text or '-'}] [{role}] {current_session_id} {turn_label}: {content}"
                    )
                    messages.append(
                        {
                            "role": "assistant" if str(role).lower() in {"assistant", "agent"} else "user",
                            "content": message_text,
                            "created_at": created_at,
                            "role_id": str(role),
                            "speaker": str(role),
                            "dia_id": f"{sample_id}:doc:{index}:{turn_label}",
                        }
                    )
                continue
            content = (
                f"[benchmark memory]\n"
                f"dataset_format: {dataset_format}\n"
                f"sample_id: {sample_id}\n"
                f"namespace: {namespace}\n"
                f"document_index: {index}\n"
                f"title: {title or '-'}\n"
                f"time: {time_text or '-'}\n\n"
                f"{text}"
            )
            messages.append(
                {
                    "role": "user",
                    "content": content,
                    "created_at": created_at,
                    "role_id": "benchmark_memory",
                    "speaker": "benchmark_memory",
                    "dia_id": f"{sample_id}:doc:{index}",
                }
            )
        if messages:
            return messages
    messages = []
    for index, event in enumerate(list(plan.get("events") or []), 1):
        time_text = str(event.get("time") or "").strip()
        created_at = normalized_created_at(time_text)
        text = str(event.get("text") or "").strip()
        if not text:
            continue
        content = (
            f"[benchmark memory]\n"
            f"dataset_format: {dataset_format}\n"
            f"sample_id: {sample_id}\n"
            f"namespace: {namespace}\n"
            f"event_index: {index}\n"
            f"time: {time_text or '-'}\n\n"
            f"{text}"
        )
        messages.append(
            {
                "role": "user",
                "content": content,
                "created_at": created_at,
                "role_id": "benchmark_memory",
                "speaker": "benchmark_memory",
                "dia_id": f"{sample_id}:event:{index}",
            }
        )
    return messages


async def import_sample_memory(
    args: argparse.Namespace,
    sdk: Any,
    sample_id: str,
    plan: dict[str, Any],
    import_dir: Path,
) -> dict[str, Any]:
    started = time.time()
    account = str(getattr(args, "account", "") or "").strip()
    user_id = str(getattr(args, "user_id", "") or "").strip()
    agent_id = str(getattr(args, "agent_id", "") or "").strip()
    if not account or not user_id or not agent_id:
        account, user_id, agent_id = sample_scope(args, sample_id)
    messages = import_messages_from_plan(args.dataset_format, sample_id, args.namespace, plan)
    print(
        f"[import-sample] sample={sample_id} messages={len(messages)} "
        f"account={account} user_id={user_id} agent_id={agent_id} import_dir={import_dir}",
        flush=True,
    )
    if not messages:
        return {
            "sample_id": sample_id,
            "account": account,
            "user_id": user_id,
            "agent_id": agent_id,
            "status": "NO_EVENTS",
            "integrity": "no_events",
            "expected_messages": 0,
            "submitted_messages": 0,
            "session_id": "",
            "memory_uri": f"echo://{account}/memories/",
            "estimated_import_tokens": 0,
            "import_llm_prompt_tokens": 0,
            "import_llm_completion_tokens": 0,
            "import_llm_total_tokens": 0,
            "import_embedding_total_tokens": 0,
            "import_total_tokens": 0,
            "error": "no memory events recognized for sample",
        }
    requested_session_id = f"generic-{safe_slug(args.namespace, 36)}-{safe_slug(args.dataset_format, 24)}-{safe_slug(sample_id, 52)}-{uuid.uuid4().hex[:8]}"
    import_args = argparse.Namespace(**vars(args))
    import_args.user_id = user_id
    import_args.agent_id = agent_id
    import_started_at = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
    print(f"[import-sample] sample={sample_id} requested_session_id={requested_session_id}", flush=True)
    record = await import_one_session(import_args, sdk, requested_session_id, messages, f"{args.dataset_format}/{sample_id}")
    import_finished_at = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
    token_usage = workspace_token_usage_summary(
        args.workspace,
        account,
        start_time=import_started_at,
        end_time=import_finished_at,
    )
    print(
        f"[import-sample] sample={sample_id} session_id={record.get('session_id') or ''} "
        f"integrity={record.get('integrity') or ''} submitted={record.get('submitted_messages') or 0}",
        flush=True,
    )
    record.update(
        {
            "sample_id": sample_id,
            "account": account,
            "user_id": str((record.get("create_response") or {}).get("user_id") or user_id),
            "agent_id": str((record.get("create_response") or {}).get("agent_id") or agent_id),
            "status": "ECHOMEMORY_IMPORT_DONE" if record.get("integrity") == "complete" else "ECHOMEMORY_IMPORT_INCOMPLETE",
            "memory_uri": f"echo://{account}/memories/",
            "estimated_import_tokens": sum(token_estimate(str(msg.get("content") or "")) for msg in messages),
            "import_llm_prompt_tokens": int(token_usage.get("import_llm_prompt_tokens") or 0),
            "import_llm_completion_tokens": int(token_usage.get("import_llm_completion_tokens") or 0),
            "import_llm_total_tokens": int(token_usage.get("import_llm_total_tokens") or 0),
            "import_embedding_total_tokens": int(token_usage.get("import_embedding_total_tokens") or 0),
            "import_total_tokens": int(token_usage.get("import_total_tokens") or 0),
            "requested_session_id": requested_session_id,
            "import_started_at": import_started_at,
            "import_finished_at": import_finished_at,
            "import_elapsed_s": round(time.time() - started, 4),
            "import_commit_elapsed_s": round(float((record.get("commit_response") or {}).get("elapsed_s") or 0.0), 4),
            "import_flush_elapsed_s": round(float((record.get("atom_flush") or {}).get("elapsed_s") or 0.0), 4),
            "import_artifact_wait_elapsed_s": round(float((record.get("commit_artifacts") or {}).get("wait_elapsed_s") or 0.0), 4),
        }
    )
    write_json(import_dir / f"{safe_slug(sample_id)}_messages.json", messages)
    return record


def workspace_token_usage_summary_multi(
    workspace: str,
    accounts: list[str],
    *,
    start_time: datetime | str | None = None,
    end_time: datetime | str | None = None,
) -> dict[str, Any]:
    unique_accounts = [item for item in dict.fromkeys(str(account or "").strip() for account in accounts) if item]
    if not unique_accounts:
        return workspace_token_usage_summary(workspace, "default", start_time=start_time, end_time=end_time)
    if len(unique_accounts) == 1:
        return workspace_token_usage_summary(workspace, unique_accounts[0], start_time=start_time, end_time=end_time)
    rows: list[dict[str, Any]] = []
    used_trace_fallback = False
    for account in unique_accounts:
        account_rows = load_workspace_token_rows(workspace, account)
        if not account_rows:
            account_rows = load_workspace_trace_token_rows(workspace, account)
            if account_rows:
                used_trace_fallback = True
        rows.extend(account_rows)
    overall = summarize_token_rows(rows, start_time=start_time, end_time=end_time)
    import_usage = summarize_token_rows(rows, exclude_call_sites={"search_intent"}, start_time=start_time, end_time=end_time)
    qa_internal = summarize_token_rows(rows, include_call_sites={"search_intent"}, start_time=start_time, end_time=end_time)
    embedding_usage = overall["by_call_site"].get("embedding", {})
    return {
        "llm_log_source": "trace_completed_fallback_multi" if used_trace_fallback and rows else ("metrics_jsonl_multi" if rows else "none"),
        "llm_log_rows": len(rows),
        "llm_input_tokens": overall["total_input_tokens"],
        "llm_output_tokens": overall["total_output_tokens"],
        "llm_total_tokens": overall["total_tokens"],
        "llm_call_count": overall["call_count"],
        "llm_total_latency_ms": overall["total_latency_ms"],
        "llm_avg_latency_ms": overall["avg_latency_ms"],
        "llm_max_latency_ms": overall["max_latency_ms"],
        "import_llm_prompt_tokens": import_usage["total_input_tokens"],
        "import_llm_completion_tokens": import_usage["total_output_tokens"],
        "import_llm_total_tokens": import_usage["total_tokens"],
        "import_llm_total_latency_ms": import_usage["total_latency_ms"],
        "import_llm_avg_latency_ms": import_usage["avg_latency_ms"],
        "import_llm_max_latency_ms": import_usage["max_latency_ms"],
        "import_embedding_total_tokens": int(embedding_usage.get("total_tokens") or 0),
        "import_total_tokens": import_usage["total_tokens"],
        "search_intent_total_tokens": qa_internal["total_tokens"],
        "search_intent_call_count": qa_internal["call_count"],
        "search_intent_total_latency_ms": qa_internal["total_latency_ms"],
        "search_intent_avg_latency_ms": qa_internal["avg_latency_ms"],
        "search_intent_max_latency_ms": qa_internal["max_latency_ms"],
        "embedding_total_tokens": int(embedding_usage.get("total_tokens") or 0),
        "embedding_call_count": int(embedding_usage.get("call_count") or 0),
        "call_sites": overall["by_call_site"],
    }


def import_record_summary(import_records: dict[str, dict[str, Any]], workspace: str, account: str) -> dict[str, Any]:
    accounts = sorted(
        {
            record_account(item, account)
            for item in import_records.values()
            if record_account(item, account)
        }
    )
    summary = {
        "status": "ECHOMEMORY_GENERIC_IMPORT_DONE",
        "records": list(import_records.values()),
        "samples": len(import_records),
        "complete_samples": sum(1 for item in import_records.values() if item.get("integrity") == "complete"),
        "pending_async_samples": sum(1 for item in import_records.values() if item.get("integrity") == "pending_async_memory"),
        "partial_samples": sum(1 for item in import_records.values() if item.get("integrity") == "partial"),
        "failed_samples": sum(1 for item in import_records.values() if item.get("integrity") == "failed"),
        "no_event_samples": sum(1 for item in import_records.values() if item.get("integrity") == "no_events"),
        "expected_messages": sum(int(item.get("expected_messages") or 0) for item in import_records.values()),
        "submitted_messages": sum(int(item.get("submitted_messages") or 0) for item in import_records.values()),
        "estimated_import_tokens": sum(int(item.get("estimated_import_tokens") or 0) for item in import_records.values()),
        "import_llm_prompt_tokens_records": sum(int(item.get("import_llm_prompt_tokens") or 0) for item in import_records.values()),
        "import_llm_completion_tokens_records": sum(int(item.get("import_llm_completion_tokens") or 0) for item in import_records.values()),
        "import_llm_total_tokens_records": sum(int(item.get("import_llm_total_tokens") or 0) for item in import_records.values()),
        "import_embedding_total_tokens_records": sum(int(item.get("import_embedding_total_tokens") or 0) for item in import_records.values()),
        "import_total_tokens_records": sum(int(item.get("import_total_tokens") or 0) for item in import_records.values()),
        "memory_injection_time_s_total": round(sum(float(item.get("memory_injection_time_s") or item.get("import_elapsed_s") or 0.0) for item in import_records.values()), 4),
        "memory_settle_wait_time_s_total": round(sum(float(item.get("memory_settle_wait_elapsed_s") or 0.0) for item in import_records.values()), 4),
        "repair_time_s_total": round(sum(float(item.get("repair_elapsed_s") or 0.0) for item in import_records.values()), 4),
        "accounts": accounts,
        "account_count": len(accounts),
    }
    sample_count = max(1, len(import_records)) if import_records else 0
    if sample_count:
        summary["avg_memory_injection_time_s"] = round(float(summary["memory_injection_time_s_total"]) / sample_count, 4)
        summary["avg_memory_settle_wait_time_s"] = round(float(summary["memory_settle_wait_time_s_total"]) / sample_count, 4)
        summary["avg_repair_time_s"] = round(float(summary["repair_time_s_total"]) / sample_count, 4)
    record_start_times = [
        item.get("import_started_at")
        for item in import_records.values()
        if str(item.get("import_started_at") or "").strip()
    ]
    record_end_times = [
        item.get("import_finished_at")
        for item in import_records.values()
        if str(item.get("import_finished_at") or "").strip()
    ]
    token_window_start = min(record_start_times) if record_start_times else None
    token_window_end = max(record_end_times) if record_end_times else None
    summary.update(
        workspace_token_usage_summary_multi(
            workspace,
            accounts or [account],
            start_time=token_window_start,
            end_time=token_window_end,
        )
    )
    if token_window_start:
        summary["import_started_at"] = str(token_window_start)
    if token_window_end:
        summary["import_finished_at"] = str(token_window_end)
    return summary


def current_sample_expected_sessions(record: dict[str, Any]) -> int:
    """Return the readiness gate for the current sample only.

    The wait loop snapshots one sample at a time, so comparing it against a
    shard-level max session count can falsely keep readiness blocked forever.
    Generic benchmark imports currently materialize one committed session per
    sample; if we have submitted messages or a session id, require at least one
    visible session for this sample.
    """
    if not isinstance(record, dict):
        return 1
    for key in ("session_count", "session_limit", "progress_sessions_total", "original_session_count"):
        try:
            value = int(record.get(key) or 0)
        except Exception:
            continue
        if value > 0:
            return value
    session_records = record.get("session_records") or []
    if isinstance(session_records, list) and session_records:
        return len(session_records)
    if str(record.get("session_id") or "").strip():
        return 1
    try:
        if int(record.get("submitted_messages") or 0) > 0:
            return 1
    except Exception:
        pass
    return 1


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = csv_fieldnames(rows) if rows else list(benchmark_adapter.Job.__dataclass_fields__.keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def safe_float(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def running_summary_payload(rows: list[dict[str, Any]], *, status: str, csv_path: Path) -> dict[str, Any]:
    sums = {
        "memory_injection_time_s": 0.0,
        "memory_settle_wait_elapsed_s": 0.0,
        "qa_time_s": 0.0,
        "end_to_end_time_s": 0.0,
    }
    counts = {key: 0 for key in sums}
    last_question_id = ""
    for row in rows:
        last_question_id = str(row.get("question_id") or row.get("native_question_id") or row.get("sample_id") or last_question_id)
        for key in ("memory_injection_time_s", "memory_settle_wait_elapsed_s", "end_to_end_time_s"):
            value = safe_float(row.get(key))
            if value is None:
                continue
            sums[key] += value
            counts[key] += 1
        qa_value = safe_float(row.get("qa_time_s"))
        if qa_value is None:
            qa_value = safe_float(row.get("time_cost"))
        if qa_value is not None:
            sums["qa_time_s"] += qa_value
            counts["qa_time_s"] += 1

    def avg(key: str) -> float | None:
        count = counts[key]
        return round(sums[key] / count, 4) if count else None

    def total(key: str) -> float | None:
        return round(sums[key], 4) if counts[key] else None

    return {
        "rows": len(rows),
        "last_question_id": last_question_id,
        "total_memory_injection_time_s": total("memory_injection_time_s"),
        "avg_memory_injection_time_s": avg("memory_injection_time_s"),
        "total_memory_settle_wait_time_s": total("memory_settle_wait_elapsed_s"),
        "avg_memory_settle_wait_time_s": avg("memory_settle_wait_elapsed_s"),
        "total_qa_time_s": total("qa_time_s"),
        "avg_qa_time_s": avg("qa_time_s"),
        "total_end_to_end_time_s": total("end_to_end_time_s"),
        "avg_end_to_end_time_s": avg("end_to_end_time_s"),
        "status": status,
        "csv_path": str(csv_path),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def write_running_summary(path: Path, rows: list[dict[str, Any]], *, status: str, csv_path: Path) -> None:
    payload = running_summary_payload(rows, status=status, csv_path=csv_path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_hotpotqa_checkpoint_eval(args: argparse.Namespace, csv_path: Path, out_dir: Path, count: int) -> dict[str, Any]:
    checkpoint_dir = out_dir / "checkpoints" / f"after_{count:04d}"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "hotpotqa_answer_eval.py"),
        "--csv",
        str(csv_path),
        "--reference",
        str(args.dataset_path),
        "--out-dir",
        str(checkpoint_dir),
        "--limit",
        str(count),
    ]
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    summary_path = checkpoint_dir / "hotpotqa_answer_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    row = {
        "checkpoint_count": count,
        "returncode": proc.returncode,
        "elapsed_s": round(time.time() - started, 4),
        "summary_path": str(summary_path),
        "answer_em": summary.get("answer_em"),
        "answer_f1": summary.get("answer_f1"),
        "supporting_facts_em": summary.get("supporting_facts_em"),
        "supporting_facts_f1": summary.get("supporting_facts_f1"),
        "supporting_facts_micro_precision": summary.get("supporting_facts_micro_precision"),
        "supporting_facts_micro_recall": summary.get("supporting_facts_micro_recall"),
        "joint_em": summary.get("joint_em"),
        "joint_f1": summary.get("joint_f1"),
        "stdout_tail": "\n".join((proc.stdout or "").splitlines()[-20:]),
    }
    checkpoint_index_path = out_dir / "hotpotqa_checkpoint_scores.json"
    previous: list[dict[str, Any]] = []
    if checkpoint_index_path.exists():
        try:
            parsed = json.loads(checkpoint_index_path.read_text(encoding="utf-8"))
            if isinstance(parsed, list):
                previous = parsed
        except Exception:
            previous = []
    previous = [item for item in previous if int(item.get("checkpoint_count") or 0) != count]
    previous.append(row)
    previous.sort(key=lambda item: int(item.get("checkpoint_count") or 0))
    write_json(checkpoint_index_path, previous)
    print(
        "[checkpoint] "
        + json.dumps(
            {
                "count": count,
                "answer_em": row["answer_em"],
                "answer_f1": row["answer_f1"],
                "supporting_facts_f1": row["supporting_facts_f1"],
                "joint_f1": row["joint_f1"],
                "returncode": proc.returncode,
                "summary_path": str(summary_path),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return row


def run_longmemeval_checkpoint_eval(args: argparse.Namespace, csv_path: Path, out_dir: Path, count: int) -> dict[str, Any]:
    checkpoint_dir = out_dir / "checkpoints" / f"after_{count:04d}"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "longmemeval_official_eval.py"),
        "--csv",
        str(csv_path),
        "--reference",
        str(args.dataset_path),
        "--out-dir",
        str(checkpoint_dir),
        "--base-url",
        str(args.judge_base_url or args.answer_base_url),
        "--model",
        str(args.judge_model or args.answer_model),
        "--parallel",
        "1",
        "--limit",
        str(count),
        "--timeout-s",
        str(args.timeout_s),
        "--retries",
        str(args.model_retries),
    ]
    env = os.environ.copy()
    token = str(args.judge_token or args.answer_token or "").strip()
    if token:
        env["LOCOMO_JUDGE_TOKEN"] = token
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    summary_path = checkpoint_dir / "longmemeval_official_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    row = {
        "checkpoint_count": count,
        "returncode": proc.returncode,
        "elapsed_s": round(time.time() - started, 4),
        "summary_path": str(summary_path),
        "overall_accuracy": summary.get("overall_accuracy"),
        "task_averaged_accuracy": summary.get("task_averaged_accuracy"),
        "abstention_accuracy": summary.get("abstention_accuracy"),
        "judge_error_count": summary.get("judge_error_count"),
        "judge_total_tokens": summary.get("judge_total_tokens"),
        "judge_retry_total": summary.get("judge_retry_total"),
        "stdout_tail": "\n".join((proc.stdout or "").splitlines()[-20:]),
    }
    checkpoint_index_path = out_dir / "longmemeval_checkpoint_scores.json"
    previous: list[dict[str, Any]] = []
    if checkpoint_index_path.exists():
        try:
            parsed = json.loads(checkpoint_index_path.read_text(encoding="utf-8"))
            if isinstance(parsed, list):
                previous = parsed
        except Exception:
            previous = []
    previous = [item for item in previous if int(item.get("checkpoint_count") or 0) != count]
    previous.append(row)
    previous.sort(key=lambda item: int(item.get("checkpoint_count") or 0))
    write_json(checkpoint_index_path, previous)
    print(
        "[checkpoint] "
        + json.dumps(
            {
                "count": count,
                "overall_accuracy": row["overall_accuracy"],
                "task_averaged_accuracy": row["task_averaged_accuracy"],
                "judge_error_count": row["judge_error_count"],
                "returncode": proc.returncode,
                "summary_path": str(summary_path),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return row


def checkpoint_scores_path_for_dataset(dataset_format: str, out_dir: Path) -> Path:
    fmt = str(dataset_format or "").strip().lower()
    if fmt == "hotpotqa":
        return out_dir / "hotpotqa_checkpoint_scores.json"
    if fmt == "longmemeval":
        return out_dir / "longmemeval_checkpoint_scores.json"
    return out_dir / "checkpoint_scores.json"


def run_checkpoint_eval(args: argparse.Namespace, csv_path: Path, out_dir: Path, count: int) -> dict[str, Any] | None:
    fmt = str(getattr(args, "dataset_format", "") or "").strip().lower()
    if fmt == "hotpotqa":
        return run_hotpotqa_checkpoint_eval(args, csv_path, out_dir, count)
    if fmt == "longmemeval":
        return run_longmemeval_checkpoint_eval(args, csv_path, out_dir, count)
    return None


async def open_sdk_runtime(open_runtime: Any, sdk_cls: Any, config_path: Path) -> tuple[Any, Any]:
    runtime = await open_runtime(str(config_path))
    return runtime, sdk_cls(runtime)


async def close_sdk_runtime(runtime: Any, sdk: Any = None, *, drain_pending: bool) -> None:
    close = getattr(sdk, "close", None)
    if callable(close):
        await close()
    if runtime is None:
        return
    stop = getattr(runtime, "stop", None)
    if not callable(stop):
        return
    try:
        await stop(drain_pending=drain_pending)
    except TypeError:
        await stop()


async def close_sdk_runtime_with_timeout(
    runtime: Any,
    sdk: Any = None,
    *,
    drain_pending: bool,
    timeout_s: float = 20.0,
) -> None:
    try:
        await asyncio.wait_for(
            close_sdk_runtime(runtime, sdk, drain_pending=drain_pending),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        print(
            f"[runtime-close-timeout] drain_pending={drain_pending} timeout_s={timeout_s}",
            flush=True,
        )


def failed_import_record(
    args: argparse.Namespace,
    sample_id: str,
    plan: dict[str, Any],
    *,
    error: str,
    elapsed_s: float = 0.0,
    user_id: str = "",
    agent_id: str = "",
) -> dict[str, Any]:
    messages = import_messages_from_plan(args.dataset_format, sample_id, args.namespace, plan)
    safe_account, derived_user_id, derived_agent_id = sample_scope(args, sample_id)
    safe_user_id = user_id or derived_user_id
    safe_agent_id = agent_id or derived_agent_id
    return {
        "sample_id": sample_id,
        "account": safe_account,
        "user_id": safe_user_id,
        "agent_id": safe_agent_id,
        "status": "ECHOMEMORY_IMPORT_FAILED",
        "integrity": "failed",
        "expected_messages": len(messages),
        "submitted_messages": 0,
        "session_id": "",
        "memory_uri": f"echo://{safe_account}/memories/",
        "estimated_import_tokens": sum(token_estimate(str(msg.get("content") or "")) for msg in messages),
        "import_llm_prompt_tokens": 0,
        "import_llm_completion_tokens": 0,
        "import_llm_total_tokens": 0,
        "import_embedding_total_tokens": 0,
        "import_total_tokens": 0,
        "error": str(error or "import failed"),
        "requested_session_id": "",
        "import_elapsed_s": round(float(elapsed_s or 0.0), 4),
        "import_commit_elapsed_s": 0.0,
        "import_flush_elapsed_s": 0.0,
        "import_artifact_wait_elapsed_s": 0.0,
        "memory_settle_wait_elapsed_s": 0.0,
        "repair_elapsed_s": 0.0,
        "memory_injection_time_s": round(float(elapsed_s or 0.0), 4),
    }


def failed_row_from_import(
    job: benchmark_adapter.Job,
    args: argparse.Namespace,
    record: dict[str, Any],
    *,
    error_kind: str,
    error_message: str,
    health_status: str = "import_failed",
) -> dict[str, Any]:
    memory_injection_time_s = round(float(record.get("memory_injection_time_s") or record.get("import_elapsed_s") or 0.0), 4)
    return {
        **benchmark_adapter.asdict(job),
        "response": "",
        "simple_grade": "NEEDS_JUDGE",
        "result": "",
        "reasoning": f"[IMPORT ERROR] {error_message}",
        "time_cost": "0",
        "backend": "echomemory",
        "eval_engine": "echomemory_generic_qa",
        "namespace": args.namespace,
        "dataset_path": str(args.dataset_path),
        "memory_uri": str(record.get("memory_uri") or "echo://user/memories/"),
        "qa_account_id": str(record.get("account") or account_from_memory_uri(record.get("memory_uri")) or args.account),
        "qa_user_id": str(record.get("user_id") or ""),
        "qa_agent_id": str(record.get("agent_id") or ""),
        "identity_mode": args.identity_mode,
        "relevant_memory": "[]",
        "retrieval_count": "0",
        "memory_hit_count": "0",
        "retrieval_tokens_est": "0",
        "answer_prompt_tokens": "0",
        "answer_completion_tokens": "0",
        "answer_total_tokens": "0",
        "token_usage": token_usage_json(0, 0, 0),
        "model_status": "failed",
        "model_error_kind": error_kind,
        "model_error": str(error_message),
        "retrieval_status": "unknown",
        "answer_status": "failed",
        "health_status": health_status,
        "import_session_id": str(record.get("session_id") or ""),
        "import_status": str(record.get("status") or ""),
        "import_integrity": str(record.get("integrity") or ""),
        "import_expected_messages": str(record.get("expected_messages") or 0),
        "import_submitted_messages": str(record.get("submitted_messages") or 0),
        "import_error": str(record.get("error") or error_message),
        "import_elapsed_s": str(record.get("import_elapsed_s") or 0),
        "memory_settle_wait_elapsed_s": str(record.get("memory_settle_wait_elapsed_s") or 0),
        "repair_elapsed_s": str(record.get("repair_elapsed_s") or 0),
        "memory_injection_time_s": str(memory_injection_time_s),
        "qa_time_s": "0",
        "end_to_end_time_s": str(memory_injection_time_s),
    }


async def run_hotpotqa_global_sentence_corpus(
    args: argparse.Namespace,
    sdk: Any,
    root: Path,
    out_dir: Path,
    import_dir: Path,
    status_path: Path,
    import_summary_path: Path,
    config_path: Path,
) -> None:
    run_started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    selected = list(iter_hotpotqa_raw_job_plans(args))
    if args.random_count:
        rnd = random.Random(args.random_seed)
        selected = rnd.sample(selected, min(args.random_count, len(selected)))
        selected = [(index + 1, raw_index, raw, job, plan) for index, (_old_index, raw_index, raw, job, plan) in enumerate(selected)]
    if not selected:
        raise SystemExit(f"no HotpotQA jobs found in {args.dataset_path}")

    jobs = [entry[3] for entry in selected]
    messages = hotpotqa_global_sentence_messages(args, selected)
    sentence_records = hotpotqa_global_sentence_records(args, selected)
    corpus_sample_id = "__hotpotqa_global_sentence_corpus__"
    requested_session_id = f"hotpotqa-global-sentence-{safe_slug(args.namespace, 44)}-{uuid.uuid4().hex[:8]}"
    corpus_manifest = {
        "status": "HOTPOTQA_GLOBAL_SENTENCE_CORPUS_READY",
        "dataset": str(args.dataset_path),
        "namespace": args.namespace,
        "selected_questions": len(jobs),
        "sentence_messages": len(messages),
        "sentence_records": len(sentence_records),
        "top_k": args.top_k,
        "global_import_mode": args.hotpotqa_global_import_mode,
        "requested_session_id": requested_session_id,
        "user_id": args.user_id,
        "agent_id": args.agent_id,
        "question_ids": [job.question_id for job in jobs],
        "message_file": str(import_dir / "hotpotqa_global_sentence_messages.json"),
    }
    write_json(import_dir / "hotpotqa_global_sentence_messages.json", messages)
    write_json(import_dir / "hotpotqa_global_sentence_records.json", sentence_records)
    write_json(import_dir / "hotpotqa_global_sentence_manifest.json", corpus_manifest)
    print(
        f"[hotpotqa-global] questions={len(jobs)} sentence_messages={len(messages)} "
        f"user_id={args.user_id} agent_id={args.agent_id} top_k={args.top_k} "
        f"import_mode={args.hotpotqa_global_import_mode}",
        flush=True,
    )

    import_records: dict[str, dict[str, Any]] = {}
    import_record: dict[str, Any] | None = None
    existing_global_session_id = str(getattr(args, "hotpotqa_existing_global_session_id", "") or "").strip()
    if existing_global_session_id:
        import_record = {
            "sample_id": corpus_sample_id,
            "session_id": existing_global_session_id,
            "requested_session_id": existing_global_session_id,
            "user_id": args.user_id,
            "agent_id": args.agent_id,
            "status": "ECHOMEMORY_GLOBAL_CORPUS_REUSED",
            "integrity": "complete",
            "memory_uri": f"echo://{args.account}/memories/",
            "expected_messages": len(messages),
            "submitted_messages": len(messages),
            "estimated_import_tokens": sum(token_estimate(str(msg.get("content") or "")) for msg in messages),
            "import_elapsed_s": 0.0,
            "memory_injection_time_s": 0.0,
            "reused_existing_session": True,
        }
        import_records[corpus_sample_id] = import_record
        write_json(import_summary_path, import_record_summary(import_records, args.workspace, args.account))
        print(f"[hotpotqa-global] reuse existing corpus session={existing_global_session_id}", flush=True)
    if args.resume and import_summary_path.exists():
        try:
            payload = json.loads(import_summary_path.read_text(encoding="utf-8"))
            for item in payload.get("records") or []:
                sample_id = str(item.get("sample_id") or "").strip()
                if sample_id:
                    item["account"] = str(item.get("account") or account_from_memory_uri(item.get("memory_uri")) or sample_account(args, sample_id))
                    import_records[sample_id] = item
            existing = import_records.get(corpus_sample_id)
            if existing and str(existing.get("integrity") or "").strip().lower() in {"complete", "pending_async_memory"}:
                import_record = existing
                print(f"[hotpotqa-global] resume corpus session={existing.get('session_id') or ''}", flush=True)
        except Exception as exc:
            print(f"[hotpotqa-global] could not read import summary: {exc}", flush=True)

    if import_record is None:
        write_status(
            status_path,
            {
                "stage": "importing_hotpotqa_global_sentence_corpus",
                "questions": len(jobs),
                "sentence_messages": len(messages),
                "requested_session_id": requested_session_id,
            },
        )
        import_started = time.time()
        import_started_at = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        import_args = argparse.Namespace(**vars(args))
        import_args.dataset_format = "hotpotqa"
        if str(getattr(args, "hotpotqa_global_import_mode", "") or "").strip().lower() == "projection":
            import_record = await import_hotpotqa_global_sentence_projection(
                import_args,
                sdk,
                requested_session_id,
                messages,
                f"hotpotqa/global_sentence_corpus/{args.namespace}",
            )
        else:
            import_record = await import_one_session(
                import_args,
                sdk,
                requested_session_id,
                messages,
                f"hotpotqa/global_sentence_corpus/{args.namespace}",
            )
        import_record.update(
            {
                "sample_id": corpus_sample_id,
                "user_id": args.user_id,
                "agent_id": args.agent_id,
                "status": "ECHOMEMORY_GLOBAL_CORPUS_IMPORT_DONE",
                "memory_uri": f"echo://{args.account}/memories/",
                "estimated_import_tokens": sum(token_estimate(str(msg.get("content") or "")) for msg in messages),
                "requested_session_id": requested_session_id,
                "import_started_at": import_started_at,
                "import_finished_at": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
                "import_elapsed_s": round(time.time() - import_started, 4),
                "memory_injection_time_s": round(time.time() - import_started, 4),
            }
        )
        import_records[corpus_sample_id] = import_record
        write_json(import_summary_path, import_record_summary(import_records, args.workspace, args.account))

    if args.import_only:
        csv_path = out_dir / "echomemory_generic_qa_results.csv"
        write_csv(csv_path, [])
        write_running_summary(running_summary_path := out_dir / "running_summary.json", [], status="succeeded", csv_path=csv_path)
        import_summary = import_record_summary(import_records, args.workspace, args.account)
        write_json(import_summary_path, import_summary)
        summary = {
            **alignment_metadata("echomemory", ECHOMEMORY_BACKEND_ROUTE),
            "status": "ECHOMEMORY_HOTPOTQA_GLOBAL_SENTENCE_IMPORT_ONLY_DONE",
            "dataset_format": "hotpotqa",
            "hotpotqa_corpus_mode": "global_sentence_corpus",
            "dataset": str(args.dataset_path),
            "sample": args.sample,
            "count": len(jobs),
            "rows": 0,
            "output_csv": str(csv_path),
            "backend": "echomemory",
            "eval_engine": "echomemory_hotpotqa_global_sentence_corpus",
            "echomem_root": str(root),
            "echomem_config": str(config_path),
            "workspace": str(Path(args.workspace).expanduser().resolve()),
            "account": args.account,
            "namespace": args.namespace,
            "identity_mode": "fixed",
            "user_id": args.user_id,
            "agent_id": args.agent_id,
            "run_started_at": run_started_at,
            "run_finished_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "global_corpus_questions": len(jobs),
            "global_corpus_messages": len(messages),
            "global_corpus_manifest": str(import_dir / "hotpotqa_global_sentence_manifest.json"),
            "global_corpus_import_elapsed_s": import_record.get("memory_injection_time_s") or import_record.get("import_elapsed_s"),
            "import_summary": import_summary,
            "import_summary_path": str(import_summary_path),
            "answer_model": args.answer_model,
            "official_eval_after": False,
            "official_eval": {"enabled": False, "reason": "import_only"},
            "top_k": args.top_k,
            "score_threshold": args.score_threshold,
            "tool_search_limit": args.tool_search_limit,
            "tool_min_score": args.tool_min_score,
            "tool_set": args.tool_set,
            "max_iterations": args.max_iterations,
            "prompt_mode": args.prompt_mode,
            "vikingboat_alignment_profile": VIKINGBOT_ALIGNMENT_PROFILE,
            "vikingbot_prompt_aligned": args.prompt_mode in VIKINGBOT_ALIGNED_PROMPT_MODES,
            "vikingboat_compat": bool(args.vikingboat_compat),
            "memory_tool_loop_enabled": False,
            "memory_tool_set": args.tool_set,
            "fallback_to_one_shot": bool(args.fallback_to_one_shot),
            "question_timeout_s": args.question_timeout_s,
            "retrieval_query_strategy": args.retrieval_query_strategy,
            "checkpoint_interval": max(0, int(getattr(args, "checkpoint_interval", 0) or 0)),
            "checkpoint_scores": [],
            "checkpoint_scores_path": str(out_dir / "hotpotqa_checkpoint_scores.json"),
            "retrieval_ok_count": 0,
            "retrieval_empty_count": 0,
            "model_ok_count": 0,
            "model_failed_count": 0,
            "answer_ok_count": 0,
            "answer_total_tokens": 0,
            "retrieval_tokens_est": 0,
            "memory_hit_total": 0,
            "avg_retrieval_count": 0,
            "health_counts": {},
            "total_qa_time_s": 0,
            "avg_qa_time_s": None,
            "total_retrieval_latency_ms": 0,
            "avg_retrieval_latency_ms": None,
            "total_injection_time_s": import_record.get("memory_injection_time_s") or import_record.get("import_elapsed_s"),
            "avg_per_question_memory_injection_time_s": 0,
        }
        summary.update(
            {
                key: import_summary.get(key)
                for key in (
                    "llm_input_tokens",
                    "llm_output_tokens",
                    "llm_total_tokens",
                    "llm_call_count",
                    "import_llm_prompt_tokens",
                    "import_llm_completion_tokens",
                    "import_llm_total_tokens",
                    "import_embedding_total_tokens",
                    "import_total_tokens",
                    "search_intent_total_tokens",
                    "search_intent_call_count",
                    "embedding_total_tokens",
                    "embedding_call_count",
                    "call_sites",
                )
                if key in import_summary
            }
        )
        write_json(out_dir / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return

    csv_path = out_dir / "echomemory_generic_qa_results.csv"
    running_summary_path = out_dir / "running_summary.json"
    existing_rows = load_existing_csv(csv_path) if args.resume else []
    existing_by_key = {row_key(row): row for row in existing_rows if row_key(row)}
    rows: list[dict[str, Any]] = []
    checkpoint_interval = max(0, int(getattr(args, "checkpoint_interval", 0) or 0))
    total_label = str(len(jobs))
    write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)

    for index, job in enumerate(jobs, 1):
        key = str(job.question_id or job.sample_id or "").strip()
        existing = existing_by_key.get(key)
        if existing and not should_retry_row(existing, args.retry_failed, args.retry_empty_answers):
            rows.append(existing)
            print(f"[resume] skip {index}/{total_label} {job.question_id}", flush=True)
            continue
        qa_args = argparse.Namespace(**vars(args))
        qa_args.user_id = args.user_id
        qa_args.agent_id = args.agent_id
        qa_args.identity_mode = "fixed"
        qa_args.import_session_id = str(import_record.get("session_id") or "")
        print(f"[qa-global] {index}/{total_label} {job.question_id} {job.question[:90]}", flush=True)
        try:
            if args.question_timeout_s and args.question_timeout_s > 0:
                row = await asyncio.wait_for(answer_question(qa_args, sdk, job, out_dir=out_dir, question_no=index), timeout=args.question_timeout_s)
            else:
                row = await answer_question(qa_args, sdk, job, out_dir=out_dir, question_no=index)
        except asyncio.TimeoutError:
            row = {
                **benchmark_adapter.asdict(job),
                "response": "",
                "simple_grade": "NEEDS_JUDGE",
                "result": "",
                "reasoning": f"[QA ERROR] question exceeded timeout_s={args.question_timeout_s}",
                "time_cost": str(round(args.question_timeout_s, 3)),
                "backend": "echomemory",
                "relevant_memory": "[]",
                "retrieval_count": "0",
                "memory_hit_count": "0",
                "retrieval_tokens_est": "0",
                "answer_prompt_tokens": "0",
                "answer_completion_tokens": "0",
                "answer_total_tokens": "0",
                "token_usage": token_usage_json(0, 0, 0),
                "model_status": "failed",
                "model_error_kind": "question_timeout",
                "model_error": f"question exceeded timeout_s={args.question_timeout_s}",
                "retrieval_status": "unknown",
                "answer_status": "failed",
                "health_status": "question_timeout",
            }
        except Exception as exc:
            row = {
                **benchmark_adapter.asdict(job),
                "response": "",
                "simple_grade": "NEEDS_JUDGE",
                "result": "",
                "reasoning": f"[QA ERROR] {exc}",
                "time_cost": "0",
                "backend": "echomemory",
                "relevant_memory": "[]",
                "retrieval_count": "0",
                "memory_hit_count": "0",
                "retrieval_tokens_est": "0",
                "answer_prompt_tokens": "0",
                "answer_completion_tokens": "0",
                "answer_total_tokens": "0",
                "token_usage": token_usage_json(0, 0, 0),
                "model_status": "failed",
                "model_error_kind": classify_model_error(str(exc)),
                "model_error": str(exc),
                "retrieval_status": "unknown",
                "answer_status": "failed",
                "health_status": classify_model_error(str(exc)),
            }
        row.update(
            {
                "backend": "echomemory",
                "eval_engine": "echomemory_hotpotqa_global_sentence_corpus",
                "hotpotqa_corpus_mode": "global_sentence_corpus",
                "namespace": args.namespace,
                "dataset_path": str(args.dataset_path),
                "memory_uri": str(import_record.get("memory_uri") or "echo://user/memories/"),
                "qa_user_id": args.user_id,
                "qa_agent_id": args.agent_id,
                "identity_mode": "fixed",
                "import_session_id": str(import_record.get("session_id") or ""),
                "import_status": str(import_record.get("status") or ""),
                "import_integrity": str(import_record.get("integrity") or ""),
                "import_expected_messages": str(import_record.get("expected_messages") or len(messages)),
                "import_submitted_messages": str(import_record.get("submitted_messages") or 0),
                "import_error": str(import_record.get("error") or ""),
                "import_estimated_tokens": str(import_record.get("estimated_import_tokens") or 0),
                "import_llm_prompt_tokens": str(import_record.get("import_llm_prompt_tokens") or 0),
                "import_llm_completion_tokens": str(import_record.get("import_llm_completion_tokens") or 0),
                "import_llm_total_tokens": str(import_record.get("import_llm_total_tokens") or 0),
                "import_embedding_total_tokens": str(import_record.get("import_embedding_total_tokens") or 0),
                "import_total_tokens": str(import_record.get("import_total_tokens") or 0),
                "global_corpus_import_elapsed_s": str(import_record.get("memory_injection_time_s") or import_record.get("import_elapsed_s") or 0),
                "global_corpus_messages": str(len(messages)),
                "global_corpus_questions": str(len(jobs)),
                "memory_injection_time_s": "0",
                "memory_settle_wait_elapsed_s": "0",
                "repair_elapsed_s": "0",
                "qa_time_s": str(row.get("time_cost") or 0),
                "end_to_end_time_s": str(row.get("time_cost") or 0),
            }
        )
        rows.append(row)
        write_csv(csv_path, rows)
        write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)
        if checkpoint_interval and len(rows) % checkpoint_interval == 0:
            run_hotpotqa_checkpoint_eval(args, csv_path, out_dir, len(rows))

    write_csv(csv_path, rows)
    write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)
    import_summary = import_record_summary(import_records, args.workspace, args.account)
    write_json(import_summary_path, import_summary)
    official_eval_result = run_official_eval(args, csv_path)
    if checkpoint_interval and (not rows or len(rows) % checkpoint_interval != 0):
        run_hotpotqa_checkpoint_eval(args, csv_path, out_dir, len(rows))
    health_counts: Counter = Counter(str(row.get("health_status") or "unknown") for row in rows)
    checkpoint_scores_path = out_dir / "hotpotqa_checkpoint_scores.json"
    checkpoint_scores = json.loads(checkpoint_scores_path.read_text(encoding="utf-8")) if checkpoint_scores_path.exists() else []
    summary = {
        **alignment_metadata("echomemory", ECHOMEMORY_BACKEND_ROUTE),
        "status": "ECHOMEMORY_HOTPOTQA_GLOBAL_SENTENCE_QA_DONE",
        "dataset_format": "hotpotqa",
        "hotpotqa_corpus_mode": "global_sentence_corpus",
        "dataset": str(args.dataset_path),
        "sample": args.sample,
        "count": len(rows),
        "rows": len(rows),
        "output_csv": str(csv_path),
        "backend": "echomemory",
        "eval_engine": "echomemory_hotpotqa_global_sentence_corpus",
        "echomem_root": str(root),
        "echomem_config": str(config_path),
        "workspace": str(Path(args.workspace).expanduser().resolve()),
        "account": args.account,
        "namespace": args.namespace,
        "identity_mode": "fixed",
        "user_id": args.user_id,
        "agent_id": args.agent_id,
        "run_started_at": run_started_at,
        "run_finished_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "global_corpus_questions": len(jobs),
        "global_corpus_messages": len(messages),
        "global_corpus_manifest": str(import_dir / "hotpotqa_global_sentence_manifest.json"),
        "global_corpus_import_elapsed_s": import_record.get("memory_injection_time_s") or import_record.get("import_elapsed_s"),
        "import_summary": import_summary,
        "import_summary_path": str(import_summary_path),
        "answer_model": args.answer_model,
        "official_eval_after": bool(args.official_eval_after),
        "official_eval": official_eval_result,
        "top_k": args.top_k,
        "score_threshold": args.score_threshold,
        "tool_search_limit": args.tool_search_limit,
        "tool_min_score": args.tool_min_score,
        "tool_set": args.tool_set,
        "max_iterations": args.max_iterations,
        "prompt_mode": args.prompt_mode,
        "vikingboat_alignment_profile": VIKINGBOT_ALIGNMENT_PROFILE,
        "vikingbot_prompt_aligned": args.prompt_mode in VIKINGBOT_ALIGNED_PROMPT_MODES,
        "vikingboat_compat": bool(args.vikingboat_compat),
        "memory_tool_loop_enabled": bool(args.prompt_mode in VIKINGBOT_ALIGNED_PROMPT_MODES and args.vikingboat_tool_loop),
        "memory_tool_set": args.tool_set,
        "fallback_to_one_shot": bool(args.fallback_to_one_shot),
        "question_timeout_s": args.question_timeout_s,
        "retrieval_query_strategy": args.retrieval_query_strategy,
        "checkpoint_interval": checkpoint_interval,
        "checkpoint_scores": checkpoint_scores,
        "checkpoint_scores_path": str(checkpoint_scores_path),
        "retrieval_ok_count": sum(1 for row in rows if row.get("retrieval_status") == "ok"),
        "retrieval_empty_count": sum(1 for row in rows if row.get("retrieval_status") == "empty"),
        "model_ok_count": sum(1 for row in rows if row.get("model_status") == "ok"),
        "model_failed_count": sum(1 for row in rows if row.get("model_status") == "failed"),
        "answer_ok_count": sum(1 for row in rows if row.get("answer_status") == "ok"),
        "answer_total_tokens": sum(int(row.get("answer_total_tokens") or 0) for row in rows),
        "retrieval_tokens_est": sum(int(row.get("retrieval_tokens_est") or 0) for row in rows),
        "memory_hit_total": sum(int(row.get("memory_hit_count") or 0) for row in rows),
        "avg_retrieval_count": round(sum(int(row.get("retrieval_count") or 0) for row in rows) / len(rows), 2) if rows else 0,
        "health_counts": dict(health_counts),
        "total_qa_time_s": round(sum(float(row.get("qa_time_s") or row.get("time_cost") or 0.0) for row in rows), 4),
        "avg_qa_time_s": round(sum(float(row.get("qa_time_s") or row.get("time_cost") or 0.0) for row in rows) / len(rows), 4) if rows else None,
        "total_retrieval_latency_ms": round(sum(float(row.get("retrieval_latency_ms") or 0.0) for row in rows), 1),
        "avg_retrieval_latency_ms": round(sum(float(row.get("retrieval_latency_ms") or 0.0) for row in rows) / len(rows), 1) if rows else None,
        "total_injection_time_s": import_record.get("memory_injection_time_s") or import_record.get("import_elapsed_s"),
        "avg_per_question_memory_injection_time_s": 0,
    }
    summary.update(official_metric_summary(args.dataset_format, official_eval_result))
    write_json(out_dir / "summary.json", summary)
    write_running_summary(running_summary_path, rows, status="succeeded", csv_path=csv_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    official_failed = official_eval_result.get("enabled") and int(official_eval_result.get("returncode") or 0) != 0
    if official_failed:
        raise SystemExit(2)


async def run(args: argparse.Namespace) -> None:
    root = ensure_echomem_imports(args.echomem_root)
    layout = detect_echomem_layout(root)
    transport_mode = echomem_transport_mode(args.echomem_base_url, args.echomem_transport)

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    import_dir = out_dir / "echomemory_import"
    import_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / "generic_qa_status.json"
    repair_log = out_dir / "generic_qa_repair.log"
    repair_summary_path = out_dir / "echomemory_repair_summary.json"
    import_summary_path = import_dir / "echomemory_generic_import_summary.json"
    csv_path = out_dir / "echomemory_generic_qa_results.csv"
    running_summary_path = out_dir / "running_summary.json"
    config_dir = out_dir / "echomem_runtime_configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    if args.echomem_config:
        explicit_config_path = Path(args.echomem_config).expanduser().resolve()
    elif transport_mode == "http":
        explicit_config_path = config_dir / "echomem.http.json"
        write_json(
            explicit_config_path,
            {
                "transport": "http",
                "base_url": str(args.echomem_base_url or "").strip(),
                "auth_key_present": bool(str(args.echomem_auth_key or "").strip()),
            },
        )
    else:
        explicit_config_path = None
    runtime_config_cache: dict[tuple[str, str], Path] = {}

    def config_path_for_scope(account: str, user_id: str) -> Path:
        if explicit_config_path is not None:
            return explicit_config_path
        key = (account or "default", user_id or "default")
        cached = runtime_config_cache.get(key)
        if cached is not None:
            return cached
        scope_dir = config_dir / safe_slug(account or "default", 80)
        path = write_echomem_config(
            scope_dir,
            account,
            args.workspace,
            root,
            args.fallback_to_mock,
            fallback_to_mock_embedding_only=args.fallback_to_mock_embedding_only,
            user_id=user_id,
            recall_routers=EVAL_RECALL_ROUTERS,
        )
        runtime_config_cache[key] = path
        return path

    startup_config_path = config_path_for_scope(args.account, args.user_id)
    write_status(
        status_path,
        {
            "stage": "opening_runtime",
            "dataset": str(args.dataset_path),
            "dataset_format": str(args.dataset_format),
            "namespace": str(args.namespace),
            "workspace": str(args.workspace),
            "account": str(args.account),
            "echomem_root": str(root),
            "layout": layout,
            "config_path": str(startup_config_path),
        },
    )
    write_running_summary(running_summary_path, [], status="starting", csv_path=csv_path)
    runtime = None
    sdk = None
    runtime_generation = 0
    runtime_account = ""
    runtime_user_id = ""
    runtime_agent_id = ""
    runtime_config_path = startup_config_path

    async def recycle_runtime(
        reason: str,
        *,
        account: str,
        user_id: str,
        agent_id: str,
        config_path: Path,
    ) -> None:
        nonlocal runtime, sdk, runtime_generation
        nonlocal runtime_account, runtime_user_id, runtime_agent_id, runtime_config_path
        if runtime is not None:
            await close_sdk_runtime_with_timeout(
                runtime,
                sdk,
                drain_pending=not bool(getattr(args, "defer_artifact_wait", False)),
            )
        sdk, runtime, _layout = await open_echomem_sdk(
            echomem_root=root,
            workspace=args.workspace,
            account=account,
            user_id=user_id,
            agent_id=agent_id,
            config_path=config_path,
            base_url=args.echomem_base_url,
            auth_key=args.echomem_auth_key,
            transport_mode=args.echomem_transport,
            http_timeout_s=args.echomem_http_timeout_s,
        )
        runtime_generation += 1
        runtime_account = account
        runtime_user_id = user_id
        runtime_agent_id = agent_id
        runtime_config_path = config_path
        print(
            f"[runtime] generation={runtime_generation} reason={reason} "
            f"account={account} user_id={user_id} agent_id={agent_id} config={config_path}",
            flush=True,
        )

    await recycle_runtime(
        "startup",
        account=args.account,
        user_id=args.user_id,
        agent_id=args.agent_id,
        config_path=startup_config_path,
    )
    if (
        str(args.dataset_format or "").strip().lower() == "hotpotqa"
        and str(getattr(args, "hotpotqa_corpus_mode", "") or "").strip().lower() == "global_sentence_corpus"
    ):
        try:
            await run_hotpotqa_global_sentence_corpus(
                args,
                sdk,
                root,
                out_dir,
                import_dir,
                status_path,
                import_summary_path,
                startup_config_path,
            )
        finally:
            await close_sdk_runtime_with_timeout(
                runtime,
                sdk,
                drain_pending=not bool(getattr(args, "defer_artifact_wait", False)),
            )
        return
    existing_rows = load_existing_csv(csv_path) if args.resume else []
    existing_by_key = {row_key(row): row for row in existing_rows if row_key(row)}
    resumed_existing_rows = len(existing_by_key)
    skipped_existing_rows = 0
    rerun_existing_rows = 0
    import_records: dict[str, dict[str, Any]] = {}
    if args.resume and import_summary_path.exists():
        try:
            payload = json.loads(import_summary_path.read_text(encoding="utf-8"))
            for item in payload.get("records") or []:
                sample_id = str(item.get("sample_id") or "").strip()
                if sample_id:
                    import_records[sample_id] = item
        except Exception as exc:
            print(f"[resume] could not read import summary: {exc}", flush=True)
    for row in existing_rows:
        item = import_record_from_row(row, args)
        if item and item["sample_id"] not in import_records:
            item["account"] = str(row.get("qa_account_id") or account_from_memory_uri(item.get("memory_uri")) or sample_account(args, item["sample_id"]))
            import_records[item["sample_id"]] = item

    total_jobs = planned_job_count(args)
    total_label = str(total_jobs) if total_jobs is not None else "?"
    print(
        f"[qa] dataset={args.dataset_path} format={args.dataset_format} jobs={total_label} "
        f"backend=echomemory root={root} namespace={args.namespace}",
        flush=True,
    )
    if existing_by_key:
        print(f"[resume] existing_rows={len(existing_by_key)} retry_failed={bool(args.retry_failed)} csv={csv_path}", flush=True)

    rows: list[dict[str, Any]] = []
    checkpoint_interval = max(0, int(getattr(args, "checkpoint_interval", 0) or 0))
    write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)
    processed_jobs = 0
    recycle_every = max(0, int(getattr(args, "runtime_recycle_every", 0) or 0))
    import_timeout_s = max(0, int(getattr(args, "import_timeout_s", 0) or 0))
    runtime_jobs = 0
    fast_wait_mode = str(getattr(args, "import_wait_mode", "") or "").strip().lower() == "fast"
    strict_ready_required = str(getattr(args, "dataset_format", "") or getattr(args, "format", "") or "").strip().lower() in STRICT_READY_DATASET_FORMATS
    late_ready_grace_seconds = 120 if strict_ready_required else 0
    print(
        f"[runtime] recycle_every={recycle_every} import_wait_mode={'fast' if fast_wait_mode else 'full'} "
        f"defer_artifact_wait={bool(getattr(args, 'defer_artifact_wait', False))} "
        f"strict_ready_required={strict_ready_required} "
        f"import_timeout_s={import_timeout_s}",
        flush=True,
    )
    try:
        for index, job, plan in iter_job_plans(args):
            processed_jobs = index
            key = str(job.question_id or job.sample_id or "").strip()
            existing = existing_by_key.get(key)
            if existing and not should_retry_row(existing, args.retry_failed, args.retry_empty_answers):
                rows.append(existing)
                skipped_existing_rows += 1
                if job.sample_id not in import_records:
                    item = import_record_from_row(existing, args)
                    if item:
                        item["account"] = str(existing.get("qa_account_id") or account_from_memory_uri(item.get("memory_uri")) or sample_account(args, job.sample_id))
                        import_records[job.sample_id] = item
                print(f"[resume] skip {index}/{total_label} {job.question_id}", flush=True)
                continue
            scoped_account, scoped_user_id, scoped_agent_id = sample_scope(args, job.sample_id)
            scoped_config_path = config_path_for_scope(scoped_account, scoped_user_id)
            scope_changed = (
                runtime_account != scoped_account
                or runtime_user_id != scoped_user_id
                or runtime_agent_id != scoped_agent_id
                or runtime_config_path != scoped_config_path
            )
            if scope_changed:
                await recycle_runtime(
                    f"scope_change_{safe_slug(job.sample_id, 32)}",
                    account=scoped_account,
                    user_id=scoped_user_id,
                    agent_id=scoped_agent_id,
                    config_path=scoped_config_path,
                )
                runtime_jobs = 0
            elif recycle_every and runtime_jobs >= recycle_every:
                await recycle_runtime(
                    f"recycle_after_{runtime_jobs}_jobs",
                    account=scoped_account,
                    user_id=scoped_user_id,
                    agent_id=scoped_agent_id,
                    config_path=scoped_config_path,
                )
                runtime_jobs = 0
            if existing:
                rerun_existing_rows += 1
                print(f"[resume] retry {index}/{total_label} {job.question_id}", flush=True)
            sample_args = argparse.Namespace(**vars(args))
            sample_args.account = scoped_account
            sample_args.user_id = scoped_user_id
            sample_args.agent_id = scoped_agent_id
            sample_args.echomem_config = str(scoped_config_path)
            if job.sample_id not in import_records:
                print(f"[import] {index}/{total_label} sample={job.sample_id} events={len(plan.get('events') or [])}", flush=True)
                write_status(
                    status_path,
                    {
                        "stage": "importing_memory",
                        "sample": job.sample_id,
                        "question_id": job.question_id,
                        "job_index": index,
                        "job_total": total_jobs,
                        "import_timeout_s": import_timeout_s,
                    },
                )
                import_started = time.time()
                try:
                    if import_timeout_s > 0:
                        import_records[job.sample_id] = await asyncio.wait_for(
                            import_sample_memory(sample_args, sdk, job.sample_id, plan, import_dir),
                            timeout=import_timeout_s,
                        )
                    else:
                        import_records[job.sample_id] = await import_sample_memory(sample_args, sdk, job.sample_id, plan, import_dir)
                except asyncio.TimeoutError:
                    elapsed_s = time.time() - import_started
                    print(
                        f"[import-timeout] {index}/{total_label} sample={job.sample_id} timeout_s={import_timeout_s}",
                        flush=True,
                    )
                    import_records[job.sample_id] = failed_import_record(
                        sample_args,
                        job.sample_id,
                        plan,
                        error=f"TimeoutError: import exceeded {import_timeout_s}s",
                        elapsed_s=elapsed_s,
                    )
                except Exception as exc:
                    print(f"[import-error] {index}/{total_label} sample={job.sample_id} error={exc}", flush=True)
                    import_records[job.sample_id] = failed_import_record(
                        sample_args,
                        job.sample_id,
                        plan,
                        error=f"{type(exc).__name__}: {exc}",
                        elapsed_s=time.time() - import_started,
                    )
                import_summary = import_record_summary(import_records, args.workspace, args.account)
                write_json(import_summary_path, import_summary)
            if args.import_only:
                runtime_jobs += 1
                print(f"[import-only] {index}/{total_label} sample={job.sample_id}", flush=True)
                continue
            record = import_records[job.sample_id]
            if str(record.get("integrity") or "").strip().lower() == "failed":
                row = failed_row_from_import(
                    job,
                    args,
                    record,
                    error_kind="import_failed",
                    error_message=str(record.get("error") or "import failed"),
                )
                rows.append(row)
                write_csv(csv_path, rows)
                write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)
                runtime_jobs += 1
                continue
            import_summary = import_record_summary(import_records, args.workspace, args.account)
            expected_sessions = current_sample_expected_sessions(record)
            try:
                write_status(
                    status_path,
                    {
                        "stage": "waiting_async_memory_settle",
                        "import_summary": str(import_summary_path),
                        "import_status": str(import_summary.get("status") or ""),
                        "sample": job.sample_id,
                        "account": scoped_account,
                        "expected_sessions": expected_sessions,
                    },
                )
                target_session_id = str(record.get("session_id") or "")
                stabilize_started = time.time()
                fast_wait_timeout_s = (
                    fast_wait_timeout_seconds(args, strict_ready_required=strict_ready_required)
                    if fast_wait_mode
                    else int(args.stabilize_timeout_seconds)
                )
                stabilize_result = wait_for_async_memory_stability(
                    workspace=Path(args.workspace).expanduser().resolve(),
                    account=scoped_account,
                    sample=job.sample_id,
                    target_session_id=target_session_id,
                    expected_sessions_total=expected_sessions,
                    stabilize_timeout_seconds=fast_wait_timeout_s,
                    poll_seconds=max(5, int(args.poll_seconds)) if fast_wait_mode else int(args.poll_seconds),
                    stability_polls=1 if fast_wait_mode else int(args.stability_polls),
                    status_path=status_path,
                    import_summary=import_summary_path,
                    import_status=str(import_summary.get("status") or ""),
                )
                if fast_wait_mode and strict_ready_required:
                    summary_ready = snapshot_has_summary_ready(
                        stabilize_result.get("snapshot") or {},
                        expected_sessions,
                    )
                    summary_deadline = time.time() + max(0, int(fast_wait_timeout_s))
                    latest_snapshot = stabilize_result.get("snapshot") or {}
                    while not summary_ready and time.time() < summary_deadline:
                        time.sleep(max(5, int(args.poll_seconds)))
                        latest_snapshot = build_workspace_snapshot(
                            Path(args.workspace).expanduser().resolve(),
                            scoped_account,
                            job.sample_id,
                            target_session_id=target_session_id,
                        )
                        summary_ready = snapshot_has_summary_ready(latest_snapshot, expected_sessions)
                        write_status(
                            status_path,
                            {
                                "stage": "waiting_summary_ready",
                                "import_summary": str(import_summary_path),
                                "import_status": str(import_summary.get("status") or ""),
                                "sample": job.sample_id,
                                "account": scoped_account,
                                "expected_sessions": expected_sessions,
                                "summary_ready": summary_ready,
                                "snapshot": latest_snapshot,
                            },
                        )
                    stabilize_result = {
                        **stabilize_result,
                        "ready": bool(summary_ready),
                        "summary_ready": bool(summary_ready),
                        "snapshot": latest_snapshot,
                        "timed_out": not bool(summary_ready),
                    }
                stabilize_elapsed_s = round(time.time() - stabilize_started, 4)
                import_records[job.sample_id]["memory_settle_wait_elapsed_s"] = stabilize_elapsed_s
                repair_code = 0
                repair_elapsed_s = 0.0
                if (
                    not stabilize_result.get("ready")
                    and not fast_wait_mode
                    and late_ready_grace_seconds > 0
                ):
                    grace_started = time.time()
                    grace_result = late_ready_grace_check(
                        workspace=Path(args.workspace).expanduser().resolve(),
                        account=scoped_account,
                        sample=job.sample_id,
                        expected_sessions_total=expected_sessions,
                        grace_seconds=late_ready_grace_seconds,
                        poll_seconds=max(5, int(args.poll_seconds)),
                    )
                    stabilize_elapsed_s = round(stabilize_elapsed_s + (time.time() - grace_started), 4)
                    import_records[job.sample_id]["memory_settle_wait_elapsed_s"] = stabilize_elapsed_s
                    if grace_result.get("ready"):
                        stabilize_result = {
                            **stabilize_result,
                            **grace_result,
                            "ready": True,
                            "late_ready_grace_used": True,
                        }
                if not stabilize_result.get("ready") and args.repair_before_qa and not fast_wait_mode:
                    repair_cmd = [
                        sys.executable,
                        str(ROOT / "scripts" / "echomemory_repair_sessions.py"),
                        "--out-dir",
                        str(out_dir),
                        "--echomem-root",
                        str(root),
                        "--echomem-config",
                        str(scoped_config_path),
                        "--workspace",
                        args.workspace,
                        "--account",
                        scoped_account,
                        "--user-id",
                        scoped_user_id,
                        "--agent-id",
                        scoped_agent_id,
                        "--sample",
                        job.sample_id,
                        "--include-complete",
                        "--commit-wait-s",
                        str(args.repair_commit_wait_s),
                        "--flush-call-timeout-s",
                        str(args.repair_flush_call_timeout_s),
                        "--flush-attempts",
                        str(args.repair_flush_attempts),
                    ]
                    write_status(
                        status_path,
                        {
                            "stage": "running_repair",
                            "repair_log": str(repair_log),
                            "repair_cmd": repair_cmd,
                            "sample": job.sample_id,
                            "memory_settle": stabilize_result,
                        },
                    )
                    repair_started = time.time()
                    repair_code = run_and_log(repair_cmd, repair_log, dict(os.environ))
                    repair_elapsed_s = round(time.time() - repair_started, 4)
                    import_records[job.sample_id]["repair_elapsed_s"] = repair_elapsed_s
                    if repair_code == 0:
                        stabilize_started = time.time()
                        stabilize_result = wait_for_async_memory_stability(
                            workspace=Path(args.workspace).expanduser().resolve(),
                            account=scoped_account,
                            sample=job.sample_id,
                            target_session_id=target_session_id,
                            expected_sessions_total=expected_sessions,
                            stabilize_timeout_seconds=min(int(args.stabilize_timeout_seconds), 180),
                            poll_seconds=max(5, int(args.poll_seconds)),
                            stability_polls=max(1, int(args.stability_polls)),
                            status_path=status_path,
                            import_summary=import_summary_path,
                            import_status=str(import_summary.get("status") or ""),
                        )
                        stabilize_elapsed_s = round(stabilize_elapsed_s + (time.time() - stabilize_started), 4)
                        import_records[job.sample_id]["memory_settle_wait_elapsed_s"] = stabilize_elapsed_s
                if fast_wait_mode and not stabilize_result.get("ready"):
                    print(f"[fast-wait] sample={job.sample_id} proceeding before async artifacts fully stabilize", flush=True)
                allow_partial_memory = bool(fast_wait_mode and not strict_ready_required)
                require_memory_ready_or_exit(
                    status_path=status_path,
                    stage="qa_blocked_memory_not_ready",
                    import_summary=import_summary_path,
                    import_status=str(import_summary.get("status") or ""),
                    stabilize_result=stabilize_result,
                    expected_sessions=expected_sessions,
                    allow_partial=allow_partial_memory,
                )
            except SystemExit as exc:
                message = f"memory not ready before QA (exit={exc.code})"
                import_records[job.sample_id]["status"] = "ECHOMEMORY_IMPORT_FAILED"
                import_records[job.sample_id]["integrity"] = "failed"
                import_records[job.sample_id]["error"] = message
                import_records[job.sample_id]["memory_injection_time_s"] = round(
                    float(import_records[job.sample_id].get("import_elapsed_s") or 0.0)
                    + float(import_records[job.sample_id].get("memory_settle_wait_elapsed_s") or 0.0)
                    + float(import_records[job.sample_id].get("repair_elapsed_s") or 0.0),
                    4,
                )
                write_json(import_summary_path, import_record_summary(import_records, args.workspace, args.account))
                row = failed_row_from_import(
                    job,
                    args,
                    import_records[job.sample_id],
                    error_kind="memory_not_ready",
                    error_message=message,
                    health_status="memory_not_ready",
                )
                rows.append(row)
                write_csv(csv_path, rows)
                write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)
                runtime_jobs += 1
                continue
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                import_records[job.sample_id]["status"] = "ECHOMEMORY_IMPORT_FAILED"
                import_records[job.sample_id]["integrity"] = "failed"
                import_records[job.sample_id]["error"] = message
                import_records[job.sample_id]["memory_injection_time_s"] = round(
                    float(import_records[job.sample_id].get("import_elapsed_s") or 0.0)
                    + float(import_records[job.sample_id].get("memory_settle_wait_elapsed_s") or 0.0)
                    + float(import_records[job.sample_id].get("repair_elapsed_s") or 0.0),
                    4,
                )
                write_json(import_summary_path, import_record_summary(import_records, args.workspace, args.account))
                row = failed_row_from_import(
                    job,
                    args,
                    import_records[job.sample_id],
                    error_kind="import_pipeline_error",
                    error_message=message,
                    health_status="import_pipeline_error",
                )
                rows.append(row)
                write_csv(csv_path, rows)
                runtime_jobs += 1
                continue
            record = import_records[job.sample_id]
            record["memory_ready_before_qa"] = bool(stabilize_result.get("ready"))
            record["memory_wait_mode"] = "fast" if fast_wait_mode else "full"
            record["strict_ready_required"] = strict_ready_required
            record["account"] = str(record.get("account") or scoped_account)
            record["memory_injection_time_s"] = round(
                float(record.get("import_elapsed_s") or 0.0)
                + float(record.get("memory_settle_wait_elapsed_s") or 0.0)
                + float(record.get("repair_elapsed_s") or 0.0),
                4,
            )
            qa_args = argparse.Namespace(**vars(sample_args))
            qa_args.account = str(record.get("account") or scoped_account)
            qa_args.user_id = str(record.get("user_id") or scoped_user_id)
            qa_args.agent_id = str(record.get("agent_id") or scoped_agent_id)
            qa_args.import_session_id = str(record.get("session_id") or "")
            qa_args.echomem_config = str(scoped_config_path)
            print(f"[qa] {index}/{total_label} {job.question_id} {job.question[:90]}", flush=True)
            try:
                if args.question_timeout_s and args.question_timeout_s > 0:
                    row = await asyncio.wait_for(answer_question(qa_args, sdk, job, out_dir=out_dir, question_no=index), timeout=args.question_timeout_s)
                else:
                    row = await answer_question(qa_args, sdk, job, out_dir=out_dir, question_no=index)
            except asyncio.TimeoutError:
                row = {
                    **benchmark_adapter.asdict(job),
                    "response": "",
                    "simple_grade": "NEEDS_JUDGE",
                    "result": "",
                    "reasoning": f"[QA ERROR] question exceeded timeout_s={args.question_timeout_s}",
                    "time_cost": str(round(args.question_timeout_s, 3)),
                    "backend": "echomemory",
                    "eval_engine": "echomemory_generic_qa",
                    "namespace": args.namespace,
                    "dataset_path": str(args.dataset_path),
                    "memory_uri": str(record.get("memory_uri") or "echo://user/memories/"),
                    "qa_account_id": str(record.get("account") or scoped_account),
                    "qa_user_id": str(record.get("user_id") or ""),
                    "qa_agent_id": str(record.get("agent_id") or ""),
                    "identity_mode": args.identity_mode,
                    "relevant_memory": "[]",
                    "retrieval_count": "0",
                    "memory_hit_count": "0",
                    "retrieval_tokens_est": "0",
                    "answer_prompt_tokens": "0",
                    "answer_completion_tokens": "0",
                    "answer_total_tokens": "0",
                    "token_usage": token_usage_json(0, 0, 0),
                    "model_status": "failed",
                    "model_error_kind": "question_timeout",
                    "model_error": f"question exceeded timeout_s={args.question_timeout_s}",
                    "retrieval_status": "unknown",
                    "answer_status": "failed",
                    "health_status": "question_timeout",
                }
            except Exception as exc:
                row = {
                    **benchmark_adapter.asdict(job),
                    "response": "",
                    "simple_grade": "NEEDS_JUDGE",
                    "result": "",
                    "reasoning": f"[QA ERROR] {exc}",
                    "time_cost": "0",
                    "backend": "echomemory",
                    "eval_engine": "echomemory_generic_qa",
                    "namespace": args.namespace,
                    "dataset_path": str(args.dataset_path),
                    "memory_uri": str(record.get("memory_uri") or "echo://user/memories/"),
                    "qa_account_id": str(record.get("account") or scoped_account),
                    "qa_user_id": str(record.get("user_id") or ""),
                    "qa_agent_id": str(record.get("agent_id") or ""),
                    "identity_mode": args.identity_mode,
                    "relevant_memory": "[]",
                    "retrieval_count": "0",
                    "memory_hit_count": "0",
                    "retrieval_tokens_est": "0",
                    "answer_prompt_tokens": "0",
                    "answer_completion_tokens": "0",
                    "answer_total_tokens": "0",
                    "token_usage": token_usage_json(0, 0, 0),
                    "model_status": "failed",
                    "model_error_kind": "api_error",
                    "model_error": str(exc),
                    "retrieval_status": "unknown",
                    "answer_status": "failed",
                    "health_status": "api_error",
                }
            row.update(
                {
                    "backend": "echomemory",
                    "eval_engine": "echomemory_generic_qa",
                    "namespace": args.namespace,
                    "dataset_path": str(args.dataset_path),
                    "memory_uri": str(record.get("memory_uri") or row.get("memory_uri") or "echo://user/memories/"),
                    "qa_account_id": str(record.get("account") or row.get("qa_account_id") or scoped_account),
                    "qa_user_id": str(record.get("user_id") or row.get("qa_user_id") or ""),
                    "qa_agent_id": str(record.get("agent_id") or row.get("qa_agent_id") or ""),
                    "identity_mode": args.identity_mode,
                    "import_session_id": str(record.get("session_id") or ""),
                    "import_status": str(record.get("status") or ""),
                    "import_integrity": str(record.get("integrity") or ""),
                    "import_expected_messages": str(record.get("expected_messages") or 0),
                    "import_submitted_messages": str(record.get("submitted_messages") or 0),
                    "import_error": str(record.get("error") or ""),
                    "import_elapsed_s": str(record.get("import_elapsed_s") or 0),
                    "import_estimated_tokens": str(record.get("estimated_import_tokens") or 0),
                    "import_llm_prompt_tokens": str(record.get("import_llm_prompt_tokens") or 0),
                    "import_llm_completion_tokens": str(record.get("import_llm_completion_tokens") or 0),
                    "import_llm_total_tokens": str(record.get("import_llm_total_tokens") or 0),
                    "import_embedding_total_tokens": str(record.get("import_embedding_total_tokens") or 0),
                    "import_total_tokens": str(record.get("import_total_tokens") or 0),
                    "memory_settle_wait_elapsed_s": str(record.get("memory_settle_wait_elapsed_s") or 0),
                    "repair_elapsed_s": str(record.get("repair_elapsed_s") or 0),
                    "memory_injection_time_s": str(record.get("memory_injection_time_s") or 0),
                    "qa_time_s": str(row.get("time_cost") or 0),
                    "end_to_end_time_s": str(
                        round(
                            float(record.get("memory_injection_time_s") or 0.0)
                            + float(row.get("time_cost") or 0.0),
                            4,
                        )
                    ),
                }
            )
            rows.append(row)
            write_csv(csv_path, rows)
            write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)
            if checkpoint_interval and len(rows) % checkpoint_interval == 0:
                run_checkpoint_eval(args, csv_path, out_dir, len(rows))
            runtime_jobs += 1
    finally:
        await close_sdk_runtime_with_timeout(
            runtime,
            sdk,
            drain_pending=not bool(getattr(args, "defer_artifact_wait", False)),
        )

    if processed_jobs == 0:
        raise SystemExit(f"no jobs found in {args.dataset_path}")
    write_csv(csv_path, rows)
    write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)
    import_summary = import_record_summary(import_records, args.workspace, args.account)
    write_json(import_summary_path, import_summary)
    judge_result = {"enabled": False, "reason": "import_only"} if args.import_only else run_judge(args, csv_path)
    official_eval_result = {"enabled": False, "reason": "import_only"} if args.import_only else run_official_eval(args, csv_path)
    if checkpoint_interval and (not rows or len(rows) % checkpoint_interval != 0):
        run_checkpoint_eval(args, csv_path, out_dir, len(rows))
    health_counts: Counter = Counter(str(row.get("health_status") or "unknown") for row in rows)
    tool_counts: Counter = Counter()
    for row in rows:
        try:
            tool_counts.update(json.loads(str(row.get("tool_call_name_counts") or "{}")))
        except Exception:
            pass
    judged_summary = judge_result.get("summary") if isinstance(judge_result, dict) else {}
    if not isinstance(judged_summary, dict):
        judged_summary = {}
    summary = {
        **alignment_metadata("echomemory", ECHOMEMORY_BACKEND_ROUTE),
        "status": "ECHOMEMORY_GENERIC_IMPORT_ONLY_DONE" if args.import_only else "ECHOMEMORY_GENERIC_QA_DONE",
        "dataset_format": args.dataset_format,
        "dataset": str(args.dataset_path),
        "sample": args.sample,
        "count": len(rows),
        "rows": len(rows),
        "output_csv": str(csv_path),
        "backend": "echomemory",
        "eval_engine": "echomemory_generic_qa",
        "echomem_root": str(root),
        "echomem_config": str(runtime_config_path),
        "workspace": str(Path(args.workspace).expanduser().resolve()),
        "account": args.account,
        "effective_accounts": sorted(
            {
                record_account(item, args.account)
                for item in import_records.values()
                if record_account(item, args.account)
            }
        ),
        "namespace": args.namespace,
        "identity_mode": args.identity_mode,
        "answer_model": args.answer_model,
        "judge_after": bool(args.judge_after),
        "judge": judge_result,
        "official_eval_after": bool(args.official_eval_after),
        "official_eval": official_eval_result,
        "checkpoint_interval": checkpoint_interval,
        "checkpoint_scores_path": str(checkpoint_scores_path_for_dataset(args.dataset_format, out_dir)),
        "checkpoint_scores": (
            json.loads(checkpoint_scores_path_for_dataset(args.dataset_format, out_dir).read_text(encoding="utf-8"))
            if checkpoint_scores_path_for_dataset(args.dataset_format, out_dir).exists()
            else []
        ),
        "graded": judged_summary.get("graded"),
        "correct": judged_summary.get("correct"),
        "wrong": judged_summary.get("wrong"),
        "accuracy": judged_summary.get("accuracy"),
        "import_summary": import_summary,
        "import_summary_path": str(import_summary_path),
        "retrieval_ok_count": sum(1 for row in rows if row.get("retrieval_status") == "ok"),
        "retrieval_empty_count": sum(1 for row in rows if row.get("retrieval_status") == "empty"),
        "model_ok_count": sum(1 for row in rows if row.get("model_status") == "ok"),
        "model_failed_count": sum(1 for row in rows if row.get("model_status") == "failed"),
        "answer_ok_count": sum(1 for row in rows if row.get("answer_status") == "ok"),
        "answer_total_tokens": sum(int(row.get("answer_total_tokens") or 0) for row in rows),
        "retrieval_tokens_est": sum(int(row.get("retrieval_tokens_est") or 0) for row in rows),
        "memory_hit_total": sum(int(row.get("memory_hit_count") or 0) for row in rows),
        "avg_retrieval_count": round(sum(int(row.get("retrieval_count") or 0) for row in rows) / len(rows), 2) if rows else 0,
        "health_counts": dict(health_counts),
        "tool_name_counts": dict(tool_counts),
        "resume_enabled": bool(args.resume),
        "resumed_existing_rows": resumed_existing_rows,
        "skipped_existing_rows": skipped_existing_rows,
        "rerun_existing_rows": rerun_existing_rows,
        "prompt_mode": args.prompt_mode,
        "vikingboat_alignment_profile": VIKINGBOT_ALIGNMENT_PROFILE,
        "vikingbot_prompt_aligned": args.prompt_mode in VIKINGBOT_ALIGNED_PROMPT_MODES,
        "vikingboat_compat": bool(args.vikingboat_compat),
        "memory_tool_loop_enabled": bool(args.prompt_mode in VIKINGBOT_ALIGNED_PROMPT_MODES and args.vikingboat_tool_loop),
        "memory_tool_set": args.tool_set,
        "top_k": args.top_k,
        "score_threshold": args.score_threshold,
        "retrieval_mode": args.retrieval_mode,
        "retrieval_query_strategy": args.retrieval_query_strategy,
        "total_memory_injection_time_s": round(sum(float(row.get("memory_injection_time_s") or 0.0) for row in rows), 4),
        "avg_memory_injection_time_s": round(
            sum(float(row.get("memory_injection_time_s") or 0.0) for row in rows) / len(rows),
            4,
        ) if rows else None,
        "total_memory_settle_wait_time_s": round(sum(float(row.get("memory_settle_wait_elapsed_s") or 0.0) for row in rows), 4),
        "avg_memory_settle_wait_time_s": round(
            sum(float(row.get("memory_settle_wait_elapsed_s") or 0.0) for row in rows) / len(rows),
            4,
        ) if rows else None,
        "total_qa_time_s": round(sum(float(row.get("qa_time_s") or row.get("time_cost") or 0.0) for row in rows), 4),
        "avg_qa_time_s": round(
            sum(float(row.get("qa_time_s") or row.get("time_cost") or 0.0) for row in rows) / len(rows),
            4,
        ) if rows else None,
        "total_end_to_end_time_s": round(sum(float(row.get("end_to_end_time_s") or 0.0) for row in rows), 4),
        "avg_end_to_end_time_s": round(
            sum(float(row.get("end_to_end_time_s") or 0.0) for row in rows) / len(rows),
            4,
        ) if rows else None,
        "avg_time": round(
            sum(float(row.get("qa_time_s") or row.get("time_cost") or 0.0) for row in rows) / len(rows),
            4,
        ) if rows else None,
    }
    summary.update(
        {
            key: import_summary.get(key)
            for key in (
                "llm_input_tokens",
                "llm_output_tokens",
                "llm_total_tokens",
                "llm_call_count",
                "import_llm_prompt_tokens",
                "import_llm_completion_tokens",
                "import_llm_total_tokens",
                "import_embedding_total_tokens",
                "import_total_tokens",
                "search_intent_total_tokens",
                "search_intent_call_count",
                "embedding_total_tokens",
                "embedding_call_count",
                "call_sites",
            )
            if key in import_summary
        }
    )
    summary.update(official_metric_summary(args.dataset_format, official_eval_result))
    write_json(out_dir / "summary.json", summary)
    judge_failed = judge_result.get("enabled") and int(judge_result.get("returncode") or 0) != 0
    official_failed = official_eval_result.get("enabled") and int(official_eval_result.get("returncode") or 0) != 0
    final_status = "failed" if (judge_failed or official_failed) else "succeeded"
    write_running_summary(running_summary_path, rows, status=final_status, csv_path=csv_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if judge_failed or official_failed:
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run generic memory benchmarks through EchoMemory local SDK import, retrieval, LLM answer, and optional Judge.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--format", dest="dataset_format", default="auto")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--sample", default="all")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--questions", default="")
    parser.add_argument("--namespace", default="")
    parser.add_argument("--hotpotqa-corpus-mode", choices=["per_question_documents", "global_sentence_corpus"], default="per_question_documents")
    parser.add_argument("--hotpotqa-global-import-mode", choices=["projection", "messages"], default="projection")
    parser.add_argument("--hotpotqa-projection-embed-batch-size", type=int, default=10)
    parser.add_argument("--hotpotqa-existing-global-session-id", default="")
    parser.add_argument("--checkpoint-interval", type=int, default=100)
    parser.add_argument("--echomem-root", default=str(DEFAULT_ECHOMEM_ROOT))
    parser.add_argument("--echomem-config", default="")
    parser.add_argument("--echomem-transport", choices=["local", "http"], default="")
    parser.add_argument("--echomem-base-url", default="")
    parser.add_argument("--echomem-auth-key", default=os.environ.get("ECHOMEM_AUTH_KEY") or "")
    parser.add_argument("--echomem-http-timeout-s", type=float, default=60.0)
    parser.add_argument("--workspace", default="/tmp/locomo-eval-echomemory")
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--identity-mode", choices=["isolated_sample", "fixed"], default="isolated_sample")
    parser.add_argument("--user-prefix", default="eval-user")
    parser.add_argument("--agent-prefix", default="eval-agent")
    parser.add_argument("--prompt-mode", choices=["vikingboat_lite", "vikingboat_compat", "one_shot"], default="one_shot")
    parser.add_argument("--vikingboat-compat", dest="vikingboat_compat", action="store_true")
    parser.add_argument("--no-vikingboat-compat", dest="vikingboat_compat", action="store_false")
    parser.add_argument("--top-k", type=int, default=VIKINGBOT_INITIAL_SEARCH_LIMIT)
    parser.add_argument("--score-threshold", type=float, default=VIKINGBOT_INITIAL_MIN_SCORE)
    parser.add_argument("--memory-budget-chars", type=int, default=VIKINGBOT_USER_MEMORY_BUDGET_CHARS + VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS)
    parser.add_argument("--user-memory-budget-chars", type=int, default=VIKINGBOT_USER_MEMORY_BUDGET_CHARS)
    parser.add_argument("--agent-memory-budget-chars", type=int, default=VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS)
    parser.add_argument("--retrieval-mode", choices=["find", "search", "both", "local"], default="search")
    parser.add_argument("--retrieval-ranker", choices=["diversified", "score"], default="score")
    parser.add_argument("--retrieval-query-strategy", choices=["expanded", "direct"], default="direct")
    parser.add_argument("--no-local-session-summaries", dest="local_session_summaries", action="store_false")
    parser.add_argument("--local-session-summaries", dest="local_session_summaries", action="store_true")
    parser.add_argument("--no-local-atoms", dest="local_atoms", action="store_false")
    parser.add_argument("--local-atoms", dest="local_atoms", action="store_true")
    parser.add_argument("--local-messages", dest="local_messages", action="store_true")
    parser.add_argument("--no-local-messages", dest="local_messages", action="store_false")
    parser.add_argument("--no-local-timeline-hints", dest="local_timeline_hints", action="store_false")
    parser.add_argument("--local-timeline-hints", dest="local_timeline_hints", action="store_true")
    parser.add_argument("--local-score-threshold", type=float, default=0.08)
    parser.add_argument("--local-summary-max", type=int, default=12)
    parser.add_argument("--local-atom-max", type=int, default=24)
    parser.add_argument("--local-message-max", type=int, default=16)
    parser.add_argument("--local-message-window", type=int, default=1)
    parser.add_argument("--no-local-memory-artifacts", dest="local_memory_artifacts", action="store_false")
    parser.add_argument("--local-memory-artifacts", dest="local_memory_artifacts", action="store_true")
    parser.add_argument("--local-artifact-max", type=int, default=24)
    parser.add_argument("--vikingboat-tool-loop", dest="vikingboat_tool_loop", action="store_true")
    parser.add_argument("--no-vikingboat-tool-loop", dest="vikingboat_tool_loop", action="store_false")
    parser.add_argument("--toolloop-rescue-on-toollike-answer", dest="toolloop_rescue_on_toollike_answer", action="store_true")
    parser.add_argument("--no-toolloop-rescue-on-toollike-answer", dest="toolloop_rescue_on_toollike_answer", action="store_false")
    parser.add_argument("--max-tool-calls", type=int, default=5)
    parser.add_argument("--tool-set", choices=["vikingboat_default", "search_read", "search_only", VIKINGBOT_TOOL_SET], default="search_read")
    parser.add_argument("--tool-search-limit", type=int, default=VIKINGBOT_TOOL_SEARCH_LIMIT)
    parser.add_argument("--tool-min-score", type=float, default=VIKINGBOT_TOOL_MIN_SCORE)
    parser.add_argument("--tool-log-chars", type=int, default=1200)
    parser.add_argument("--prefetch-read-count", type=int, default=4)
    parser.add_argument("--prefetch-context-chars", type=int, default=5000)
    parser.add_argument("--initial-tool-prefetch", dest="initial_tool_prefetch", action="store_true")
    parser.add_argument("--no-initial-tool-prefetch", dest="initial_tool_prefetch", action="store_false")
    parser.add_argument("--answer-base-url", default=os.environ.get("JUDGE_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1")
    parser.add_argument("--answer-model", default=os.environ.get("JUDGE_MODEL", "deepseek-v4-flash"))
    parser.add_argument("--answer-token", default=os.environ.get("LOCOMO_JUDGE_TOKEN") or os.environ.get("JUDGE_TOKEN") or os.environ.get("OPENAI_API_KEY") or "")
    parser.add_argument("--judge-base-url", default="")
    parser.add_argument("--judge-model", default="")
    parser.add_argument("--judge-token", default=os.environ.get("LOCOMO_JUDGE_TOKEN") or os.environ.get("JUDGE_TOKEN") or os.environ.get("OPENAI_API_KEY") or "")
    parser.add_argument("--judge-parallel", type=int, default=4)
    parser.add_argument("--judge-after", action="store_true")
    parser.add_argument("--official-eval-after", action="store_true")
    parser.add_argument("--model-retries", type=int, default=5)
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--question-timeout-s", type=int, default=600)
    parser.add_argument("--answer-refinement", dest="answer_refinement", action="store_true")
    parser.add_argument("--no-answer-refinement", dest="answer_refinement", action="store_false")
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--import-only", action="store_true")
    parser.add_argument("--resume", dest="resume", action="store_true", default=True)
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-empty-answers", action="store_true")
    parser.add_argument("--random-count", type=int, default=0)
    parser.add_argument("--random-seed", type=int, default=30)
    parser.add_argument("--fallback-to-mock", action="store_true")
    parser.add_argument("--fallback-to-mock-embedding-only", action="store_true")
    parser.add_argument("--fallback-to-one-shot", dest="fallback_to_one_shot", action="store_true")
    parser.add_argument("--no-fallback-to-one-shot", dest="fallback_to_one_shot", action="store_false")
    parser.add_argument("--import-wait-mode", choices=["fast", "full"], default="full")
    parser.add_argument("--allow-fast-wait-for-strict-ready", action="store_true")
    parser.add_argument("--defer-artifact-wait", action="store_true")
    parser.add_argument("--skip-session-commit", action="store_true")
    parser.add_argument("--continue-on-session-error", action="store_true")
    parser.add_argument("--commit-wait-s", type=int, default=300)
    parser.add_argument("--commit-call-timeout-s", type=int, default=300)
    parser.add_argument("--flush-call-timeout-s", type=int, default=600)
    parser.add_argument("--flush-attempts", type=int, default=2)
    parser.add_argument("--stabilize-timeout-seconds", type=int, default=300)
    parser.add_argument("--stability-polls", type=int, default=3)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--repair-before-qa", action="store_true", default=True)
    parser.add_argument("--no-repair-before-qa", dest="repair_before_qa", action="store_false")
    parser.add_argument("--repair-flush-call-timeout-s", type=int, default=600)
    parser.add_argument("--repair-flush-attempts", type=int, default=2)
    parser.add_argument("--repair-commit-wait-s", type=int, default=300)
    parser.add_argument("--runtime-recycle-every", type=int, default=50)
    parser.add_argument("--import-timeout-s", type=int, default=180)
    parser.set_defaults(
        vikingboat_compat=False,
        local_session_summaries=False,
        local_atoms=False,
        local_messages=False,
        local_timeline_hints=False,
        local_memory_artifacts=False,
        vikingboat_tool_loop=False,
        toolloop_rescue_on_toollike_answer=False,
        initial_tool_prefetch=False,
        fallback_to_one_shot=True,
        answer_refinement=False,
    )
    return parser.parse_args()


# LongMemEval in OpenViking follows an import-then-retrieve flow and does not
# hard-gate QA on every late summary artifact becoming complete. Keeping it in
# the strict-ready path both slows runs dramatically and inflates false
# memory_not_ready failures. Reserve strict gating for datasets whose agent
# workflows truly depend on full post-import stabilization before QA starts.
STRICT_READY_DATASET_FORMATS = {"evolvingevents", "proagentbench", "tau2bench"}


def main() -> None:
    args = parse_args()
    dataset_format = str(getattr(args, "dataset_format", "") or getattr(args, "format", "") or "").strip().lower()
    strict_ready_override = bool(getattr(args, "allow_fast_wait_for_strict_ready", False))
    if dataset_format in STRICT_READY_DATASET_FORMATS and not strict_ready_override:
        args.import_wait_mode = "full"
        args.defer_artifact_wait = False
    answer_base_url = str(args.answer_base_url or "").strip()
    answer_model = str(args.answer_model or "").strip()
    answer_token = str(args.answer_token or "").strip()
    judge_token = str(args.judge_token or "").strip()
    if answer_base_url and not os.environ.get("ECHOMEM_CHAT_BASE_URL"):
        os.environ["ECHOMEM_CHAT_BASE_URL"] = answer_base_url
    if answer_base_url and not os.environ.get("DASHSCOPE_BASE_URL"):
        os.environ["DASHSCOPE_BASE_URL"] = answer_base_url
    if answer_model and not os.environ.get("ECHOMEM_CHAT_MODEL"):
        os.environ["ECHOMEM_CHAT_MODEL"] = answer_model
    if answer_token and not os.environ.get("ECHOMEM_CHAT_API_KEY"):
        os.environ["ECHOMEM_CHAT_API_KEY"] = answer_token
    if answer_token and not os.environ.get("DASHSCOPE_API_KEY"):
        os.environ["DASHSCOPE_API_KEY"] = answer_token
    if judge_token and not os.environ.get("LOCOMO_JUDGE_TOKEN"):
        os.environ["LOCOMO_JUDGE_TOKEN"] = judge_token
    args.dataset_path = Path(args.dataset).expanduser().resolve()
    if args.dataset_format == "auto":
        data = read_dataset(args.dataset_path)
        args.dataset_format = benchmark_adapter.infer_format(args.dataset_path, data)
    args.dataset_format = str(args.dataset_format or "generic").strip().lower() or "generic"
    if args.dataset_format in STRICT_READY_DATASET_FORMATS and not strict_ready_override:
        args.import_wait_mode = "full"
        args.defer_artifact_wait = False
    args.retrieval_mode = normalize_retrieval_mode(args.retrieval_mode)
    args.tool_set = normalize_echomemory_tool_set(args.tool_set, vikingboat_compat=bool(args.vikingboat_compat))
    hotpotqa_disable_answer_tooling(args)
    if args.dataset_format == "hotpotqa" and args.hotpotqa_corpus_mode == "global_sentence_corpus":
        if int(args.top_k or 0) == int(VIKINGBOT_INITIAL_SEARCH_LIMIT):
            args.top_k = 20
        if not args.import_only:
            args.official_eval_after = True
        args.identity_mode = "fixed"
    if not args.namespace:
        args.namespace = f"{args.dataset_format}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    if args.random_count:
        random.seed(args.random_seed)
    asyncio.run(run(args))


if __name__ == "__main__":
    exit_code = 0
    try:
        main()
    except SystemExit as exc:
        code = exc.code
        if code is None:
            exit_code = 0
        elif isinstance(code, int):
            exit_code = code
        else:
            print(code, file=sys.stderr, flush=True)
            exit_code = 1
    except Exception:
        traceback.print_exc()
        exit_code = 1
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            os._exit(int(exit_code))

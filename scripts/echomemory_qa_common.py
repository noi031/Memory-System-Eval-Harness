from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request

from echomemory_common import write_json
from memory.vikingboat_alignment import VIKINGBOT_TOOL_SET
from openviking_memory_qa import (
    ModelCallError,
    call_openai,
    classify_model_error,
    csv_fieldnames,
    openai_payload_variants,
    openai_response_message,
    parse_openai_compatible_response,
)

ROOT = Path(__file__).resolve().parents[1]

MEMORY_SEARCH_TOOL_NAME = "memory_search"
MEMORY_MULTI_READ_TOOL_NAME = "memory_read_many"
MEMORY_LIST_TOOL_NAME = "memory_list"
MEMORY_GREP_TOOL_NAME = "memory_grep"
MEMORY_GLOB_TOOL_NAME = "memory_glob"
ECHOMEMORY_BACKEND_ROUTE = "custom_agent_echomemory_sdk_memory_tools"
ECHOMEMORY_VIKINGBOAT_TOOL_SET = "vikingboat_default"
LONGMEMEVAL_ABSTAIN_TEXT = "The information provided is not enough."


def load_official_longmemeval_prompt_builder() -> Any | None:
    candidates = [
        Path.home() / "Code" / "openviking" / "versions" / "v0.4.4" / "benchmark" / "longmemeval" / "openviking" / "longmemeval_prompts.py",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location("openviking_longmemeval_prompts_for_echomemory", path)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            helper = getattr(module, "get_answer_generation_prompt", None)
            if callable(helper):
                return helper
        except Exception:
            continue
    return None


OFFICIAL_LONGMEMEVAL_PROMPT_BUILDER = load_official_longmemeval_prompt_builder()


def normalize_echomemory_tool_set(value: Any, *, vikingboat_compat: bool = False) -> str:
    raw = str(value or "").strip() or "search_read"
    if raw == VIKINGBOT_TOOL_SET:
        return ECHOMEMORY_VIKINGBOAT_TOOL_SET
    if vikingboat_compat and raw == "search_read":
        return ECHOMEMORY_VIKINGBOAT_TOOL_SET
    return raw


def normalize_retrieval_mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw == "local":
        return "local"
    return "search"


def token_usage_json(prompt_tokens: Any, completion_tokens: Any, total_tokens: Any) -> str:
    try:
        prompt = int(prompt_tokens or 0)
    except Exception:
        prompt = 0
    try:
        completion = int(completion_tokens or 0)
    except Exception:
        completion = 0
    try:
        total = int(total_tokens or (prompt + completion))
    except Exception:
        total = prompt + completion
    return json.dumps(
        {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        },
        ensure_ascii=False,
    )


def ms_since(started: float) -> float:
    return round((time.time() - started) * 1000, 1)


def timed_call_openai(
    base_url: str,
    model: str,
    token: str,
    messages: list[dict[str, str]],
    timeout: int,
    max_retries: int = 5,
) -> tuple[dict[str, Any], float]:
    started = time.time()
    result = call_openai(base_url, model, token, messages, timeout, max_retries)
    return result, ms_since(started)


def compact(text: Any, limit: int = 1400) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def call_openai_without_signal(
    base_url: str,
    model: str,
    token: str,
    messages: list[dict[str, str]],
    timeout: int,
    max_retries: int = 5,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    last_error = ""
    last_kind = "api_error"
    data: dict[str, Any] | None = None
    retry_count = 0
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    attempts = max(1, max_retries + 1)
    payload_variants = openai_payload_variants(model, messages, max_tokens)
    for attempt in range(attempts):
        payload = payload_variants[attempt % len(payload_variants)]
        try:
            req = request.Request(
                base_url.rstrip("/") + "/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                method="POST",
            )
            with request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            candidate = parse_openai_compatible_response(body)
            openai_response_message(candidate)
            usage = candidate.get("usage") or {}
            prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
            total_usage["prompt_tokens"] += prompt_tokens
            total_usage["completion_tokens"] += completion_tokens
            total_usage["total_tokens"] += total_tokens
            data = candidate
            retry_count = attempt
            break
        except TimeoutError as exc:
            last_error = str(exc)
            last_kind = "timeout"
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {exc.code}: {body[:1000]}"
            last_kind = classify_model_error(last_error)
        except Exception as exc:
            last_error = str(exc)
            last_kind = classify_model_error(last_error)
        if attempt < attempts - 1:
            sleep_s = min(30, 2 ** attempt)
            if last_kind == "rate_limited":
                sleep_s = min(45, 5 * (attempt + 1))
            variant = (attempt % len(payload_variants)) + 1
            print(
                f"[model] retry={attempt + 1}/{max_retries} variant={variant} "
                f"kind={last_kind} error={compact(last_error, 220)}",
                flush=True,
            )
            time.sleep(sleep_s)
    if data is None:
        raise ModelCallError(
            last_error or "model call failed",
            max_retries,
            last_kind,
            prompt_tokens=total_usage["prompt_tokens"],
            completion_tokens=total_usage["completion_tokens"],
            total_tokens=total_usage["total_tokens"],
        )
    msg = openai_response_message(data).get("content") or ""
    return {
        "answer": msg.strip(),
        "prompt_tokens": total_usage["prompt_tokens"],
        "completion_tokens": total_usage["completion_tokens"],
        "total_tokens": total_usage["total_tokens"],
        "model_retry_count": retry_count,
        "model_error_kind": "",
    }


async def timed_call_openai_async(
    base_url: str,
    model: str,
    token: str,
    messages: list[dict[str, str]],
    timeout: int,
    max_retries: int = 5,
) -> tuple[dict[str, Any], float]:
    started = time.time()
    result = await asyncio.to_thread(
        call_openai_without_signal,
        base_url,
        model,
        token,
        messages,
        timeout,
        max_retries,
    )
    return result, ms_since(started)


def default_retrieval_timing() -> dict[str, Any]:
    return {
        "primary_search_ms": 0.0,
        "adaptive_followup_search_ms": 0.0,
        "followup_search_ms": 0.0,
        "current_session_raw_fallback_ms": 0.0,
        "current_session_raw_fallback_hits_added": 0,
        "current_session_raw_fallback_triggered": False,
        "overview_enrichment_ms": 0.0,
        "overview_enrichment_hits_added": 0,
        "overview_enrichment_triggered": False,
        "longmemeval_current_session_summary_fallback_hits_added": 0,
        "longmemeval_current_session_summary_fallback_triggered": False,
        "hotpot_empty_overview_fallback_hits_added": 0,
        "hotpot_empty_overview_fallback_triggered": False,
        "segment_readback_ms": 0.0,
        "segment_readback_hits_added": 0,
        "segment_readback_triggered": False,
        "precision_session_readback_ms": 0.0,
        "precision_session_readback_hits_added": 0,
        "precision_session_readback_triggered": False,
        "precision_grounded_projection_ms": 0.0,
        "precision_grounded_projection_hits_added": 0,
        "precision_grounded_projection_triggered": False,
        "local_evidence_ms": 0.0,
        "dedup_ms": 0.0,
        "rank_ms": 0.0,
        "postprocess_ms": 0.0,
        "total_ms": 0.0,
        "primary_search_queries": 0,
        "adaptive_followup_search_queries": 0,
        "followup_search_queries": 0,
        "allow_local_evidence": False,
    }


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl_file(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except FileNotFoundError:
        return []
    return rows


def write_rows_csv(csv_path: Path, rows: list[dict[str, str] | None]) -> None:
    materialized = [row for row in rows if row]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fieldnames(materialized), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(materialized)


def read_rows_csv(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        shutil.copy2(src, dst)


def merge_materialized_rows(
    current_rows: list[dict[str, str] | None],
    updated_rows: list[dict[str, str]],
) -> None:
    materialized_index = 0
    for index, row in enumerate(current_rows):
        if row is None:
            continue
        if materialized_index >= len(updated_rows):
            break
        current_rows[index] = updated_rows[materialized_index]
        materialized_index += 1


def float_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def checkpoint_csv_path(out_dir: Path, answered_count: int, total_count: int) -> Path:
    if total_count > 0 and answered_count >= total_count:
        return out_dir / "judge_snapshot_latest.csv"
    return out_dir / f"judge_snapshot_{answered_count:03d}.csv"


def checkpoint_summary_path(out_dir: Path, answered_count: int, total_count: int) -> Path:
    if total_count > 0 and answered_count >= total_count:
        return out_dir / "judge_snapshot_latest_summary.json"
    return out_dir / f"judge_snapshot_{answered_count:03d}_summary.json"


def load_snapshot_index(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = read_json(path)
    except Exception:
        return []
    if isinstance(data, dict):
        items = data.get("snapshots")
        return items if isinstance(items, list) else []
    return data if isinstance(data, list) else []


def write_snapshot_index(path: Path, snapshots: list[dict[str, Any]]) -> None:
    write_json(path, {"snapshots": snapshots, "count": len(snapshots)})


def judge_runtime_settings(args: argparse.Namespace) -> dict[str, Any]:
    base_url = str(getattr(args, "judge_base_url", "") or getattr(args, "answer_base_url", "") or "")
    model = str(getattr(args, "judge_model", "") or getattr(args, "answer_model", "") or "")
    token = str(
        getattr(args, "judge_token", "")
        or getattr(args, "answer_token", "")
        or os.environ.get("LOCOMO_JUDGE_TOKEN")
        or os.environ.get("JUDGE_TOKEN")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )
    parallel = max(1, int(getattr(args, "judge_parallel", 6) or 6))
    enabled = bool(base_url and model and token and int(getattr(args, "judge_every", 0) or 0) > 0)
    reason = ""
    if int(getattr(args, "judge_every", 0) or 0) <= 0:
        reason = "judge_every_disabled"
    elif not base_url:
        reason = "missing_judge_base_url"
    elif not model:
        reason = "missing_judge_model"
    elif not token:
        reason = "missing_judge_token"
    return {
        "enabled": enabled,
        "base_url": base_url,
        "model": model,
        "token": token,
        "parallel": parallel,
        "reason": reason,
    }


def run_incremental_judge(
    args: argparse.Namespace,
    csv_path: Path,
    out_dir: Path,
    answered_count: int,
    total_count: int,
) -> dict[str, Any]:
    settings = judge_runtime_settings(args)
    snapshot_index_path = out_dir / "judge_snapshot_index.json"
    latest_snapshot_path = out_dir / "judge_snapshot_latest.csv"
    latest_summary_path = out_dir / "judge_snapshot_latest_summary.json"
    if not settings["enabled"]:
        return {
            "enabled": False,
            "reason": settings["reason"],
            "answered_count": answered_count,
            "snapshot_index_path": str(snapshot_index_path),
            "latest_snapshot_path": str(latest_snapshot_path),
            "latest_summary_path": str(latest_summary_path),
        }

    env = os.environ.copy()
    env["LOCOMO_JUDGE_TOKEN"] = settings["token"]
    cmd = [
        sys.executable,
        str(ROOT / "benchmark" / "locomo" / "echomemory" / "judge.py"),
        "--input",
        str(csv_path),
        "--base-url",
        settings["base_url"],
        "--model",
        settings["model"],
        "--parallel",
        str(settings["parallel"]),
        "--timeout-s",
        str(getattr(args, "judge_timeout_s", getattr(args, "timeout_s", 120)) or 120),
        "--retries",
        str(getattr(args, "judge_retries", getattr(args, "model_retries", 5)) or 5),
        "--only-pending",
    ]
    print(
        f"[judge-checkpoint] start answered={answered_count}/{total_count or '-'} model={settings['model']} base_url={settings['base_url'] or '-'}",
        flush=True,
    )
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line.rstrip("\n"), flush=True)
    rc = proc.wait()

    summary_path = csv_path.parent / "judge_summary.json"
    summary = read_json(summary_path) if summary_path.exists() else {}
    snapshot_path = checkpoint_csv_path(out_dir, answered_count, total_count)
    snapshot_summary_path = checkpoint_summary_path(out_dir, answered_count, total_count)
    if answered_count >= total_count > 0:
        copy_if_exists(csv_path, out_dir / f"judge_snapshot_{answered_count:03d}.csv")
        copy_if_exists(summary_path, out_dir / f"judge_snapshot_{answered_count:03d}_summary.json")
    copy_if_exists(csv_path, snapshot_path)
    copy_if_exists(summary_path, snapshot_summary_path)
    copy_if_exists(csv_path, latest_snapshot_path)
    copy_if_exists(summary_path, latest_summary_path)

    snapshots = load_snapshot_index(snapshot_index_path)
    snapshot_record = {
        "answered_count": answered_count,
        "total_count": total_count,
        "returncode": rc,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "csv_path": str(snapshot_path),
        "summary_path": str(snapshot_summary_path),
        "latest_csv_path": str(latest_snapshot_path),
        "latest_summary_path": str(latest_summary_path),
        "accuracy": summary.get("accuracy"),
        "graded": summary.get("graded"),
        "correct": summary.get("correct"),
        "wrong": summary.get("wrong"),
        "selected": summary.get("selected"),
        "judge_model": settings["model"],
    }
    replaced = False
    for index, item in enumerate(snapshots):
        if int(item.get("answered_count") or 0) == answered_count:
            snapshots[index] = snapshot_record
            replaced = True
            break
    if not replaced:
        snapshots.append(snapshot_record)
    snapshots.sort(key=lambda item: int(item.get("answered_count") or 0))
    write_snapshot_index(snapshot_index_path, snapshots)
    print(
        f"[judge-checkpoint] done answered={answered_count}/{total_count or '-'} rc={rc} accuracy={summary.get('accuracy')}",
        flush=True,
    )
    return {
        "enabled": True,
        "returncode": rc,
        "answered_count": answered_count,
        "summary": summary,
        "summary_path": str(summary_path),
        "snapshot_path": str(snapshot_path),
        "snapshot_summary_path": str(snapshot_summary_path),
        "snapshot_index_path": str(snapshot_index_path),
        "latest_snapshot_path": str(latest_snapshot_path),
        "latest_summary_path": str(latest_summary_path),
        "checkpoint_count": len(snapshots),
    }


def int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def tool_search_result_count(tools_used: list[dict[str, Any]]) -> int:
    total = 0
    for item in tools_used:
        if item.get("tool_name") != MEMORY_SEARCH_TOOL_NAME:
            continue
        total += int_or_zero(item.get("result_count"))
    return total


def tool_read_call_count(tools_used: list[dict[str, Any]]) -> int:
    return sum(1 for item in tools_used if item.get("tool_name") == MEMORY_MULTI_READ_TOOL_NAME)


def recall_tool_result_summary(tools_used: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(tools_used, 1):
        name = str(item.get("tool_name") or "")
        if name not in {MEMORY_SEARCH_TOOL_NAME, MEMORY_MULTI_READ_TOOL_NAME}:
            continue
        rows.append(
            {
                "index": index,
                "tool_name": name,
                "result_count": int_or_zero(item.get("result_count")),
                "args": item.get("args") or {},
                "result_preview": compact(item.get("result") or "", 1000),
            }
        )
    return rows

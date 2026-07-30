"""LoCoMo dataset parsing and conversation normalization."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from shared.dataset_io import BenchmarkQuestion, read_dataset


def _compact(value: Any, limit: int = 320) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _token_estimate(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in (
        "%I:%M %p on %d %B, %Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(value.strip().upper(), fmt)
        except ValueError:
            continue
    if " on " in value:
        try:
            return datetime.strptime(value.split(" on ", 1)[1].strip(), "%d %B, %Y")
        except ValueError:
            pass
    return None


def _session_keys(conversation: dict[str, Any]) -> list[str]:
    keys = [
        str(key)
        for key, value in conversation.items()
        if re.fullmatch(r"session_\d+", str(key)) and isinstance(value, list)
    ]
    return sorted(keys, key=lambda key: int(key.split("_", 1)[1]))


def _sample_question_time(sample: dict[str, Any]) -> str:
    conversation = sample.get("conversation") or {}
    for key in reversed(_session_keys(conversation)):
        if not conversation.get(key):
            continue
        parsed = _parse_datetime(str(conversation.get(f"{key}_date_time") or ""))
        if parsed:
            return parsed.strftime("%Y-%m-%d")
    return ""


def _memory_users(sample: dict[str, Any]) -> list[str]:
    conversation = sample.get("conversation") or {}
    users: list[str] = []
    for key in ("speaker_a", "speaker_b"):
        value = str(conversation.get(key) or "").strip()
        if value and value not in users:
            users.append(value)
    return users


def conversation_events(sample: dict[str, Any]) -> list[dict[str, str]]:
    conversation = sample.get("conversation") or {}
    events: list[dict[str, str]] = []
    for key in _session_keys(conversation):
        session_time = _compact(conversation.get(f"{key}_date_time"), 80)
        for message in conversation.get(key) or []:
            if not isinstance(message, dict):
                continue
            speaker = str(message.get("speaker") or message.get("role") or "")
            dia_id = str(message.get("dia_id") or key)
            parts: list[str] = []
            if message.get("text"):
                parts.append(str(message["text"]))
            if message.get("blip_caption"):
                parts.append(f"image: {message['blip_caption']}")
            if message.get("query"):
                parts.append(f"query: {message['query']}")
            if parts:
                events.append({
                    "time": session_time,
                    "text": _compact(f"{speaker} {dia_id}: {' '.join(parts)}", 900),
                })
    return events


def session_batches(sample: dict[str, Any]) -> list[dict[str, Any]]:
    conversation = sample.get("conversation") or {}
    batches: list[dict[str, Any]] = []
    for key in _session_keys(conversation):
        date_time = str(conversation.get(f"{key}_date_time") or "")
        base_time = _parse_datetime(date_time)
        messages: list[dict[str, Any]] = []
        for index, raw in enumerate(conversation.get(key) or []):
            if not isinstance(raw, dict):
                continue
            speaker = str(raw.get("speaker") or raw.get("role") or "speaker")
            parts: list[str] = []
            if raw.get("text"):
                parts.append(str(raw["text"]))
            if raw.get("blip_caption"):
                parts.append(
                    f"(attached image; image description: {raw['blip_caption']})"
                )
            if raw.get("query"):
                parts.append(
                    f"(attached image; image search/query text: {raw['query']})"
                )
            if not parts:
                continue
            message = {
                "role": (
                    "assistant"
                    if speaker.lower() in {"assistant", "agent"}
                    else "user"
                ),
                "content": "\n".join(parts),
                "role_id": speaker,
                "speaker": speaker,
                "dia_id": str(raw.get("dia_id") or f"{key}:{index}"),
            }
            if base_time:
                message["created_at"] = (
                    base_time.replace(second=0, microsecond=0)
                    + timedelta(seconds=index)
                ).isoformat()
            messages.append(message)
        if messages:
            batches.append({
                "session_key": key,
                "date_time": date_time,
                "messages": messages,
            })
    return batches


def load_dataset(
    path: str | Path,
    sample_filter: str = "all",
) -> tuple[list[BenchmarkQuestion], list[dict[str, Any]]]:
    raw = read_dataset(path)
    samples = raw if isinstance(raw, list) else [raw]
    jobs: list[BenchmarkQuestion] = []
    plans: list[dict[str, Any]] = []
    for sample_index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            continue
        sample_id = str(sample.get("sample_id") or f"sample_{sample_index}")
        if sample_filter not in ("", "all", str(sample_index), sample_id):
            continue
        events = conversation_events(sample)
        question_time = _sample_question_time(sample)
        users = _memory_users(sample)
        plans.append({
            "sample_id": sample_id,
            "event_count": len(events),
            "events": events,
            "preview_events": events[:20],
            "memory_users": users,
            "question_time": question_time,
            "session_batches": session_batches(sample),
        })
        context = "\n".join(event["text"] for event in events[:12])
        for question_index, qa in enumerate(sample.get("qa") or []):
            if not isinstance(qa, dict) or str(qa.get("category") or "") == "5":
                continue
            jobs.append(BenchmarkQuestion(
                dataset_format="locomo",
                sample_id=sample_id,
                question_id=f"{sample_id}_qa{question_index}",
                question=str(qa.get("question") or ""),
                answer=str(qa.get("answer") or ""),
                category=str(qa.get("category") or ""),
                query_time=str(qa.get("question_time") or question_time),
                injection_events=len(events),
                injection_tokens_est=_token_estimate(context),
                context_preview=_compact(context),
                original_sample_id=sample_id,
                question_index=str(question_index),
                memory_users=json.dumps(users, ensure_ascii=False),
                native_question_id=f"sample_{sample_index}_qa{question_index}",
            ))
    return jobs, plans

"""LongMemEval dataset parsing and session normalization."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from shared.dataset_io import BenchmarkQuestion, list_payload, read_dataset


def _compact(value: Any, limit: int = 320) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _token_estimate(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    iso_value = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        return datetime.fromisoformat(iso_value)
    except ValueError:
        pass
    for fmt in (
        "%Y/%m/%d (%a) %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _sessions(item: dict[str, Any]):
    sessions = (
        item.get("haystack_sessions")
        or item.get("sessions")
        or item.get("conversation")
        or []
    )
    return sessions if isinstance(sessions, list) else []


def _messages(session: Any) -> list[Any]:
    if isinstance(session, dict):
        rows = (
            session.get("messages")
            or session.get("conversation")
            or session.get("turns")
            or []
        )
    else:
        rows = session
    return rows if isinstance(rows, list) else [rows]


def session_batches(item: dict[str, Any]) -> list[dict[str, Any]]:
    dates = item.get("haystack_dates") or []
    session_ids = item.get("haystack_session_ids") or []
    batches: list[dict[str, Any]] = []
    for session_index, session in enumerate(_sessions(item)):
        session_time = _compact(
            dates[session_index] if session_index < len(dates) else "",
            80,
        )
        session_id = _compact(
            session_ids[session_index]
            if session_index < len(session_ids)
            else f"session_{session_index}",
            120,
        )
        base_time = parse_datetime(session_time)
        rows: list[dict[str, Any]] = []
        for message_index, message in enumerate(_messages(session)):
            if isinstance(message, dict):
                role = str(
                    message.get("role")
                    or message.get("speaker")
                    or message.get("user")
                    or "user"
                ).strip() or "user"
                content = str(
                    message.get("content")
                    or message.get("text")
                    or message.get("message")
                    or ""
                ).strip()
            else:
                role = "user"
                content = str(message).strip()
            if not content:
                continue
            row = {
                "role": role,
                "content": content,
                "parts": [{"type": "text", "text": content}],
                "speaker": role,
                "dia_id": f"{session_id}:{message_index}",
                "created_at": None,
            }
            if base_time:
                row["created_at"] = (
                    base_time.replace(second=0, microsecond=0)
                    + timedelta(seconds=len(rows))
                ).isoformat()
            rows.append(row)
        if rows:
            batches.append({
                "session_key": session_id or f"session_{session_index}",
                "date_time": session_time,
                "messages": rows,
            })
    return batches


def _events(batches: list[dict[str, Any]]) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for batch in batches:
        for index, message in enumerate(batch["messages"]):
            events.append({
                "time": str(batch.get("date_time") or ""),
                "text": _compact(
                    f"{batch['session_key']} turn_{index} "
                    f"{message['role']}: {message['content']}",
                    1400,
                ),
            })
    return events


def _documents(batches: list[dict[str, Any]]) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    for batch in batches:
        lines = [
            "source_dataset: LongMemEval",
            f"session_id: {batch['session_key']}",
            f"time: {batch.get('date_time') or '-'}",
            "",
            "Conversation turns:",
        ]
        for index, message in enumerate(batch["messages"]):
            lines.append(
                f"turn_{index} {message['role']}: "
                f"{_compact(message['content'], 2200)}"
            )
        documents.append({
            "doc_id": batch["session_key"],
            "title": batch["session_key"],
            "time": str(batch.get("date_time") or ""),
            "text": "\n".join(lines),
        })
    return documents


def load_dataset(
    path: str | Path,
    sample_filter: str = "all",
) -> tuple[list[BenchmarkQuestion], list[dict[str, Any]]]:
    jobs: list[BenchmarkQuestion] = []
    plans: list[dict[str, Any]] = []
    for index, raw in enumerate(list_payload(read_dataset(path))):
        item = raw if isinstance(raw, dict) else {"input": raw}
        sample_id = str(
            item.get("question_id")
            or item.get("sample_id")
            or item.get("id")
            or f"longmemeval_{index}"
        )
        if sample_filter not in ("", "all", str(index), sample_id):
            continue
        batches = session_batches(item)
        events = _events(batches)
        raw_query_time = str(
            item.get("question_date")
            or item.get("query_time")
            or item.get("question_time")
            or ""
        )
        parsed_query_time = parse_datetime(raw_query_time)
        query_time = (
            parsed_query_time.strftime("%Y-%m-%d")
            if parsed_query_time
            else raw_query_time
        )
        context = "\n".join(event["text"] for event in events[:12])
        plans.append({
            "sample_id": sample_id,
            "event_count": len(events),
            "events": events,
            "preview_events": events[:20],
            "memory_documents": _documents(batches),
            "session_batches": batches,
        })
        jobs.append(BenchmarkQuestion(
            dataset_format="longmemeval",
            sample_id=sample_id,
            question_id=sample_id,
            question=str(item.get("question") or item.get("query") or ""),
            answer=str(
                item.get("answer")
                or item.get("gold_answer")
                or item.get("target")
                or ""
            ),
            category=str(
                item.get("question_type")
                or item.get("category")
                or "longmemeval"
            ),
            query_time=query_time,
            injection_events=len(events),
            injection_tokens_est=_token_estimate(context),
            context_preview=_compact(context),
        ))
    return jobs, plans

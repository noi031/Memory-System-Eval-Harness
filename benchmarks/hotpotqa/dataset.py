"""HotpotQA dataset parsing and context normalization."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from shared.dataset_io import BenchmarkQuestion, list_payload, read_dataset


ID_KEYS = ("_id", "id", "uid", "uuid", "sample_id", "question_id", "qid")
TIME_KEYS = (
    "query_time",
    "question_time",
    "question_date",
    "time",
    "timestamp",
    "date",
    "datetime",
)


def _compact(value: Any, limit: int = 320) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _pick(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        if lowered.get(key) not in (None, ""):
            return lowered[key]
    return ""


def _context_pairs(context: Any) -> list[tuple[str, list[str]]]:
    if isinstance(context, dict):
        titles = context.get("title") or context.get("titles") or []
        sentences = context.get("sentences") or context.get("sentence") or []
        return [
            (
                str(title),
                [
                    str(sentence)
                    for sentence in (
                        sentences[index] if index < len(sentences) else []
                    )
                ],
            )
            for index, title in enumerate(titles)
        ]
    pairs: list[tuple[str, list[str]]] = []
    for item in context if isinstance(context, list) else []:
        if isinstance(item, dict):
            title = str(
                item.get("title")
                or item.get("name")
                or item.get("document")
                or ""
            )
            raw_sentences = (
                item.get("sentences")
                or item.get("sentence")
                or item.get("text")
                or item.get("content")
                or []
            )
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            title, raw_sentences = str(item[0]), item[1]
        else:
            continue
        sentences = (
            [raw_sentences]
            if isinstance(raw_sentences, str)
            else [str(sentence) for sentence in raw_sentences]
            if isinstance(raw_sentences, list)
            else []
        )
        pairs.append((title, sentences))
    return pairs


def context_events(item: dict[str, Any]) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for index, (title, sentences) in enumerate(
        _context_pairs(item.get("context")),
        1,
    ):
        body = " ".join(
            _compact(sentence, 900)
            for sentence in sentences
            if str(sentence).strip()
        )
        if title or body:
            events.append({
                "time": "",
                "text": _compact(
                    f"document_{index} {title}: {body}",
                    1600,
                ),
            })
    return events


def context_documents(item: dict[str, Any]) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    for index, (title, sentences) in enumerate(
        _context_pairs(item.get("context")),
        1,
    ):
        body = " ".join(
            _compact(sentence, 1400)
            for sentence in sentences
            if str(sentence).strip()
        )
        if not title and not body:
            continue
        document_title = title or f"document_{index}"
        documents.append({
            "doc_id": f"document_{index}_{document_title}",
            "title": document_title,
            "time": "",
            "text": "\n".join([
                "source_dataset: HotpotQA",
                f"title: {document_title}",
                "",
                body,
            ]).strip(),
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
        sample_id = str(_pick(item, ID_KEYS) or f"hotpotqa_{index}")
        if sample_filter not in ("", "all", str(index), sample_id):
            continue
        events = context_events(item)
        documents = context_documents(item)
        context = "\n".join(event["text"] for event in events[:12])
        category = str(item.get("type") or item.get("category") or "hotpotqa")
        if item.get("level"):
            category = f"{category}/{item['level']}"
        plans.append({
            "sample_id": sample_id,
            "event_count": len(events),
            "events": events,
            "preview_events": events[:20],
            "memory_documents": documents,
            "supporting_facts": item.get("supporting_facts") or [],
            "type": str(item.get("type") or item.get("category") or "hotpotqa"),
            "level": str(item.get("level") or "").strip(),
            "has_answer": bool(
                str(item.get("answer") or item.get("gold_answer") or "").strip()
            ),
        })
        jobs.append(BenchmarkQuestion(
            dataset_format="hotpotqa",
            sample_id=sample_id,
            question_id=sample_id,
            question=str(item.get("question") or item.get("query") or ""),
            answer=str(item.get("answer") or item.get("gold_answer") or ""),
            category=category,
            query_time=str(_pick(item, TIME_KEYS) or ""),
            injection_events=len(events),
            injection_tokens_est=max(1, (len(context) + 3) // 4) if context else 0,
            context_preview=_compact(context),
            native_question_id=sample_id,
        ))
    return jobs, plans

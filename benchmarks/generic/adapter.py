#!/usr/bin/env python3
"""Emit backend-neutral dry-run plans for supported memory datasets.

Dataset-specific normalization lives under ``benchmarks/<dataset>/dataset.py``.
This script owns only format detection, generic fallback parsing, and artifact
serialization for the legacy dry-run command.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.hotpotqa.dataset import load_dataset as load_hotpotqa
from benchmarks.locomo.dataset import load_dataset as load_locomo
from benchmarks.longmemeval.dataset import load_dataset as load_longmemeval
from shared.dataset_io import BenchmarkQuestion, list_payload, read_dataset


QUESTION_KEYS = ("question", "query", "input", "prompt", "question_text")
ANSWER_KEYS = ("answer", "gold_answer", "target", "output", "reference", "label")
TIME_KEYS = (
    "query_time",
    "question_time",
    "question_date",
    "time",
    "timestamp",
    "date",
    "datetime",
)
ID_KEYS = ("_id", "id", "uid", "uuid", "sample_id", "question_id", "qid")
EVENT_KEYS = (
    "events",
    "event",
    "memories",
    "memory",
    "messages",
    "conversation",
    "history",
    "sessions",
    "context",
    "contexts",
    "passages",
    "documents",
    "tools",
    "tool",
)
EVENT_TEXT_KEYS = (
    "time",
    "timestamp",
    "date",
    "role",
    "speaker",
    "user",
    "title",
    "name",
    "description",
    "task",
    "content",
    "text",
    "message",
    "event",
    "sentence",
    "sentences",
    "paragraph",
    "paragraphs",
)


def compact(value: Any, limit: int = 320) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def pick(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        if lowered.get(key) not in (None, ""):
            return lowered[key]
    for key, value in lowered.items():
        if any(marker in key for marker in keys) and value not in (None, ""):
            return value
    return ""


def event_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return compact(json.dumps(value, ensure_ascii=False, sort_keys=True), 600)
    parts: list[str] = []
    for key in EVENT_TEXT_KEYS:
        if value.get(key) in (None, ""):
            continue
        item = value[key]
        if isinstance(item, list):
            item = " ".join(compact(part, 500) for part in item)
        elif isinstance(item, dict):
            item = compact(
                json.dumps(item, ensure_ascii=False, sort_keys=True),
                900,
            )
        parts.append(f"{key}: {item}")
    return " | ".join(parts)


def collect_events(value: Any) -> list[dict[str, str]]:
    if isinstance(value, str):
        return [{"time": "", "text": value}]
    if isinstance(value, list):
        return [
            event
            for item in value
            for event in collect_events(item)
        ]
    if not isinstance(value, dict):
        return []

    events: list[dict[str, str]] = []
    direct_text = event_text(value)
    lowered_keys = {str(key).lower() for key in value}
    if direct_text and any(key in lowered_keys for key in EVENT_TEXT_KEYS):
        events.append({
            "time": compact(pick(value, TIME_KEYS), 80),
            "text": direct_text,
        })
    for key, child in value.items():
        lowered = str(key).lower()
        if lowered in EVENT_KEYS or any(marker in lowered for marker in EVENT_KEYS):
            events.extend(collect_events(child))
    return events


def evolving_events(item: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    raw_events = item.get("events") or item.get("timeline") or []
    for raw in raw_events if isinstance(raw_events, list) else []:
        if isinstance(raw, dict):
            event_time = str(
                raw.get("timestamp")
                or raw.get("time")
                or raw.get("date")
                or ""
            ).strip()
            text = str(
                raw.get("event")
                or raw.get("text")
                or raw.get("description")
                or raw.get("content")
                or ""
            ).strip()
        else:
            event_time, text = "", str(raw).strip()
        if text:
            rows.append({"time": event_time, "text": text})
    return rows


def chenmo_items(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    turns = [
        {"role": role, "content": content}
        for role, content in re.findall(
            r'\("(user|assistant)"\s*,\s*"(.*?)"\)',
            text,
            re.S,
        )
    ]
    events = [
        {
            "time": "",
            "text": f"{turn['role']}: {turn['content']}",
        }
        for turn in turns
        if turn["content"]
    ]
    category = "ChenMo"
    items: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        section = re.match(r"^###\s+(.+?)\s*$", line)
        if section:
            category = section.group(1).strip()
            continue
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3 or not re.match(r"^[A-Z]+\d+$", cells[0]):
            continue
        items.append({
            "sample_id": "chenmo",
            "question_id": cells[0],
            "question": cells[1],
            "answer": cells[2],
            "category": category,
            "events": events,
        })
    return items


def infer_format(path: Path, data: Any) -> str:
    name = path.name.lower()
    if path.suffix.lower() in {".md", ".markdown"} or "chenmo" in name:
        return "chenmo"
    rows = list_payload(data)
    if rows and isinstance(rows[0], dict):
        if "qa" in rows[0] and "conversation" in rows[0]:
            return "locomo"
        if "haystack_sessions" in rows[0]:
            return "longmemeval"
        if "supporting_facts" in rows[0] and "context" in rows[0]:
            return "hotpotqa"
    for marker, value in (
        ("hotpot", "hotpotqa"),
        ("longmem", "longmemeval"),
        ("proagent", "proagentbench"),
        ("tau2", "tau2bench"),
        ("evolving", "evolvingevents"),
    ):
        if marker in name:
            return value
    return "generic"


def generic_jobs(
    dataset_format: str,
    data: Any,
    limit: int | None,
) -> tuple[list[BenchmarkQuestion], list[dict[str, Any]]]:
    jobs: list[BenchmarkQuestion] = []
    plans: list[dict[str, Any]] = []
    for index, raw in enumerate(list_payload(data)):
        item = raw if isinstance(raw, dict) else {"input": raw}
        sample_id = str(pick(item, ID_KEYS) or f"{dataset_format}_{index}")
        events = (
            evolving_events(item)
            if dataset_format == "evolvingevents"
            else collect_events(item)
        )
        if not events:
            for key, value in item.items():
                if str(key).lower() not in QUESTION_KEYS + ANSWER_KEYS:
                    events.extend(collect_events(value))
        context = "\n".join(event["text"] for event in events[:12])
        plans.append({
            "sample_id": sample_id,
            "event_count": len(events),
            "events": events,
            "preview_events": events[:20],
        })
        jobs.append(BenchmarkQuestion(
            dataset_format=dataset_format,
            sample_id=sample_id,
            question_id=str(
                pick(item, ("question_id", "qid", "id"))
                or f"{sample_id}_q0"
            ),
            question=str(pick(item, QUESTION_KEYS) or ""),
            answer=str(pick(item, ANSWER_KEYS) or ""),
            category=str(
                item.get("category")
                or item.get("type")
                or dataset_format
            ),
            query_time=str(pick(item, TIME_KEYS) or ""),
            injection_events=len(events),
            injection_tokens_est=(
                max(1, (len(context) + 3) // 4) if context else 0
            ),
            context_preview=compact(context),
        ))
        if limit and len(jobs) >= limit:
            break
    return jobs, plans


def load_jobs(
    path: Path,
    dataset_format: str,
    limit: int | None,
) -> tuple[list[BenchmarkQuestion], list[dict[str, Any]]]:
    if dataset_format == "locomo":
        jobs, plans = load_locomo(path)
    elif dataset_format == "longmemeval":
        jobs, plans = load_longmemeval(path)
    elif dataset_format == "hotpotqa":
        jobs, plans = load_hotpotqa(path)
    else:
        data = (
            {"items": chenmo_items(path)}
            if dataset_format == "chenmo"
            else read_dataset(path)
        )
        jobs, plans = generic_jobs(dataset_format, data, limit)
    if limit:
        selected_ids = {job.sample_id for job in jobs[:limit]}
        jobs = jobs[:limit]
        plans = [plan for plan in plans if plan.get("sample_id") in selected_ids]
    return jobs, plans


def write_csv(path: Path, jobs: list[BenchmarkQuestion]) -> None:
    fieldnames = [field.name for field in fields(BenchmarkQuestion)]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for job in jobs:
            writer.writerow(asdict(job))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize memory datasets and emit dry-run adapter jobs."
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument(
        "--format",
        default="auto",
        choices=[
            "auto",
            "locomo",
            "longmemeval",
            "evolvingevents",
            "hotpotqa",
            "proagentbench",
            "tau2bench",
            "chenmo",
            "generic",
        ],
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--memory-mode", default="read_only_recommended")
    parser.add_argument("--namespace", default="")
    parser.add_argument("--mode", default="dry-run", choices=["dry-run"])
    parser.add_argument("--timeout-s", type=int, default=180)
    args = parser.parse_args()

    dataset_path = Path(args.dataset).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    probe = (
        {"items": chenmo_items(dataset_path)}
        if dataset_path.suffix.lower() in {".md", ".markdown"}
        else read_dataset(dataset_path)
    )
    dataset_format = (
        infer_format(dataset_path, probe)
        if args.format == "auto"
        else args.format
    )
    jobs, plans = load_jobs(
        dataset_path,
        dataset_format,
        args.count or None,
    )

    output_csv = out_dir / "benchmark_adapter_results.csv"
    write_csv(output_csv, jobs)
    namespace = (
        args.namespace
        or f"{dataset_format}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    injection_plan = {
        "mode": args.mode,
        "dataset": str(dataset_path),
        "dataset_format": dataset_format,
        "namespace": namespace,
        "memory_safety_mode": args.memory_mode,
        "jobs": len(jobs),
        "samples": len(plans),
        "total_injection_events": sum(
            int(plan.get("event_count") or 0) for plan in plans
        ),
        "pollution_guard": {
            "write_to_memory_backend": False,
            "guard_reason": (
                "dry-run planner only; use EchoMemory benchmark commands "
                "for real import and QA"
            ),
            "requires_isolated_workspace_for_real_injection": True,
            "requires_isolated_graph_or_collection_for_real_injection": True,
            "recommended_namespace": namespace,
        },
        "samples_preview": plans[:20],
    }
    plan_path = out_dir / "injection_plan.json"
    plan_path.write_text(
        json.dumps(injection_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = {
        "count": len(jobs),
        "correct": 0,
        "wrong": 0,
        "accuracy": None,
        "graded": 0,
        "status": "NEEDS_BACKEND_ADAPTER",
        "dataset_format": dataset_format,
        "output_csv": str(output_csv),
        "injection_plan": str(plan_path),
        "guard_reason": "dry-run planner only",
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

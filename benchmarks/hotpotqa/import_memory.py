"""HotpotQA global and per-question import workflows."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tqdm import tqdm

from shared.eval_base import EvalConfig


IMPORT_FIELDS = (
    "question_id",
    "session_id",
    "status",
    "messages",
    "elapsed_s",
    "error",
)


@dataclass
class ImportReport:
    rows: list[dict[str, Any]]
    question_to_session: dict[str, str]
    completed: int
    total: int
    incomplete: int


def _add_events(memory_client, session_id: str, plan: dict[str, Any]) -> int:
    count = 0
    for event in plan.get("events", []):
        text = str(event.get("text") or "")
        if not text:
            continue
        memory_client.add_message(
            session_id,
            "user",
            text,
            created_at=str(event.get("time") or ""),
        )
        count += 1
    return count


def import_hotpotqa_memory(
    jobs,
    plans,
    memory_client,
    config: EvalConfig,
    result_dir: Path,
    log,
    *,
    import_mode: str,
) -> ImportReport:
    rows: list[dict[str, Any]] = []
    question_to_session: dict[str, str] = {}
    if import_mode == "global":
        session_id = ""
        try:
            session_id = memory_client.open_session(title="hotpotqa_global")
            message_count = 0
            for plan in tqdm(plans, desc="导入 passages", unit="plan"):
                message_count += _add_events(memory_client, session_id, plan)
            archive_id = memory_client.commit_session(session_id)
            result = memory_client.poll_commit(
                session_id,
                archive_id,
                timeout_s=config.commit_timeout_s,
                poll_interval_s=config.commit_poll_interval_s,
            )
            rows.append({
                "question_id": "global",
                "session_id": session_id,
                "status": result.status,
                "messages": message_count,
                "elapsed_s": round(result.elapsed_s, 1),
                "error": result.error,
            })
            for job in jobs:
                question_to_session[job.question_id] = session_id
        except Exception as exc:
            log.error("HotpotQA global import failed: %s", exc)
            rows.append({
                "question_id": "global",
                "session_id": session_id,
                "status": "error",
                "messages": 0,
                "elapsed_s": 0,
                "error": str(exc),
            })
    else:
        for job, plan in tqdm(
            list(zip(jobs, plans)),
            desc="导入记忆",
            unit="q",
        ):
            session_id = ""
            try:
                session_id = memory_client.open_session(
                    title=f"hotpotqa_{job.question_id}"
                )
                message_count = _add_events(memory_client, session_id, plan)
                archive_id = memory_client.commit_session(session_id)
                result = memory_client.poll_commit(
                    session_id,
                    archive_id,
                    timeout_s=config.commit_timeout_s,
                    poll_interval_s=config.commit_poll_interval_s,
                )
                question_to_session[job.question_id] = session_id
                rows.append({
                    "question_id": job.question_id,
                    "session_id": session_id,
                    "status": result.status,
                    "messages": message_count,
                    "elapsed_s": round(result.elapsed_s, 1),
                    "error": result.error,
                })
            except Exception as exc:
                log.error("  导入 %s 失败: %s", job.question_id, exc)
                rows.append({
                    "question_id": job.question_id,
                    "session_id": session_id,
                    "status": "error",
                    "messages": 0,
                    "elapsed_s": 0,
                    "error": str(exc),
                })

    output_path = result_dir / "import_results.csv"
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=IMPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    formal_rows = rows
    completed = sum(1 for row in formal_rows if row["status"] == "completed")
    return ImportReport(
        rows=rows,
        question_to_session=question_to_session,
        completed=completed,
        total=len(formal_rows),
        incomplete=len(formal_rows) - completed,
    )

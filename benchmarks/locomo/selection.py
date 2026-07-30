"""LoCoMo question selection helpers."""

from __future__ import annotations


def parse_question_ids(value: str) -> list[str]:
    return list(dict.fromkeys(
        part.strip()
        for part in str(value or "").split(",")
        if part.strip()
    ))


def select_questions(
    jobs,
    *,
    question_ids: list[str] | None = None,
    limit: int = 0,
):
    selected = list(jobs)
    if question_ids:
        requested = set(question_ids)
        selected = [job for job in selected if job.question_id in requested]
        found = {job.question_id for job in selected}
        missing = [question_id for question_id in question_ids if question_id not in found]
        if missing:
            raise ValueError(
                "unknown LoCoMo question ids: " + ", ".join(missing)
            )
    if limit > 0:
        selected = selected[:limit]
    return selected

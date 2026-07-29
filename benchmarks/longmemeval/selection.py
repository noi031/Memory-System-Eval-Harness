"""LongMemEval question selection."""

from __future__ import annotations

import random


def parse_question_ids(value: str) -> list[str]:
    return list(dict.fromkeys(
        part.strip()
        for part in str(value or "").split(",")
        if part.strip()
    ))


def select_jobs_and_plans(
    jobs,
    plans,
    *,
    question_ids: list[str] | None = None,
    limit: int = 0,
    random_count: int = 0,
    random_seed: int = 30,
):
    pairs = list(zip(jobs, plans))
    if question_ids:
        requested = set(question_ids)
        pairs = [
            pair for pair in pairs
            if pair[0].question_id in requested
            or str(getattr(pair[0], "native_question_id", "") or "") in requested
            or str(pair[0].sample_id or "") in requested
        ]
        found: set[str] = set()
        for job, _plan in pairs:
            for candidate in (
                job.question_id,
                str(getattr(job, "native_question_id", "") or ""),
                str(job.sample_id or ""),
            ):
                if candidate in requested:
                    found.add(candidate)
        missing = [question_id for question_id in question_ids if question_id not in found]
        if missing:
            raise ValueError(
                "unknown LongMemEval question ids: " + ", ".join(missing)
            )
    if random_count > 0:
        generator = random.Random(random_seed)
        pairs = generator.sample(pairs, min(random_count, len(pairs)))
    if limit > 0:
        pairs = pairs[:limit]
    return (
        [job for job, _plan in pairs],
        [plan for _job, plan in pairs],
    )

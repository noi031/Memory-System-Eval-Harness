"""CSV helpers for benchmark artifacts with large JSON fields."""

from __future__ import annotations

import csv
from pathlib import Path


DEFAULT_FIELD_SIZE_LIMIT = 16 * 1024 * 1024


def read_dict_rows(
    path: str | Path,
    *,
    missing_ok: bool = False,
    field_size_limit: int = DEFAULT_FIELD_SIZE_LIMIT,
) -> list[dict[str, str]]:
    source = Path(path)
    if missing_ok and not source.is_file():
        return []
    csv.field_size_limit(max(csv.field_size_limit(), field_size_limit))
    with source.open(
        "r",
        encoding="utf-8",
        errors="replace",
        newline="",
    ) as handle:
        return list(csv.DictReader(handle))

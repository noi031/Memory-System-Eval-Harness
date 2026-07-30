"""Small dataset-agnostic text normalization helpers."""

from __future__ import annotations

import re
import string
from typing import Any


def normalize_answer(text: Any) -> str:
    value = str(text or "").lower()
    value = "".join(
        character
        for character in value
        if character not in set(string.punctuation)
    )
    value = re.sub(r"\b(a|an|the)\b", " ", value)
    return " ".join(value.split())

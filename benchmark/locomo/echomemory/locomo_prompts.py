#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.openviking_memory_qa import (  # noqa: E402
    ANSWER_GENERATION_PROMPT,
    CATEGORIES_TO_EVALUATE,
    CATEGORY_NAMES,
    JUDGE_SYSTEM_PROMPT,
    get_answer_generation_prompt,
    get_judge_prompt,
    get_judge_prompt_with_evidence,
    get_strict_judge_prompt,
    get_strict_judge_prompt_with_evidence,
    preprocess_answer,
)

__all__ = [
    "ANSWER_GENERATION_PROMPT",
    "CATEGORIES_TO_EVALUATE",
    "CATEGORY_NAMES",
    "JUDGE_SYSTEM_PROMPT",
    "get_answer_generation_prompt",
    "get_judge_prompt",
    "get_judge_prompt_with_evidence",
    "get_strict_judge_prompt",
    "get_strict_judge_prompt_with_evidence",
    "preprocess_answer",
]

#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("local_judge.py")
SPEC = importlib.util.spec_from_file_location("local_judge", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def assert_grade(text: str, expected: str | None) -> None:
    parsed = MODULE.parse_judge_json(text)
    actual = parsed[0] if parsed else None
    if actual != expected:
        raise AssertionError(f"expected {expected!r}, got {actual!r} for {text!r}")


assert_grade('{"is_correct":"CORRECT","reasoning":"match"}', "CORRECT")
assert_grade('```json\n{"is_correct":"WRONG","reasoning":"mismatch"}\n```', "WRONG")
assert_grade('prefix {"is_correct":"WRONG","reasoning":"mismatch"} suffix', "WRONG")
assert_grade('{"result":"CORRECT","reasoning":"unsupported schema"}', None)
assert_grade("INCORRECT", None)
assert_grade("CORRECT", None)

print("local_judge parser smoke passed")

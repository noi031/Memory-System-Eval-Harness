#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for candidate in (ROOT, SCRIPTS):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

from echomemory_evaluation_profiles import (
    EVALUATION_PROFILE_CUSTOM,
    EVALUATION_PROFILE_LEGACY_77,
    EVALUATION_PROFILE_TEST_BEST,
    apply_evaluation_profile,
    evaluation_profile_explicit_overrides,
    evaluation_profile_metadata,
)


def main() -> None:
    custom = argparse.Namespace(
        evaluation_profile=EVALUATION_PROFILE_CUSTOM,
        top_k=7,
    )
    assert apply_evaluation_profile(custom) == {}
    assert custom.top_k == 7

    legacy = argparse.Namespace(evaluation_profile=EVALUATION_PROFILE_LEGACY_77)
    legacy_settings = apply_evaluation_profile(legacy)
    legacy.evaluation_profile_resolved_settings = legacy_settings
    assert legacy.initial_retrieval_query_mode == "question_only"
    assert legacy.prompt_context_mode == "legacy_eval"
    assert legacy.session_context_mode == "group"
    assert legacy.current_time_mode == "question_time"
    assert legacy.omit_answer_temperature is True
    assert legacy.retrieval_uri_dedup is False
    assert legacy.search_tool_target_uri_schema is True
    assert evaluation_profile_metadata(legacy)[
        "evaluation_profile_historical_result"
    ] == "77.78% (63/81)"

    explicit_tool_disable = argparse.Namespace(
        evaluation_profile=EVALUATION_PROFILE_LEGACY_77,
        vikingboat_tool_loop=False,
    )
    explicit_overrides = evaluation_profile_explicit_overrides(
        ["--no-vikingboat-tool-loop"]
    )
    explicit_settings = apply_evaluation_profile(
        explicit_tool_disable,
        explicit_overrides=explicit_overrides,
    )
    assert explicit_tool_disable.vikingboat_tool_loop is False
    assert explicit_settings["vikingboat_tool_loop"] is False

    implicit_tool_default = argparse.Namespace(
        evaluation_profile=EVALUATION_PROFILE_LEGACY_77,
        vikingboat_tool_loop=False,
    )
    implicit_settings = apply_evaluation_profile(implicit_tool_default)
    assert implicit_tool_default.vikingboat_tool_loop is True
    assert implicit_settings["vikingboat_tool_loop"] is True

    test_best = argparse.Namespace(evaluation_profile=EVALUATION_PROFILE_TEST_BEST)
    test_best_settings = apply_evaluation_profile(test_best)
    test_best.evaluation_profile_resolved_settings = test_best_settings
    assert test_best.initial_retrieval_query_mode == "vikingbot_prompt"
    assert test_best.prompt_context_mode == "vikingbot_aligned"
    assert test_best.session_context_mode == "single"
    assert test_best.current_time_mode == "runtime"
    assert test_best.answer_temperature == 0.7
    assert test_best.omit_answer_temperature is False
    assert test_best.retrieval_uri_dedup is False
    assert test_best.search_tool_target_uri_schema is False
    assert evaluation_profile_metadata(test_best)[
        "evaluation_profile_historical_result"
    ] == "85.19% (69/81)"

    unknown = argparse.Namespace(evaluation_profile="unknown")
    try:
        apply_evaluation_profile(unknown)
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown evaluation profiles must be rejected")

    print("EchoMemory evaluation profile smoke passed")


if __name__ == "__main__":
    main()

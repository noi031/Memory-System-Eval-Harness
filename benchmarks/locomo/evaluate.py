#!/usr/bin/env python3
"""Judge an existing LoCoMo QA CSV without rerunning retrieval."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from benchmarks.locomo.judge import (
    LOCOMO_JUDGE_SYSTEM,
    LOCOMO_JUDGE_TEMPLATE,
    judge_locomo_results,
)
from benchmarks.locomo.resume import (
    build_judge_resume_manifest,
    load_judge_resume_state,
    write_judge_resume_manifest,
)
from benchmarks.locomo.stats import summarize_judge_rows
from shared.csv_io import read_dict_rows
from shared.llm_client import LLMClient
from shared.qa import QAResult


HEALTHY_ARTIFACT_STATUSES = {"ok", "success", "completed", "healthy"}


def _artifact_error(
    row: dict[str, str],
    *error_fields: str,
    status_fields: tuple[str, ...],
) -> str:
    status = next(
        (
            str(row.get(field) or "").strip().lower()
            for field in status_fields
            if str(row.get(field) or "").strip()
        ),
        "",
    )
    if status in HEALTHY_ARTIFACT_STATUSES:
        return ""
    return next(
        (
            str(row.get(field) or "").strip()
            for field in error_fields
            if str(row.get(field) or "").strip()
        ),
        "",
    )


def load_qa_results(path: Path) -> list[QAResult]:
    rows = read_dict_rows(path)
    results: list[QAResult] = []
    for row in rows:
        try:
            retrieval_items = json.loads(
                str(row.get("retrieval_items_json") or "[]")
            )
        except json.JSONDecodeError:
            retrieval_items = []
        results.append(QAResult(
            question_id=str(row.get("question_id") or ""),
            question=str(row.get("question") or ""),
            answer=str(row.get("answer") or ""),
            response=str(row.get("response") or ""),
            retrieval_items=(
                retrieval_items if isinstance(retrieval_items, list) else []
            ),
            retrieval_error=_artifact_error(
                row,
                "retrieval_error",
                status_fields=("retrieval_status",),
            ),
            llm_error=_artifact_error(
                row,
                "llm_error",
                "model_error",
                status_fields=("answer_status", "model_status"),
            ),
            elapsed_s=float(row.get("elapsed_s") or 0),
            prompt_tokens=int(row.get("prompt_tokens") or 0),
            completion_tokens=int(row.get("completion_tokens") or 0),
            tool_call_count=int(row.get("tool_call_count") or 0),
            iterations=int(row.get("iterations") or 1),
            qa_profile=str(row.get("qa_profile") or "one-shot"),
        ))
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Judge an existing LoCoMo qa_results.csv"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--judge-base-url",
        default=os.getenv("JUDGE_BASE_URL") or os.getenv("LLM_BASE_URL") or "",
    )
    parser.add_argument(
        "--judge-api-key",
        default=os.getenv("JUDGE_TOKEN") or os.getenv("LLM_API_KEY") or "",
    )
    parser.add_argument(
        "--judge-model",
        default=os.getenv("JUDGE_MODEL") or os.getenv("LLM_MODEL") or "",
    )
    parser.add_argument("--judge-timeout-s", type=float, default=120.0)
    parser.add_argument("--judge-retries", type=int, default=3)
    parser.add_argument("--judge-concurrency", type=int, default=4)
    parser.add_argument("--judge-checkpoint-interval", type=int, default=10)
    parser.add_argument(
        "--resume-judge",
        default="",
        help="Prior Judge run directory or CSV to reuse matching verdicts",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.judge_base_url or not args.judge_api_key or not args.judge_model:
        raise ValueError("judge base URL, API key, and model are required")
    if (
        args.judge_timeout_s <= 0
        or args.judge_retries < 1
        or args.judge_concurrency < 1
        or args.judge_checkpoint_interval < 0
    ):
        raise ValueError(
            "judge timeout, retries, and concurrency must be positive; "
            "checkpoint interval must be >= 0"
        )

    input_path = Path(args.input).expanduser().resolve()
    output_dir = Path(args.out_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("eval.locomo.judge")
    logging.basicConfig(level=logging.INFO)
    judge_llm = LLMClient(
        base_url=args.judge_base_url,
        api_key=args.judge_api_key,
        model=args.judge_model,
        temperature=0.0,
        max_tokens=512,
        timeout_s=args.judge_timeout_s,
        max_retries=args.judge_retries,
    )
    resume_manifest = build_judge_resume_manifest(
        base_url=judge_llm.base_url,
        model=judge_llm.model,
        system_prompt=LOCOMO_JUDGE_SYSTEM,
        prompt_template=LOCOMO_JUDGE_TEMPLATE,
    )
    write_judge_resume_manifest(output_dir, resume_manifest)
    resume_state = (
        load_judge_resume_state(
            args.resume_judge,
            expected_manifest=resume_manifest,
        )
        if args.resume_judge
        else None
    )
    report = judge_locomo_results(
        load_qa_results(input_path),
        judge_llm,
        output_dir,
        logger,
        concurrency=args.judge_concurrency,
        checkpoint_interval=args.judge_checkpoint_interval,
        existing_rows=resume_state.rows if resume_state else None,
    )
    summary = summarize_judge_rows(report.rows)
    summary.update({
        "input": str(input_path),
        "judge_model": args.judge_model,
        "judge_results": str(output_dir / "judge_results.csv"),
        "judge_checkpoint_interval": args.judge_checkpoint_interval,
        "judge_resume_source": (
            str(resume_state.source_csv) if resume_state else ""
        ),
    })
    summary_path = output_dir / "judge_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if report.errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

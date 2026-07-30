#!/usr/bin/env python3
"""Strict HTTP black-box metrics for LoCoMo runs.

Only values observed by the harness at the EchoMemory, answer-model, and
judge-model API boundaries are included. Missing observations remain ``None``;
the report never estimates internal EchoMemory token usage or readiness time.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.csv_io import read_dict_rows


SCHEMA_VERSION = 1
METRICS_FILENAME = "strict_blackbox_metrics.json"
REPORT_FILENAME = "strict_blackbox_report.md"


def _number(value: Any) -> float | None:
    if value is None or not str(value).strip():
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def percentile(values: Iterable[float], quantile: float) -> float | None:
    cleaned = sorted(value for value in values if math.isfinite(value))
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return cleaned[0]
    position = (len(cleaned) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return cleaned[lower]
    return cleaned[lower] + (
        cleaned[upper] - cleaned[lower]
    ) * (position - lower)


def metric_stats(values: Iterable[float]) -> dict[str, float | int | None]:
    cleaned = [value for value in values if math.isfinite(value)]
    return {
        "count": len(cleaned),
        "sum": round(sum(cleaned), 4) if cleaned else None,
        "avg": round(statistics.fmean(cleaned), 4) if cleaned else None,
        "p50": (
            round(percentile(cleaned, 0.50) or 0.0, 4)
            if cleaned
            else None
        ),
        "p95": (
            round(percentile(cleaned, 0.95) or 0.0, 4)
            if cleaned
            else None
        ),
        "p99": (
            round(percentile(cleaned, 0.99) or 0.0, 4)
            if cleaned
            else None
        ),
        "max": round(max(cleaned), 4) if cleaned else None,
    }


def metric_definitions() -> list[dict[str, str]]:
    return [
        {
            "name": "accuracy",
            "formula": "CORRECT / (CORRECT + WRONG)",
            "source": "judge_results.csv: verdict",
            "boundary": "Judge errors are excluded from the denominator.",
        },
        {
            "name": "request_success_rate",
            "formula": "rows with all four statuses ok / rows with all statuses",
            "source": (
                "qa_results.csv: retrieval_status, answer_status, "
                "model_status, health_status"
            ),
            "boundary": "This measures pipeline health, not answer correctness.",
        },
        {
            "name": "empty_retrieval_rate",
            "formula": "retrieval_count = 0 / rows with retrieval_count",
            "source": "qa_results.csv: retrieval_count",
            "boundary": "A non-empty retrieval does not prove relevant evidence.",
        },
        {
            "name": "visible_model_tokens",
            "formula": "answer API usage + judge API usage",
            "source": "OpenAI-compatible usage fields persisted by the harness",
            "boundary": "Missing usage remains N/A; no character-based estimate.",
        },
        {
            "name": "latency_distributions",
            "formula": "average, p50, p95, p99, max of observed wall-clock values",
            "source": (
                "qa_results.csv: end_to_end_ms, retrieval_latency_ms, "
                "injection_total_ms, llm_total_ms"
            ),
            "boundary": "Internal EchoMemory stages cannot be decomposed.",
        },
        {
            "name": "submission_rate",
            "formula": "submitted_messages / expected_messages",
            "source": "import_results.csv",
            "boundary": "Submission does not by itself prove indexing readiness.",
        },
        {
            "name": "batch_wall_clock_and_throughput",
            "formula": "finished - started; completed rows / wall-clock seconds",
            "source": "summary.json run timestamps and qa_results.csv row count",
            "boundary": "Includes all work between the recorded run boundaries.",
        },
        {
            "name": "internal_memory_tokens",
            "formula": "N/A",
            "source": "Not exposed by the EchoMemory HTTP API",
            "boundary": "Never estimated by this harness.",
        },
    ]


def _read_csv(path: Path) -> list[dict[str, str]]:
    return read_dict_rows(path, missing_ok=True)


def _elapsed_seconds(started_at: Any, finished_at: Any) -> float | None:
    if not started_at or not finished_at:
        return None
    try:
        started = datetime.fromisoformat(
            str(started_at).replace("Z", "+00:00")
        )
        finished = datetime.fromisoformat(
            str(finished_at).replace("Z", "+00:00")
        )
        elapsed = (finished - started).total_seconds()
    except (TypeError, ValueError):
        return None
    return round(elapsed, 4) if elapsed >= 0 else None


def _join_rows(
    qa_rows: list[dict[str, Any]],
    judge_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    judge_by_id = {
        str(row.get("question_id") or ""): row
        for row in judge_rows
        if str(row.get("question_id") or "")
    }
    return [
        {
            **row,
            **{
                key: value
                for key, value in judge_by_id.get(
                    str(row.get("question_id") or ""),
                    {},
                ).items()
                if key not in {
                    "question_id",
                    "question",
                    "answer",
                    "response",
                }
            },
        }
        for row in qa_rows
    ]


def _import_observation(
    import_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    formal_rows = [
        row
        for row in import_rows
        if str(row.get("status") or "").lower() != "reused"
    ]
    expected = sum(
        _integer(row.get("message_count")) or 0 for row in formal_rows
    )
    submitted = sum(
        _integer(row.get("submitted_messages")) or 0 for row in formal_rows
    )
    statuses = {
        str(row.get("status") or "unknown").lower() for row in import_rows
    }
    if statuses == {"reused"}:
        status = "reused"
    elif formal_rows and statuses <= {"completed"}:
        status = "completed"
    elif formal_rows:
        status = "incomplete"
    else:
        status = "N/A"
    return {
        "status": status,
        "expected_messages": expected if formal_rows else None,
        "submitted_messages": submitted if formal_rows else None,
    }


def observed_metrics(
    qa_rows: list[dict[str, Any]],
    judge_rows: list[dict[str, Any]],
    import_rows: list[dict[str, Any]],
    run_observation: dict[str, Any],
) -> dict[str, Any]:
    rows = _join_rows(qa_rows, judge_rows)
    categories: dict[str, dict[str, int | float | None]] = {}
    for row in rows:
        category = str(row.get("category") or "unknown")
        item = categories.setdefault(
            category,
            {"correct": 0, "wrong": 0, "graded": 0, "accuracy": None},
        )
        verdict = str(row.get("verdict") or "").upper()
        if verdict == "CORRECT":
            item["correct"] = int(item["correct"]) + 1
            item["graded"] = int(item["graded"]) + 1
        elif verdict == "WRONG":
            item["wrong"] = int(item["wrong"]) + 1
            item["graded"] = int(item["graded"]) + 1
    for item in categories.values():
        graded = int(item["graded"])
        item["accuracy"] = (
            int(item["correct"]) / graded if graded else None
        )

    correct = sum(
        str(row.get("verdict") or "").upper() == "CORRECT" for row in rows
    )
    wrong = sum(
        str(row.get("verdict") or "").upper() == "WRONG" for row in rows
    )
    required_statuses = (
        "retrieval_status",
        "answer_status",
        "model_status",
        "health_status",
    )
    status_rows = [
        row
        for row in rows
        if all(str(row.get(key) or "").strip() for key in required_statuses)
    ]
    successful = [
        row
        for row in status_rows
        if all(
            str(row.get(key) or "").strip().lower() == "ok"
            for key in required_statuses
        )
    ]
    retrieval_rows = [
        row for row in rows if _integer(row.get("retrieval_count")) is not None
    ]
    empty_retrieval = [
        row
        for row in retrieval_rows
        if _integer(row.get("retrieval_count")) == 0
    ]
    retry_rows = [
        row for row in rows if _integer(row.get("model_retry_count")) is not None
    ]
    retried = [
        row
        for row in retry_rows
        if (_integer(row.get("model_retry_count")) or 0) > 0
    ]

    def values(field: str, *, scale: float = 1.0) -> list[float]:
        return [
            value / scale
            for row in rows
            if (value := _number(row.get(field))) is not None
        ]

    answer_token_rows = [
        row for row in rows if _integer(row.get("answer_total_tokens")) is not None
    ]
    graded_rows = [
        row
        for row in rows
        if str(row.get("verdict") or "").upper() in {"CORRECT", "WRONG"}
    ]
    judge_token_rows = [
        row
        for row in graded_rows
        if _integer(row.get("judge_total_tokens")) is not None
    ]
    answer_usage_complete = len(answer_token_rows) == len(rows) and bool(rows)
    judge_usage_complete = (
        len(judge_token_rows) == len(graded_rows) and bool(graded_rows)
    )
    answer_token_total = sum(
        _integer(row.get("answer_total_tokens")) or 0
        for row in answer_token_rows
    )
    judge_token_total = sum(
        _integer(row.get("judge_total_tokens")) or 0
        for row in judge_token_rows
    )
    import_observation = _import_observation(import_rows)
    wall_clock_s = _elapsed_seconds(
        run_observation.get("run_started_at"),
        run_observation.get("run_finished_at"),
    )
    return {
        "categories": categories,
        "graded_count": correct + wrong,
        "correct_count": correct,
        "wrong_count": wrong,
        "accuracy": correct / (correct + wrong) if correct + wrong else None,
        "request_success_count": len(successful),
        "request_status_count": len(status_rows),
        "request_success_rate": (
            len(successful) / len(status_rows) if status_rows else None
        ),
        "failure_count": len(status_rows) - len(successful),
        "failure_rate": (
            (len(status_rows) - len(successful)) / len(status_rows)
            if status_rows
            else None
        ),
        "empty_retrieval_count": len(empty_retrieval),
        "retrieval_observed_count": len(retrieval_rows),
        "empty_retrieval_rate": (
            len(empty_retrieval) / len(retrieval_rows)
            if retrieval_rows
            else None
        ),
        "retried_count": len(retried),
        "retry_observed_count": len(retry_rows),
        "retry_rate": len(retried) / len(retry_rows) if retry_rows else None,
        "end_to_end_s": metric_stats(values("end_to_end_ms", scale=1000.0)),
        "retrieval_latency_s": metric_stats(
            values("retrieval_latency_ms", scale=1000.0)
        ),
        "injection_total_s": metric_stats(
            values("injection_total_ms", scale=1000.0)
        ),
        "llm_total_s": metric_stats(values("llm_total_ms", scale=1000.0)),
        "answer_prompt_tokens": metric_stats(values("answer_prompt_tokens")),
        "answer_completion_tokens": metric_stats(
            values("answer_completion_tokens")
        ),
        "answer_total_tokens": metric_stats(values("answer_total_tokens")),
        "answer_usage_observed_count": len(answer_token_rows),
        "answer_usage_expected_count": len(rows),
        "answer_usage_complete": answer_usage_complete,
        "judge_prompt_tokens": metric_stats(values("judge_prompt_tokens")),
        "judge_completion_tokens": metric_stats(
            values("judge_completion_tokens")
        ),
        "judge_total_tokens": metric_stats(values("judge_total_tokens")),
        "judge_usage_observed_count": len(judge_token_rows),
        "judge_usage_expected_count": len(graded_rows),
        "judge_usage_complete": judge_usage_complete,
        "visible_model_total_tokens": (
            answer_token_total + judge_token_total
            if answer_usage_complete and judge_usage_complete
            else None
        ),
        "tokens_per_correct": (
            answer_token_total / correct
            if answer_usage_complete and correct
            else None
        ),
        "expected_messages": import_observation["expected_messages"],
        "submitted_messages": import_observation["submitted_messages"],
        "submission_rate": (
            import_observation["submitted_messages"]
            / import_observation["expected_messages"]
            if import_observation["submitted_messages"] is not None
            and import_observation["expected_messages"]
            else None
        ),
        "import_status": import_observation["status"],
        "qa_parallelism": _integer(run_observation.get("qa_parallelism")),
        "batch_wall_clock_s": wall_clock_s,
        "qa_throughput_qps": (
            round(len(rows) / wall_clock_s, 6) if wall_clock_s else None
        ),
        "run_started_at": run_observation.get("run_started_at"),
        "run_finished_at": run_observation.get("run_finished_at"),
        "internal_memory_injection_tokens": None,
        "initial_memory_import_time_s": None,
    }


def build_snapshot(
    *,
    qa_rows: list[dict[str, Any]],
    judge_rows: list[dict[str, Any]],
    import_rows: list[dict[str, Any]],
    run_observation: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "strict_blackbox_metrics",
        "mode": "strict_observed",
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "artifact_path": str((output_dir / METRICS_FILENAME).resolve()),
        "report_path": str((output_dir / REPORT_FILENAME).resolve()),
        "row_count": len(qa_rows),
        "metrics": observed_metrics(
            qa_rows,
            judge_rows,
            import_rows,
            run_observation,
        ),
        "definitions": metric_definitions(),
        "unavailable": {
            "internal_memory_injection_tokens": None,
            "initial_memory_import_time_s": None,
        },
    }


def _display(value: Any, *, percent: bool = False) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    return f"{number * 100:.2f}%" if percent else f"{number:,.4f}"


def render_markdown(snapshot: dict[str, Any]) -> str:
    metrics = snapshot["metrics"]
    lines = [
        "# Strict Black-box Metrics",
        "",
        "Only directly observed API-boundary values are reported. Missing "
        "usage or internal EchoMemory values remain N/A.",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Accuracy | {_display(metrics.get('accuracy'), percent=True)} |",
        (
            "| QA request success rate | "
            f"{_display(metrics.get('request_success_rate'), percent=True)} |"
        ),
        (
            "| Empty retrieval rate | "
            f"{_display(metrics.get('empty_retrieval_rate'), percent=True)} |"
        ),
        f"| Failure rate | {_display(metrics.get('failure_rate'), percent=True)} |",
        f"| Visible retry rate | {_display(metrics.get('retry_rate'), percent=True)} |",
        (
            "| Message submission rate | "
            f"{_display(metrics.get('submission_rate'), percent=True)} |"
        ),
        (
            "| Visible model total tokens | "
            f"{_display(metrics.get('visible_model_total_tokens'))} |"
        ),
        (
            "| Tokens per correct answer | "
            f"{_display(metrics.get('tokens_per_correct'))} |"
        ),
        (
            "| Batch wall clock (s) | "
            f"{_display(metrics.get('batch_wall_clock_s'))} |"
        ),
        (
            "| QA throughput (questions/s) | "
            f"{_display(metrics.get('qa_throughput_qps'))} |"
        ),
        "",
        "## Latency",
        "",
        "| Stage | Average | P50 | P95 | P99 | Max |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, key in (
        ("End to end", "end_to_end_s"),
        ("Retrieval", "retrieval_latency_s"),
        ("Retrieval + prompt orchestration", "injection_total_s"),
        ("Answer model", "llm_total_s"),
    ):
        stats = metrics.get(key) or {}
        lines.append(
            f"| {label} | {_display(stats.get('avg'))} | "
            f"{_display(stats.get('p50'))} | {_display(stats.get('p95'))} | "
            f"{_display(stats.get('p99'))} | {_display(stats.get('max'))} |"
        )
    lines.extend([
        "",
        "## Boundaries",
        "",
        "- Internal EchoMemory extraction, embedding, reranking, and other "
        "model tokens: N/A.",
        "- Initial memory readiness time: N/A unless the service exposes a "
        "reliable completion event.",
        "- Non-empty retrieval is not equivalent to relevant retrieval.",
        "",
    ])
    return "\n".join(lines)


def write_artifacts(
    *,
    qa_rows: list[dict[str, Any]],
    judge_rows: list[dict[str, Any]],
    import_rows: list[dict[str, Any]],
    run_observation: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = build_snapshot(
        qa_rows=qa_rows,
        judge_rows=judge_rows,
        import_rows=import_rows,
        run_observation=run_observation,
        output_dir=output_dir,
    )
    (output_dir / METRICS_FILENAME).write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / REPORT_FILENAME).write_text(
        render_markdown(snapshot),
        encoding="utf-8",
    )
    return snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build strict black-box metrics from LoCoMo artifacts"
    )
    parser.add_argument("--qa", required=True, help="qa_results.csv")
    parser.add_argument("--judge", required=True, help="judge_results.csv")
    parser.add_argument("--import-results", default="")
    parser.add_argument("--summary", default="")
    parser.add_argument("--out-dir", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary: dict[str, Any] = {}
    if args.summary:
        summary_path = Path(args.summary).expanduser().resolve()
        if summary_path.is_file():
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                summary = loaded
    snapshot = write_artifacts(
        qa_rows=_read_csv(Path(args.qa).expanduser().resolve()),
        judge_rows=_read_csv(Path(args.judge).expanduser().resolve()),
        import_rows=(
            _read_csv(Path(args.import_results).expanduser().resolve())
            if args.import_results
            else []
        ),
        run_observation={
            "qa_parallelism": (
                summary.get("qa_parallelism")
                or summary.get("concurrency")
            ),
            "run_started_at": (
                summary.get("run_started_at")
                or summary.get("started_at")
            ),
            "run_finished_at": (
                summary.get("run_finished_at")
                or summary.get("finished_at")
            ),
        },
        output_dir=Path(args.out_dir).expanduser().resolve(),
    )
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

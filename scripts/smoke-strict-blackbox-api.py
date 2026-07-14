#!/usr/bin/env python3
"""Smoke-test the strict black-box payload attached by the V2 API proxy."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dev_server import merge_strict_blackbox_summary


def main() -> None:
    rows = [
        {
            "category": "1",
            "result": "CORRECT",
            "retrieval_status": "ok",
            "answer_status": "ok",
            "model_status": "ok",
            "health_status": "ok",
            "retrieval_count": "2",
            "model_retry_count": "0",
            "end_to_end_ms": "100",
            "retrieval_latency_ms": "40",
            "injection_total_ms": "45",
            "llm_total_ms": "55",
            "answer_prompt_tokens": "10",
            "answer_completion_tokens": "5",
            "answer_total_tokens": "15",
            "retrieval_tokens_est": "99999",
            "injection_tokens_est": "99999",
        },
        {
            "category": "4",
            "result": "WRONG",
            "retrieval_status": "failed",
            "answer_status": "failed",
            "model_status": "failed",
            "health_status": "failed",
            "retrieval_count": "0",
            "model_retry_count": "1",
            "end_to_end_ms": "300",
            "retrieval_latency_ms": "120",
            "injection_total_ms": "130",
            "llm_total_ms": "170",
            "answer_prompt_tokens": "30",
            "answer_completion_tokens": "15",
            "answer_total_tokens": "45",
            "retrieval_tokens_est": "99999",
            "injection_tokens_est": "99999",
        },
    ]
    with tempfile.TemporaryDirectory(prefix="strict-blackbox-api-") as temp_dir:
        csv_path = Path(temp_dir) / "results.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        (Path(temp_dir) / "summary.json").write_text(
            json.dumps(
                {
                    "qa_parallelism": 4,
                    "run_started_at": "2026-07-14T00:00:00+00:00",
                    "run_finished_at": "2026-07-14T00:00:08+00:00",
                }
            ),
            encoding="utf-8",
        )
        summary = merge_strict_blackbox_summary({}, csv_path)
        strict = summary["strict_blackbox"]
        artifact_path = Path(strict["artifact_path"])
        assert artifact_path.exists()
        persisted = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert persisted["source_signature"] == strict["source_signature"]
        assert persisted["artifact_path"] == str(artifact_path)
        report_path = Path(strict["report_path"])
        assert report_path.exists()
        report_html = report_path.read_text(encoding="utf-8")
        assert "严格黑盒指标报告" in report_html
        assert "核心比例" in report_html
        assert "初始记忆导入时间 · N/A" in report_html
        assert summary["strict_blackbox_report_path"] == str(report_path)

    metrics = strict["metrics"]
    assert strict["mode"] == "strict_observed"
    assert strict["row_count"] == 2
    assert len(strict["definitions"]) == 18
    assert Path(strict["artifact_path"]).name == "strict_blackbox_metrics.json"
    assert Path(strict["report_path"]).name == "strict_blackbox_report.html"
    assert metrics["accuracy"] == 0.5
    assert metrics["graded_count"] == 2
    assert metrics["correct_count"] == 1
    assert metrics["wrong_count"] == 1
    assert metrics["request_success_rate"] == 0.5
    assert metrics["empty_retrieval_rate"] == 0.5
    assert metrics["answer_total_tokens"]["sum"] == 60.0
    assert metrics["tokens_per_correct"] == 60.0
    assert metrics["expected_messages"] is None
    assert metrics["submitted_messages"] is None
    assert metrics["qa_parallelism"] == 4
    assert metrics["batch_wall_clock_s"] == 8.0
    assert metrics["qa_throughput_qps"] == 0.25
    assert strict["unavailable"]["internal_memory_injection_tokens"] is None
    assert strict["unavailable"]["initial_memory_import_time_ms"] is None
    assert metrics["internal_memory_injection_tokens"] is None
    assert metrics["initial_memory_import_time_ms"] is None
    print("strict black-box API smoke passed")


if __name__ == "__main__":
    main()

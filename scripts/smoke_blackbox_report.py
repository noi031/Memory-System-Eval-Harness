#!/usr/bin/env python3
"""Smoke-test strict black-box report metrics with synthetic observations."""

import tempfile
from pathlib import Path

from generate_html_report import generate_html_report, observed_blackbox_metrics


def main() -> None:
    rows = [
        {
            "question_id": "q1",
            "question": "Question 1",
            "answer": "A",
            "response": "A",
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
            "time_cost": "0.1",
            "retrieval_tokens_est": "99999",
            "injection_tokens_est": "99999",
        },
        {
            "question_id": "q2",
            "question": "Question 2",
            "answer": "B",
            "response": "C",
            "category": "4",
            "result": "WRONG",
            "retrieval_status": "ok",
            "answer_status": "ok",
            "model_status": "ok",
            "health_status": "ok",
            "retrieval_count": "0",
            "model_retry_count": "1",
            "end_to_end_ms": "300",
            "retrieval_latency_ms": "120",
            "injection_total_ms": "130",
            "llm_total_ms": "170",
            "answer_prompt_tokens": "30",
            "answer_completion_tokens": "15",
            "answer_total_tokens": "45",
            "time_cost": "0.3",
            "retrieval_tokens_est": "99999",
            "injection_tokens_est": "99999",
        },
    ]
    import_summary = {
        "status": "ECHOMEMORY_IMPORT_INCOMPLETE",
        "expected_messages": 4,
        "submitted_messages": 3,
    }
    metrics = observed_blackbox_metrics(rows, import_summary)
    assert metrics["request_success_rate"] == 1.0
    assert metrics["empty_retrieval_rate"] == 0.5
    assert metrics["failure_rate"] == 0.0
    assert metrics["retry_rate"] == 0.5
    assert metrics["answer_total_tokens"]["sum"] == 60.0
    assert metrics["tokens_per_correct"] == 60.0
    assert metrics["end_to_end_ms"]["p50"] == 200.0
    assert metrics["submission_rate"] == 0.75

    with tempfile.TemporaryDirectory(prefix="blackbox-report-smoke-") as temp_dir:
        output = Path(temp_dir) / "report.html"
        generate_html_report(
            rows,
            str(output),
            "Strict black-box smoke",
            {"evidence_policy": "blackbox"},
            import_summary,
        )
        report = output.read_text(encoding="utf-8")

    required = [
        "100.00%",
        "50.00%",
        "75.00%",
        "60.0",
        "ECHOMEMORY_IMPORT_INCOMPLETE",
        "内部记忆注入 Token",
        "指标口径说明",
        "QA 侧编排注入时延",
        "初始记忆导入时间",
        "当前不可严格计算",
        "<strong>N/A</strong>",
    ]
    for value in required:
        assert value in report, f"report is missing {value!r}"
    forbidden = [
        "估算",
        "retrieval_tokens_est",
        "injection_tokens_est",
        "99,999",
        "199,998",
    ]
    for value in forbidden:
        assert value not in report, f"report exposed estimated metric {value!r}"

    print("strict black-box report smoke passed")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Compare two LoCoMo run directories using their persisted artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.csv_io import read_dict_rows


def _read_csv(path: Path) -> list[dict[str, str]]:
    return read_dict_rows(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _run_signature(path: Path) -> dict[str, Any]:
    summary = _read_json(path / "summary.json")
    config_payload = _read_json(path / "config.json")
    config = config_payload.get("config", config_payload)
    config = config if isinstance(config, dict) else {}
    provenance = _read_json(path / "memory_provenance.json")
    qa_manifest = _read_json(path / "qa_resume_manifest.json")
    judge_manifest = _read_json(path / "judge_resume_manifest.json")
    qa = qa_manifest.get("qa") or {}
    answer_model = qa_manifest.get("answer_model") or {}
    memory_identity = qa_manifest.get("memory_identity") or {}
    judge = judge_manifest.get("judge") or {}
    qa_contract = qa_manifest.get("qa_contract") or {}
    return {
        "dataset_sha256": str(provenance.get("dataset_sha256") or ""),
        "memory_account": str(
            memory_identity.get("account")
            or config.get("account")
            or ""
        ),
        "memory_session_uris": sorted(
            str(uri)
            for uri in provenance.get("session_uris") or []
            if str(uri).strip()
        ),
        "qa_profile": str(
            summary.get("qa_profile")
            or qa.get("profile")
            or ""
        ),
        "answer_base_url": str(
            answer_model.get("base_url")
            or config.get("llm_base_url")
            or ""
        ).rstrip("/"),
        "answer_model": str(
            answer_model.get("model")
            or config.get("llm_model")
            or ""
        ),
        "qa_contract_sha256": str(qa_contract.get("sha256") or ""),
        "tool_protocol_sha256": sorted(
            str(value)
            for value in summary.get("tool_protocol_sha256") or []
            if str(value).strip()
        ),
        "judge_base_url": str(judge.get("base_url") or "").rstrip("/"),
        "judge_model": str(judge.get("model") or ""),
        "judge_prompt_sha256": str(judge.get("prompt_sha256") or ""),
    }


def _compatibility(left_dir: Path, right_dir: Path) -> dict[str, Any]:
    left = _run_signature(left_dir)
    right = _run_signature(right_dir)
    groups = {
        "memory": (
            "dataset_sha256",
            "memory_account",
            "memory_session_uris",
        ),
        "qa": (
            "qa_profile",
            "answer_base_url",
            "answer_model",
            "qa_contract_sha256",
            "tool_protocol_sha256",
        ),
        "judge": (
            "judge_base_url",
            "judge_model",
            "judge_prompt_sha256",
        ),
    }
    fields: dict[str, dict[str, Any]] = {}
    for group, names in groups.items():
        for name in names:
            left_value = left.get(name)
            right_value = right.get(name)
            missing = (
                left_value == ""
                or left_value == []
                or right_value == ""
                or right_value == []
            )
            status = (
                "unknown"
                if missing
                else "matched"
                if left_value == right_value
                else "mismatch"
            )
            fields[name] = {
                "group": group,
                "status": status,
                "left": left_value,
                "right": right_value,
            }
    mismatches = [
        name for name, item in fields.items()
        if item["status"] == "mismatch"
    ]
    unknown = [
        name for name, item in fields.items()
        if item["status"] == "unknown"
    ]
    status = "mismatch" if mismatches else "unknown" if unknown else "matched"
    return {
        "status": status,
        "comparable": status == "matched",
        "mismatches": mismatches,
        "unknown_fields": unknown,
        "fields": fields,
    }


def _load_run(path: Path) -> dict[str, dict[str, str]]:
    qa_rows = _read_csv(path / "qa_results.csv")
    judge_rows = _read_csv(path / "judge_results.csv")
    judges = {
        str(row.get("question_id") or ""): row
        for row in judge_rows
        if str(row.get("question_id") or "")
    }
    return {
        str(row.get("question_id") or ""): {
            **row,
            **{
                key: value
                for key, value in judges.get(
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
        if str(row.get("question_id") or "")
    }


def _verdict(row: dict[str, Any] | None) -> str:
    return str((row or {}).get("verdict") or "").upper()


def _transition(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> str:
    if left is None:
        return "added"
    if right is None:
        return "missing"
    pair = (_verdict(left), _verdict(right))
    if pair == ("WRONG", "CORRECT"):
        return "improved"
    if pair == ("CORRECT", "WRONG"):
        return "regressed"
    if pair == ("CORRECT", "CORRECT"):
        return "stable_correct"
    if pair == ("WRONG", "WRONG"):
        return "stable_wrong"
    if "ERROR" in pair or "" in pair:
        return "ungraded"
    return "changed"


def _category_summary(
    rows: dict[str, dict[str, str]],
) -> dict[str, dict[str, int | float | None]]:
    counts: dict[str, Counter[str]] = {}
    for row in rows.values():
        category = str(row.get("category") or "unknown")
        counts.setdefault(category, Counter())[_verdict(row)] += 1
    summary: dict[str, dict[str, int | float | None]] = {}
    for category, counter in counts.items():
        correct = counter["CORRECT"]
        wrong = counter["WRONG"]
        graded = correct + wrong
        summary[category] = {
            "correct": correct,
            "wrong": wrong,
            "graded": graded,
            "accuracy": correct / graded if graded else None,
        }
    return summary


def compare_runs(left_dir: Path, right_dir: Path) -> dict[str, Any]:
    left = _load_run(left_dir)
    right = _load_run(right_dir)
    question_ids = list(dict.fromkeys([*left, *right]))
    rows: list[dict[str, Any]] = []
    for question_id in question_ids:
        left_row = left.get(question_id)
        right_row = right.get(question_id)
        source = right_row or left_row or {}
        rows.append({
            "question_id": question_id,
            "category": str(source.get("category") or ""),
            "question": str(source.get("question") or ""),
            "gold_answer": str(source.get("answer") or ""),
            "left_verdict": _verdict(left_row),
            "right_verdict": _verdict(right_row),
            "transition": _transition(left_row, right_row),
            "left_response": str((left_row or {}).get("response") or ""),
            "right_response": str((right_row or {}).get("response") or ""),
            "left_retrieval_count": str(
                (left_row or {}).get("retrieval_count")
                or (left_row or {}).get("num_retrieved")
                or ""
            ),
            "right_retrieval_count": str(
                (right_row or {}).get("retrieval_count")
                or (right_row or {}).get("num_retrieved")
                or ""
            ),
        })
    transitions = Counter(str(row["transition"]) for row in rows)
    left_categories = _category_summary(left)
    right_categories = _category_summary(right)
    categories: dict[str, dict[str, Any]] = {}
    for category in dict.fromkeys([*left_categories, *right_categories]):
        left_item = left_categories.get(category, {})
        right_item = right_categories.get(category, {})
        left_accuracy = left_item.get("accuracy")
        right_accuracy = right_item.get("accuracy")
        categories[category] = {
            "left": left_item,
            "right": right_item,
            "accuracy_delta": (
                float(right_accuracy) - float(left_accuracy)
                if left_accuracy is not None and right_accuracy is not None
                else None
            ),
        }
    return {
        "schema_version": 2,
        "left": str(left_dir),
        "right": str(right_dir),
        "compatibility": _compatibility(left_dir, right_dir),
        "question_count": len(rows),
        "transition_counts": dict(transitions),
        "categories": categories,
        "rows": rows,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["question_id"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _percent(value: Any) -> str:
    return "N/A" if value is None else f"{float(value) * 100:.2f}%"


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["transition_counts"]
    compatibility = report.get("compatibility") or {}
    lines = [
        "# LoCoMo Run Comparison",
        "",
        f"- Left: `{report['left']}`",
        f"- Right: `{report['right']}`",
        f"- Questions: {report['question_count']}",
        f"- Improved: {counts.get('improved', 0)}",
        f"- Regressed: {counts.get('regressed', 0)}",
        f"- Stable correct: {counts.get('stable_correct', 0)}",
        f"- Stable wrong: {counts.get('stable_wrong', 0)}",
        "",
        "## Compatibility",
        "",
        f"- Status: **{compatibility.get('status', 'unknown')}**",
        f"- Strictly comparable: **{str(bool(compatibility.get('comparable'))).lower()}**",
    ]
    if compatibility.get("mismatches"):
        lines.append(
            "- Mismatched fields: "
            + ", ".join(compatibility["mismatches"])
        )
    if compatibility.get("unknown_fields"):
        lines.append(
            "- Unverified fields: "
            + ", ".join(compatibility["unknown_fields"])
        )
    if not compatibility.get("comparable"):
        lines.extend([
            "",
            "> Warning: score deltas below are descriptive only because the "
            "run contracts are not proven identical.",
        ])
    lines.extend([
        "",
        "## Category Accuracy",
        "",
        "| Category | Left | Right | Delta |",
        "|---|---:|---:|---:|",
    ])
    for category, item in report["categories"].items():
        left_accuracy = (item.get("left") or {}).get("accuracy")
        right_accuracy = (item.get("right") or {}).get("accuracy")
        lines.append(
            f"| {category} | {_percent(left_accuracy)} | "
            f"{_percent(right_accuracy)} | {_percent(item.get('accuracy_delta'))} |"
        )
    lines.extend([
        "",
        "## Changed Questions",
        "",
        "| ID | Category | Transition | Left | Right |",
        "|---|---|---|---|---|",
    ])
    for row in report["rows"]:
        if row["transition"] not in {"improved", "regressed", "changed"}:
            continue
        lines.append(
            f"| {row['question_id']} | {row['category']} | "
            f"{row['transition']} | {row['left_verdict']} | "
            f"{row['right_verdict']} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_report(
    left_dir: Path,
    right_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = compare_runs(left_dir, right_dir)
    json_path = output_dir / "comparison.json"
    csv_path = output_dir / "comparison.csv"
    markdown_path = output_dir / "comparison.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(csv_path, report["rows"])
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    report["artifacts"] = {
        "json": str(json_path),
        "csv": str(csv_path),
        "markdown": str(markdown_path),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare two LoCoMo result directories"
    )
    parser.add_argument("--left", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    report = write_report(
        Path(args.left).expanduser().resolve(),
        Path(args.right).expanduser().resolve(),
        Path(args.out_dir).expanduser().resolve(),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

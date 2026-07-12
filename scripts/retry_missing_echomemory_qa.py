#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def resolve_python_bin(echomem_root: str) -> str:
    return str(Path(sys.executable).resolve())


def build_retry_command(args: argparse.Namespace, question_ids: list[str], round_dir: Path) -> list[str]:
    command = [
        resolve_python_bin(args.echomem_root),
        str(Path(__file__).resolve().parent.parent / "benchmark" / "locomo" / "echomemory" / "run_eval.py"),
        "--dataset",
        str(Path(args.dataset).expanduser().resolve()),
        "--out-dir",
        str(round_dir),
        "--sample",
        "all",
        "--questions",
        ",".join(question_ids),
        "--echomem-root",
        args.echomem_root,
        "--echomem-transport",
        "http",
        "--echomem-base-url",
        args.echomem_base_url,
        "--echomem-http-timeout-s",
        str(args.echomem_http_timeout_s),
        "--workspace",
        args.workspace,
        "--account",
        args.account,
        "--user-id",
        args.user_id,
        "--agent-id",
        args.agent_id,
        "--prompt-mode",
        args.prompt_mode if args.prompt_mode != "one_shot" else "vikingboat_lite",
        "--top-k",
        str(args.top_k),
        "--score-threshold",
        str(args.score_threshold),
        "--memory-budget-chars",
        str(args.memory_budget_chars),
        "--user-memory-budget-chars",
        str(args.user_memory_budget_chars),
        "--agent-memory-budget-chars",
        str(args.agent_memory_budget_chars),
        "--retrieval-mode",
        "search",
        "--evidence-policy",
        "blackbox",
        "--retrieval-source-mode",
        "echo_http_native",
        "--answer-base-url",
        args.answer_base_url,
        "--answer-model",
        args.answer_model,
        "--model-retries",
        str(args.model_retries),
        "--timeout-s",
        str(args.timeout_s),
        "--question-timeout-s",
        str(args.question_timeout_s),
        "--qa-parallelism",
        str(args.qa_parallelism),
        "--tool-set",
        args.tool_set,
        "--tool-search-limit",
        str(args.tool_search_limit),
        "--tool-min-score",
        str(args.tool_min_score),
        "--tool-log-chars",
        str(args.tool_log_chars),
        "--prefetch-read-count",
        str(args.prefetch_read_count),
        "--prefetch-context-chars",
        str(args.prefetch_context_chars),
        "--max-iterations",
        str(args.max_iterations),
    ]
    if args.echomem_config:
        command += ["--echomem-config", args.echomem_config]
    if args.echomem_auth_key:
        command += ["--echomem-auth-key", args.echomem_auth_key]
    command.append("--qa-memory-injection" if args.qa_memory_injection else "--no-qa-memory-injection")
    command += [
        "--no-local-session-summaries",
        "--no-local-atoms",
        "--no-local-messages",
        "--no-local-timeline-hints",
        "--no-local-memory-artifacts",
    ]
    command.append("--vikingboat-tool-loop" if args.vikingboat_tool_loop else "--no-vikingboat-tool-loop")
    command.append("--vikingboat-compat" if args.vikingboat_compat else "--no-vikingboat-compat")
    command.append("--initial-tool-prefetch" if args.initial_tool_prefetch else "--no-initial-tool-prefetch")
    command.append("--fallback-to-one-shot" if args.fallback_to_one_shot else "--no-fallback-to-one-shot")
    command += [
        "--no-search-overview-enrichment",
        "--no-current-session-raw-fallback",
        "--no-precision-session-readback",
        "--no-precision-grounded-projection",
        "--no-longmemeval-current-session-summary-fallback",
        "--no-hotpot-empty-overview-fallback",
    ]
    if args.answer_token:
        command += ["--answer-token", args.answer_token]
    return command


def main() -> None:
    parser = argparse.ArgumentParser(description="Retry missing EchoMemory QA questions and append them to the source CSV.")
    parser.add_argument("--input", required=True, help="Original EchoMemory QA result CSV")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--question-ids", required=True)
    parser.add_argument("--echomem-root", required=True)
    parser.add_argument("--echomem-config", default="")
    parser.add_argument("--echomem-transport", choices=["http"], default="http")
    parser.add_argument("--echomem-base-url", required=True)
    parser.add_argument("--echomem-auth-key", default=os.environ.get("ECHOMEM_AUTH_KEY") or "")
    parser.add_argument("--echomem-http-timeout-s", type=float, default=60.0)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--prompt-mode", choices=["one_shot", "vikingboat_lite", "vikingboat_compat"], default="vikingboat_lite")
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--score-threshold", type=float, default=0.1)
    parser.add_argument("--memory-budget-chars", type=int, default=6000)
    parser.add_argument("--user-memory-budget-chars", type=int, default=4000)
    parser.add_argument("--agent-memory-budget-chars", type=int, default=2000)
    parser.add_argument("--retrieval-mode", choices=["search"], default="search")
    parser.add_argument("--answer-base-url", required=True)
    parser.add_argument("--answer-model", default="gpt-5.5")
    parser.add_argument("--answer-token", default=os.environ.get("LOCOMO_JUDGE_TOKEN") or os.environ.get("JUDGE_TOKEN") or os.environ.get("OPENAI_API_KEY") or "")
    parser.add_argument("--model-retries", type=int, default=5)
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--question-timeout-s", type=int, default=600)
    parser.add_argument("--qa-parallelism", type=int, default=5)
    parser.add_argument("--qa-memory-injection", dest="qa_memory_injection", action="store_true", default=True)
    parser.add_argument("--no-qa-memory-injection", dest="qa_memory_injection", action="store_false")
    parser.add_argument("--tool-set", default="search_read")
    parser.add_argument("--tool-search-limit", type=int, default=20)
    parser.add_argument("--tool-min-score", type=float, default=0.35)
    parser.add_argument("--tool-log-chars", type=int, default=1200)
    parser.add_argument("--prefetch-read-count", type=int, default=4)
    parser.add_argument("--prefetch-context-chars", type=int, default=5000)
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--vikingboat-tool-loop", dest="vikingboat_tool_loop", action="store_true", default=False)
    parser.add_argument("--no-vikingboat-tool-loop", dest="vikingboat_tool_loop", action="store_false")
    parser.add_argument("--vikingboat-compat", dest="vikingboat_compat", action="store_true", default=False)
    parser.add_argument("--no-vikingboat-compat", dest="vikingboat_compat", action="store_false")
    parser.add_argument("--initial-tool-prefetch", dest="initial_tool_prefetch", action="store_true", default=False)
    parser.add_argument("--no-initial-tool-prefetch", dest="initial_tool_prefetch", action="store_false")
    parser.add_argument("--fallback-to-one-shot", dest="fallback_to_one_shot", action="store_true", default=True)
    parser.add_argument("--no-fallback-to-one-shot", dest="fallback_to_one_shot", action="store_false")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    original_rows = read_csv(input_path)
    existing_ids = {row.get("question_id", "") for row in original_rows if row.get("question_id")}
    requested_question_ids = [item.strip() for item in args.question_ids.split(",") if item.strip()]
    question_ids = [qid for qid in dict.fromkeys(requested_question_ids) if qid not in existing_ids]
    summary: dict[str, Any] = {
        "input": str(input_path),
        "backend": "echomemory",
        "requested_questions": len(requested_question_ids),
        "missing_questions": len(question_ids),
        "question_ids": question_ids,
        "dry_run": bool(args.dry_run),
    }
    if not question_ids or args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        (out_dir / "retry_missing_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    round_dir = out_dir / "retry_missing_round1"
    round_dir.mkdir(parents=True, exist_ok=True)
    command = build_retry_command(args, question_ids, round_dir)
    redacted = ["******" if index and command[index - 1] in {"--answer-token"} else item for index, item in enumerate(command)]
    print("$ " + " ".join(redacted), flush=True)
    proc = subprocess.run(command, cwd=str(Path(__file__).resolve().parent.parent), text=True)
    summary["returncode"] = proc.returncode
    retry_csv = round_dir / "echomemory_memory_qa_results.csv"
    summary["retry_csv"] = str(retry_csv)
    if proc.returncode != 0 or not retry_csv.exists():
        summary["merged"] = False
        (out_dir / "retry_missing_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        raise SystemExit(proc.returncode or 1)

    retry_rows = read_csv(retry_csv)
    retry_rows = [row for row in retry_rows if row.get("question_id") in set(question_ids)]
    retry_by_id = {row.get("question_id", ""): row for row in retry_rows if row.get("question_id")}
    appended_rows = [retry_by_id[qid] for qid in question_ids if qid in retry_by_id]
    backup = input_path.with_suffix(input_path.suffix + ".before_missing_retry.bak")
    backup.write_text(input_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    write_csv(input_path, original_rows + appended_rows)
    summary.update(
        {
            "merged": True,
            "backup": str(backup),
            "retried_rows": len(retry_rows),
            "appended_rows": len(appended_rows),
            "still_missing_after_retry": len(question_ids) - len(appended_rows),
        }
    )
    (out_dir / "retry_missing_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

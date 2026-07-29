"""Parallel LongMemEval orchestration using the current dataset CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from benchmarks.longmemeval.recovery import merge_shard_artifacts


VALUE_OPTIONS_TO_REPLACE = {
    "--out-dir",
    "--question-ids",
    "--questions",
    "--parallel-shards",
    "--parallel-workers",
}
FLAG_OPTIONS_TO_REMOVE = {"--parallel-dry-run"}


def partition_question_ids(
    question_ids: list[str],
    shard_count: int,
) -> list[list[str]]:
    count = max(1, min(int(shard_count or 1), len(question_ids)))
    shards = [[] for _ in range(count)]
    for index, question_id in enumerate(question_ids):
        shards[index % count].append(question_id)
    return [shard for shard in shards if shard]


def _clean_forwarded_args(argv: list[str]) -> list[str]:
    cleaned: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in FLAG_OPTIONS_TO_REMOVE:
            index += 1
            continue
        if item in VALUE_OPTIONS_TO_REPLACE:
            index += 2
            continue
        cleaned.append(item)
        index += 1
    return cleaned


def build_shard_commands(
    argv: list[str],
    shards: list[list[str]],
    output_dir: Path,
) -> list[dict[str, Any]]:
    base_args = _clean_forwarded_args(argv)
    runner = Path(__file__).with_name("run_eval.py")
    commands: list[dict[str, Any]] = []
    for index, question_ids in enumerate(shards, 1):
        shard_root = output_dir / "shards" / f"shard_{index:03d}"
        commands.append({
            "index": index,
            "question_ids": question_ids,
            "output_dir": str(shard_root),
            "command": [
                sys.executable,
                str(runner),
                *base_args,
                "--parallel-shards",
                "1",
                "--question-ids",
                ",".join(question_ids),
                "--out-dir",
                str(shard_root),
            ],
        })
    return commands


def _run_shard(spec: dict[str, Any]) -> dict[str, Any]:
    log_path = Path(spec["output_dir"]) / "runner.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        spec["command"],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log_path.write_text(completed.stdout or "", encoding="utf-8")
    result_dirs = sorted(
        (
            path
            for path in Path(spec["output_dir"]).iterdir()
            if path.is_dir() and (path / "summary.json").is_file()
        ),
        key=lambda path: path.stat().st_mtime,
    )
    return {
        **spec,
        "returncode": completed.returncode,
        "log_path": str(log_path),
        "result_dir": str(result_dirs[-1]) if result_dirs else "",
    }


def run_parallel(
    *,
    argv: list[str],
    question_ids: list[str],
    output_dir: Path,
    shard_count: int,
    worker_count: int,
    dry_run: bool,
) -> dict[str, Any]:
    shards = partition_question_ids(question_ids, shard_count)
    commands = build_shard_commands(argv, shards, output_dir)
    manifest = {
        "benchmark": "longmemeval",
        "mode": "parallel",
        "selected_questions": len(question_ids),
        "shards": len(shards),
        "workers": max(1, min(worker_count, len(shards))),
        "dry_run": dry_run,
        "commands": commands,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "parallel_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return manifest

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(
        max_workers=max(1, min(worker_count, len(commands)))
    ) as pool:
        futures = {pool.submit(_run_shard, spec): spec for spec in commands}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item["index"])
    run_dirs = [
        result["result_dir"] for result in results if result["result_dir"]
    ]
    merged_summary = merge_shard_artifacts(
        run_dirs,
        output_dir / "merged",
    )
    failures = [
        result for result in results if result["returncode"] != 0
    ]
    summary = {
        "status": "failed" if failures else merged_summary["status"],
        "benchmark": "longmemeval",
        "mode": "parallel",
        "selected_questions": len(question_ids),
        "shards": len(shards),
        "workers": max(1, min(worker_count, len(shards))),
        "results": results,
        "failures": failures,
        "merged": merged_summary,
    }
    (output_dir / "parallel_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary

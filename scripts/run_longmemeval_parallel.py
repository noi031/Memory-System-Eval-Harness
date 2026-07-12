#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import benchmark_adapter


def safe_slug(value: Any, limit: int = 72) -> str:
    text = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip()).strip("-._")
    return (text or "item")[:limit]


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="Run LongMemEval through multiple isolated echomemory_generic_qa shards and merge the outputs."
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--shards", type=int, default=2)
    parser.add_argument("--sample", default="all")
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--questions", default="")
    parser.add_argument("--random-count", type=int, default=0)
    parser.add_argument("--random-seed", type=int, default=30)
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--workspace-root", default="/tmp/locomo-eval-echomemory-parallel")
    parser.add_argument("--account-prefix", default="longmemeval-parallel")
    parser.add_argument("--namespace-prefix", default="longmemeval-parallel")
    parser.add_argument("--stagger-s", type=float, default=2.0)
    parser.add_argument("--reap-completed-after-s", type=float, default=45.0)
    parser.add_argument("--reap-kill-after-s", type=float, default=15.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_known_args()


def selected_question_ids(raw: str) -> set[str]:
    return {item.strip() for item in str(raw or "").split(",") if item.strip()}


def iter_jobs(dataset_path: Path, sample: str) -> list[tuple[Any, dict[str, Any]]]:
    items: list[tuple[Any, dict[str, Any]]] = []
    for raw_index, raw in benchmark_adapter.iter_payload_from_path(dataset_path):
        built = benchmark_adapter.longmemeval_job_plan(raw, raw_index, sample)
        if built is None:
            continue
        items.append(built)
    return items


def apply_selection(
    jobs_and_plans: list[tuple[Any, dict[str, Any]]],
    *,
    sample: str,
    question_filter: set[str],
    count: int,
    random_count: int,
    random_seed: int,
) -> list[tuple[Any, dict[str, Any]]]:
    filtered: list[tuple[Any, dict[str, Any]]] = []
    for job, plan in jobs_and_plans:
        if sample not in ("", "all") and str(job.sample_id or "").strip() != sample:
            continue
        if question_filter:
            candidates = {
                str(job.question_id or "").strip(),
                str(job.native_question_id or "").strip(),
                str(job.sample_id or "").strip(),
            }
            if not question_filter.intersection(candidates):
                continue
        filtered.append((job, plan))
    if random_count and random_count > 0:
        import random

        rnd = random.Random(random_seed)
        filtered = rnd.sample(filtered, min(random_count, len(filtered)))
    if count and count > 0:
        filtered = filtered[:count]
    return filtered


def isolated_job_groups(selected: list[tuple[Any, dict[str, Any]]]) -> list[list[tuple[Any, dict[str, Any]]]]:
    groups: dict[str, list[tuple[Any, dict[str, Any]]]] = {}
    for job, plan in selected:
        key = str(job.sample_id or job.question_id or len(groups)).strip() or f"group-{len(groups) + 1}"
        groups.setdefault(key, []).append((job, plan))
    ordered_groups = sorted(
        groups.items(),
        key=lambda item: (
            -len(item[1]),
            str(item[1][0][0].question_id or ""),
            str(item[0]),
        ),
    )
    return [group_items for _group_id, group_items in ordered_groups]


def merge_csvs(paths: list[Path], dest: Path) -> int:
    rows: list[dict[str, str]] = []
    fieldnames: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append(row)
                for key in row.keys():
                    if key not in fieldnames:
                        fieldnames.append(key)
    if not rows:
        return 0
    rows.sort(
        key=lambda row: (
            str(row.get("question_id") or row.get("native_question_id") or ""),
            str(row.get("sample_id") or ""),
        )
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def copy_recall_logs(shard_dirs: list[Path], dest: Path) -> int:
    copied = 0
    dest.mkdir(parents=True, exist_ok=True)
    for shard_dir in shard_dirs:
        for path in shard_dir.glob("q*.recall.json"):
            shutil.copy2(path, dest / f"{shard_dir.parent.name}_{path.name}")
            copied += 1
    return copied


def summary_from_rows(csv_path: Path) -> dict[str, Any]:
    if not csv_path.exists():
        return {}
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return {"rows": 0}
    total_qa = sum(float(row.get("qa_time_s") or row.get("time_cost") or 0.0) for row in rows)
    total_injection = sum(float(row.get("memory_injection_time_s") or 0.0) for row in rows)
    total_end_to_end = sum(float(row.get("end_to_end_time_s") or 0.0) for row in rows)
    tool_calls = sum(int(float(row.get("tool_call_count") or 0)) for row in rows)
    return {
        "rows": len(rows),
        "avg_qa_time_s": round(total_qa / len(rows), 4),
        "avg_memory_injection_time_s": round(total_injection / len(rows), 4),
        "avg_end_to_end_time_s": round(total_end_to_end / len(rows), 4),
        "tool_call_total": tool_calls,
        "avg_tool_call_count": round(tool_calls / len(rows), 4),
    }


def _sum_int_metric(summaries: list[dict[str, Any]], key: str) -> int | None:
    values = [int(item.get(key) or 0) for item in summaries if item.get(key) not in (None, "")]
    if not values:
        return None
    return sum(values)


def _merge_count_maps(summaries: list[dict[str, Any]], key: str) -> dict[str, int]:
    merged: dict[str, int] = {}
    for item in summaries:
        value = item.get(key)
        if not isinstance(value, dict):
            continue
        for name, count in value.items():
            merged[str(name)] = merged.get(str(name), 0) + int(count or 0)
    return merged


def merged_summary_from_shards(csv_path: Path, shard_root: Path) -> dict[str, Any]:
    merged = summary_from_rows(csv_path)
    shard_summaries: list[dict[str, Any]] = []
    for path in sorted(shard_root.glob("shard_*/run/summary.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            shard_summaries.append(data)
    if not shard_summaries:
        return merged
    for key in (
        "answer_total_tokens",
        "retrieval_tokens_est",
        "memory_hit_total",
        "llm_input_tokens",
        "llm_output_tokens",
        "llm_total_tokens",
        "llm_call_count",
        "import_llm_prompt_tokens",
        "import_llm_completion_tokens",
        "import_llm_total_tokens",
        "import_embedding_total_tokens",
        "import_total_tokens",
        "search_intent_total_tokens",
        "search_intent_call_count",
        "embedding_total_tokens",
        "embedding_call_count",
    ):
        total = _sum_int_metric(shard_summaries, key)
        if total is not None:
            merged[key] = total
    for key in ("health_counts", "tool_name_counts", "model_status_counts", "retrieval_status_counts", "answer_status_counts"):
        merged_map = _merge_count_maps(shard_summaries, key)
        if merged_map:
            merged[key] = merged_map
    rows = int(merged.get("rows") or 0)
    answer_total_tokens = merged.get("answer_total_tokens")
    if rows and answer_total_tokens is not None:
        merged["avg_answer_total_tokens"] = round(float(answer_total_tokens) / rows, 1)
    return merged


def shard_final_artifacts_ready(shard_dir: Path, passthrough: list[str]) -> bool:
    run_dir = shard_dir / "run"
    csv_path = run_dir / "echomemory_generic_qa_results.csv"
    summary_path = run_dir / "summary.json"
    if not csv_path.exists() or not summary_path.exists():
        return False
    if "--official-eval-after" in passthrough:
        return (run_dir / "longmemeval_official_summary.json").exists()
    return True


def shard_latest_artifact_mtime(shard_dir: Path, passthrough: list[str]) -> float:
    run_dir = shard_dir / "run"
    paths = [
        run_dir / "echomemory_generic_qa_results.csv",
        run_dir / "summary.json",
    ]
    if "--official-eval-after" in passthrough:
        paths.append(run_dir / "longmemeval_official_summary.json")
    mtimes = [path.stat().st_mtime for path in paths if path.exists()]
    return max(mtimes) if mtimes else 0.0


def build_shard_command(
    args: argparse.Namespace,
    passthrough: list[str],
    *,
    shard_index: int,
    shard_jobs: list[tuple[Any, dict[str, Any]]],
    shard_out_dir: Path,
    shard_workspace: Path,
) -> list[str]:
    question_ids = [str(job.question_id or "").strip() for job, _plan in shard_jobs if str(job.question_id or "").strip()]
    namespace = f"{safe_slug(args.namespace_prefix, 40)}-{time.strftime('%Y%m%d%H%M%S')}-s{shard_index:02d}-{uuid.uuid4().hex[:6]}"
    account = f"{safe_slug(args.account_prefix, 40)}-s{shard_index:02d}"
    python_bin = str(Path(args.python_bin).expanduser())
    cmd = [
        python_bin,
        str(ROOT / "scripts" / "echomemory_generic_qa.py"),
        "--dataset",
        str(Path(args.dataset).expanduser().resolve()),
        "--format",
        "longmemeval",
        "--out-dir",
        str(shard_out_dir),
        "--sample",
        args.sample,
        "--questions",
        ",".join(question_ids),
        "--workspace",
        str(shard_workspace),
        "--account",
        account,
        "--namespace",
        namespace,
    ]
    return cmd + passthrough


def run_official_eval_if_requested(
    passthrough: list[str],
    *,
    csv_path: Path,
    dataset_path: Path,
    merged_dir: Path,
    python_bin: str,
    env: dict[str, str],
) -> dict[str, Any] | None:
    if "--official-eval-after" not in passthrough:
        return None
    timeout_s = "120"
    retries = "5"
    base_url = env.get("JUDGE_BASE_URL") or env.get("ECHOMEM_CHAT_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model = env.get("JUDGE_MODEL") or env.get("ECHOMEM_CHAT_MODEL") or "deepseek-v4-flash"
    passthrough_token = ""
    for idx, item in enumerate(passthrough):
        if item == "--timeout-s" and idx + 1 < len(passthrough):
            timeout_s = passthrough[idx + 1]
        elif item == "--model-retries" and idx + 1 < len(passthrough):
            retries = passthrough[idx + 1]
        elif item == "--judge-base-url" and idx + 1 < len(passthrough) and passthrough[idx + 1].strip():
            base_url = passthrough[idx + 1]
        elif item == "--answer-base-url" and idx + 1 < len(passthrough) and passthrough[idx + 1].strip():
            base_url = passthrough[idx + 1]
        elif item == "--judge-model" and idx + 1 < len(passthrough) and passthrough[idx + 1].strip():
            model = passthrough[idx + 1]
        elif item == "--answer-model" and idx + 1 < len(passthrough) and passthrough[idx + 1].strip():
            model = passthrough[idx + 1]
        elif item == "--judge-token" and idx + 1 < len(passthrough) and passthrough[idx + 1].strip():
            passthrough_token = passthrough[idx + 1]
        elif item == "--answer-token" and idx + 1 < len(passthrough) and passthrough[idx + 1].strip() and not passthrough_token:
            passthrough_token = passthrough[idx + 1]
    cmd = [
        str(Path(python_bin).expanduser()),
        str(ROOT / "scripts" / "longmemeval_official_eval.py"),
        "--csv",
        str(csv_path),
        "--reference",
        str(dataset_path),
        "--out-dir",
        str(merged_dir / "official_eval"),
        "--base-url",
        str(base_url),
        "--model",
        str(model),
        "--parallel",
        "1",
        "--limit",
        str(summary_from_rows(csv_path).get("rows") or 0),
        "--timeout-s",
        str(timeout_s),
        "--retries",
        str(retries),
    ]
    token = (
        passthrough_token
        or env.get("LOCOMO_JUDGE_TOKEN")
        or env.get("JUDGE_TOKEN")
        or env.get("OPENAI_API_KEY")
        or env.get("ECHOMEM_CHAT_API_KEY")
        or env.get("DASHSCOPE_API_KEY")
        or ""
    )
    eval_env = dict(env)
    if token:
        eval_env["LOCOMO_JUDGE_TOKEN"] = token
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=eval_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    summary_path = merged_dir / "official_eval" / "longmemeval_official_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    return {
        "returncode": proc.returncode,
        "summary_path": str(summary_path),
        "stdout_tail": "\n".join((proc.stdout or "").splitlines()[-20:]),
        "summary": summary,
    }


def write_merged_summary_artifacts(
    *,
    merged_dir: Path,
    merged_csv: Path,
    dataset_path: Path,
    sample: str,
    merged_rows: int,
    merged_summary: dict[str, Any],
    official_eval_requested: bool,
    official_eval: dict[str, Any] | None,
    status: str,
) -> None:
    payload = {
        "status": status,
        "dataset_format": "longmemeval",
        "dataset": str(dataset_path),
        "sample": sample,
        "count": merged_rows,
        "rows": merged_rows,
        "output_csv": str(merged_csv),
        "official_eval_after": bool(official_eval_requested),
        **dict(merged_summary or {}),
    }
    official_summary = {}
    official_summary_path = ""
    if isinstance(official_eval, dict):
        official_summary = dict(official_eval.get("summary") or {})
        official_summary_path = str(official_eval.get("summary_path") or "").strip()
    if official_summary_path:
        source = Path(official_summary_path)
        target = merged_dir / "longmemeval_official_summary.json"
        if source.exists():
            shutil.copyfile(source, target)
            official_summary_path = str(target)
    if official_summary:
        payload.update({
            "official_metric": "overall_accuracy",
            "official_score": official_summary.get("overall_accuracy"),
            "official_overall_accuracy": official_summary.get("overall_accuracy"),
            "official_task_averaged_accuracy": official_summary.get("task_averaged_accuracy"),
            "official_graded": official_summary.get("graded"),
            "official_correct": official_summary.get("correct"),
            "official_wrong": official_summary.get("wrong"),
            "longmemeval_official_summary_path": official_summary_path or str(merged_dir / "longmemeval_official_summary.json"),
            "official_eval": {
                "enabled": True,
                "returncode": official_eval.get("returncode") if isinstance(official_eval, dict) else None,
                "summary": official_summary,
                "summary_path": official_summary_path or str(merged_dir / "longmemeval_official_summary.json"),
            },
        })
    elif official_eval_requested:
        payload["official_eval"] = {
            "enabled": True,
            "returncode": official_eval.get("returncode") if isinstance(official_eval, dict) else None,
            "summary": {},
            "summary_path": official_summary_path,
        }
    else:
        payload["official_eval"] = {"enabled": False}
    (merged_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args, passthrough = parse_args()
    forbidden_passthrough = {
        "--dataset",
        "--format",
        "--out-dir",
        "--sample",
        "--count",
        "--questions",
        "--workspace",
        "--account",
        "--namespace",
    }
    conflicting = [item for item in passthrough if item in forbidden_passthrough]
    if conflicting:
        raise SystemExit(f"wrapper args must not be repeated in passthrough: {', '.join(conflicting)}")
    dataset_path = Path(args.dataset).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    shard_root = out_dir / "shards"
    merged_dir = out_dir / "merged"
    workspace_root = Path(args.workspace_root).expanduser().resolve() / safe_slug(out_dir.name, 48)

    jobs_and_plans = iter_jobs(dataset_path, args.sample)
    selected = apply_selection(
        jobs_and_plans,
        sample=args.sample,
        question_filter=selected_question_ids(args.questions),
        count=args.count,
        random_count=args.random_count,
        random_seed=args.random_seed,
    )
    if not selected:
        raise SystemExit("no LongMemEval jobs matched the requested selection")
    isolated_groups = isolated_job_groups(selected)
    max_concurrency = max(1, int(args.shards or 1))

    manifest = {
        "dataset": str(dataset_path),
        "selected_jobs": len(selected),
        "selected_question_ids": [str(job.question_id or "") for job, _plan in selected],
        "selected_sample_ids": [str(job.sample_id or "") for job, _plan in selected],
        "requested_parallelism": max_concurrency,
        "isolation_unit_count": len(isolated_groups),
        "passthrough_args": passthrough,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "parallel_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    env = dict(os.environ)
    process_specs: list[dict[str, Any]] = []
    shard_meta: list[dict[str, Any]] = []
    for shard_index, shard_items in enumerate(isolated_groups, 1):
        shard_dir = shard_root / f"shard_{shard_index:03d}"
        shard_out_dir = shard_dir / "run"
        shard_workspace = workspace_root / f"shard_{shard_index:03d}"
        shard_dir.mkdir(parents=True, exist_ok=True)
        shard_workspace.mkdir(parents=True, exist_ok=True)
        cmd = build_shard_command(
            args,
            passthrough,
            shard_index=shard_index,
            shard_jobs=shard_items,
            shard_out_dir=shard_out_dir,
            shard_workspace=shard_workspace,
        )
        questions = [str(job.question_id or "") for job, _plan in shard_items]
        shard_info = {
            "shard_index": shard_index,
            "jobs": len(shard_items),
            "questions": questions,
            "sample_ids": [str(job.sample_id or "") for job, _plan in shard_items],
            "out_dir": str(shard_out_dir),
            "workspace": str(shard_workspace),
            "cmd": cmd,
        }
        shard_meta.append(shard_info)
        if args.dry_run:
            continue
        process_specs.append(
            {
                "shard_index": shard_index,
                "shard_dir": shard_dir,
                "log_path": shard_dir / "runner.log",
                "cmd": cmd,
            }
        )

    manifest["shards"] = shard_meta
    (out_dir / "parallel_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return

    failures: list[dict[str, Any]] = []
    pending_specs = list(process_specs)
    active_processes: list[dict[str, Any]] = []
    while pending_specs or active_processes:
        while pending_specs and len(active_processes) < max_concurrency:
            spec = pending_specs.pop(0)
            handle = Path(spec["log_path"]).open("w", encoding="utf-8")
            proc = subprocess.Popen(
                spec["cmd"],
                cwd=str(ROOT),
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            active_processes.append(
                {
                    **spec,
                    "proc": proc,
                    "handle": handle,
                }
            )
            if args.stagger_s > 0:
                time.sleep(args.stagger_s)
        if not active_processes:
            continue
        next_active: list[dict[str, Any]] = []
        for item in active_processes:
            if item["proc"].poll() is None and shard_final_artifacts_ready(item["shard_dir"], passthrough):
                latest_artifact_mtime = shard_latest_artifact_mtime(item["shard_dir"], passthrough)
                artifact_age_s = time.time() - latest_artifact_mtime if latest_artifact_mtime else 0.0
                if artifact_age_s >= max(1.0, float(args.reap_completed_after_s or 0.0)):
                    if not item.get("term_sent_at"):
                        item["proc"].terminate()
                        item["term_sent_at"] = time.time()
                        item["reaped_completed"] = True
                    elif (time.time() - float(item["term_sent_at"])) >= max(1.0, float(args.reap_kill_after_s or 0.0)):
                        item["proc"].kill()
                        item["kill_sent_at"] = time.time()
                        item["reaped_completed"] = True
            code = item["proc"].poll()
            if code is None:
                next_active.append(item)
                continue
            item["handle"].close()
            if code != 0 and not (item.get("reaped_completed") and shard_final_artifacts_ready(item["shard_dir"], passthrough)):
                failures.append(
                    {
                        "shard_index": item["shard_index"],
                        "returncode": code,
                        "log_path": str(item["log_path"]),
                        "shard_dir": str(item["shard_dir"]),
                    }
                )
        active_processes = next_active
        if active_processes:
            time.sleep(1.0)

    shard_csvs = [meta_dir / "run" / "echomemory_generic_qa_results.csv" for meta_dir in shard_root.glob("shard_*")]
    merged_csv = merged_dir / "echomemory_generic_qa_results.csv"
    merged_rows = merge_csvs(sorted(shard_csvs), merged_csv)
    copied_recalls = copy_recall_logs([path / "run" for path in shard_root.glob("shard_*")], merged_dir)
    merged_summary = merged_summary_from_shards(merged_csv, shard_root)
    official_eval = run_official_eval_if_requested(
        passthrough,
        csv_path=merged_csv,
        dataset_path=dataset_path,
        merged_dir=merged_dir,
        python_bin=args.python_bin,
        env=env,
    )
    official_eval_requested = "--official-eval-after" in passthrough
    final_status = "LONGMEMEVAL_PARALLEL_FAILED" if failures else "LONGMEMEVAL_PARALLEL_DONE"
    write_merged_summary_artifacts(
        merged_dir=merged_dir,
        merged_csv=merged_csv,
        dataset_path=dataset_path,
        sample=args.sample,
        merged_rows=merged_rows,
        merged_summary=merged_summary,
        official_eval_requested=official_eval_requested,
        official_eval=official_eval,
        status=final_status,
    )
    result = {
        "status": "failed" if failures else "completed",
        "dataset": str(dataset_path),
        "out_dir": str(out_dir),
        "requested_parallelism": max_concurrency,
        "isolation_unit_count": len(isolated_groups),
        "merged_rows": merged_rows,
        "copied_recall_logs": copied_recalls,
        "merged_csv": str(merged_csv),
        "merged_summary": merged_summary,
        "official_eval": official_eval,
        "failures": failures,
    }
    (out_dir / "parallel_run_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

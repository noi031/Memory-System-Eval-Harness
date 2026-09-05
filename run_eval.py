#!/usr/bin/env python3
"""统一评测入口：benchmarks 与 dynamic 的注入→查询→评价流程。

用法::

    python run_eval.py --dataset locomo [--dataset-path <path>] [段参数...]
    python run_eval.py --dataset hotpotqa [--dataset-path <path>] [段参数...]
    python run_eval.py --dataset longmemeval [--dataset-path <path>] [段参数...]
    python run_eval.py --dataset dynamic [段参数...]

根级承载四个评测共用的骨架（CLI 分发、EvalConfig 构造、EvalRun 结果目录、
agent 插件加载、记忆后端健康检查、日志收尾），每个数据集只通过
``build_<name>_parser`` 声明自己的参数、通过 ``run_<name>`` 定义自己的
流程（数据集加载、注入、QA、判分），评测方法（judge/qa/import 等）留在
各自数据集目录下。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins import load_agent_plugin
from shared.eval_base import (
    EvalConfig,
    EvalRun,
    add_agent_plugin_args,
    add_eval_args,
    add_judge_args,
    build_config_from_args,
    resolve_llm_credentials,
    results_root_for,
    validate_eval_config,
)
from shared.dataset_io import resolve_dataset_path

DATASETS = ("locomo", "hotpotqa", "longmemeval", "dynamic")

_BENCHMARK_DIRS = {
    "locomo": Path(__file__).parent / "benchmarks" / "locomo",
    "hotpotqa": Path(__file__).parent / "benchmarks" / "hotpotqa",
    "longmemeval": Path(__file__).parent / "benchmarks" / "longmemeval",
    "dynamic": Path(__file__).parent / "dynamic",
}


# ------------------------------------------------------------------ #
#  公共骨架                                                            #
# ------------------------------------------------------------------ #

def _scan_dataset(argv: list[str]) -> str:
    """从 argv 提取 ``--dataset`` 选择器值。"""
    for index, item in enumerate(argv):
        if item == "--dataset" and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith("--dataset="):
            return item.split("=", 1)[1]
    raise ValueError("missing --dataset (locomo/hotpotqa/longmemeval/dynamic)")


def _build_parser_for(dataset: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="统一评测入口：benchmarks 与 dynamic 的注入→查询→评价流程",
    )
    parser.add_argument(
        "--dataset",
        required=True,
        choices=DATASETS,
        help="评测类型",
    )
    return _DATASET_PARSERS[dataset](parser)


def _start_run(dataset: str, args: argparse.Namespace) -> tuple[EvalRun, logging.Logger, EvalConfig]:
    """构造 EvalConfig 并创建 EvalRun 结果目录（公共前置）。"""
    if dataset == "dynamic":
        resolve_llm_credentials(args)
    config = build_config_from_args(args)
    if dataset != "dynamic":
        config.sample_filter = args.sample
        config.question_limit = args.questions
    validate_eval_config(config)
    run = EvalRun(
        benchmark_name=dataset,
        results_root=results_root_for(_BENCHMARK_DIRS[dataset], args.out_dir),
        config=config,
    )
    return run, run.logger, config


def _resume_qa_for(dataset: str, args: argparse.Namespace) -> str:
    """插件读 ``config["resume_qa"]`` 跳过身份隔离，各段语义不同。"""
    if dataset == "locomo":
        return str(args.resume or args.resume_qa or args.reuse_memory_from)
    if dataset == "hotpotqa":
        return str(args.resume or args.reuse_memory_from)
    if dataset == "longmemeval":
        return str(args.resume)
    return ""


def _load_agent_plugin(
    args: argparse.Namespace,
    run: EvalRun,
    dataset: str,
    resume_qa: str,
) -> Any:
    agent_config = {
        **vars(args),
        "benchmark_name": dataset,
        "run_id": run.result_dir.name,
    }
    if resume_qa:
        agent_config["resume_qa"] = resume_qa
    return load_agent_plugin(args.agent_plugin, agent_config)


def main() -> None:
    try:
        dataset = _scan_dataset(sys.argv[1:])
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    if dataset not in DATASETS:
        print(
            f"error: argument --dataset: invalid choice: {dataset!r} "
            f"(choose from {', '.join(DATASETS)})",
            file=sys.stderr,
        )
        raise SystemExit(2)
    parser = _build_parser_for(dataset)
    args = parser.parse_args()

    run, log, config = _start_run(dataset, args)

    # longmemeval parallel 是纯子进程编排，不加载插件、不需要记忆后端在线
    parallel_only = (
        dataset == "longmemeval" and getattr(args, "parallel_shards", 1) > 1
    )
    agent_plugin = None
    if not parallel_only:
        agent_plugin = _load_agent_plugin(
            args,
            run,
            dataset,
            _resume_qa_for(dataset, args),
        )
        try:
            agent_plugin.memory_client.health()
        except Exception as exc:
            log.error("memory backend health check failed: %s", exc)
            agent_plugin.teardown()
            sys.exit(1)
        log.info("agent plugin loaded: %s", args.agent_plugin)

    try:
        _RUNNERS[dataset](args, run, config, agent_plugin)
    finally:
        if agent_plugin is not None:
            log_json = agent_plugin.getlog()
            (run.result_dir / "backend_logs.json").write_text(
                log_json, encoding="utf-8"
            )
            agent_plugin.teardown()


# ------------------------------------------------------------------ #
#  LoCoMo 段                                                          #
# ------------------------------------------------------------------ #

def _redact_secret(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    return text[:4] + "***" + text[-4:] if len(text) > 8 else "***"


def _build_agent_options(args, config) -> dict[str, Any]:
    """Capture run-affecting plugin options for reproducible QA reports."""
    options: dict[str, Any] = {
        "agent_plugin": getattr(args, "agent_plugin", ""),
        "qa_profile": getattr(args, "qa_profile", None) or "",
        "tool_calling": bool(
            getattr(args, "tools", getattr(args, "tool_calling", True))
        ),
        "initial_retrieval_protocol": "mcp",
        "search_in_tools": bool(getattr(args, "search_in_tools", False)),
        "top_k": config.top_k,
        "memory_budget_chars": config.memory_budget_chars,
        "question_timeout_s": config.question_timeout_s,
        "llm_temperature": config.llm_temperature,
        "llm_timeout_s": config.llm_timeout_s,
        "llm_retries": config.llm_retries,
        "qa_concurrency": config.concurrency,
        "judge_concurrency": getattr(args, "judge_concurrency", None),
    }
    for name in (
        "mcp_url",
        "mcp_max_iterations",
        "mcp_read_mode",
        "user_memory_budget_chars",
        "agent_memory_budget_chars",
    ):
        if hasattr(args, name):
            options[name] = getattr(args, name)
    for name in ("mcp_auth_key", "echomem_auth_key"):
        if hasattr(args, name):
            value = getattr(args, name)
            options[f"{name}_configured"] = bool(value)
            options[f"{name}_redacted"] = _redact_secret(value)
    return options


def _write_agent_options_to_config(result_dir: Path, options: dict[str, Any]) -> None:
    config_path = result_dir / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["agent_options"] = options
    config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_locomo_parser(
    parser: argparse.ArgumentParser | None = None,
) -> argparse.ArgumentParser:
    from benchmarks.locomo.profiles import (
        VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
        VIKINGBOAT_0411_PROFILE,
    )

    if parser is None:
        parser = argparse.ArgumentParser(description="LoCoMo benchmark evaluation")
    parser.add_argument("--dataset-path", default="", help="LoCoMo JSON 数据集路径 (不指定则自动查找或下载)")
    parser.add_argument("--sample", default="all", help="筛选 sample (all 或 sample_id)")
    parser.add_argument("--questions", type=int, default=0, help="限制 QA 数量 (0=all)")
    parser.add_argument(
        "--question-ids",
        default="",
        help="Comma-separated LoCoMo question ids; applied before --questions",
    )
    parser.add_argument(
        "--session-mode",
        choices=["auto", "locomo", "single"],
        default="auto",
        help="auto=单 sample 按原始 session, 多 sample 各自合并; locomo=原始 session; single=合并",
    )
    parser.add_argument("--max-sessions", type=int, default=0, help="每个 sample 最多导入多少个原始 session (0=全部)")
    # 共享参数
    add_agent_plugin_args(parser, default_plugin="vikingbot")
    add_eval_args(parser)
    qa = parser.add_argument_group("LoCoMo QA")
    qa.add_argument(
        "--qa-profile",
        choices=[
            VIKINGBOAT_0411_PROFILE,
            VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
        ],
        default=None,
        help=(
            "LoCoMo QA executor; vikingboat0411 adapts the v0.4.11 "
            "VikingBot agent behavior to EchoMemory tools; "
            "vikingboat0411-natural-no-tools keeps only complete initially "
            "retrieved memory excerpts"
        ),
    )
    qa.add_argument(
        "--qa-prompt-file",
        default="",
        help=(
            "Append a local UTF-8 text file to the selected profile's system "
            "prompt; the file content is not copied into repository metadata"
        ),
    )
    qa.add_argument(
        "--checkpoint-interval",
        type=int,
        default=10,
        help="Persist partial QA CSV after every N completed questions (0=off)",
    )
    qa.add_argument(
        "--resume",
        default="",
        help=(
            "Resume a prior locomo run directory or qa_results CSV: reuse the "
            "prior identity, skip already-injected import batches, reuse "
            "healthy QA answers, and reuse judge verdicts; only run the "
            "missing/unhealthy remainder. Metrics (tokens/latency/accuracy) "
            "are computed over the merged whole run."
        ),
    )
    qa.add_argument(
        "--resume-qa",
        default="",
        help=(
            "Resume QA from a prior LoCoMo run directory or qa_results CSV; "
            "reuses the prior identity and skips already-injected sessions "
            "(superseded by --resume)"
        ),
    )
    qa.add_argument(
        "--reuse-memory-from",
        default="",
        help=(
            "Reuse the identity and completed memory imports from a prior run, "
            "but execute a fresh QA/Judge pass with the current MCP mode "
            "(superseded by --resume)"
        ),
    )
    # judge 参数 (三个基础参数由共享 helper 声明, locomo 额外参数在此声明)
    add_judge_args(parser)
    g = parser.add_argument_group("Judge")
    g.add_argument(
        "--judge-concurrency",
        type=int,
        default=int(os.getenv("JUDGE_CONCURRENCY", "4")),
        help="Maximum concurrent Judge requests",
    )
    g.add_argument(
        "--judge-checkpoint-interval",
        type=int,
        default=10,
        help="Persist partial Judge CSV after every N completed questions (0=off)",
    )
    g.add_argument(
        "--resume-judge",
        default="",
        help=(
            "Resume matching Judge rows from a prior LoCoMo run directory "
            "or judge_results CSV (superseded by --resume)"
        ),
    )
    return parser


def load_qa_prompt_append(path_value: str) -> tuple[str, str, str]:
    value = str(path_value or "").strip()
    if not value:
        return "", "", ""
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"QA prompt file does not exist: {path}")
    prompt = path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"QA prompt file is empty: {path}")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return prompt, digest, path.name


def _load_prior_import_rows(resume_source: str) -> list[dict]:
    """Load import_results.csv from a prior run directory for resume."""
    import csv

    source = Path(resume_source)
    csv_path = (
        source / "import_results.csv"
        if source.is_dir()
        else source.parent / "import_results.csv"
    )
    if not csv_path.is_file():
        return []
    with csv_path.open("r", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def run_locomo(
    args: argparse.Namespace,
    run: EvalRun,
    config: EvalConfig,
    agent_plugin: Any,
) -> None:
    from benchmarks.locomo.blackbox import write_artifacts as write_blackbox_artifacts
    from benchmarks.locomo.dataset import load_dataset
    from benchmarks.locomo.diagnosis import diagnose_run
    from benchmarks.locomo.import_memory import (
        ImportOptions,
        import_locomo_memory,
        resolve_session_mode,
    )
    from benchmarks.locomo.judge import (
        LOCOMO_JUDGE_SYSTEM,
        LOCOMO_JUDGE_TEMPLATE,
        judge_locomo_results,
    )
    from benchmarks.locomo.memory_scope import SessionPrefixMemoryClient
    from benchmarks.locomo.provenance import (
        inspect_memory_provenance,
        write_memory_provenance,
    )
    from benchmarks.locomo.qa import (
        QAOptions,
        build_qa_tasks,
        run_locomo_qa,
        write_tool_audits,
    )
    from benchmarks.locomo.reporting import build_summary
    from benchmarks.locomo.resume import (
        build_judge_resume_manifest,
        build_qa_resume_manifest,
        copy_resume_traces,
        find_judge_resume_csv,
        find_qa_resume_csv,
        load_judge_resume_state,
        load_qa_resume_state,
        restore_resume_traces,
        write_judge_resume_manifest,
        write_qa_resume_manifest,
    )
    from benchmarks.locomo.selection import parse_question_ids, select_questions
    from shared.import_guard import require_complete_imports
    from shared.llm_client import LLMClient
    from shared.resume_identity import apply_resume_memory_identity

    log = run.logger

    if getattr(args, "max_sessions", 0) < 0:
        raise ValueError("max sessions must be >= 0")
    if args.checkpoint_interval < 0:
        raise ValueError("checkpoint interval must be >= 0")
    if args.judge_concurrency < 1:
        raise ValueError("judge concurrency must be >= 1")
    if args.judge_checkpoint_interval < 0:
        raise ValueError("judge checkpoint interval must be >= 0")

    (
        system_prompt_append,
        system_prompt_append_sha256,
        system_prompt_append_source,
    ) = load_qa_prompt_append(args.qa_prompt_file)

    dataset_path = resolve_dataset_path("locomo", args.dataset_path)
    config.dataset_path = dataset_path
    question_ids = parse_question_ids(args.question_ids)

    agent_options = _build_agent_options(args, config)
    _write_agent_options_to_config(run.result_dir, agent_options)

    # 加载数据集
    log.info("加载 LoCoMo 数据集: %s", dataset_path)
    jobs, plans = load_dataset(dataset_path, sample_filter=args.sample)
    log.info("共 %d 个 sample, %d 个 QA 问题", len(plans), len(jobs))
    session_mode = resolve_session_mode(args.session_mode, len(plans))
    log.info("LoCoMo session mode: %s", session_mode)

    jobs = select_questions(
        jobs,
        question_ids=question_ids,
        limit=config.question_limit,
    )
    if question_ids:
        log.info("按 question id 选择 %d 题", len(jobs))
    elif config.question_limit > 0:
        log.info("限制 QA 数量为 %d", len(jobs))
    if not plans or not jobs:
        message = "dataset/sample filter produced no LoCoMo samples or questions"
        run.save_summary({
            "status": "failed",
            "phase": "dataset",
            "dataset": dataset_path,
            "sample_filter": args.sample,
            "error": message,
        })
        raise ValueError(message)

    echomem = agent_plugin.memory_client
    raw_echomem = echomem
    memory_reuse_source = args.resume or args.resume_qa or args.reuse_memory_from
    if memory_reuse_source:
        apply_resume_memory_identity(echomem, memory_reuse_source, log)
    evaluation_identity = {
        "mode": (
            "resumed"
            if (args.resume or args.resume_qa)
            else "reused"
            if args.reuse_memory_from
            else "fresh"
        ),
        "tenant_id": echomem.account,
        "user_id": echomem.user_id,
        "auth_key": echomem.auth_key,
    }
    log.info(
        "Memory identity: %s tenant=%s user=%s",
        evaluation_identity.get("mode", "none"),
        evaluation_identity.get("tenant_id", ""),
        evaluation_identity.get("user_id", ""),
    )
    memory_session_prefix = ""
    if memory_reuse_source:
        sample = str(args.sample or "").strip()
        if re.fullmatch(r"conv-\d+", sample):
            memory_session_prefix = f"echomem-locomo-{sample}-"
    if memory_session_prefix and not args.reuse_memory_from:
        echomem = SessionPrefixMemoryClient(
            echomem,
            memory_session_prefix,
        )
        log.info(
            "Memory session scope: prefix=%s",
            memory_session_prefix,
        )

    # 尽早写 resume manifest（含身份）：即使导入中断，目录也留有身份供后续 --resume 复用。
    qa_options = QAOptions(
        profile=agent_plugin.qa_profile,
        checkpoint_interval=args.checkpoint_interval,
        top_k=config.top_k,
        memory_budget_chars=config.memory_budget_chars,
        tools_enabled=bool(
            getattr(args, "tools", getattr(args, "tool_calling", True))
        ),
        system_prompt_append=system_prompt_append,
        system_prompt_append_sha256=system_prompt_append_sha256,
        system_prompt_append_source=system_prompt_append_source,
        agent_options=agent_options,
    )
    qa_resume_manifest = build_qa_resume_manifest(
        dataset_path=dataset_path,
        sample_filter=args.sample,
        session_mode=session_mode,
        config=config,
        options=qa_options,
        memory_identity={
            "account": echomem.account,
            "user_id": echomem.user_id,
            "agent_id": echomem.agent_id,
            "auth_key": echomem.auth_key,
        },
    )
    write_qa_resume_manifest(run.result_dir, qa_resume_manifest)

    # -- 阶段 1: 导入记忆 --
    log.info("=" * 60)
    prior_import_rows = (
        _load_prior_import_rows(memory_reuse_source)
        if memory_reuse_source
        else None
    )
    if prior_import_rows is not None:
        log.info("阶段 1: 导入记忆 (resume, 跳过已完成 batches)")
    else:
        log.info("阶段 1: 导入记忆 (共 %d 个 sample)", len(plans))

    import_report = import_locomo_memory(
        plans,
        echomem,
        config,
        ImportOptions(
            session_mode=session_mode,
            max_sessions=args.max_sessions,
            resume_qa=bool(memory_reuse_source),
            sample_filter=args.sample,
            prior_import_rows=prior_import_rows,
        ),
        run.result_dir,
        log,
    )
    log.info(
        "导入完成: %d/%d 成功",
        import_report.completed,
        import_report.total,
    )
    try:
        require_complete_imports(
            import_report.rows,
            allow_incomplete=args.allow_diagnostics,
        )
    except RuntimeError as exc:
        run.save_summary({
            "status": "failed",
            "phase": "import",
            "dataset": dataset_path,
            "sample_filter": args.sample,
            "import_ok": import_report.completed,
            "import_total": import_report.total,
            "error": str(exc),
        })
        log.error("%s", exc)
        raise SystemExit(2) from exc

    memory_provenance = inspect_memory_provenance(
        raw_echomem,
        dataset_path=dataset_path,
        plans=plans,
        session_mode=session_mode,
        max_sessions=args.max_sessions,
    )
    memory_provenance["session_prefix"] = memory_session_prefix
    provenance_path = write_memory_provenance(
        run.result_dir,
        memory_provenance,
    )
    log.info(
        "Memory provenance: status=%s sessions=%d/%d artifact=%s",
        memory_provenance["status"],
        memory_provenance["actual_session_count"],
        memory_provenance["expected_session_count"],
        provenance_path,
    )
    if (
        memory_provenance["status"] != "matched"
        and not args.allow_diagnostics
    ):
        message = (
            "EchoMemory provenance mismatch: expected "
            f"{memory_provenance['expected_session_count']} sessions but found "
            f"{memory_provenance['actual_session_count']}; use "
            "--allow-diagnostics only for diagnostics"
        )
        run.save_summary({
            "status": "failed",
            "phase": "memory_provenance",
            "dataset": dataset_path,
            "sample_filter": args.sample,
            "memory_provenance": memory_provenance,
            "error": message,
        })
        log.error("%s", message)
        raise SystemExit(2)

    # -- 阶段 2: 逐题 QA --
    log.info("=" * 60)
    log.info("阶段 2: QA (共 %d 题, 并发=%d)", len(jobs), config.concurrency)

    qa_tasks = build_qa_tasks(
        jobs,
        import_report.sample_to_session_ids,
        config,
        qa_options,
        agent_id=echomem.agent_id,
    )
    qa_resume_state = None
    if args.resume or args.resume_qa:
        qa_resume_source = args.resume or args.resume_qa
        prior_qa_csv = find_qa_resume_csv(qa_resume_source)
        if prior_qa_csv is None:
            log.info(
                "QA resume: no prior QA results under %s (import-only run), "
                "running full QA",
                qa_resume_source,
            )
        else:
            qa_resume_state = load_qa_resume_state(
                qa_resume_source,
                tasks=qa_tasks,
                expected_manifest=qa_resume_manifest,
            )
            copied_traces = copy_resume_traces(
                qa_resume_state,
                run.result_dir,
            )
            log.info(
                "QA resume: source=%s reused=%d discarded=%d traces=%d",
                qa_resume_state.source_csv,
                len(qa_resume_state.results),
                len(qa_resume_state.discarded_question_ids),
                copied_traces,
            )
    qa_results = run_locomo_qa(
        qa_tasks,
        agent_plugin,
        config,
        qa_options,
        run.result_dir,
        log,
        existing_results=(
            qa_resume_state.results if qa_resume_state else None
        ),
    )
    if qa_resume_state:
        restored = restore_resume_traces(qa_results, run.result_dir)
        log.info("QA resume: restored %d traces from source run", restored)
        # 全量写 tool audits：让 resume 目录与从 0 运行的目录等价（含复用题的审计）
        write_tool_audits(run.result_dir, qa_results)

    # -- 阶段 3: LLM Judge --
    log.info("=" * 60)
    log.info("阶段 3: Judge (共 %d 题)", len(qa_results))

    judge_llm = LLMClient(
        base_url=args.judge_base_url or config.llm_base_url,
        api_key=args.judge_api_key or config.llm_api_key,
        model=args.judge_model or config.llm_model,
        temperature=0.0,
        max_tokens=512,
        timeout_s=config.llm_timeout_s,
        max_retries=config.llm_retries,
    )
    judge_resume_manifest = build_judge_resume_manifest(
        base_url=judge_llm.base_url,
        model=judge_llm.model,
        system_prompt=LOCOMO_JUDGE_SYSTEM,
        prompt_template=LOCOMO_JUDGE_TEMPLATE,
    )
    write_judge_resume_manifest(
        run.result_dir,
        judge_resume_manifest,
    )
    judge_resume_state = None
    if args.resume or args.resume_judge:
        judge_resume_source = args.resume or args.resume_judge
        prior_judge_csv = find_judge_resume_csv(judge_resume_source)
        if prior_judge_csv is None:
            log.info(
                "Judge resume: no prior judge results under %s, running full judge",
                judge_resume_source,
            )
        else:
            judge_resume_state = load_judge_resume_state(
                judge_resume_source,
                expected_manifest=judge_resume_manifest,
            )
            log.info(
                "Judge resume: source=%s candidate_rows=%d",
                judge_resume_state.source_csv,
                len(judge_resume_state.rows),
            )

    judge_report = judge_locomo_results(
        qa_results,
        judge_llm,
        run.result_dir,
        log,
        concurrency=args.judge_concurrency,
        checkpoint_interval=args.judge_checkpoint_interval,
        existing_rows=(
            judge_resume_state.rows if judge_resume_state else None
        ),
    )
    log.info(
        "Judge 完成: %d CORRECT, %d WRONG, accuracy=%.2f%%",
        judge_report.correct,
        judge_report.wrong,
        judge_report.accuracy * 100,
    )
    diagnosis = diagnose_run(
        run.result_dir / "qa_results.csv",
        run.result_dir / "judge_results.csv",
        Path(dataset_path),
        args.sample,
        run.result_dir,
    )
    log.info(
        "诊断完成: failures=%d retryable=%d",
        diagnosis["failed"],
        len(diagnosis["retryable_question_ids"]),
    )

    # 保存 summary
    summary = build_summary(
        dataset_path=dataset_path,
        sample_filter=args.sample,
        total_samples=len(plans),
        total_questions=len(jobs),
        import_report=import_report,
        resume_qa=bool(memory_reuse_source),
        qa_results=qa_results,
        judge_report=judge_report,
        qa_options=qa_options,
        session_mode=session_mode,
        evaluation_identity=evaluation_identity,
    )
    summary["diagnosis"] = {
        "path": str(run.result_dir / "diagnosis.json"),
        "retrieval_traces": str(run.result_dir / "retrieval_traces.jsonl"),
        "retrieval_coverage": diagnosis["retrieval_coverage"],
        "failure_breakdown": diagnosis["failure_breakdown"],
        "retryable_question_ids": diagnosis["retryable_question_ids"],
        "missing_question_ids": diagnosis["missing_question_ids"],
    }
    summary["qa_parallelism"] = config.concurrency
    summary["resume"] = {
        "enabled": bool(memory_reuse_source),
        "source": str(memory_reuse_source or ""),
        "mode": evaluation_identity.get("mode"),
        "reused_qa": (
            len(qa_resume_state.results) if qa_resume_state else 0
        ),
        "discarded_qa": (
            qa_resume_state.discarded_question_ids
            if qa_resume_state
            else []
        ),
        "reused_import_batches": sum(
            1
            for row in import_report.rows
            if str(row.get("status") or "").strip().lower() == "reused"
        ),
        "reused_judge_rows": (
            len(judge_resume_state.rows) if judge_resume_state else 0
        ),
    }
    summary["qa_resume"] = {
        "enabled": bool(qa_resume_state),
        "source": (
            str(qa_resume_state.source_csv) if qa_resume_state else ""
        ),
        "reused": (
            len(qa_resume_state.results) if qa_resume_state else 0
        ),
        "discarded": (
            qa_resume_state.discarded_question_ids
            if qa_resume_state
            else []
        ),
    }
    summary["memory_reuse"] = {
        "enabled": bool(args.reuse_memory_from),
        "source": str(args.reuse_memory_from or ""),
    }
    summary["judge_parallelism"] = args.judge_concurrency
    summary["judge_checkpoint_interval"] = args.judge_checkpoint_interval
    summary["judge_resume"] = {
        "enabled": bool(judge_resume_state),
        "source": (
            str(judge_resume_state.source_csv)
            if judge_resume_state
            else ""
        ),
        "candidate_rows": (
            len(judge_resume_state.rows) if judge_resume_state else 0
        ),
    }
    summary["memory_provenance"] = {
        **memory_provenance,
        "artifact_path": str(provenance_path),
    }
    summary["run_started_at"] = run.started_at.isoformat()
    summary["run_finished_at"] = run.finished_at_iso()
    if qa_resume_state:
        # 续跑延续源运行的原始启动时间：批次耗时/吞吐按「原启动 → 本次结束」计算。
        source_summary_path = Path(qa_resume_state.source_csv).parent / "summary.json"
        if source_summary_path.is_file():
            try:
                with open(source_summary_path, encoding="utf-8") as f:
                    source_summary = json.load(f)
            except (OSError, ValueError) as exc:
                log.warning("读取续跑源 summary 失败: %s", exc)
                source_summary = {}
            source_started_at = source_summary.get("run_started_at")
            if source_started_at:
                summary["qa_resume"]["original_started_at"] = source_started_at
                summary["run_started_at"] = source_started_at
    blackbox = write_blackbox_artifacts(
        qa_rows=[result.to_csv_row() for result in qa_results],
        judge_rows=judge_report.rows,
        import_rows=import_report.rows,
        run_observation={
            "qa_parallelism": config.concurrency,
            "run_started_at": summary["run_started_at"],
            "run_finished_at": summary["run_finished_at"],
        },
        output_dir=run.result_dir,
    )
    summary["strict_blackbox"] = blackbox
    summary["strict_blackbox_metrics_path"] = blackbox["artifact_path"]
    summary["strict_blackbox_report_path"] = blackbox["report_path"]
    run.save_summary(summary)

    if summary["status"] != "completed":
        log.error("评测包含运行错误，结果不能作为正式分数")
        raise SystemExit(2)

    log.info("=" * 60)
    log.info("评测完成! 结果目录: %s", run.result_dir)
    log.info(
        "Accuracy: %.2f%% (%d/%d)",
        judge_report.accuracy * 100,
        judge_report.correct,
        len(judge_report.rows),
    )


# ------------------------------------------------------------------ #
#  HotpotQA 段                                                        #
# ------------------------------------------------------------------ #

def build_hotpotqa_parser(
    parser: argparse.ArgumentParser | None = None,
) -> argparse.ArgumentParser:
    if parser is None:
        parser = argparse.ArgumentParser(description="HotpotQA benchmark evaluation")
    parser.add_argument("--dataset-path", default="", help="HotpotQA JSON 数据集路径 (不指定则自动查找或下载)")
    parser.add_argument("--sample", default="all", help="筛选 sample (all 或 sample index/id)")
    parser.add_argument("--questions", type=int, default=0, help="限制 QA 数量 (0=all)")
    parser.add_argument(
        "--question-ids",
        default="",
        help="Comma-separated question/native/sample ids",
    )
    parser.add_argument("--import-mode", default="per_question",
                        choices=["per_question", "global", "documents"],
                        help="导入模式: per_question=每题各自导入; global=合并共享 session; documents=文档资源语料(RAG)")
    parser.add_argument(
        "--checkpoint-interval",
        type=int,
        default=10,
        help="Persist partial QA CSV after every N completed questions (0=off)",
    )
    parser.add_argument(
        "--resume",
        default="",
        help=(
            "Resume a prior hotpotqa run directory or qa_results CSV: reuse "
            "the prior identity, skip already-completed import batches, and "
            "reuse healthy QA answers; only run the missing/unhealthy "
            "remainder. Metrics (tokens/latency/F1) are computed over the "
            "merged whole run."
        ),
    )
    parser.add_argument(
        "--reuse-memory-from",
        default="",
        help=(
            "Reuse the identity and completed memory imports from a prior "
            "run, but execute a fresh QA pass (superseded by --resume)"
        ),
    )
    add_agent_plugin_args(parser, default_plugin="vikingbot")
    add_eval_args(parser)
    return parser


def run_hotpotqa(
    args: argparse.Namespace,
    run: EvalRun,
    config: EvalConfig,
    agent_plugin: Any,
) -> None:
    from benchmarks.hotpotqa.dataset import load_dataset
    from benchmarks.hotpotqa.diagnosis import diagnose_run
    from benchmarks.hotpotqa.evaluate import evaluate_hotpotqa, load_references
    from benchmarks.hotpotqa.import_memory import import_hotpotqa_memory
    from benchmarks.hotpotqa.qa import (
        build_qa_tasks,
        run_hotpotqa_qa,
        write_tool_audits,
    )
    from benchmarks.hotpotqa.reporting import build_summary
    from benchmarks.hotpotqa.resume import (
        build_resume_manifest,
        copy_resume_traces,
        load_resume_manifest,
        restore_resume_traces,
        validate_resume_manifest,
        write_resume_manifest,
    )
    from benchmarks.hotpotqa.selection import (
        parse_question_ids,
        select_jobs_and_plans,
    )
    from shared.import_guard import require_complete_imports
    from shared.resume_identity import apply_resume_memory_identity
    from shared.resume_qa import (
        find_qa_resume_csv,
        load_prior_import_rows,
        load_resume_qa_results,
    )

    log = run.logger

    dataset_path = resolve_dataset_path("hotpotqa", args.dataset_path)
    config.dataset_path = dataset_path
    question_ids = parse_question_ids(args.question_ids)

    # 加载数据集
    log.info("加载 HotpotQA 数据集: %s", dataset_path)
    jobs, plans = load_dataset(dataset_path, sample_filter=args.sample)
    log.info("共 %d 个问题", len(jobs))

    jobs, plans = select_jobs_and_plans(
        jobs,
        plans,
        question_ids=question_ids,
        limit=config.question_limit,
    )
    if question_ids:
        log.info("按 question id 选择 %d 题", len(jobs))
    elif config.question_limit > 0:
        log.info("限制 QA 数量为 %d", len(jobs))
    if not jobs or not plans:
        message = "dataset/sample filter produced no HotpotQA questions"
        run.save_summary({
            "status": "failed",
            "phase": "dataset",
            "dataset": dataset_path,
            "sample_filter": args.sample,
            "error": message,
        })
        raise ValueError(message)

    echomem = agent_plugin.memory_client
    reuse_source = args.resume or args.reuse_memory_from
    if reuse_source:
        apply_resume_memory_identity(echomem, reuse_source, log)
    evaluation_identity = {
        "mode": (
            "resumed"
            if args.resume
            else "reused"
            if args.reuse_memory_from
            else "fresh"
        ),
        "tenant_id": echomem.account,
        "user_id": echomem.user_id,
    }
    log.info(
        "Memory identity: %s tenant=%s user=%s",
        evaluation_identity.get("mode", "none"),
        evaluation_identity.get("tenant_id", ""),
        evaluation_identity.get("user_id", ""),
    )

    # 尽早写 resume manifest（含身份）：即使导入中断，目录也留有身份供后续 --resume 复用。
    resume_manifest = build_resume_manifest(
        dataset_path=dataset_path,
        import_mode=args.import_mode,
        config=config,
        memory_identity={
            "account": echomem.account,
            "user_id": echomem.user_id,
            "auth_key": echomem.auth_key,
        },
    )
    write_resume_manifest(run.result_dir, resume_manifest)

    # -- 阶段 1: 导入记忆 --
    log.info("=" * 60)
    log.info("阶段 1: 导入记忆 (模式=%s)", args.import_mode)
    prior_import_rows = (
        load_prior_import_rows(reuse_source) if reuse_source else None
    )
    if prior_import_rows is not None:
        log.info("阶段 1: 导入记忆 (resume, 跳过已完成 batches)")
    import_report = import_hotpotqa_memory(
        jobs,
        plans,
        echomem,
        config,
        run.result_dir,
        log,
        import_mode=args.import_mode,
        prior_import_rows=prior_import_rows,
        reuse_memory=bool(args.reuse_memory_from),
    )
    log.info(
        "导入完成: %d/%d 成功",
        import_report.completed,
        import_report.total,
    )
    if args.import_mode == "documents":
        if not hasattr(agent_plugin, "path_title_map"):
            log.error(
                "documents 模式需要支持文档资源检索的插件（提供 path_title_map，"
                "如 vikingbot 或 echomem_mcp）；当前插件 %s 不支持",
                args.agent_plugin,
            )
            raise SystemExit(2)
        agent_plugin.path_title_map = import_report.document_path_titles
        log.info(
            "文档语料注入完成: %d 篇唯一文档, path→title 映射 %d 条",
            import_report.rows[0].get("messages", 0) if import_report.rows else 0,
            len(import_report.document_path_titles),
        )
    try:
        require_complete_imports(
            import_report.rows,
            allow_incomplete=args.allow_diagnostics,
        )
    except RuntimeError as exc:
        run.save_summary({
            "status": "failed",
            "phase": "import",
            "dataset": dataset_path,
            "import_ok": import_report.completed,
            "import_total": import_report.total,
            "error": str(exc),
        })
        log.error("%s", exc)
        raise SystemExit(2) from exc

    # -- 阶段 2: 逐题 QA --
    log.info("=" * 60)
    log.info("阶段 2: QA (共 %d 题, 并发=%d)", len(jobs), config.concurrency)

    qa_tasks = build_qa_tasks(
        jobs,
        import_report.question_to_session,
        config,
        agent_id=echomem.agent_id,
    )
    qa_resume_state = None
    if args.resume:
        prior_qa_csv = find_qa_resume_csv(args.resume)
        if prior_qa_csv is None:
            log.info(
                "QA resume: no prior QA results under %s (import-only run), "
                "running full QA",
                args.resume,
            )
        else:
            source_dir = Path(args.resume).expanduser().resolve()
            validate_resume_manifest(
                resume_manifest,
                load_resume_manifest(
                    source_dir if source_dir.is_dir() else source_dir.parent
                ),
            )
            qa_resume_state = load_resume_qa_results(args.resume, qa_tasks)
            copied_traces = copy_resume_traces(
                qa_resume_state,
                run.result_dir,
            )
            log.info(
                "QA resume: source=%s reused=%d discarded=%d traces=%d",
                qa_resume_state.source_csv,
                len(qa_resume_state.results),
                len(qa_resume_state.discarded_question_ids),
                copied_traces,
            )
    qa_results = run_hotpotqa_qa(
        qa_tasks,
        agent_plugin,
        config,
        run.result_dir,
        log,
        existing_results=(
            qa_resume_state.results if qa_resume_state else None
        ),
        checkpoint_interval=args.checkpoint_interval,
    )
    if qa_resume_state:
        restored = restore_resume_traces(qa_results, run.result_dir)
        log.info("QA resume: restored %d traces from source run", restored)
        # 全量写 tool audits：让 resume 目录与从 0 运行的目录等价（含复用题的审计）
        write_tool_audits(run.result_dir, qa_results)

    # -- 阶段 3: 官方 answer/supporting-fact/joint 评测 --
    log.info("=" * 60)
    log.info("阶段 3: HotpotQA 官方指标")
    references = load_references(Path(dataset_path))
    evaluation_report = evaluate_hotpotqa(
        qa_results,
        references,
        run.result_dir,
    )
    log.info(
        "评测完成: answer F1=%.4f EM=%.4f, support F1=%.4f EM=%.4f, "
        "joint F1=%.4f EM=%.4f",
        evaluation_report.answer_f1,
        evaluation_report.answer_em,
        evaluation_report.supporting_facts_f1,
        evaluation_report.supporting_facts_em,
        evaluation_report.joint_f1,
        evaluation_report.joint_em,
    )

    diagnosis = diagnose_run(
        run.result_dir / "qa_results.csv",
        run.result_dir / "eval_results.csv",
        Path(dataset_path),
        args.sample,
        config.question_limit,
        run.result_dir,
    )
    log.info(
        "诊断完成: failures=%d retryable=%d",
        diagnosis["failed"],
        len(diagnosis["retryable_question_ids"]),
    )

    summary = build_summary(
        dataset_path=dataset_path,
        import_mode=args.import_mode,
        jobs=jobs,
        import_report=import_report,
        qa_results=qa_results,
        evaluation_report=evaluation_report,
        evaluation_identity=evaluation_identity,
        resumed=bool(reuse_source),
    )
    summary["memory_reuse"] = {
        "enabled": bool(args.reuse_memory_from),
        "source": str(args.reuse_memory_from or ""),
    }
    summary["resume"] = {
        "enabled": bool(args.resume),
        "source": str(args.resume or ""),
        "mode": evaluation_identity.get("mode"),
        "reused_qa": (
            len(qa_resume_state.results) if qa_resume_state else 0
        ),
        "discarded_qa": (
            qa_resume_state.discarded_question_ids
            if qa_resume_state
            else []
        ),
        "reused_import_batches": sum(
            1
            for row in import_report.rows
            if str(row.get("status") or "").strip().lower() == "reused"
        ),
    }
    if args.resume:
        # 续跑延续源运行的原始启动时间：批次耗时/吞吐按「原启动 → 本次结束」计算。
        source_summary_path = Path(args.resume) / "summary.json"
        if source_summary_path.is_file():
            try:
                with open(source_summary_path, encoding="utf-8") as f:
                    source_summary = json.load(f)
            except (OSError, ValueError) as exc:
                log.warning("读取续跑源 summary 失败: %s", exc)
                source_summary = {}
            source_started_at = source_summary.get("run_started_at")
            if source_started_at:
                summary["resume"]["original_started_at"] = source_started_at
                summary["run_started_at"] = source_started_at
    summary["diagnosis"] = {
        "path": str(run.result_dir / "diagnosis.json"),
        "retrieval_traces": str(run.result_dir / "retrieval_traces.jsonl"),
        "retrieval_coverage": diagnosis["retrieval_coverage"],
        "failure_breakdown": diagnosis["failure_breakdown"],
        "retryable_question_ids": diagnosis["retryable_question_ids"],
        "missing_question_ids": diagnosis["missing_question_ids"],
    }
    run.save_summary(summary)

    if summary["status"] != "completed":
        log.error("评测包含运行错误，结果不能作为正式分数")
        raise SystemExit(2)

    log.info("=" * 60)
    log.info("评测完成! 结果目录: %s", run.result_dir)
    log.info(
        "answer_F1=%.4f answer_EM=%.4f joint_F1=%.4f joint_EM=%.4f "
        "(%d questions)",
        evaluation_report.answer_f1,
        evaluation_report.answer_em,
        evaluation_report.joint_f1,
        evaluation_report.joint_em,
        len(qa_results),
    )


# ------------------------------------------------------------------ #
#  LongMemEval 段                                                     #
# ------------------------------------------------------------------ #

def build_longmemeval_parser(
    parser: argparse.ArgumentParser | None = None,
) -> argparse.ArgumentParser:
    if parser is None:
        parser = argparse.ArgumentParser(description="LongMemEval benchmark evaluation")
    parser.add_argument("--dataset-path", default="", help="LongMemEval JSON 数据集路径 (不指定则自动查找或下载)")
    parser.add_argument("--sample", default="all", help="筛选 sample (all 或 index/id)")
    parser.add_argument("--questions", type=int, default=0, help="限制 QA 数量 (0=all)")
    parser.add_argument(
        "--question-ids",
        default="",
        help="Comma-separated question/native/sample ids",
    )
    parser.add_argument("--random-count", type=int, default=0)
    parser.add_argument("--random-seed", type=int, default=30)
    parallel = parser.add_argument_group("Parallel execution")
    parallel.add_argument(
        "--parallel-shards",
        type=int,
        default=1,
        help="Split selected questions across isolated CLI shard processes",
    )
    parallel.add_argument(
        "--parallel-workers",
        type=int,
        default=2,
        help="Maximum number of shard processes to run concurrently",
    )
    parallel.add_argument(
        "--parallel-dry-run",
        action="store_true",
        help="Write the shard manifest without starting evaluation processes",
    )
    parser.add_argument(
        "--checkpoint-interval",
        type=int,
        default=10,
        help="Persist partial QA CSV after every N completed questions (0=off)",
    )
    parser.add_argument(
        "--resume",
        default="",
        help=(
            "Resume a prior longmemeval run directory or qa_results CSV: "
            "reuse the prior identity, skip already-completed import batches, "
            "reuse healthy QA answers, and reuse matching judge rows; only "
            "run the missing/unhealthy remainder. Metrics are computed over "
            "the merged whole run."
        ),
    )
    add_agent_plugin_args(parser, default_plugin="vikingbot")
    add_eval_args(parser)
    add_judge_args(parser)
    return parser


def run_longmemeval(
    args: argparse.Namespace,
    run: EvalRun,
    config: EvalConfig,
    agent_plugin: Any,
) -> None:
    from benchmarks.longmemeval.dataset import load_dataset
    from benchmarks.longmemeval.evaluate import evaluate_longmemeval
    from benchmarks.longmemeval.import_memory import import_longmemeval_memory
    from benchmarks.longmemeval.parallel import run_parallel
    from benchmarks.longmemeval.qa import build_qa_tasks, run_longmemeval_qa
    from benchmarks.longmemeval.reporting import build_summary
    from benchmarks.longmemeval.resume import (
        build_resume_manifest,
        load_prior_eval_rows,
        load_resume_manifest,
        validate_resume_manifest,
        write_resume_manifest,
    )
    from benchmarks.longmemeval.selection import (
        parse_question_ids,
        select_jobs_and_plans,
    )
    from shared.import_guard import require_complete_imports
    from shared.llm_client import LLMClient
    from shared.resume_identity import apply_resume_memory_identity
    from shared.resume_qa import (
        find_qa_resume_csv,
        load_prior_import_rows,
        load_resume_qa_results,
    )

    log = run.logger

    if args.random_count < 0:
        raise ValueError("random count must be >= 0")
    if args.parallel_shards < 1 or args.parallel_workers < 1:
        raise ValueError("parallel shards and workers must be >= 1")
    if args.resume and args.parallel_shards > 1:
        raise ValueError("--resume is not supported together with --parallel-shards")

    dataset_path = resolve_dataset_path("longmemeval", args.dataset_path)
    config.dataset_path = dataset_path
    question_ids = parse_question_ids(args.question_ids)

    if args.parallel_shards > 1:
        jobs, plans = load_dataset(dataset_path, sample_filter=args.sample)
        jobs, plans = select_jobs_and_plans(
            jobs,
            plans,
            question_ids=question_ids,
            limit=config.question_limit,
            random_count=args.random_count,
            random_seed=args.random_seed,
        )
        if not jobs or not plans:
            raise ValueError(
                "dataset/sample filter produced no LongMemEval questions"
            )
        root = results_root_for(_BENCHMARK_DIRS["longmemeval"], args.out_dir)
        output_dir = root / (
            "parallel_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        )
        summary = run_parallel(
            argv=sys.argv[1:],
            question_ids=[job.question_id for job in jobs],
            output_dir=output_dir,
            shard_count=args.parallel_shards,
            worker_count=args.parallel_workers,
            dry_run=args.parallel_dry_run,
        )
        if not args.parallel_dry_run and summary["status"] != "completed":
            raise SystemExit(2)
        return

    # 加载数据集
    log.info("加载 LongMemEval 数据集: %s", dataset_path)
    jobs, plans = load_dataset(dataset_path, sample_filter=args.sample)
    log.info("共 %d 个问题", len(jobs))

    jobs, plans = select_jobs_and_plans(
        jobs,
        plans,
        question_ids=question_ids,
        limit=config.question_limit,
        random_count=args.random_count,
        random_seed=args.random_seed,
    )
    if question_ids:
        log.info("按 question id 选择 %d 题", len(jobs))
    elif args.random_count > 0:
        log.info("随机选择 %d 题 (seed=%d)", len(jobs), args.random_seed)
    elif config.question_limit > 0:
        log.info("限制 QA 数量为 %d", len(jobs))
    if not jobs or not plans:
        message = "dataset/sample filter produced no LongMemEval questions"
        run.save_summary({
            "status": "failed",
            "phase": "dataset",
            "dataset": dataset_path,
            "sample_filter": args.sample,
            "error": message,
        })
        raise ValueError(message)

    echomem = agent_plugin.memory_client
    if args.resume:
        apply_resume_memory_identity(echomem, args.resume, log)
    evaluation_identity = {
        "mode": "resumed" if args.resume else "fresh",
        "tenant_id": echomem.account,
        "user_id": echomem.user_id,
    }
    log.info(
        "Memory identity: %s tenant=%s user=%s",
        evaluation_identity.get("mode", "none"),
        evaluation_identity.get("tenant_id", ""),
        evaluation_identity.get("user_id", ""),
    )

    # 尽早写 resume manifest（含身份）：即使导入中断，目录也留有身份供后续 --resume 复用。
    resume_manifest = build_resume_manifest(
        dataset_path=dataset_path,
        config=config,
        memory_identity={
            "account": echomem.account,
            "user_id": echomem.user_id,
            "auth_key": echomem.auth_key,
        },
    )
    write_resume_manifest(run.result_dir, resume_manifest)

    # -- 阶段 1: 逐题隔离导入 --
    log.info("=" * 60)
    log.info("阶段 1: 逐题导入 haystack sessions (共 %d 题)", len(plans))
    prior_import_rows = (
        load_prior_import_rows(args.resume) if args.resume else None
    )
    if prior_import_rows is not None:
        log.info("阶段 1: 逐题导入 haystack sessions (resume, 跳过已完成)")
    import_report = import_longmemeval_memory(
        jobs,
        plans,
        echomem,
        config,
        run.result_dir,
        log,
        prior_import_rows=prior_import_rows,
    )
    log.info(
        "导入完成: %d/%d 成功",
        import_report.completed,
        import_report.total,
    )
    try:
        require_complete_imports(
            import_report.rows,
            allow_incomplete=args.allow_diagnostics,
        )
    except RuntimeError as exc:
        run.save_summary({
            "status": "failed",
            "phase": "import",
            "dataset": dataset_path,
            "import_ok": import_report.completed,
            "import_total": import_report.total,
            "error": str(exc),
        })
        log.error("%s", exc)
        raise SystemExit(2) from exc

    # -- 阶段 2: 逐题 QA --
    log.info("=" * 60)
    log.info("阶段 2: QA (共 %d 题, 并发=%d)", len(jobs), config.concurrency)

    qa_tasks = build_qa_tasks(
        jobs,
        import_report.question_to_session,
        config,
        agent_id=echomem.agent_id,
    )
    qa_resume_state = None
    if args.resume:
        prior_qa_csv = find_qa_resume_csv(args.resume)
        if prior_qa_csv is None:
            log.info(
                "QA resume: no prior QA results under %s (import-only run), "
                "running full QA",
                args.resume,
            )
        else:
            source_dir = Path(args.resume).expanduser().resolve()
            validate_resume_manifest(
                resume_manifest,
                load_resume_manifest(
                    source_dir if source_dir.is_dir() else source_dir.parent
                ),
            )
            qa_resume_state = load_resume_qa_results(args.resume, qa_tasks)
            log.info(
                "QA resume: source=%s reused=%d discarded=%d",
                qa_resume_state.source_csv,
                len(qa_resume_state.results),
                len(qa_resume_state.discarded_question_ids),
            )
    qa_results = run_longmemeval_qa(
        qa_tasks,
        agent_plugin,
        config,
        run.result_dir,
        log,
        existing_results=(
            qa_resume_state.results if qa_resume_state else None
        ),
        checkpoint_interval=args.checkpoint_interval,
    )

    # -- 阶段 3: 官方 accuracy 评测 --
    log.info("=" * 60)
    log.info("阶段 3: LLM Judge (yes/no per question type)")

    judge_llm = LLMClient(
        base_url=args.judge_base_url or config.llm_base_url,
        api_key=args.judge_api_key or config.llm_api_key,
        model=args.judge_model or config.llm_model,
        temperature=0.0,
        max_tokens=256,
        timeout_s=config.llm_timeout_s,
        max_retries=config.llm_retries,
    )

    prior_eval_rows = (
        load_prior_eval_rows(args.resume) if args.resume else None
    )
    if prior_eval_rows:
        log.info("阶段 3: Judge (resume, 复用 %d 个旧判定)", len(prior_eval_rows))
    elif args.resume:
        log.info(
            "阶段 3: Judge (no prior judge results under %s, running full judge)",
            args.resume,
        )
    evaluation_report = evaluate_longmemeval(
        qa_results,
        jobs,
        judge_llm,
        run.result_dir,
        log,
        existing_rows=prior_eval_rows,
    )
    log.info(
        "Judge 完成: %d/%d correct, accuracy=%.2f%%",
        evaluation_report.correct,
        evaluation_report.graded,
        evaluation_report.overall_accuracy * 100,
    )
    for task_type, stats in evaluation_report.per_type.items():
        log.info(
            "  %s: %d/%d (%.1f%%)",
            task_type,
            stats["correct"],
            stats["total"],
            stats["accuracy"] * 100,
        )

    summary = build_summary(
        dataset_path=dataset_path,
        jobs=jobs,
        import_report=import_report,
        qa_results=qa_results,
        evaluation_report=evaluation_report,
        evaluation_identity=evaluation_identity,
        resumed=bool(args.resume),
    )
    summary["resume"] = {
        "enabled": bool(args.resume),
        "source": str(args.resume or ""),
        "mode": evaluation_identity.get("mode"),
        "reused_qa": (
            len(qa_resume_state.results) if qa_resume_state else 0
        ),
        "discarded_qa": (
            qa_resume_state.discarded_question_ids
            if qa_resume_state
            else []
        ),
        "reused_import_batches": sum(
            1
            for row in import_report.rows
            if str(row.get("status") or "").strip().lower() == "reused"
        ),
        "reused_judge_rows": len(prior_eval_rows or []),
    }
    run.save_summary(summary)

    if summary["status"] != "completed":
        log.error("评测包含运行错误，结果不能作为正式分数")
        raise SystemExit(2)

    log.info("=" * 60)
    log.info("评测完成! 结果目录: %s", run.result_dir)
    log.info(
        "Accuracy: %.2f%% (%d/%d)",
        evaluation_report.overall_accuracy * 100,
        evaluation_report.correct,
        evaluation_report.graded,
    )


# ------------------------------------------------------------------ #
#  Dynamic 段                                                         #
# ------------------------------------------------------------------ #

def build_dynamic_parser(
    parser: argparse.ArgumentParser | None = None,
) -> argparse.ArgumentParser:
    configs_dir = _BENCHMARK_DIRS["dynamic"] / "configs"
    if parser is None:
        parser = argparse.ArgumentParser(description="动态评测: 仿真 Agent+记忆系统 线上效果")

    # 模式选择
    g = parser.add_argument_group("模式")
    g.add_argument(
        "--dataset-path",
        default="",
        help="数据集路径 (指定则进入 replay 模式; 不指定则 generate 模式)",
    )
    g.add_argument("--sample", default="all")
    g.add_argument("--questions", type=int, default=0)

    # 评测器配置 (两种模式共用)
    g = parser.add_argument_group("评测器配置")
    g.add_argument(
        "--evaluator-config",
        default=str(configs_dir / "evaluator_template.yaml"),
        help="评测器配置 YAML，路径相对于 run_eval.py (默认 configs/evaluator_template.yaml)",
    )

    # Generate 模式参数
    g = parser.add_argument_group("Generate 模式")
    g.add_argument("--num-memories", type=int, default=5, help="生成的背景记忆数")
    g.add_argument("--num-queries", type=int, default=10, help="生成的提问数")
    g.add_argument("--new-session-ratio", type=float, default=0.3)
    g.add_argument("--typing-speed-ms", type=int, default=200)
    g.add_argument("--typing-jitter-ms", type=int, default=20)
    g.add_argument(
        "--user-simulator-config",
        default=str(configs_dir / "user_simulator_default.yaml"),
        help="用户模拟器配置，路径相对于 run_eval.py (默认 configs/user_simulator_default.yaml)",
    )

    # 场景生成 LLM (仅 generate 模式使用, 用于生成背景记忆和 query)
    g = parser.add_argument_group("场景生成 LLM")
    g.add_argument(
        "--scenario-model",
        default=os.environ.get("ECHOAGENT_TEST_SCENARIO_MODEL", "deepseek-v4-flash-0731"),
    )
    g.add_argument(
        "--scenario-base-url",
        default=os.environ.get("ECHOAGENT_TEST_SCENARIO_BASE_URL", ""),
    )
    g.add_argument(
        "--scenario-api-key",
        default=os.environ.get("ECHOAGENT_TEST_SCENARIO_API_KEY", ""),
    )

    # Agent 插件 (声明 LLM / 记忆后端 / 插件特有参数)
    add_agent_plugin_args(parser, default_plugin="echo_agent")

    # 评测基础设施参数 (dynamic 不支持并发, 不声明 --concurrency)
    g = parser.add_argument_group("评测")
    g.add_argument("--out-dir", default="results", help="结果输出目录")

    return parser


def validate_dynamic_args(args: argparse.Namespace) -> list[str]:
    errors: list[str] = []

    evaluator_path = Path(args.evaluator_config).expanduser()
    if not evaluator_path.is_file():
        errors.append(f"evaluator config not found: {evaluator_path}")
    if args.dataset_path:
        dataset_path = Path(args.dataset_path).expanduser()
        if not dataset_path.is_file():
            errors.append(f"dataset not found: {dataset_path}")
    else:
        for name, value in (
            ("scenario base URL", args.scenario_base_url),
            ("scenario model", args.scenario_model),
            ("scenario API key", args.scenario_api_key),
        ):
            if not str(value or "").strip():
                errors.append(f"missing {name}")
        simulator_path = Path(args.user_simulator_config).expanduser()
        if not simulator_path.is_file():
            errors.append(f"user simulator config not found: {simulator_path}")

    if args.questions < 0:
        errors.append("questions must be >= 0")
    if args.num_memories < 1:
        errors.append("num memories must be >= 1")
    if args.num_queries < 1:
        errors.append("num queries must be >= 1")
    if not 0 <= args.new_session_ratio <= 1:
        errors.append("new session ratio must be between 0 and 1")
    if args.typing_speed_ms < 0 or args.typing_jitter_ms < 0:
        errors.append("typing speed and jitter must be >= 0")
    return errors


def run_dynamic(
    args: argparse.Namespace,
    run: EvalRun,
    config: EvalConfig,
    agent_plugin: Any,
) -> None:
    from dynamic.workflows import run_generate_mode, run_replay_mode
    from shared.llm_client import LLMClient

    log = run.logger

    errors = validate_dynamic_args(args)
    if errors:
        raise ValueError("; ".join(errors))

    llm = LLMClient(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        temperature=0.3,
        max_tokens=4096,
        timeout_s=config.llm_timeout_s,
        max_retries=config.llm_retries,
    )

    if args.dataset_path:
        run_replay_mode(args, run, agent_plugin, llm)
    else:
        run_generate_mode(args, run, agent_plugin, llm)


# ------------------------------------------------------------------ #
#  分发表                                                             #
# ------------------------------------------------------------------ #

_DATASET_PARSERS = {
    "locomo": build_locomo_parser,
    "hotpotqa": build_hotpotqa_parser,
    "longmemeval": build_longmemeval_parser,
    "dynamic": build_dynamic_parser,
}

_RUNNERS = {
    "locomo": run_locomo,
    "hotpotqa": run_hotpotqa,
    "longmemeval": run_longmemeval,
    "dynamic": run_dynamic,
}


if __name__ == "__main__":
    main()

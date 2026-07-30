#!/usr/bin/env python3
"""LoCoMo benchmark evaluation script.

流程:
  1. 集中导入所有 sample 的 conversation sessions 到 EchoMem (open -> add_messages -> commit -> poll)
  2. 逐题 QA: search EchoMem -> build prompt -> LLM answer (仅检索不写入)
  3. LLM judge: CORRECT / WRONG

用法见 docs/usage.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# 确保能 import shared 包
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backends import create_backend_client
from benchmarks.locomo.dataset import load_dataset
from benchmarks.locomo.diagnosis import diagnose_run
from benchmarks.locomo.blackbox import write_artifacts as write_blackbox_artifacts
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
from benchmarks.locomo.profiles import (
    LEGACY_77_PROFILE,
    default_vikingbot_workspace,
    profile_settings,
    VIKINGBOAT_0411_PROFILE,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
)
from benchmarks.locomo.provenance import (
    inspect_memory_provenance,
    write_memory_provenance,
)
from benchmarks.locomo.memory_scope import (
    ExcludingMemoryFilesClient,
    SessionPrefixMemoryClient,
)
from benchmarks.locomo.qa import QAOptions, build_qa_tasks, run_locomo_qa
from benchmarks.locomo.resume import (
    build_qa_resume_manifest,
    build_judge_resume_manifest,
    copy_resume_traces,
    load_judge_resume_state,
    load_qa_resume_state,
    write_judge_resume_manifest,
    write_qa_resume_manifest,
)
from benchmarks.locomo.reporting import build_summary
from benchmarks.locomo.selection import parse_question_ids, select_questions
from shared.dataset_io import resolve_dataset_path
from shared.eval_base import (
    EvalRun,
    add_echomem_args,
    add_llm_args,
    add_eval_args,
    apply_evaluation_identity,
    build_config_from_args,
    isolate_evaluation_identity,
    results_root_for,
    validate_eval_config,
)
from shared.import_guard import require_complete_imports
from shared.llm_client import LLMClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LoCoMo benchmark evaluation")
    parser.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dataset", default="", help="LoCoMo JSON 数据集路径 (不指定则自动查找或下载)")
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
    parser.add_argument(
        "--allow-memory-provenance-mismatch",
        action="store_true",
        help=(
            "Continue when the reused EchoMemory session count does not match "
            "the selected dataset/session mode (diagnostics only)"
        ),
    )
    parser.add_argument(
        "--memory-session-prefix",
        default="",
        help=(
            "In reuse mode, expose only session-backed memory whose session "
            "id starts with this prefix"
        ),
    )
    parser.add_argument(
        "--inject-memory",
        action="store_true",
        help=(
            "Import the selected LoCoMo conversations into a fresh isolated "
            "identity instead of reusing existing memory"
        ),
    )
    parser.add_argument(
        "--memory-identity-file",
        default="",
        help=(
            "Local JSON file for a kept isolated EchoMem identity. A new "
            "injection writes it; a later run reuses the identity from it."
        ),
    )
    parser.add_argument(
        "--exclude-memory-file",
        action="append",
        default=[],
        help=(
            "Hide and reject reads of an EchoMemory filesystem leaf name; "
            "repeat for multiple names"
        ),
    )
    # 共享参数
    add_echomem_args(parser)
    add_llm_args(parser)
    add_eval_args(parser)
    qa = parser.add_argument_group("Historical VikingBot QA")
    qa.add_argument(
        "--qa-profile",
        choices=[
            LEGACY_77_PROFILE,
            VIKINGBOAT_0411_PROFILE,
            VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
        ],
        default=None,
        help=(
            "LoCoMo QA executor; legacy-77 preserves the actual July 13 "
            "head_clean 63/81 run; vikingboat0411 adapts the v0.4.11 "
            "VikingBot agent behavior to EchoMemory tools; "
            "vikingboat0411-natural-no-tools keeps only complete initially "
            "retrieved memory excerpts"
        ),
    )
    qa.add_argument(
        "--tool-search-limit",
        type=int,
        default=None,
    )
    qa.add_argument(
        "--user-memory-budget-chars",
        type=int,
        default=None,
    )
    qa.add_argument(
        "--agent-memory-budget-chars",
        type=int,
        default=None,
    )
    qa.add_argument(
        "--max-iterations",
        type=int,
        default=None,
    )
    qa.add_argument("--initial-min-score", type=float, default=None)
    qa.add_argument("--tool-min-score", type=float, default=None)
    qa.add_argument(
        "--tool-search-pool-multiplier",
        type=int,
        default=None,
    )
    qa.add_argument(
        "--tool-set",
        choices=[
            "search_read",
            "vikingbot_native_safe",
            "vikingbot_echo_native",
        ],
        default=None,
    )
    qa.add_argument(
        "--tools",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Expose the profile's memory tools to the answer model; "
            "--no-tools keeps the same profile prompt and initial memory "
            "injection but performs a single model turn"
        ),
    )
    qa.add_argument(
        "--search",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Enable semantic memory_search, including initial retrieval; "
            "--no-search keeps the other memory tools"
        ),
    )
    qa.add_argument(
        "--vikingbot-workspace",
        default=default_vikingbot_workspace(),
        help="Workspace supplying the historical SOUL.md and TOOLS.md bootstrap",
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
        "--resume-qa",
        default="",
        help=(
            "Resume healthy QA rows from a prior LoCoMo run directory or "
            "qa_results CSV; requires --reuse-memory-account"
        ),
    )
    parser.set_defaults(
        top_k=None,
        memory_budget_chars=None,
        question_timeout_s=None,
        llm_max_tokens=None,
        llm_retries=None,
    )
    # judge 参数
    g = parser.add_argument_group("Judge")
    g.add_argument("--judge-model", default=os.getenv("JUDGE_MODEL", ""), help="Judge LLM 模型名 (默认同 --llm-model)")
    g.add_argument("--judge-api-key", default=os.getenv("JUDGE_TOKEN", ""), help="Judge API key (默认同 --llm-api-key)")
    g.add_argument("--judge-base-url", default=os.getenv("JUDGE_BASE_URL", ""), help="Judge base URL (默认同 --llm-base-url)")
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
            "or judge_results CSV"
        ),
    )
    return parser


def apply_locomo_cli_defaults(args) -> None:
    if args.qa_profile is None:
        args.qa_profile = (
            VIKINGBOAT_0411_PROFILE
            if args.tools
            else VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE
        )
    if (
        args.qa_profile == VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE
        and args.tools
    ):
        raise ValueError(
            "vikingboat0411-natural-no-tools requires --no-tools"
        )

    if args.inject_memory and args.reuse_memory_account:
        raise ValueError(
            "--inject-memory and --reuse-memory-account are mutually exclusive"
        )
    if args.inject_memory or args.keep_memory_account:
        args.reuse_memory_account = False
    else:
        args.reuse_memory_account = True

    sample = str(args.sample or "").strip()
    if (
        args.reuse_memory_account
        and not args.memory_session_prefix
        and not args.memory_identity_file
        and re.fullmatch(r"conv-\d+", sample)
    ):
        args.memory_session_prefix = f"echomem-locomo-{sample}-"


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


def load_memory_identity_file(path_value: str) -> dict[str, str]:
    value = str(path_value or "").strip()
    if not value:
        return {}
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid memory identity file: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid memory identity file: {path}")
    required = ("tenant_id", "user_id", "auth_key")
    identity = {
        key: str(raw.get(key) or "").strip()
        for key in (*required, "agent_id")
    }
    missing = [key for key in required if not identity[key]]
    if missing:
        raise ValueError(
            "Memory identity file is missing "
            f"{', '.join(missing)}: {path}"
        )
    return identity


def write_memory_identity_file(
    path_value: str,
    identity: dict[str, str],
    *,
    auth_key: str,
    agent_id: str,
) -> None:
    value = str(path_value or "").strip()
    if not value:
        return
    path = Path(value).expanduser().resolve()
    if not auth_key:
        raise RuntimeError("Kept EchoMem identity has no auth key")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tenant_id": identity["tenant_id"],
        "user_id": identity["user_id"],
        "agent_id": agent_id,
        "auth_key": auth_key,
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            )
    finally:
        if fd >= 0:
            os.close(fd)


def main() -> None:
    args = build_parser().parse_args()
    apply_locomo_cli_defaults(args)
    selected_profile = profile_settings(args.qa_profile)
    for name in (
        "top_k",
        "memory_budget_chars",
        "question_timeout_s",
        "llm_max_tokens",
        "llm_retries",
        "tool_search_limit",
        "user_memory_budget_chars",
        "agent_memory_budget_chars",
        "max_iterations",
        "initial_min_score",
        "tool_min_score",
        "tool_search_pool_multiplier",
        "tool_set",
    ):
        if getattr(args, name) is None:
            setattr(args, name, selected_profile[name])
    config = build_config_from_args(args)
    (
        system_prompt_append,
        system_prompt_append_sha256,
        system_prompt_append_source,
    ) = load_qa_prompt_append(args.qa_prompt_file)
    saved_memory_identity = load_memory_identity_file(
        args.memory_identity_file
    )
    if saved_memory_identity:
        if args.inject_memory or args.keep_memory_account:
            raise ValueError(
                "An existing --memory-identity-file cannot be combined with "
                "--inject-memory or --keep-memory-account"
            )
        args.reuse_memory_account = True
        config.account = saved_memory_identity["tenant_id"]
        config.user_id = saved_memory_identity["user_id"]
        config.agent_id = (
            saved_memory_identity["agent_id"] or config.agent_id
        )
        config.echomem_auth_key = saved_memory_identity["auth_key"]
    config.sample_filter = args.sample
    config.question_limit = args.questions
    validate_eval_config(config)
    if args.max_sessions < 0:
        raise ValueError("max sessions must be >= 0")
    if args.tool_search_limit < 1:
        raise ValueError("tool search limit must be >= 1")
    if args.user_memory_budget_chars < 1 or args.agent_memory_budget_chars < 1:
        raise ValueError("VikingBot memory budgets must be >= 1")
    if args.max_iterations < 1:
        raise ValueError("max iterations must be >= 1")
    if args.initial_min_score < 0 or args.tool_min_score < 0:
        raise ValueError("VikingBot score thresholds must be >= 0")
    if args.tool_search_pool_multiplier < 1:
        raise ValueError("tool search pool multiplier must be >= 1")
    if args.checkpoint_interval < 0:
        raise ValueError("checkpoint interval must be >= 0")
    if args.resume_qa and not args.reuse_memory_account:
        raise ValueError(
            "--resume-qa requires --reuse-memory-account to prevent "
            "duplicate memory injection"
        )
    if args.memory_session_prefix and not args.reuse_memory_account:
        raise ValueError(
            "--memory-session-prefix requires --reuse-memory-account"
        )
    if args.judge_concurrency < 1:
        raise ValueError("judge concurrency must be >= 1")
    if args.judge_checkpoint_interval < 0:
        raise ValueError("judge checkpoint interval must be >= 0")
    dataset_path = resolve_dataset_path("locomo", args.dataset)
    config.dataset_path = dataset_path
    question_ids = parse_question_ids(args.question_ids)
    if args.check:
        jobs, plans = load_dataset(dataset_path, sample_filter=args.sample)
        session_mode = resolve_session_mode(args.session_mode, len(plans))
        jobs = select_questions(
            jobs,
            question_ids=question_ids,
            limit=config.question_limit,
        )
        if not plans or not jobs:
            raise ValueError("dataset/sample filter produced no LoCoMo samples or questions")
        print(
            f"[check] OK benchmark=locomo dataset={dataset_path} "
            f"samples={len(plans)} questions={len(jobs)} session_mode={session_mode}"
        )
        return

    # 创建评测运行
    run = EvalRun(
        benchmark_name="locomo",
        results_root=results_root_for(Path(__file__).parent, args.out_dir),
        config=config,
    )
    log = run.logger

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

    # 创建 EchoMem 客户端
    echomem = create_backend_client(
        "echomemory",
        base_url=config.echomem_url,
        api_key=config.echomem_auth_key,
        account=config.account,
        user_id=config.user_id,
        agent_id=config.agent_id,
        workspace=config.workspace,
        timeout_s=60.0,
        max_retries=3,
    )
    echomem.health()
    evaluation_identity = isolate_evaluation_identity(
        echomem,
        "locomo",
        run.result_dir.name,
        reuse=args.reuse_memory_account,
        keep=args.keep_memory_account,
    )
    apply_evaluation_identity(config, run, echomem, evaluation_identity)
    if args.memory_identity_file and evaluation_identity["mode"] == "isolated":
        write_memory_identity_file(
            args.memory_identity_file,
            evaluation_identity,
            auth_key=echomem.auth_key,
            agent_id=config.agent_id,
        )
    log.info(
        "EchoMem identity: %s tenant=%s user=%s",
        evaluation_identity["mode"],
        evaluation_identity["tenant_id"],
        evaluation_identity["user_id"],
    )
    if args.memory_session_prefix:
        echomem = SessionPrefixMemoryClient(
            echomem,
            args.memory_session_prefix,
        )
        log.info(
            "Memory session scope: prefix=%s",
            args.memory_session_prefix,
        )

    # 创建 LLM 客户端
    llm = LLMClient(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        temperature=config.llm_temperature,
        max_tokens=config.llm_max_tokens,
        timeout_s=config.llm_timeout_s,
        max_retries=config.llm_retries,
    )

    # -- 阶段 1: 复用已有记忆或集中导入所有 session --
    log.info("=" * 60)
    reuse_existing_memory = args.reuse_memory_account
    if reuse_existing_memory:
        log.info(
            "阶段 1: 跳过导入，直接复用已有记忆 account=%s user=%s",
            config.account,
            config.user_id,
        )
    else:
        log.info("阶段 1: 导入记忆 (共 %d 个 sample)", len(plans))

    import_report = import_locomo_memory(
        plans,
        echomem,
        config,
        ImportOptions(
            session_mode=session_mode,
            max_sessions=args.max_sessions,
            reuse_existing_memory=reuse_existing_memory,
            sample_filter=args.sample,
        ),
        run.result_dir,
        log,
    )
    if reuse_existing_memory:
        log.info("已有记忆复用模式：未执行任何写入或 commit")
    else:
        log.info(
            "导入完成: %d/%d 成功",
            import_report.completed,
            import_report.total,
        )
        try:
            require_complete_imports(
                import_report.rows,
                allow_incomplete=args.allow_incomplete_imports,
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
        echomem,
        dataset_path=dataset_path,
        plans=plans,
        session_mode=session_mode,
        max_sessions=args.max_sessions,
    )
    memory_provenance["session_prefix"] = args.memory_session_prefix
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
        and not args.allow_memory_provenance_mismatch
    ):
        message = (
            "EchoMemory provenance mismatch: expected "
            f"{memory_provenance['expected_session_count']} sessions but found "
            f"{memory_provenance['actual_session_count']}; use "
            "--allow-memory-provenance-mismatch only for diagnostics"
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

    excluded_memory_files = list(dict.fromkeys(
        str(filename or "").strip()
        for filename in args.exclude_memory_file
        if str(filename or "").strip()
    ))
    if excluded_memory_files:
        echomem = ExcludingMemoryFilesClient(
            echomem,
            excluded_memory_files,
        )
        access_policy = {
            "mode": "exclude_files",
            "excluded_filenames": excluded_memory_files,
        }
        (run.result_dir / "memory_access_policy.json").write_text(
            json.dumps(access_policy, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        log.info(
            "Memory access policy: excluded files=%s",
            ",".join(excluded_memory_files),
        )

    # -- 阶段 2: 逐题 QA --
    log.info("=" * 60)
    log.info("阶段 2: QA (共 %d 题, 并发=%d)", len(jobs), config.concurrency)

    qa_options = QAOptions(
        profile=args.qa_profile,
        tool_search_limit=args.tool_search_limit,
        user_memory_budget_chars=args.user_memory_budget_chars,
        agent_memory_budget_chars=args.agent_memory_budget_chars,
        max_iterations=args.max_iterations,
        vikingbot_workspace=args.vikingbot_workspace,
        checkpoint_interval=args.checkpoint_interval,
        initial_min_score=args.initial_min_score,
        tool_min_score=args.tool_min_score,
        tool_search_pool_multiplier=args.tool_search_pool_multiplier,
        tool_set=args.tool_set,
        top_k=config.top_k,
        memory_budget_chars=config.memory_budget_chars,
        answer_temperature=selected_profile.get(
            "answer_temperature",
            config.llm_temperature,
        ),
        omit_answer_temperature=selected_profile.get(
            "omit_answer_temperature",
            True,
        ),
        initial_retrieval_query_mode=selected_profile.get(
            "initial_retrieval_query_mode",
            "question_only",
        ),
        tool_query_dedup_scope=selected_profile.get(
            "tool_query_dedup_scope",
            "question",
        ),
        retrieval_uri_dedup=selected_profile.get(
            "retrieval_uri_dedup",
            True,
        ),
        search_tool_target_uri_schema=selected_profile.get(
            "search_tool_target_uri_schema",
            False,
        ),
        tools_enabled=args.tools,
        search_enabled=args.search,
        system_prompt_append=system_prompt_append,
        system_prompt_append_sha256=system_prompt_append_sha256,
        system_prompt_append_source=system_prompt_append_source,
    )
    qa_tasks = build_qa_tasks(
        jobs,
        import_report.sample_to_session_ids,
        config,
        qa_options,
    )
    qa_resume_manifest = build_qa_resume_manifest(
        dataset_path=dataset_path,
        sample_filter=args.sample,
        session_mode=session_mode,
        config=config,
        options=qa_options,
    )
    write_qa_resume_manifest(run.result_dir, qa_resume_manifest)
    qa_resume_state = None
    if args.resume_qa:
        qa_resume_state = load_qa_resume_state(
            args.resume_qa,
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
        echomem,
        llm,
        config,
        qa_options,
        run.result_dir,
        log,
        existing_results=(
            qa_resume_state.results if qa_resume_state else None
        ),
    )

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
    if args.resume_judge:
        judge_resume_state = load_judge_resume_state(
            args.resume_judge,
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

    # 收集 EchoMem 日志
    run.collect_echomem_logs()

    # 保存 summary
    summary = build_summary(
        dataset_path=dataset_path,
        sample_filter=args.sample,
        total_samples=len(plans),
        total_questions=len(jobs),
        import_report=import_report,
        reuse_existing_memory=reuse_existing_memory,
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


if __name__ == "__main__":
    main()

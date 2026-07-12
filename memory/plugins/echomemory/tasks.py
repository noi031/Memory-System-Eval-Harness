from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Callable

from ...vikingboat_alignment import (
    VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
    VIKINGBOT_INITIAL_MIN_SCORE,
    VIKINGBOT_INITIAL_SEARCH_LIMIT,
    VIKINGBOT_MAX_ITERATIONS,
    VIKINGBOT_TOOL_MIN_SCORE,
    VIKINGBOT_TOOL_SEARCH_LIMIT,
    VIKINGBOT_TOOL_SET,
    VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
    alignment_metadata,
)
from ..base import PluginTaskSpec


SafePath = Callable[[str], Path]
ResolveToken = Callable[[dict[str, Any], Path], str]


def looks_like_echomem_root(path: Path) -> bool:
    return (
        ((path / "packages" / "echomem" / "src").exists() and (path / "packages" / "echofs" / "src").exists())
        or ((path / "src" / "echomem").exists() and (path / "src" / "echo0").exists() and (path / "pyproject.toml").exists())
        or ((path / "echomem").exists() and (path / "pyproject.toml").exists())
    )


def is_develop_echomem_root(path: Path) -> bool:
    return (path / "src" / "echomem").exists() and (path / "src" / "echo0").exists() and (path / "pyproject.toml").exists()


def default_echomem_root() -> Path:
    candidates = [
        os.environ.get("ECHOMEM_ROOT"),
        os.environ.get("ECHOMEMORY_ROOT"),
        Path.home() / "Code" / "echomemory" / "EchoMem_develop",
        Path.home() / "Code" / "echomemory" / "echo_memory_v010",
        Path.home() / "Code" / "echomemory" / "echo_memory",
        Path.cwd() / "EchoMem_develop",
        Path.cwd().parent / "EchoMem_develop",
        Path.cwd() / "echo_memory_v010",
        Path.cwd().parent / "echo_memory_v010",
        Path.cwd() / "echo_memory",
        Path.cwd().parent / "echo_memory",
        Path.cwd() / "echo_memory_v007_tag",
        Path.cwd() / "echo_memory_v007",
        Path.cwd().parent / "echo_memory_v007_tag",
        Path.cwd().parent / "echo_memory_v007",
        Path.home() / "Code" / "echomemory" / "echo_memory_v006",
        Path.home() / "Code" / "echomemory" / "echo_memory_v007_tag",
        Path.home() / "Code" / "echomemory" / "echo_memory_v007",
    ]
    for raw in candidates:
        if not raw:
            continue
        path = Path(str(raw)).expanduser()
        if looks_like_echomem_root(path):
            return path
    fallback = (
        os.environ.get("ECHOMEM_ROOT")
        or os.environ.get("ECHOMEMORY_ROOT")
        or (Path.home() / "Code" / "echomemory" / "echo_memory")
    )
    return Path(str(fallback)).expanduser()


def echomem_root_value(payload: dict[str, Any]) -> str:
    return str(payload.get("echomem_root") or payload.get("echomemRoot") or default_echomem_root())


def bool_value(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def payload_value(payload: dict[str, Any], key: str, default: Any) -> Any:
    value = payload.get(key)
    return default if value in (None, "") else value


def normalize_echomemory_tool_set(value: Any) -> str:
    raw = str(value or "").strip() or "search_read"
    return "vikingboat_default" if raw == VIKINGBOT_TOOL_SET else raw


def normalize_retrieval_mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw == "local":
        return "local"
    return "search"


def echomemory_http_mode(payload: dict[str, Any]) -> bool:
    transport = str(payload.get("echomem_transport") or payload.get("echomemTransport") or "").strip().lower()
    base_url = str(payload.get("echomem_base_url") or payload.get("echomemBaseUrl") or "").strip()
    return transport == "http" or bool(base_url)


def normalize_payload_retrieval_mode(payload: dict[str, Any]) -> str:
    if echomemory_http_mode(payload):
        return "search"
    return normalize_retrieval_mode(payload.get("retrieval_mode"))


def append_echomemory_transport_args(command: list[str], payload: dict[str, Any]) -> None:
    transport = str(payload.get("echomem_transport") or payload.get("echomemTransport") or "").strip().lower()
    base_url = str(payload.get("echomem_base_url") or payload.get("echomemBaseUrl") or "").strip()
    auth_key = str(payload.get("echomem_auth_key") or payload.get("echomemAuthKey") or "").strip()
    timeout_s = payload.get("echomem_http_timeout_s")
    if transport:
        command += ["--echomem-transport", transport]
    if base_url:
        command += ["--echomem-base-url", base_url]
    if auth_key:
        command += ["--echomem-auth-key", auth_key]
    if timeout_s not in (None, ""):
        command += ["--echomem-http-timeout-s", str(timeout_s)]


STRICT_READY_DATASET_FORMATS = {"hotpotqa", "longmemeval", "evolvingevents", "proagentbench", "tau2bench"}
DEVELOP_FULL_COMMIT_WAIT_DEFAULT = 12
DEVELOP_FULL_FLUSH_TIMEOUT_DEFAULT = 20
DEVELOP_FULL_FLUSH_ATTEMPTS_DEFAULT = 1


def import_wait_defaults(
    *,
    skip_session_commit: bool,
    defer_artifact_wait: bool,
    develop_full_wait: bool,
) -> tuple[int, int, int]:
    if skip_session_commit:
        return 20, 45, 1
    if defer_artifact_wait:
        return 8, 15, 0
    if develop_full_wait:
        return (
            DEVELOP_FULL_COMMIT_WAIT_DEFAULT,
            DEVELOP_FULL_FLUSH_TIMEOUT_DEFAULT,
            DEVELOP_FULL_FLUSH_ATTEMPTS_DEFAULT,
        )
    return 300, 600, 2


def _python_command(raw: Any) -> str | None:
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    if not text:
        return None
    if "/" not in text and not text.startswith("."):
        return text
    path = Path(text).expanduser()
    return str(path) if path.exists() else None


def echomemory_python(payload: dict[str, Any]) -> str:
    root = Path(echomem_root_value(payload)).expanduser()
    candidates = [
        payload.get("python"),
        payload.get("echomem_python"),
        os.environ.get("ECHOMEM_PYTHON"),
        os.environ.get("ECHOMEMORY_PYTHON"),
        root / ".venv/bin/python",
        Path.home() / "Code" / "echomemory" / "EchoMem_develop/.venv/bin/python",
        Path.home() / "Code" / "echomemory" / "echo_memory_v010/.venv/bin/python",
        Path.home() / "Code" / "echomemory" / "echo_memory/.venv/bin/python",
        root.parent / "EchoMem_develop/.venv/bin/python",
        root.parent / "echo_memory_v010/.venv/bin/python",
        root.parent / "echo_memory_v007_tag/.venv/bin/python",
        root.parent / "echo_memory_v007/.venv/bin/python",
        Path.home() / "Code" / "echomemory" / "echo_memory_v007_tag/.venv/bin/python",
        Path.home() / "Code" / "echomemory" / "echo_memory_v007/.venv/bin/python",
        Path.home() / "openviking-env/bin/python",
        root.parent / "echo_memory/.venv/bin/python",
        root.parent / "echo_memory_v006/.venv/bin/python",
        Path.home() / "Code" / "echomemory" / "echo_memory/.venv/bin/python",
        shutil.which("python3"),
        "python3",
    ]
    for raw in candidates:
        command = _python_command(raw)
        if command:
            return command
    return "python3"


def _append_longmemeval_parallel_passthrough(
    command: list[str],
    payload: dict[str, Any],
    defaults: dict[str, Any],
    *,
    prompt_mode: str,
    top_k: int,
    score_threshold: float,
    tool_search_limit: int,
    tool_min_score: float,
    max_iterations: int,
    retrieval_mode: str,
    tool_set: str,
    answer_base_url: str,
    answer_model: str,
    judge_base_url: str,
    judge_model: str,
) -> None:
    command.extend([
        "--top-k",
        str(top_k),
        "--score-threshold",
        str(score_threshold),
        "--prompt-mode",
        prompt_mode,
        "--retrieval-mode",
        retrieval_mode,
        "--retrieval-query-strategy",
        str(payload.get("retrieval_query_strategy") or "direct"),
        "--answer-base-url",
        str(answer_base_url),
        "--answer-model",
        str(answer_model),
        "--judge-base-url",
        str(judge_base_url),
        "--judge-model",
        str(judge_model),
        "--judge-parallel",
        str(payload.get("judge_parallel") or payload.get("parallel") or 4),
        "--model-retries",
        str(payload.get("model_retries") or 5),
        "--timeout-s",
        str(payload.get("timeout_s") or 120),
        "--question-timeout-s",
        str(payload.get("question_timeout_s") or 600),
        "--tool-set",
        tool_set,
        "--tool-search-limit",
        str(tool_search_limit),
        "--tool-min-score",
        str(tool_min_score),
        "--tool-log-chars",
        str(payload.get("tool_log_chars") or 1200),
        "--prefetch-read-count",
        str(payload.get("prefetch_read_count") or 4),
        "--prefetch-context-chars",
        str(payload.get("prefetch_context_chars") or 5000),
        "--max-iterations",
        str(max_iterations),
        "--import-wait-mode",
        str(payload.get("import_wait_mode") or "full"),
        "--commit-wait-s",
        str(payload_value(payload, "commit_wait_s", 300)),
        "--commit-call-timeout-s",
        str(payload_value(payload, "commit_call_timeout_s", 300)),
        "--flush-call-timeout-s",
        str(payload_value(payload, "flush_call_timeout_s", 600)),
        "--flush-attempts",
        str(payload_value(payload, "flush_attempts", 2)),
    ])
    if payload.get("echomem_config"):
        command += ["--echomem-config", str(payload["echomem_config"])]
    if bool_value(payload.get("import_only"), False):
        command.append("--import-only")
    if bool_value(payload.get("resume"), True) is False:
        command.append("--no-resume")
    if bool_value(payload.get("continue_on_session_error"), False):
        command.append("--continue-on-session-error")
    if bool_value(payload.get("fallback_to_mock"), False):
        command.append("--fallback-to-mock")
    if bool_value(payload.get("fallback_to_mock_embedding_only"), False):
        command.append("--fallback-to-mock-embedding-only")
    command.append("--no-vikingboat-tool-loop")
    if bool_value(payload.get("vikingboat_compat"), prompt_mode == "vikingboat_compat"):
        command.append("--vikingboat-compat")
    else:
        command.append("--no-vikingboat-compat")
    if bool_value(payload.get("initial_tool_prefetch"), False):
        command.append("--initial-tool-prefetch")
    else:
        command.append("--no-initial-tool-prefetch")
    if bool_value(payload.get("fallback_to_one_shot"), True):
        command.append("--fallback-to-one-shot")
    else:
        command.append("--no-fallback-to-one-shot")
    if bool_value(payload.get("toolloop_rescue_on_toollike_answer"), False):
        command.append("--toolloop-rescue-on-toollike-answer")
    else:
        command.append("--no-toolloop-rescue-on-toollike-answer")
    if bool_value(payload.get("answer_refinement"), False):
        command.append("--answer-refinement")
    if bool_value(payload.get("auto_judge"), True):
        command.append("--judge-after")
    if bool_value(payload.get("official_eval_after"), True):
        command.append("--official-eval-after")
    command.extend([
        "--no-local-session-summaries",
        "--no-local-atoms",
        "--no-local-messages",
        "--no-local-timeline-hints",
        "--no-local-memory-artifacts",
    ])


def build_echomemory_longmemeval_parallel_command(
    payload: dict[str, Any],
    run_dir: Path,
    config: Path,
    root: Path,
    default_data: Path,
    defaults: dict[str, Any],
    safe_path: SafePath,
    resolve_judge_token: ResolveToken,
) -> PluginTaskSpec:
    data = safe_path(str(payload.get("data") or str(default_data)))
    out_dir = safe_path(str(payload.get("echomemory_generic_qa_out_dir") or str(run_dir / "echomemory_generic_qa")))
    import_only = bool_value(payload.get("import_only"), False)
    output_file = str(
        out_dir / "merged" / ("summary.json" if import_only else "echomemory_generic_qa_results.csv")
    )
    workspace = str(payload.get("workspace") or payload.get("echomemory_workspace") or str(run_dir / "echomemory_workspace"))
    prompt_mode = str(payload.get("prompt_mode") or payload.get("qa_prompt_mode") or "one_shot").strip() or "one_shot"
    if prompt_mode not in {"one_shot", "vikingboat_lite", "vikingboat_compat"}:
        prompt_mode = "one_shot"
    score_threshold = float(payload_value(payload, "score_threshold", VIKINGBOT_INITIAL_MIN_SCORE))
    tool_search_limit = int(payload_value(payload, "tool_search_limit", VIKINGBOT_TOOL_SEARCH_LIMIT))
    tool_min_score = float(payload_value(payload, "tool_min_score", VIKINGBOT_TOOL_MIN_SCORE))
    requested_tool_set = str(payload.get("tool_set") or payload.get("openviking_tool_set") or "search_read")
    tool_set = normalize_echomemory_tool_set(requested_tool_set)
    requested_retrieval_mode = str(payload.get("retrieval_mode") or "").strip().lower()
    retrieval_mode = normalize_payload_retrieval_mode(payload)
    top_k = int(payload_value(payload, "top_k", VIKINGBOT_INITIAL_SEARCH_LIMIT))
    max_iterations = int(payload_value(payload, "max_iterations", 8))
    qa_parallelism = int(payload_value(payload, "qa_parallelism", 10))
    answer_base_url = payload.get("answer_base_url") or payload.get("judge_base_url") or defaults.get("judge_base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    answer_model = payload.get("answer_model") or payload.get("judge_model") or defaults.get("answer_model") or defaults.get("judge_model") or "deepseek-v4-flash"
    judge_base_url = payload.get("judge_base_url") or answer_base_url
    judge_model = payload.get("judge_model") or answer_model
    command = [
        "/usr/bin/env",
        echomemory_python(payload),
        str(root / "scripts" / "run_longmemeval_parallel.py"),
        "--dataset",
        str(data),
        "--out-dir",
        str(out_dir),
        "--sample",
        str(payload.get("sample") or "all"),
        "--count",
        str(payload.get("count") or 0),
        "--shards",
        str(max(1, qa_parallelism)),
        "--workspace-root",
        workspace,
        "--account-prefix",
        str(payload.get("account") or defaults.get("account") or "longmemeval-parallel"),
        "--namespace-prefix",
        str(payload.get("memory_namespace") or payload.get("experiment_name") or run_dir.name),
    ]
    if payload.get("questions"):
        command += ["--questions", str(payload.get("questions") or "")]
    if payload.get("random_count"):
        command += ["--random-count", str(payload.get("random_count"))]
    _append_longmemeval_parallel_passthrough(
        command,
        payload,
        defaults,
        prompt_mode=prompt_mode,
        top_k=top_k,
        score_threshold=score_threshold,
        tool_search_limit=tool_search_limit,
        tool_min_score=tool_min_score,
        max_iterations=max_iterations,
        retrieval_mode=retrieval_mode,
        tool_set=tool_set,
        answer_base_url=str(answer_base_url),
        answer_model=str(answer_model),
        judge_base_url=str(judge_base_url),
        judge_model=str(judge_model),
    )
    return PluginTaskSpec(
        command=command,
        output_file=output_file,
        name=payload.get("name") or "longmemeval formal EchoMemory QA",
        metadata={
            **alignment_metadata("echomemory", "echomemory_generic_qa"),
            "task_kind": "echomemory_generic_qa",
            "dataset_format": "longmemeval",
            "backend": "echomemory",
            "workspace": workspace,
            "sample": str(payload.get("sample") or "all"),
            "prompt_mode": prompt_mode,
            "memory_tool_loop_enabled": False,
            "memory_tool_set": tool_set,
            "memory_tool_set_requested": requested_tool_set,
            "retrieval_mode": retrieval_mode,
            "retrieval_mode_requested": requested_retrieval_mode or retrieval_mode,
            "tool_search_limit": tool_search_limit,
            "tool_min_score": tool_min_score,
            "initial_search_limit": top_k,
            "initial_score_threshold": score_threshold,
            "initial_tool_prefetch_enabled": bool_value(payload.get("initial_tool_prefetch"), False),
            "prefetch_read_count": int(payload.get("prefetch_read_count") or 4),
            "prefetch_context_chars": int(payload.get("prefetch_context_chars") or 5000),
            "max_iterations": max_iterations,
            "top_k": top_k,
            "score_threshold": score_threshold,
            "identity_mode": "isolated_sample",
            "import_only": import_only,
            "official_eval_after": bool_value(payload.get("official_eval_after"), True),
            "qa_parallelism": qa_parallelism,
            "parallel_wrapper": True,
            "strict_ready_required": True,
        },
    )


def build_echomemory_import_command(
    payload: dict[str, Any],
    run_dir: Path,
    root: Path,
    default_data: Path,
    safe_path: SafePath,
) -> PluginTaskSpec:
    data = safe_path(str(payload.get("data") or str(default_data)))
    out_dir = safe_path(str(payload.get("echomemory_import_out_dir") or str(run_dir / "echomemory_import")))
    output_file = str(out_dir / "echomemory_import_summary.json")
    workspace = str(payload.get("workspace") or payload.get("echomemory_workspace") or str(run_dir / "echomemory_workspace"))
    echomem_base_url = str(payload.get("echomem_base_url") or payload.get("echomemBaseUrl") or "").strip()
    requested_transport = str(payload.get("echomem_transport") or payload.get("echomemTransport") or "").strip().lower()
    if not echomem_base_url:
        raise ValueError("LoCoMo EchoMemory import requires echomem_base_url; local SDK import is not a black-box run")
    if requested_transport not in {"", "http"}:
        raise ValueError("LoCoMo EchoMemory import only supports EchoMemory HTTP black-box transport")
    command = [
        "/usr/bin/env",
        echomemory_python(payload),
        str(root / "benchmark/locomo/echomemory/import_to_echomem.py"),
        "--dataset",
        str(data),
        "--out-dir",
        str(out_dir),
        "--echomem-root",
        echomem_root_value(payload),
        "--workspace",
        workspace,
        "--account",
        str(payload.get("account") or "default"),
        "--user-id",
        str(payload.get("user_id") or payload.get("em_user_id") or "default"),
        "--agent-id",
        str(payload.get("agent_id") or payload.get("em_agent_id") or "default"),
        "--sample",
        str(payload.get("sample") or "all"),
    ]
    if payload.get("echomem_config"):
        command += ["--echomem-config", str(payload["echomem_config"])]
    append_echomemory_transport_args(
        command,
        {
            **payload,
            "echomem_transport": "http",
            "echomem_base_url": echomem_base_url,
        },
    )
    if payload.get("session_mode"):
        command += ["--session-mode", str(payload["session_mode"])]
    if payload.get("session_start"):
        command += ["--session-start", str(payload["session_start"])]
    if payload.get("session_end"):
        command += ["--session-end", str(payload["session_end"])]
    if payload.get("max_sessions"):
        command += ["--max-sessions", str(payload["max_sessions"])]
    skip_session_commit = bool_value(payload.get("skip_session_commit"), False)
    import_wait_mode = str(payload.get("import_wait_mode") or ("fast" if bool_value(payload.get("defer_artifact_wait"), True) else "full")).strip().lower()
    defer_artifact_wait = bool_value(payload.get("defer_artifact_wait"), import_wait_mode == "fast")
    develop_full_wait = is_develop_echomem_root(Path(echomem_root_value(payload)).expanduser()) and not skip_session_commit
    if develop_full_wait:
        import_wait_mode = "full"
        defer_artifact_wait = False
    default_commit_wait, default_flush_timeout, default_flush_attempts = import_wait_defaults(
        skip_session_commit=skip_session_commit,
        defer_artifact_wait=defer_artifact_wait,
        develop_full_wait=develop_full_wait,
    )
    commit_wait_s = default_commit_wait if develop_full_wait else payload_value(payload, "commit_wait_s", default_commit_wait)
    flush_call_timeout_s = default_flush_timeout if develop_full_wait else payload_value(payload, "flush_call_timeout_s", default_flush_timeout)
    flush_attempts = default_flush_attempts if develop_full_wait else payload_value(payload, "flush_attempts", default_flush_attempts)
    command += [
        "--import-wait-mode",
        import_wait_mode,
        "--commit-wait-s",
        str(commit_wait_s),
        "--commit-call-timeout-s",
        str(payload_value(payload, "commit_call_timeout_s", 300)),
        "--flush-call-timeout-s",
        str(flush_call_timeout_s),
        "--flush-attempts",
        str(flush_attempts),
    ]
    if defer_artifact_wait:
        command.append("--defer-artifact-wait")
    if bool_value(payload.get("continue_on_session_error"), False):
        command.append("--continue-on-session-error")
    if bool_value(payload.get("fallback_to_mock"), False):
        command.append("--fallback-to-mock")
    if bool_value(payload.get("fallback_to_mock_embedding_only"), False):
        command.append("--fallback-to-mock-embedding-only")
    if bool_value(payload.get("skip_model_preflight"), False):
        command.append("--skip-model-preflight")
    if skip_session_commit:
        command.append("--skip-session-commit")
    return PluginTaskSpec(
        command=command,
        output_file=output_file,
        name=payload.get("name") or "LoCoMo EchoMemory import",
        metadata={
            "task_kind": "echomemory_import",
            "backend": "echomemory",
            "workspace": workspace,
            "sample": str(payload.get("sample") or "all"),
            "session_limit": int(payload.get("max_sessions") or 0),
            "echomem_transport": "http",
            "echomem_base_url": echomem_base_url,
            "evidence_policy": "blackbox",
            "platform_workspace_access_enabled": False,
        },
    )


def build_echomemory_qa_command(
    payload: dict[str, Any],
    run_dir: Path,
    config: Path,
    root: Path,
    default_data: Path,
    defaults: dict[str, Any],
    safe_path: SafePath,
    resolve_judge_token: ResolveToken,
) -> PluginTaskSpec:
    data = safe_path(str(payload.get("data") or str(default_data)))
    out_dir = safe_path(str(payload.get("echomemory_qa_out_dir") or str(run_dir / "echomemory_qa")))
    output_file = str(out_dir / "echomemory_memory_qa_results.csv")
    workspace = str(payload.get("workspace") or payload.get("echomemory_workspace") or str(run_dir / "echomemory_workspace"))
    token = payload.get("answer_token") or payload.get("judge_token") or resolve_judge_token(payload, config)
    echomem_base_url = str(payload.get("echomem_base_url") or payload.get("echomemBaseUrl") or "").strip()
    requested_transport = str(payload.get("echomem_transport") or payload.get("echomemTransport") or "").strip().lower()
    if not echomem_base_url:
        raise ValueError("LoCoMo EchoMemory QA requires echomem_base_url; local SDK evaluation is not a black-box run")
    if requested_transport not in {"", "http"}:
        raise ValueError("LoCoMo EchoMemory QA only supports EchoMemory HTTP black-box transport")
    prompt_mode = str(payload.get("prompt_mode") or "one_shot")
    if prompt_mode not in {"vikingboat_lite", "vikingboat_compat", "one_shot"}:
        prompt_mode = "one_shot"
    vikingboat_compat = bool_value(payload.get("vikingboat_compat"), prompt_mode == "vikingboat_compat")
    vikingboat_tool_loop = bool_value(payload.get("vikingboat_tool_loop"), False)
    initial_tool_prefetch = False
    max_iterations = int(payload_value(payload, "max_iterations", VIKINGBOT_MAX_ITERATIONS))
    score_threshold = float(payload_value(payload, "score_threshold", VIKINGBOT_INITIAL_MIN_SCORE))
    tool_search_limit = int(payload_value(payload, "tool_search_limit", VIKINGBOT_TOOL_SEARCH_LIMIT))
    tool_min_score = float(payload_value(payload, "tool_min_score", VIKINGBOT_TOOL_MIN_SCORE))
    requested_tool_set = str(payload.get("tool_set") or payload.get("openviking_tool_set") or "search_read")
    tool_set = normalize_echomemory_tool_set(requested_tool_set)
    requested_retrieval_mode = "search"
    retrieval_mode = "search"
    retrieval_source_mode = "echo_http_native"
    top_k = int(payload_value(payload, "top_k", VIKINGBOT_INITIAL_SEARCH_LIMIT))
    user_budget_chars = int(payload_value(payload, "user_memory_budget_chars", VIKINGBOT_USER_MEMORY_BUDGET_CHARS))
    agent_budget_chars = int(payload_value(payload, "agent_memory_budget_chars", VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS))
    memory_budget_chars = int(payload_value(payload, "memory_budget_chars", user_budget_chars + agent_budget_chars))
    qa_parallelism = int(payload_value(payload, "qa_parallelism", 5))
    judge_every = int(payload_value(payload, "judge_every", 10))
    judge_parallel = int(payload_value(payload, "judge_parallel", 6))
    qa_memory_injection = bool_value(payload.get("qa_memory_injection"), True)
    search_overview_enrichment = bool_value(payload.get("search_overview_enrichment"), False)
    overview_budget_chars = int(payload_value(payload, "overview_budget_chars", 3000))
    command = [
        "/usr/bin/env",
        "python3",
        str(root / "benchmark/locomo/echomemory/run_eval.py"),
        "--dataset",
        str(data),
        "--out-dir",
        str(out_dir),
        "--sample",
        str(payload.get("sample") or "conv-30"),
        "--echomem-root",
        echomem_root_value(payload),
        "--workspace",
        workspace,
        "--account",
        str(payload.get("account") or defaults.get("account") or "default"),
        "--user-id",
        str(payload.get("user_id") or payload.get("em_user_id") or "default"),
        "--agent-id",
        str(payload.get("agent_id") or payload.get("em_agent_id") or "default"),
        "--identity-mode",
        "sample_question",
        "--prompt-mode",
        prompt_mode,
        "--top-k",
        str(top_k),
        "--score-threshold",
        str(score_threshold),
        "--memory-budget-chars",
        str(memory_budget_chars),
        "--user-memory-budget-chars",
        str(user_budget_chars),
        "--agent-memory-budget-chars",
        str(agent_budget_chars),
        "--retrieval-mode",
        retrieval_mode,
        "--evidence-policy",
        "blackbox",
        "--retrieval-source-mode",
        retrieval_source_mode,
        "--answer-base-url",
        payload.get("answer_base_url") or payload.get("judge_base_url") or defaults.get("judge_base_url") or "",
        "--answer-model",
        payload.get("answer_model") or payload.get("judge_model") or defaults.get("answer_model") or defaults.get("judge_model") or "deepseek-v4-flash",
        "--judge-base-url",
        str(payload.get("judge_base_url") or payload.get("answer_base_url") or defaults.get("judge_base_url") or ""),
        "--judge-model",
        str(payload.get("judge_model") or payload.get("answer_model") or defaults.get("judge_model") or defaults.get("answer_model") or "deepseek-v4-flash"),
        "--judge-every",
        str(judge_every),
        "--judge-parallel",
        str(judge_parallel),
        "--judge-timeout-s",
        str(payload.get("judge_timeout_s") or payload.get("timeout_s") or 90),
        "--judge-retries",
        str(payload.get("judge_retries") or payload.get("model_retries") or 5),
        "--model-retries",
        str(payload.get("model_retries") or 5),
        "--timeout-s",
        str(payload.get("timeout_s") or 120),
        "--question-timeout-s",
        str(payload.get("question_timeout_s") or 600),
        "--qa-parallelism",
        str(qa_parallelism),
        "--tool-set",
        tool_set,
        "--tool-search-limit",
        str(tool_search_limit),
        "--tool-min-score",
        str(tool_min_score),
        "--tool-log-chars",
        str(payload.get("tool_log_chars") or 1200),
        "--prefetch-read-count",
        str(payload.get("prefetch_read_count") or 4),
        "--prefetch-context-chars",
        str(payload.get("prefetch_context_chars") or 5000),
        "--max-iterations",
        str(max_iterations),
        "--overview-budget-chars",
        str(max(0, overview_budget_chars)),
    ]
    command.append("--qa-memory-injection" if qa_memory_injection else "--no-qa-memory-injection")
    if payload.get("echomem_config"):
        command += ["--echomem-config", str(payload["echomem_config"])]
    http_payload = {
        **payload,
        "echomem_transport": "http",
        "echomem_base_url": echomem_base_url,
    }
    append_echomemory_transport_args(command, http_payload)
    command.append(
        "--search-overview-enrichment"
        if search_overview_enrichment
        else "--no-search-overview-enrichment"
    )
    command.extend([
        "--no-current-session-raw-fallback",
        "--no-segment-readback",
        "--no-precision-session-readback",
        "--no-precision-grounded-projection",
        "--no-longmemeval-current-session-summary-fallback",
        "--no-hotpot-empty-overview-fallback",
    ])
    if payload.get("questions"):
        command += ["--questions", str(payload.get("questions") or "")]
    if payload.get("random_count"):
        command += ["--random-count", str(payload.get("random_count"))]
    command.extend([
        "--no-local-session-summaries",
        "--no-local-atoms",
        "--no-local-messages",
        "--no-local-timeline-hints",
        "--no-local-memory-artifacts",
    ])
    if vikingboat_tool_loop:
        command.append("--vikingboat-tool-loop")
    else:
        command.append("--no-vikingboat-tool-loop")
    if vikingboat_compat:
        command.append("--vikingboat-compat")
    else:
        command.append("--no-vikingboat-compat")
    if initial_tool_prefetch:
        command.append("--initial-tool-prefetch")
    else:
        command.append("--no-initial-tool-prefetch")
    if bool_value(payload.get("fallback_to_one_shot"), False):
        command.append("--fallback-to-one-shot")
    else:
        command.append("--no-fallback-to-one-shot")
    if bool_value(payload.get("fallback_to_mock"), False):
        command.append("--fallback-to-mock")
    if bool_value(payload.get("fallback_to_mock_embedding_only"), False):
        command.append("--fallback-to-mock-embedding-only")
    return PluginTaskSpec(
        command=command,
        output_file=output_file,
        name=payload.get("name") or "LoCoMo EchoMemory QA",
        metadata={
            **alignment_metadata("echomemory", "custom_agent_echomemory_sdk_memory_tools"),
            "task_kind": "echomemory_qa",
            "backend": "echomemory",
            "workspace": workspace,
            "sample": str(payload.get("sample") or "conv-30"),
            "prompt_mode": prompt_mode,
            "vikingboat_compat": vikingboat_compat,
            "memory_tool_loop_enabled": vikingboat_tool_loop,
            "memory_tool_set": tool_set,
            "memory_tool_set_requested": requested_tool_set,
            "evidence_policy": "blackbox",
            "evidence_origin": "echomemory_http_api",
            "echomem_transport": "http",
            "echomem_base_url": echomem_base_url,
            "retrieval_mode": retrieval_mode,
            "retrieval_source_mode": retrieval_source_mode,
            "neo4j_graph_evidence_enabled": False,
            "search_overview_enrichment_enabled": search_overview_enrichment,
            "overview_transport": "echomemory_http_fs_read" if search_overview_enrichment else "disabled",
            "overview_budget_chars": max(0, overview_budget_chars),
            "platform_evidence_injection_enabled": False,
            "retrieval_mode_requested": requested_retrieval_mode or retrieval_mode,
            "identity_mode": "sample_question",
            "tool_search_limit": tool_search_limit,
            "tool_min_score": tool_min_score,
            "initial_search_limit": top_k,
            "initial_score_threshold": score_threshold,
            "initial_tool_prefetch_enabled": initial_tool_prefetch,
            "prefetch_read_count": int(payload.get("prefetch_read_count") or 4),
            "prefetch_context_chars": int(payload.get("prefetch_context_chars") or 5000),
            "max_iterations": max_iterations,
            "qa_parallelism": qa_parallelism,
            "judge_every": judge_every,
            "judge_parallel": judge_parallel,
            "qa_memory_injection_enabled": qa_memory_injection,
            "top_k": top_k,
            "score_threshold": score_threshold,
            "memory_budget_chars": memory_budget_chars,
            "user_memory_budget_chars": user_budget_chars,
            "agent_memory_budget_chars": agent_budget_chars,
            "local_messages": False,
            "local_session_summaries": False,
            "local_atoms": False,
            "local_timeline_hints": False,
        },
    )


def build_echomemory_generic_qa_command(
    payload: dict[str, Any],
    run_dir: Path,
    config: Path,
    root: Path,
    default_data: Path,
    defaults: dict[str, Any],
    safe_path: SafePath,
    resolve_judge_token: ResolveToken,
) -> PluginTaskSpec:
    data = safe_path(str(payload.get("data") or str(default_data)))
    fmt = str(payload.get("dataset_format") or payload.get("format") or "generic")
    hotpotqa_corpus_mode = str(payload.get("hotpotqa_corpus_mode") or "per_question_documents").strip().lower()
    if hotpotqa_corpus_mode not in {"per_question_documents", "global_sentence_corpus"}:
        hotpotqa_corpus_mode = "per_question_documents"
    hotpotqa_global_import_mode = str(payload.get("hotpotqa_global_import_mode") or "projection").strip().lower()
    if hotpotqa_global_import_mode not in {"projection", "messages"}:
        hotpotqa_global_import_mode = "projection"
    hotpotqa_projection_embed_batch_size = int(payload_value(payload, "hotpotqa_projection_embed_batch_size", 10))
    retrieval_query_strategy = str(
        payload.get("retrieval_query_strategy")
        or ("direct" if fmt == "hotpotqa" else "direct")
    ).strip().lower()
    if retrieval_query_strategy not in {"expanded", "direct"}:
        retrieval_query_strategy = "direct"
    answer_refinement_enabled = bool_value(payload.get("answer_refinement"), fmt == "hotpotqa")
    checkpoint_interval = max(0, int(payload_value(payload, "checkpoint_interval", 100)))
    out_dir = safe_path(str(payload.get("echomemory_generic_qa_out_dir") or str(run_dir / "echomemory_generic_qa")))
    output_file = str(out_dir / "echomemory_generic_qa_results.csv")
    workspace = str(payload.get("workspace") or payload.get("echomemory_workspace") or str(run_dir / "echomemory_workspace"))
    token = payload.get("answer_token") or payload.get("judge_token") or resolve_judge_token(payload, config)
    prompt_mode = str(payload.get("prompt_mode") or payload.get("qa_prompt_mode") or "one_shot").strip() or "one_shot"
    if prompt_mode not in {"one_shot", "vikingboat_lite", "vikingboat_compat"}:
        prompt_mode = "one_shot"
    vikingboat_compat = bool_value(payload.get("vikingboat_compat"), prompt_mode == "vikingboat_compat")
    vikingboat_tool_loop = bool_value(payload.get("vikingboat_tool_loop"), False)
    initial_tool_prefetch = bool_value(payload.get("initial_tool_prefetch"), False)
    if fmt == "hotpotqa":
        vikingboat_tool_loop = False
        initial_tool_prefetch = False
    max_iterations = int(payload_value(payload, "max_iterations", VIKINGBOT_MAX_ITERATIONS if vikingboat_compat else 8))
    score_threshold = float(payload_value(payload, "score_threshold", VIKINGBOT_INITIAL_MIN_SCORE))
    tool_search_limit = int(payload_value(payload, "tool_search_limit", VIKINGBOT_TOOL_SEARCH_LIMIT))
    tool_min_score = float(payload_value(payload, "tool_min_score", VIKINGBOT_TOOL_MIN_SCORE))
    requested_tool_set = str(payload.get("tool_set") or payload.get("openviking_tool_set") or "search_read")
    tool_set = normalize_echomemory_tool_set(requested_tool_set)
    requested_retrieval_mode = str(payload.get("retrieval_mode") or "").strip().lower()
    retrieval_mode = normalize_payload_retrieval_mode(payload)
    top_k = int(payload_value(payload, "top_k", 8 if fmt == "hotpotqa" else VIKINGBOT_INITIAL_SEARCH_LIMIT))
    user_budget_chars = int(payload_value(payload, "user_memory_budget_chars", VIKINGBOT_USER_MEMORY_BUDGET_CHARS))
    agent_budget_chars = int(payload_value(payload, "agent_memory_budget_chars", VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS))
    memory_budget_chars = int(payload_value(payload, "memory_budget_chars", user_budget_chars + agent_budget_chars))
    answer_base_url = payload.get("answer_base_url") or payload.get("judge_base_url") or defaults.get("judge_base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    answer_model = payload.get("answer_model") or payload.get("judge_model") or defaults.get("answer_model") or defaults.get("judge_model") or "deepseek-v4-flash"
    judge_base_url = payload.get("judge_base_url") or answer_base_url
    judge_model = payload.get("judge_model") or answer_model
    auto_judge = bool_value(payload.get("auto_judge"), fmt != "hotpotqa")
    skip_session_commit = bool_value(payload.get("skip_session_commit"), False)
    import_wait_mode = str(payload.get("import_wait_mode") or ("fast" if bool_value(payload.get("defer_artifact_wait"), True) else "full")).strip().lower()
    defer_artifact_wait = bool_value(payload.get("defer_artifact_wait"), import_wait_mode == "fast")
    strict_ready_required = fmt in STRICT_READY_DATASET_FORMATS
    develop_full_wait = is_develop_echomem_root(Path(echomem_root_value(payload)).expanduser()) and not skip_session_commit
    if strict_ready_required or develop_full_wait:
        import_wait_mode = "full"
        defer_artifact_wait = False
    if fmt == "longmemeval":
        return build_echomemory_longmemeval_parallel_command(
            payload,
            run_dir,
            config,
            root,
            default_data,
            defaults,
            safe_path,
            resolve_judge_token,
        )
    default_commit_wait, default_flush_timeout, default_flush_attempts = import_wait_defaults(
        skip_session_commit=skip_session_commit,
        defer_artifact_wait=defer_artifact_wait,
        develop_full_wait=develop_full_wait,
    )
    command = [
        "/usr/bin/env",
        echomemory_python(payload),
        str(root / "scripts/echomemory_generic_qa.py"),
        "--dataset",
        str(data),
        "--format",
        fmt,
        "--out-dir",
        str(out_dir),
        "--namespace",
        str(payload.get("memory_namespace") or payload.get("experiment_name") or run_dir.name),
        "--sample",
        str(payload.get("sample") or "all"),
        "--echomem-root",
        echomem_root_value(payload),
        "--workspace",
        workspace,
        "--account",
        str(payload.get("account") or defaults.get("account") or "default"),
        "--user-id",
        str(payload.get("user_id") or payload.get("em_user_id") or "default"),
        "--agent-id",
        str(payload.get("agent_id") or payload.get("em_agent_id") or "default"),
        "--identity-mode",
        str(payload.get("identity_mode") or "isolated_sample"),
        "--user-prefix",
        str(payload.get("user_prefix") or "eval-user"),
        "--agent-prefix",
        str(payload.get("agent_prefix") or "eval-agent"),
        "--prompt-mode",
        prompt_mode,
        "--top-k",
        str(top_k),
        "--score-threshold",
        str(score_threshold),
        "--memory-budget-chars",
        str(memory_budget_chars),
        "--user-memory-budget-chars",
        str(user_budget_chars),
        "--agent-memory-budget-chars",
        str(agent_budget_chars),
        "--retrieval-mode",
        retrieval_mode,
        "--answer-base-url",
        str(answer_base_url),
        "--answer-model",
        str(answer_model),
        "--judge-base-url",
        str(judge_base_url),
        "--judge-model",
        str(judge_model),
        "--judge-parallel",
        str(payload.get("judge_parallel") or payload.get("parallel") or 4),
        "--model-retries",
        str(payload.get("model_retries") or 5),
        "--timeout-s",
        str(payload.get("timeout_s") or 120),
        "--question-timeout-s",
        str(payload.get("question_timeout_s") or 600),
        "--retrieval-query-strategy",
        retrieval_query_strategy,
        "--tool-set",
        tool_set,
        "--tool-search-limit",
        str(tool_search_limit),
        "--tool-min-score",
        str(tool_min_score),
        "--tool-log-chars",
        str(payload.get("tool_log_chars") or 1200),
        "--prefetch-read-count",
        str(payload.get("prefetch_read_count") or 4),
        "--prefetch-context-chars",
        str(payload.get("prefetch_context_chars") or 5000),
        "--max-iterations",
        str(max_iterations),
        "--import-wait-mode",
        import_wait_mode,
        "--commit-wait-s",
        str(payload_value(payload, "commit_wait_s", default_commit_wait)),
        "--commit-call-timeout-s",
        str(payload_value(payload, "commit_call_timeout_s", 300)),
        "--flush-call-timeout-s",
        str(payload_value(payload, "flush_call_timeout_s", default_flush_timeout)),
        "--flush-attempts",
        str(payload_value(payload, "flush_attempts", default_flush_attempts)),
    ]
    if payload.get("echomem_config"):
        command += ["--echomem-config", str(payload["echomem_config"])]
    append_echomemory_transport_args(command, payload)
    if payload.get("count") not in (None, ""):
        command += ["--count", str(payload.get("count"))]
    if payload.get("questions"):
        command += ["--questions", str(payload.get("questions") or "")]
    if payload.get("runtime_recycle_every") not in (None, ""):
        command += ["--runtime-recycle-every", str(payload.get("runtime_recycle_every"))]
    if payload.get("import_timeout_s") not in (None, ""):
        command += ["--import-timeout-s", str(payload.get("import_timeout_s"))]
    if fmt == "hotpotqa":
        command += [
            "--hotpotqa-corpus-mode",
            hotpotqa_corpus_mode,
            "--hotpotqa-global-import-mode",
            hotpotqa_global_import_mode,
            "--hotpotqa-projection-embed-batch-size",
            str(hotpotqa_projection_embed_batch_size),
        ]
    if fmt in {"hotpotqa", "longmemeval"}:
        command += [
            "--checkpoint-interval",
            str(checkpoint_interval),
        ]
    if bool_value(payload.get("import_only"), False):
        command.append("--import-only")
    if bool_value(payload.get("retry_failed"), False):
        command.append("--retry-failed")
    if bool_value(payload.get("retry_empty_answers"), False):
        command.append("--retry-empty-answers")
    if bool_value(payload.get("resume"), True) is False:
        command.append("--no-resume")
    if defer_artifact_wait:
        command.append("--defer-artifact-wait")
    if bool_value(payload.get("continue_on_session_error"), False):
        command.append("--continue-on-session-error")
    if bool_value(payload.get("fallback_to_mock"), False):
        command.append("--fallback-to-mock")
    if bool_value(payload.get("fallback_to_mock_embedding_only"), False):
        command.append("--fallback-to-mock-embedding-only")
    if skip_session_commit:
        command.append("--skip-session-commit")
    if vikingboat_tool_loop:
        command.append("--vikingboat-tool-loop")
    else:
        command.append("--no-vikingboat-tool-loop")
    if vikingboat_compat:
        command.append("--vikingboat-compat")
    else:
        command.append("--no-vikingboat-compat")
    if initial_tool_prefetch:
        command.append("--initial-tool-prefetch")
    else:
        command.append("--no-initial-tool-prefetch")
    if bool_value(payload.get("fallback_to_one_shot"), True):
        command.append("--fallback-to-one-shot")
    else:
        command.append("--no-fallback-to-one-shot")
    if bool_value(payload.get("toolloop_rescue_on_toollike_answer"), False):
        command.append("--toolloop-rescue-on-toollike-answer")
    else:
        command.append("--no-toolloop-rescue-on-toollike-answer")
    if answer_refinement_enabled:
        command.append("--answer-refinement")
    if auto_judge:
        command.append("--judge-after")
    if bool_value(payload.get("official_eval_after"), fmt in {"longmemeval", "hotpotqa"}):
        command.append("--official-eval-after")
    if retrieval_mode == "local":
        if bool_value(payload.get("local_session_summaries"), False):
            command.append("--local-session-summaries")
        else:
            command.append("--no-local-session-summaries")
        if bool_value(payload.get("local_atoms"), False):
            command.append("--local-atoms")
        else:
            command.append("--no-local-atoms")
        if bool_value(payload.get("local_messages"), False):
            command.append("--local-messages")
        else:
            command.append("--no-local-messages")
        if bool_value(payload.get("local_timeline_hints"), False):
            command.append("--local-timeline-hints")
        else:
            command.append("--no-local-timeline-hints")
        if bool_value(payload.get("local_memory_artifacts"), False):
            command.append("--local-memory-artifacts")
        else:
            command.append("--no-local-memory-artifacts")
    else:
        command.extend([
            "--no-local-session-summaries",
            "--no-local-atoms",
            "--no-local-messages",
            "--no-local-timeline-hints",
            "--no-local-memory-artifacts",
        ])
    return PluginTaskSpec(
        command=command,
        output_file=output_file,
        name=payload.get("name") or f"{fmt} formal EchoMemory QA",
        metadata={
            **alignment_metadata("echomemory", "echomemory_generic_qa"),
            "task_kind": "echomemory_generic_qa",
            "dataset_format": fmt,
            "hotpotqa_corpus_mode": hotpotqa_corpus_mode if fmt == "hotpotqa" else "",
            "hotpotqa_global_import_mode": hotpotqa_global_import_mode if fmt == "hotpotqa" else "",
            "hotpotqa_projection_embed_batch_size": hotpotqa_projection_embed_batch_size if fmt == "hotpotqa" else 0,
            "checkpoint_interval": checkpoint_interval if fmt in {"hotpotqa", "longmemeval"} else 0,
            "backend": "echomemory",
            "workspace": workspace,
            "sample": str(payload.get("sample") or "all"),
            "prompt_mode": prompt_mode,
            "vikingboat_compat": vikingboat_compat,
            "memory_tool_loop_enabled": vikingboat_tool_loop,
            "memory_tool_set": tool_set,
            "memory_tool_set_requested": requested_tool_set,
            "retrieval_mode": retrieval_mode,
            "retrieval_mode_requested": requested_retrieval_mode or retrieval_mode,
            "tool_search_limit": tool_search_limit,
            "tool_min_score": tool_min_score,
            "initial_search_limit": top_k,
            "initial_score_threshold": score_threshold,
            "initial_tool_prefetch_enabled": initial_tool_prefetch,
            "prefetch_read_count": int(payload.get("prefetch_read_count") or 4),
            "prefetch_context_chars": int(payload.get("prefetch_context_chars") or 5000),
            "max_iterations": max_iterations,
            "top_k": top_k,
            "score_threshold": score_threshold,
            "memory_budget_chars": memory_budget_chars,
            "user_memory_budget_chars": user_budget_chars,
            "agent_memory_budget_chars": agent_budget_chars,
            "identity_mode": str(payload.get("identity_mode") or "isolated_sample"),
            "auto_judge": auto_judge,
            "import_only": bool_value(payload.get("import_only"), False),
            "strict_ready_required": strict_ready_required,
            "retrieval_query_strategy": retrieval_query_strategy,
            "answer_refinement": answer_refinement_enabled,
        },
    )

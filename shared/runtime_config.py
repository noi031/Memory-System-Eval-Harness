"""Runtime configuration loading for the one-command benchmark entrypoint."""

from __future__ import annotations

import json
import os
import shlex
import urllib.request
from pathlib import Path
from typing import Any


DATASET_ENV = {
    "locomo": "LOCOMO_DATASET",
    "hotpotqa": "HOTPOTQA_DATASET",
    "longmemeval": "LONGMEMEVAL_DATASET",
}

RUNTIME_ARG_ENV = {
    "--echomem-url": "ECHOMEM_BASE_URL",
    "--echomem-auth-key": "ECHOMEM_AUTH_KEY",
    "--account": "ECHOMEM_ACCOUNT",
    "--user-id": "ECHOMEM_USER_ID",
    "--agent-id": "ECHOMEM_AGENT_ID",
    "--workspace": "ECHOMEM_WORKSPACE",
    "--llm-base-url": "LLM_BASE_URL",
    "--llm-model": "LLM_MODEL",
    "--llm-api-key": "LLM_API_KEY",
    "--judge-base-url": "JUDGE_BASE_URL",
    "--judge-model": "JUDGE_MODEL",
    "--judge-api-key": "JUDGE_TOKEN",
}


def load_env_file(path: str | Path, *, override: bool = False) -> Path | None:
    env_path = Path(path).expanduser()
    if not env_path.exists():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if value and value[0] in {'"', "'"}:
            try:
                parts = shlex.split(value, comments=False, posix=True)
                value = parts[0] if parts else ""
            except ValueError:
                value = value.strip("\"'")
        if override or key not in os.environ:
            os.environ[key] = value
    return env_path


def _nested(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first_secret(value: Any, prefix: str) -> str:
    if isinstance(value, str) and value.startswith(prefix):
        return value
    if isinstance(value, dict):
        for item in value.values():
            found = _first_secret(item, prefix)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _first_secret(item, prefix)
            if found:
                return found
    return ""


def _set_default(name: str, *values: Any) -> None:
    if os.environ.get(name):
        return
    for value in values:
        text = str(value or "").strip()
        if text:
            os.environ[name] = text
            return


def prepare_runtime_environment(project_root: str | Path, env_file: str = ".env") -> dict[str, Any]:
    root = Path(project_root).resolve()
    env_path = Path(env_file).expanduser()
    if not env_path.is_absolute():
        env_path = root / env_path
    loaded_env = load_env_file(env_path)

    workspace_text = os.environ.get("ECHOMEM_WORKSPACE", "").strip()
    workspace = Path(workspace_text).expanduser() if workspace_text else None
    config_text = os.environ.get("ECHOMEM_CONFIG", "").strip()
    config_path = Path(config_text).expanduser() if config_text else None
    if config_path is None and workspace is not None:
        candidate = workspace / "config.json"
        if candidate.exists():
            config_path = candidate

    config: dict[str, Any] = {}
    if config_path is not None and config_path.exists():
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            config = payload

    _set_default("LLM_BASE_URL", os.environ.get("ANSWER_BASE_URL"), _nested(config, "model.llm.api_base"))
    _set_default("LLM_MODEL", os.environ.get("ANSWER_MODEL"), _nested(config, "model.llm.model"))
    _set_default("LLM_API_KEY", os.environ.get("ANSWER_TOKEN"), _nested(config, "model.llm.api_key"))
    _set_default("JUDGE_BASE_URL", os.environ.get("LLM_BASE_URL"))
    _set_default("JUDGE_MODEL", os.environ.get("LLM_MODEL"))
    _set_default("JUDGE_TOKEN", os.environ.get("LLM_API_KEY"))

    auth_file_text = os.environ.get("ECHOMEM_AUTH_FILE", "").strip()
    auth_path = Path(auth_file_text).expanduser() if auth_file_text else None
    if auth_path is None and workspace is not None:
        candidate = workspace / ".echomem_http_auth_keys.json"
        if candidate.exists():
            auth_path = candidate
    if not os.environ.get("ECHOMEM_AUTH_KEY") and auth_path is not None and auth_path.exists():
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
        auth_key = _first_secret(payload, "ek_")
        if auth_key:
            os.environ["ECHOMEM_AUTH_KEY"] = auth_key

    return {
        "env_file": str(loaded_env or ""),
        "workspace": str(workspace or ""),
        "config_file": str(config_path or ""),
        "auth_file": str(auth_path or ""),
    }


def dataset_argument(benchmark: str, forwarded_args: list[str]) -> list[str]:
    if "--dataset" in forwarded_args or any(arg.startswith("--dataset=") for arg in forwarded_args):
        return forwarded_args
    env_name = DATASET_ENV.get(benchmark)
    dataset = os.environ.get(env_name or "", "").strip()
    if dataset:
        return [*forwarded_args, "--dataset", dataset]
    return forwarded_args


def apply_cli_runtime_overrides(forwarded_args: list[str]) -> None:
    """Make explicit common CLI options visible to preflight and service startup."""
    for index, argument in enumerate(forwarded_args):
        for option, env_name in RUNTIME_ARG_ENV.items():
            if argument == option and index + 1 < len(forwarded_args):
                os.environ[env_name] = forwarded_args[index + 1]
                break
            prefix = option + "="
            if argument.startswith(prefix):
                os.environ[env_name] = argument[len(prefix):]
                break


def runtime_check(benchmark: str, forwarded_args: list[str]) -> list[str]:
    errors: list[str] = []
    base_url = os.environ.get("ECHOMEM_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
    headers = {}
    if os.environ.get("ECHOMEM_AUTH_KEY"):
        headers["X-Auth-Key"] = os.environ["ECHOMEM_AUTH_KEY"]
    try:
        request = urllib.request.Request(f"{base_url}/health", headers=headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200:
                errors.append(f"EchoMem health returned HTTP {response.status}")
    except Exception as exc:
        errors.append(f"EchoMem unavailable at {base_url}: {exc}")

    for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        if not os.environ.get(name, "").strip():
            errors.append(f"missing {name}")

    args = dataset_argument(benchmark, forwarded_args)
    dataset_value = ""
    if "--dataset" in args:
        index = args.index("--dataset")
        if index + 1 < len(args):
            dataset_value = args[index + 1]
    else:
        dataset_value = next(
            (arg.split("=", 1)[1] for arg in args if arg.startswith("--dataset=")),
            "",
        )
    if dataset_value:
        dataset = Path(dataset_value).expanduser()
        if not dataset.exists():
            errors.append(f"dataset not found: {dataset}")
    return errors

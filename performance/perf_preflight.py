"""Model/config preflight gate for stress runs (top-level goal 2.13).

Reads the engine configuration actually used, checks every ``api_key_env``
variable is set and non-empty, sends one minimal real request per
LLM/embedding endpoint and verifies the model name is accepted. Any failure
stops the run and is classified as an environment/dependency error, never
attributed to the code under test.

API keys are referenced by environment-variable name only; a SHA-256 digest
of the resolved configuration (endpoints and model names, no secrets) is
recorded for change detection before fault injection.

Config shape (JSON)::

    {"engines": [
        {"id": "atomic_engine", "kind": "llm", "api_key_env": "ARK_API_KEY",
         "api_base": "https://...", "model": "doubao-seed-2.0-pro"}
    ]}

A bare list of engine dicts is accepted too.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = ("id", "kind", "api_base", "model")


def parse_engine_configs(path: str | Path) -> list[dict[str, Any]]:
    """Load and normalize engine configs from a JSON file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        entries = raw.get("engines") or [raw]
    elif isinstance(raw, list):
        entries = raw
    else:
        raise ValueError("engine config 必须是 JSON 对象或对象数组")
    engines: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("engine config 条目必须是 JSON 对象")
        for field in REQUIRED_FIELDS:
            if not str(entry.get(field) or "").strip():
                raise ValueError(f"engine config 缺字段: {field}")
        engines.append(
            {
                "id": str(entry["id"]),
                "kind": str(entry.get("kind") or "llm").lower(),
                "api_key_env": str(entry.get("api_key_env") or ""),
                "api_base": str(entry["api_base"]).rstrip("/"),
                "model": str(entry["model"]),
            }
        )
    return engines


def config_digest(engines: list[dict[str, Any]]) -> str:
    """SHA-256 over endpoints/model names (never over API key values)."""
    canonical = json.dumps(engines, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def check_env(engines: list[dict[str, Any]]) -> list[str]:
    """Every referenced ``api_key_env`` variable must exist and be non-empty."""
    errors: list[str] = []
    for engine in engines:
        env_name = engine["api_key_env"]
        if not env_name:
            continue
        if not os.environ.get(env_name, "").strip():
            errors.append(
                f"{engine['id']}: 环境变量 {env_name} 不存在或为空"
            )
    return errors


def probe_endpoint(engine: dict[str, Any], *, timeout_s: float = 20.0) -> dict[str, Any]:
    """One minimal real request against the engine endpoint.

    ``kind=llm`` probes ``{api_base}/chat/completions``; ``kind=embedding``
    probes ``{api_base}/embeddings``. A 2xx response means the model name is
    accepted; any HTTP error or timeout marks the engine as failed.
    """
    api_key = os.environ.get(engine["api_key_env"], "") if engine["api_key_env"] else ""
    if engine["kind"] == "embedding":
        path = "/embeddings"
        body: dict[str, Any] = {"model": engine["model"], "input": "ping"}
    else:
        path = "/chat/completions"
        body = {
            "model": engine["model"],
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
    url = f"{engine['api_base']}{path}"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    started = time.monotonic()
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            resp.read()
        return {
            "id": engine["id"],
            "kind": engine["kind"],
            "api_base": engine["api_base"],
            "model": engine["model"],
            "model_supported": True,
            "status": "ok",
            "code": resp.status,
            "elapsed_s": round(time.monotonic() - started, 3),
            "error": "",
        }
    except urllib.error.HTTPError as exc:
        return {
            "id": engine["id"],
            "kind": engine["kind"],
            "api_base": engine["api_base"],
            "model": engine["model"],
            "model_supported": False,
            "status": "error",
            "code": exc.code,
            "elapsed_s": round(time.monotonic() - started, 3),
            "error": f"HTTP {exc.code}（模型 {engine['model']} 可能不被该 endpoint 支持）",
        }
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        return {
            "id": engine["id"],
            "kind": engine["kind"],
            "api_base": engine["api_base"],
            "model": engine["model"],
            "model_supported": False,
            "status": "error",
            "code": None,
            "elapsed_s": round(time.monotonic() - started, 3),
            "error": f"endpoint 不可达/超时: {exc}",
        }


def run_preflight(
    config_path: str | Path,
    *,
    timeout_s: float = 20.0,
) -> dict[str, Any]:
    """Run the full preflight gate; returns a structured, secret-free result.

    ``ok`` is True only when every engine passes environment and real-request
    checks. On failure the caller must stop the run and classify the result
    as an environment/dependency error.
    """
    try:
        engines = parse_engine_configs(config_path)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "error": f"配置读取失败: {exc}",
            "engines_checked": 0,
            "engines": [],
            "digest": "",
        }
    env_errors = check_env(engines)
    probes = [probe_endpoint(engine, timeout_s=timeout_s) for engine in engines]
    failures = [
        entry for entry in probes if entry["status"] != "ok"
    ]
    if env_errors:
        return {
            "ok": False,
            "error": "; ".join(env_errors),
            "engines_checked": len(engines),
            "engines": probes,
            "digest": config_digest(engines),
        }
    if failures:
        return {
            "ok": False,
            "error": failures[0]["error"],
            "engines_checked": len(engines),
            "engines": probes,
            "digest": config_digest(engines),
        }
    return {
        "ok": True,
        "error": "",
        "engines_checked": len(engines),
        "engines": probes,
        "digest": config_digest(engines),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="EchoMem model/config preflight gate")
    parser.add_argument("config", help="engine 配置文件路径 (JSON)")
    parser.add_argument("--timeout-s", type=float, default=20.0)
    args = parser.parse_args()
    result = run_preflight(args.config, timeout_s=args.timeout_s)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()

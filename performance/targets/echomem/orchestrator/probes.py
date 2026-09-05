"""探针编排：把 instance profile 的探针配置段展开为真实 HTTP 探针运行。

``run_configured_probes`` 逐个执行 profile 里显式配置的探针段（capability /
blackbox / missing_cases / concurrent_commit / fault_isolation /
limit_failure_sweep / commit_recovery / fault_plan），每个探针以子进程方式
运行：写临时 YAML profile → ``python -m performance --target general probe
--scene <probes>/<scene>.py --profile <tmp.yaml> --out <out.json>`` → 读回
产物 → 删除临时文件。未配置的探针不跑；缺前置条件（如 blackbox 需要已完成
Commit 与租户配置）只记 INCONCLUSIVE 命令记录，不产出制品。

返回 (artifacts, commands)：artifacts 的键即 suite 顶层合并键（如
``capability_probe``/``commit_recovery``/``fault_suite``），值带 ``path``；
commands 是 run_command 结果（或 INCONCLUSIVE 标记）的列表。
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from performance.util import expand_template, read_json, run_command

PROBES_DIR = Path(__file__).resolve().parent.parent / "probes"

_AUTH_HEADER_NAMES = {"x-auth-key", "authorization"}


def _preserve_probe_status(
    execution: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """探针级 INCONCLUSIVE/NOT_IMPLEMENTED 不被子进程失败掩盖。"""
    if (
        execution.get("status") == "FAIL"
        and payload.get("status") in {"INCONCLUSIVE", "NOT_IMPLEMENTED"}
    ):
        execution["status"] = payload["status"]
    return execution


def _first_completed_commit_csv(formal_root: Path) -> tuple[Path, str] | None:
    """在套件目录里找第一条已完成 Commit 的 commit_results.csv 及租户标识。"""
    candidates = sorted(formal_root.glob("**/commit_results.csv"))
    for path in candidates:
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, csv.Error):
            continue
        for row in rows:
            if (
                str(row.get("status") or "").lower()
                in {"completed", "complete", "success", "succeeded"}
            ):
                return path, str(row.get("tenant") or "")
    return None


def _resolve_auth_key(
    tenant_config: dict[str, Any],
    tenant_selector: str = "",
) -> tuple[str, str]:
    """解析产出证据那个租户的凭据，返回 (auth_key, auth_key_env)。

    ``commit_results.csv`` 历史上记录的是零基租户下标或租户 id；如果选中了
    错误租户的 key，合法 session 会表现为 HTTP 400，所以选择必须跟随证据行。
    """
    entries = (tenant_config or {}).get("tenants") or []
    if not isinstance(entries, list) or not entries:
        return "", ""
    item: dict[str, Any] | None = None
    selector = str(tenant_selector or "").strip()
    if selector.isdigit():
        index = int(selector)
        if 0 <= index < len(entries) and isinstance(entries[index], dict):
            item = entries[index]
    if item is None:
        for candidate in entries:
            if (
                isinstance(candidate, dict)
                and str(candidate.get("tenant_id") or candidate.get("id") or "").strip()
                == selector
            ):
                item = candidate
                break
    if item is None and not selector and isinstance(entries[0], dict):
        item = entries[0]
    if item is None:
        return "", ""
    direct = str(item.get("auth_key") or "")
    env_name = str(item.get("auth_key_env") or "")
    return direct or os.getenv(env_name, ""), env_name


def _resolve_tenant_id(tenant_config: dict[str, Any], requested: str) -> str:
    """用配置里的租户，配置过期时回退到第一个真实租户。"""
    requested = str(requested or "").strip()
    entries = (tenant_config or {}).get("tenants") or []
    if not isinstance(entries, list):
        return requested
    for item in entries:
        if not isinstance(item, dict):
            continue
        tenant_id = str(
            item.get("tenant_id") or item.get("id") or item.get("user_id") or ""
        ).strip()
        if tenant_id == requested:
            return tenant_id
    for item in entries:
        if isinstance(item, dict):
            tenant_id = str(
                item.get("tenant_id") or item.get("id") or item.get("user_id") or ""
            ).strip()
            if tenant_id:
                return tenant_id
    return requested


def _materialize_fault_plan(
    plan_path: Path,
    *,
    base_url: str,
    output_path: Path,
) -> Path:
    """把 run-local fault plan 里的服务地址解析为实际 base_url。"""
    payload = read_json(plan_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            expand_template(payload, {"BASE_URL": base_url.rstrip("/")}),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return output_path


def _write_probe_profile(base_url: str, params: dict[str, Any]) -> Path:
    """把探针 profile 物化为临时 YAML 供探针 CLI 使用。

    ``base_url`` 落在 ``target.base_url``；探针参数保留原始类型。调用方负责
    在运行结束后删除返回的临时文件。
    """
    fd, path = tempfile.mkstemp(prefix="objective-probe-", suffix=".yaml", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(
                {
                    "name": "objective-probe",
                    "target": {"base_url": base_url},
                    "params": params,
                },
                handle,
                allow_unicode=True,
                sort_keys=False,
            )
    except Exception:
        os.unlink(path)
        raise
    return Path(path)


def _probe_command(scene: str, profile_path: Path, out_path: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "performance",
        "--target",
        "general",
        "probe",
        "--scene",
        str(PROBES_DIR / scene),
        "--profile",
        str(profile_path),
        "--out",
        str(out_path),
    ]


def _run_limit_failure_sweep(
    profile: dict[str, Any],
    *,
    suite_dir: Path,
    timeout_s: float,
    quick: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """在正式套件之后跑可选的真实限流阶梯。

    正式 ``saturation`` case 只测 Commit 忙时 Search，不保证出现队列满响应，
    无法单独证明 429/503/Retry-After/reason_code 契约。本阶梯显式驱动公开
    端点并保留原始行为供审计。
    """
    config = profile.get("limit_failure_sweep")
    if not isinstance(config, dict):
        return {}, [{
            "status": "INCONCLUSIVE",
            "reason": "profile 未配置真实限流阶梯",
        }]

    tenant_path = Path(str(profile.get("tenant_config") or ""))
    output = suite_dir / "limit-failure-sweep"
    output.mkdir(parents=True, exist_ok=True)
    levels = str(config.get("levels") or "16,64,128,256")
    search_count = config.get("search_count")
    open_count = config.get("open_count")
    commit_count = config.get("commit_count")
    workers = config.get("workers")
    probe_timeout_s = config.get("timeout_s") or 8.0
    if quick:
        # quick 目标运行必须保持诊断性且有界；正式套件已有墙钟上限，配置的
        # 限流阶梯会按波次引入数百请求让整个运行看起来卡住。
        levels = "4,16"
        search_count = min(int(search_count or 16), 16)
        open_count = min(int(open_count or 8), 8)
        commit_count = min(int(commit_count or 8), 8)
        workers = min(int(workers or 16), 16)
        probe_timeout_s = min(float(probe_timeout_s), 5.0)
    base_url = str(profile.get("base_url") or "http://127.0.0.1:8010")
    params: dict[str, Any] = {
        "tenant_config": str(tenant_path),
        "out_dir": str(output),
        "levels": levels,
        "timeout_s": probe_timeout_s,
    }
    session_root = str(config.get("session_root") or "").strip()
    if session_root:
        params["session_root"] = str(Path(session_root).expanduser().resolve())
    else:
        # 新 session 让阶梯不依赖正式套件先完成哪个 case，避免跨 run 污染。
        params["session_root"] = str(suite_dir)
        params["create_sessions"] = True
    for key in ("search_count", "open_count", "commit_count", "workers"):
        value = {
            "search_count": search_count,
            "open_count": open_count,
            "commit_count": commit_count,
            "workers": workers,
        }.get(key)
        if value not in (None, ""):
            params[key] = value
    profile_path = _write_probe_profile(base_url, params)
    command = _probe_command("limit_failure_sweep.py", profile_path, output / "probe-report.json")
    execution = run_command(command, timeout_s=min(timeout_s, 180 if quick else 1800))
    profile_path.unlink(missing_ok=True)
    commands: list[dict[str, Any]] = [execution]
    summary_path = output / "summary.json"
    payload = read_json(summary_path)
    if not payload:
        return {}, commands
    return {
        "limit_failure_sweep": {
            **payload,
            "path": str(summary_path),
            "requests_path": str(output / "requests.csv"),
        }
    }, commands


def run_configured_probes(
    profile: dict[str, Any],
    *,
    base_url: str,
    suite_dir: Path,
    auth_headers: dict[str, Any],
    tenant_config: dict[str, Any],
    quick: bool,
    timeout_s: float = 7200.0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """执行 profile 显式配置的真实探针，返回 (artifacts, commands)。

    ``suite_dir`` 是探针产物与已完成 Commit 证据的搜索根；``tenant_config``
    是已解析的租户 JSON（用于凭据解析），``profile["tenant_config"]`` 是已
    解析为绝对路径的租户配置文件（探针需要以文件路径读取）。``auth_headers``
    提供 X-Auth-Key 时作为凭据解析的兜底；``timeout_s`` 是探针子进程的
    墙上超时上限（各探针在此基础上再按类型收紧）。
    """
    artifacts: dict[str, Any] = {}
    commands: list[dict[str, Any]] = []
    base_url = str(base_url or profile.get("base_url") or "http://127.0.0.1:8010")
    tenant_path = Path(str(profile.get("tenant_config") or ""))
    commit_artifact = _first_completed_commit_csv(suite_dir)
    commit_csv = commit_artifact[0] if commit_artifact else None
    tenant_index = commit_artifact[1] if commit_artifact else ""
    auth_key, auth_key_env = _resolve_auth_key(tenant_config, tenant_index)
    if not auth_key:
        for header_name, header_value in (auth_headers or {}).items():
            if str(header_name).lower() in _AUTH_HEADER_NAMES:
                auth_key = str(header_value)
                break
    redact = {auth_key} if auth_key else set()

    capability = profile.get("capability_probe")
    if isinstance(capability, dict):
        output = suite_dir / "capability-probe.json"
        params: dict[str, Any] = {}
        if auth_key:
            params["auth_key"] = auth_key
        elif auth_key_env:
            params["auth_key_env"] = auth_key_env
        for key in (
            "session_id",
            "health_path",
            "metrics_path",
            "cursor_path",
            "operation_path",
            "conflict_path",
            "ttl_path",
            "engine_path",
            "fault_path",
            "timeout_s",
        ):
            value = capability.get(key)
            if value not in (None, ""):
                params[key] = value
        profile_path = _write_probe_profile(base_url, params)
        execution = run_command(
            _probe_command("capability.py", profile_path, output),
            timeout_s=min(timeout_s, 180),
            redact_values=redact,
        )
        profile_path.unlink(missing_ok=True)
        commands.append(execution)
        payload = read_json(output)
        _preserve_probe_status(execution, payload)
        if payload:
            artifacts["capability_probe"] = {**payload, "path": str(output)}

    if commit_csv and tenant_path.is_file():
        output = suite_dir / "blackbox-contract-probe.json"
        params = {"commit_csv": str(commit_csv), "tenant": tenant_index}
        if auth_key:
            params["auth_key"] = auth_key
        elif auth_key_env:
            params["auth_key_env"] = auth_key_env
        profile_path = _write_probe_profile(base_url, params)
        execution = run_command(
            _probe_command("blackbox_contract.py", profile_path, output),
            timeout_s=min(timeout_s, 180),
            redact_values=redact,
        )
        profile_path.unlink(missing_ok=True)
        commands.append(execution)
        payload = read_json(output)
        if payload:
            artifacts["blackbox_contract_probe"] = {**payload, "path": str(output)}
    else:
        commands.append({
            "status": "INCONCLUSIVE",
            "reason": (
                "本轮没有完成 Commit 或租户配置不存在，"
                "无法从真实 session 启动黑盒契约探测"
            ),
        })

    missing = profile.get("missing_cases")
    if isinstance(missing, dict) and missing.get("enabled", True):
        output = suite_dir / "missing-cases.json"
        params: dict[str, Any] = {"tenant_config": str(tenant_path)}
        for key in (
            "max_tenants",
            "auth_header",
            "commit_timeout_s",
            "search_timeout_s",
            "visibility_timeout_s",
        ):
            value = missing.get(key)
            if value not in (None, ""):
                params[key] = value
        profile_path = _write_probe_profile(base_url, params)
        execution = run_command(
            _probe_command("missing_cases.py", profile_path, output),
            timeout_s=min(timeout_s, 300 if quick else 900),
            redact_values=redact,
        )
        profile_path.unlink(missing_ok=True)
        commands.append(execution)
        payload = read_json(output)
        _preserve_probe_status(execution, payload)
        if payload:
            artifacts["missing_cases"] = {**payload, "path": str(output)}

    concurrent = profile.get("concurrent_commit")
    if isinstance(concurrent, dict) and concurrent.get("enabled", True):
        output = suite_dir / "concurrent-commit.json"
        params = {"tenant_config": str(tenant_path)}
        for key in ("concurrency", "timeout_s", "auth_header"):
            value = concurrent.get(key)
            if value not in (None, ""):
                params[key] = value
        profile_path = _write_probe_profile(base_url, params)
        execution = run_command(
            _probe_command("concurrent_commit.py", profile_path, output),
            timeout_s=min(timeout_s, 300 if quick else 900),
            redact_values=redact,
        )
        profile_path.unlink(missing_ok=True)
        commands.append(execution)
        payload = read_json(output)
        _preserve_probe_status(execution, payload)
        if payload:
            artifacts["concurrent_commit"] = {**payload, "path": str(output)}

    fault_isolation = profile.get("fault_isolation")
    if isinstance(fault_isolation, dict) and fault_isolation.get("enabled", True):
        output = suite_dir / "fault-isolation.json"
        params = {"tenant_config": str(tenant_path)}
        for key in (
            "target_tenant",
            "bystander_tenants",
            "endpoint",
            "command",
            "samples",
            "workers",
            "timeout_s",
            "control_timeout_s",
            "auth_header",
        ):
            value = fault_isolation.get(key)
            if value not in (None, ""):
                params[key] = value
        profile_path = _write_probe_profile(base_url, params)
        execution = run_command(
            _probe_command("fault_isolation.py", profile_path, output),
            timeout_s=min(timeout_s, 600 if quick else 1800),
            redact_values=redact,
        )
        profile_path.unlink(missing_ok=True)
        commands.append(execution)
        payload = read_json(output)
        _preserve_probe_status(execution, payload)
        if payload:
            artifacts["fault_isolation"] = {**payload, "path": str(output)}

    sweep_artifacts, sweep_commands = _run_limit_failure_sweep(
        profile,
        suite_dir=suite_dir,
        timeout_s=timeout_s,
        quick=quick,
    )
    artifacts.update(sweep_artifacts)
    commands.extend(sweep_commands)

    recovery = profile.get("commit_recovery")
    if isinstance(recovery, dict) and tenant_path.is_file():
        output = suite_dir / "commit-recovery.json"
        recovery_tenant = _resolve_tenant_id(
            tenant_config, str(recovery.get("tenant") or "")
        )
        params: dict[str, Any] = {
            "container": str(recovery.get("container") or ""),
            "tenant_config": str(tenant_path),
        }
        if recovery_tenant:
            params["tenant"] = recovery_tenant
        for key in (
            "kill_delay_s",
            "messages",
            "content_chars",
            "recovery_timeout_s",
            "poll_s",
            "accepted_wait_s",
        ):
            value = recovery.get(key)
            if value not in (None, ""):
                params[key] = value
        if recovery.get("require_accepted_202"):
            params["require_accepted_202"] = True
        profile_path = _write_probe_profile(base_url, params)
        execution = run_command(
            _probe_command("commit_recovery.py", profile_path, output),
            timeout_s=min(timeout_s, 900),
            redact_values=redact,
        )
        profile_path.unlink(missing_ok=True)
        commands.append(execution)
        payload = read_json(output)
        _preserve_probe_status(execution, payload)
        if payload:
            artifacts["commit_recovery"] = {**payload, "path": str(output)}

    fault_plan_value = profile.get("fault_plan")
    if fault_plan_value:
        plan_path = Path(str(fault_plan_value))
        if plan_path.is_file():
            plan_path = _materialize_fault_plan(
                plan_path,
                base_url=base_url,
                output_path=suite_dir / "fault-plan.resolved.json",
            )
        output_dir = suite_dir / "fault-suite"
        output_dir.mkdir(parents=True, exist_ok=True)
        params = {"plan": str(plan_path), "out_dir": str(output_dir)}
        if auth_key:
            params["auth_key"] = auth_key
        if commit_csv:
            params["commit_csv"] = str(commit_csv)
        profile_path = _write_probe_profile(base_url, params)
        execution = run_command(
            _probe_command("fault_suite.py", profile_path, output_dir / "probe-report.json"),
            timeout_s=min(timeout_s, 900),
            redact_values=redact,
        )
        profile_path.unlink(missing_ok=True)
        commands.append(execution)
        payload = read_json(output_dir / "fault-suite.json")
        _preserve_probe_status(execution, payload)
        if payload:
            artifacts["fault_suite"] = {
                **payload,
                "path": str(output_dir / "fault-suite.json"),
            }

    return artifacts, commands

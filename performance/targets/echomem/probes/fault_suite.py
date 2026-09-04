"""编排真实故障、恢复与 cursor 对账 case（进程内 ProbeRunner 编排）。

读取 plan JSON，对每个 fault 以不同 params 进程内运行 fault_injection 探针，
把 ``llm-*`` / ``vector-*`` 聚合为 ``llm-vector-faults`` case；plan.recovery
运行 recovery 探针；plan.cursor 且配置 commit_csv 时运行 cursor_reconcile
探针。汇总保持原 fault-suite.json 结构；``params.out_dir`` 配置时把
fault-suite.json 写入该目录。

配置经 ``ctx.params`` 读取（键名与原 CLI 参数同名）：``plan``（必填）/
``out_dir`` / ``commit_csv`` / ``auth_key`` / ``auth_key_env`` /
``auth_header`` / ``cursor_url_template`` / ``cursor_uri_template``。
``base_url`` 取 ``ctx.base_url``。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from performance.ctx import Ctx
from performance.probe import ProbeModule, ProbeRunner, summarize_probe
from performance.profile import load_profile
from performance.targets.echomem.probes import (
    cursor_reconcile,
    fault_injection,
    recovery,
)

NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_probe(module: Any, name: str) -> ProbeModule:
    """Wrap an imported probe module into the runner contract."""
    description = (module.__doc__ or "").strip().splitlines()[0] if module.__doc__ else ""
    return ProbeModule(name=name, description=description, run=module.run)


def run_probe(module: Any, name: str, base_url: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run one probe module in-process and return its summarized report."""
    probe = as_probe(module, name)
    profile = load_profile({
        "name": name,
        "target": {"base_url": base_url},
        "params": params,
    })
    result = ProbeRunner(profile, probe).run()
    return summarize_probe(probe, result, profile)


def run(ctx: Ctx) -> None:
    params = ctx.params
    base_url = ctx.base_url
    plan_ref = str(params.get("plan") or "")
    if not plan_ref:
        ctx.check(
            "fault-suite",
            status=INCONCLUSIVE,
            reason="plan is required; no fault suite configured",
        )
        return
    try:
        plan = json.loads(Path(plan_ref).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        ctx.check(
            "fault-suite",
            status=INCONCLUSIVE,
            reason=f"plan could not be loaded: {exc}",
            detail=json.dumps({"plan": plan_ref}, ensure_ascii=False)[:500],
        )
        return

    auth_key = str(params.get("auth_key") or "")
    if not auth_key:
        auth_key = os.getenv(str(params.get("auth_key_env") or "ECHOMEM_AUTH_KEY"), "")
    auth_header = str(params.get("auth_header") or "X-Auth-Key")
    commit_csv = str(params.get("commit_csv") or "")
    cursor_url_template = str(params.get("cursor_url_template") or "")
    cursor_uri_template = str(
        params.get("cursor_uri_template")
        or "echo://sessions/{session}/current/commit_cursor.json"
    )
    out_dir = str(params.get("out_dir") or "")

    cases: list[dict[str, Any]] = []
    for fault in plan.get("faults") or []:
        fault_params = {
            key: value
            for key, value in fault.items()
            if value not in (None, "")
        }
        kind = str(fault_params.get("kind") or "unknown")
        summary = run_probe(
            fault_injection,
            f"fault-{kind}",
            base_url,
            {**fault_params, "auth_key": auth_key, "auth_header": auth_header},
        )
        cases.append({"kind": kind, "status": summary["status"], "result": summary})

    fault_kinds = {
        str(case.get("kind", ""))
        for case in cases
        if str(case.get("kind", "")).startswith(("llm-", "vector-"))
    }
    if fault_kinds:
        fault_results = [
            case.get("status", INCONCLUSIVE)
            for case in cases
            if case.get("kind") in fault_kinds
        ]
        aggregate_status = (
            FAIL if FAIL in fault_results
            else INCONCLUSIVE if INCONCLUSIVE in fault_results or NOT_IMPLEMENTED in fault_results
            else PASS
        )
        cases.append({
            "kind": "llm-vector-faults",
            "status": aggregate_status,
            "result": {
                "status": aggregate_status,
                "fault_kinds": sorted(fault_kinds),
                "reason": "all configured LLM/vector controls completed"
                if aggregate_status == PASS
                else "one or more configured LLM/vector controls did not complete",
            },
        })

    recovery_plan = plan.get("recovery")
    if recovery_plan:
        recovery_params: dict[str, Any] = {}
        for key in ("health_url", "container", "pid", "restart_command", "wait_s", "poll_s"):
            value = recovery_plan.get(key)
            if value in (None, ""):
                value = params.get(key)
            if value not in (None, ""):
                recovery_params[key] = value
        summary = run_probe(recovery, "recovery", base_url, recovery_params)
        cases.append({"kind": "kill-9-recovery", "status": summary["status"], "result": summary})

    cursor = plan.get("cursor")
    if cursor and commit_csv:
        summary = run_probe(
            cursor_reconcile,
            "cursor-reconcile",
            base_url,
            {
                "commit_csv": commit_csv,
                "auth_key": auth_key,
                "auth_header": auth_header,
                "cursor_url_template": str(cursor.get("url_template") or cursor_url_template),
                "cursor_uri_template": str(cursor.get("uri_template") or cursor_uri_template),
            },
        )
        cases.append({"kind": "cursor-reconciliation", "status": summary["status"], "result": summary})
    elif cursor:
        cases.append({
            "kind": "cursor-reconciliation",
            "status": INCONCLUSIVE,
            "result": {
                "status": INCONCLUSIVE,
                "reason": "commit CSV is required for cursor reconciliation; capability was not externally tested",
            },
        })

    statuses = [case.get("status", INCONCLUSIVE) for case in cases]
    status = (
        FAIL if FAIL in statuses
        else INCONCLUSIVE if INCONCLUSIVE in statuses or NOT_IMPLEMENTED in statuses
        else PASS
    )
    result = {
        "status": status,
        "created_at": now(),
        "plan": plan_ref,
        "cases": cases,
        "summary": {
            "total": len(cases),
            "pass": statuses.count(PASS),
            "fail": statuses.count(FAIL),
            "not_implemented": statuses.count(NOT_IMPLEMENTED),
        },
    }
    if out_dir:
        out_path = Path(out_dir) / "fault-suite.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    ctx.check(
        "fault-suite",
        status=status,
        reason=(
            "all configured fault-suite cases completed"
            if status == PASS
            else "one or more fault-suite cases did not complete"
        ),
        detail=json.dumps(
            [{"kind": case["kind"], "status": case["status"]} for case in cases],
            ensure_ascii=False,
        )[:500],
    )

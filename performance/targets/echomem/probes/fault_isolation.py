"""测量真实租户故障期间旁观租户 Search 的劣化程度。

故障必须由部署方控制（HTTP 端点或命令）。本探针从不伪造依赖故障，也不把
无 before/after Search 样本的控制成功当作隔离通过。判定：旁观租户 Search
P95 劣化不超过 20% → PASS，否则 FAIL；故障控制或样本证据不完整 →
INCONCLUSIVE。

配置经 ``ctx.params`` 读取：``tenant_config``（必填）／``endpoint`` /
``command`` / ``target_tenant``（必填）／``bystander_tenants`` /
``samples``(8) / ``workers``(8) / ``timeout_s``(20) /
``control_timeout_s``(30) / ``auth_header``(X-Auth-Key)。
``base_url`` 取 ``ctx.base_url``。
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from typing import Any

from performance.ctx import Ctx
from performance.targets.echomem.probes._client import EchoMemHTTP, load_tenant_specs
from performance.targets.echomem.protocol import recall_quality

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def percentile(values: list[float], q: float = 0.95) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def control(
    config: dict[str, Any],
    *,
    action: str,
    target_tenant: str = "",
    timeout_s: float,
    token: str = "",
    fault_type: str = "reject",
    duration_s: float = 300,
    delay_ms: int = 1000,
) -> dict[str, Any]:
    endpoint = str(config.get("endpoint") or "").strip()
    command = str(config.get("command") or "").strip()
    started = time.monotonic()
    try:
        if endpoint:
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(
                    {
                        "action": action,
                        "tenant_id": target_tenant,
                        "fault_type": fault_type,
                        "duration_s": duration_s,
                        "delay_ms": delay_ms,
                        "target_tenant": target_tenant,
                        "tenant": target_tenant,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         **({"X-EchoMem-Test-Token": token} if token else {})},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                body = response.read().decode("utf-8", errors="replace")[-4000:]
                return {
                    "status": PASS if 200 <= response.status < 300 else FAIL,
                    "backend": "http",
                    "status_code": response.status,
                    "body": body,
                    "elapsed_s": time.monotonic() - started,
                }
        if command:
            rendered = command.format(
                action=action,
                target_tenant=target_tenant,
                tenant=target_tenant,
            )
            completed = subprocess.run(
                rendered,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
            return {
                "status": PASS if completed.returncode == 0 else FAIL,
                "backend": "command",
                "command": shlex.split(rendered),
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
                "elapsed_s": time.monotonic() - started,
            }
        return {
            "status": INCONCLUSIVE,
            "reason": "未配置真实故障控制 endpoint 或 command",
        }
    except urllib.error.HTTPError as exc:
        return {
            "status": INCONCLUSIVE if exc.code == 404 else FAIL,
            "backend": "http",
            "status_code": exc.code,
            "body": exc.read().decode("utf-8", errors="replace")[-4000:],
            "elapsed_s": time.monotonic() - started,
        }
    except (OSError, urllib.error.URLError, subprocess.TimeoutExpired) as exc:
        return {
            "status": FAIL,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": time.monotonic() - started,
        }


def sample_search(
    clients: dict[str, EchoMemHTTP],
    sessions: dict[str, str],
    *,
    count: int,
    workers: int,
    timeout_s: float,
    phase: str,
    queries: dict[str, str] | None = None,
) -> dict[str, Any]:
    def one(tenant_id: str, index: int) -> dict[str, Any]:
        started = time.monotonic()
        query = (queries or {}).get(tenant_id, f"PR397 fault isolation sample {index}")
        response = clients[tenant_id].search(
            sessions[tenant_id],
            query,
            timeout_s=timeout_s,
        )
        quality = recall_quality(response.payload, query)
        return {
            "tenant": tenant_id,
            "status_code": response.status_code,
            "elapsed_s": time.monotonic() - started,
            "error": response.error,
            "quality_ok": bool(queries and quality["quality_ok"]),
            "degraded": quality["degraded"], "degraded_reasons": quality["degraded_reasons"],
        }

    jobs = [
        (tenant_id, index)
        for index in range(max(1, count))
        for tenant_id in sessions
    ]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        rows = list(executor.map(lambda item: one(*item), jobs))
    by_tenant: dict[str, dict[str, Any]] = {}
    for tenant_id in sessions:
        selected = [row for row in rows if row["tenant"] == tenant_id]
        latencies = [
            float(row["elapsed_s"])
            for row in selected
        ]
        successful = [r for r in selected if isinstance(r.get("status_code"), int)
                      and 200 <= r["status_code"] < 300 and r["quality_ok"]]
        by_tenant[tenant_id] = {
            "submitted": len(selected),
            "succeeded": len(successful),
            "p95_s": percentile(latencies),
            "median_s": median(latencies) if latencies else None,
            "rows": selected,
        }
    return {"phase": phase, "by_tenant": by_tenant}


def _detail(fields: dict[str, Any]) -> str:
    return json.dumps(fields, ensure_ascii=False)


def run(ctx: Ctx) -> None:
    params = ctx.params
    tenant_config = str(params.get("tenant_config") or "")
    target_tenant = str(params.get("target_tenant") or "")
    bystander_csv = str(params.get("bystander_tenants") or "")
    endpoint = str(params.get("endpoint") or "")
    command = str(params.get("command") or "")
    samples = max(1, int(params.get("samples", 8)))
    workers = max(1, int(params.get("workers", 8)))
    timeout_s = float(params.get("timeout_s", 20))
    control_timeout_s = float(params.get("control_timeout_s", 30))
    auth_header = str(params.get("auth_header") or "X-Auth-Key")
    base_url = ctx.base_url
    token = os.environ.get(str(params.get("token_env", "ECHOMEM_TEST_CONTROL_TOKEN")), "")
    if endpoint and not token:
        ctx.check("fault-isolation", status=INCONCLUSIVE,
                  reason="Fault control token missing; deployment control plane not connected")
        return
    control_args = {"token": token, "fault_type": str(params.get("fault_type", "reject")),
                    "duration_s": float(params.get("duration_s", 300)),
                    "delay_ms": int(params.get("delay_ms", 1000))}

    if not tenant_config or not target_tenant:
        ctx.check(
            "fault-isolation",
            status=INCONCLUSIVE,
            reason="tenant_config and target_tenant are required",
        )
        return
    try:
        specs = load_tenant_specs(Path(tenant_config))
    except (OSError, ValueError) as exc:
        ctx.check(
            "fault-isolation",
            status=INCONCLUSIVE,
            reason=f"tenant config could not be loaded: {exc}",
        )
        return

    selected = {
        spec.tenant_id: spec
        for spec in specs
        if spec.tenant_id == target_tenant
        or spec.tenant_id in {
            item.strip() for item in bystander_csv.split(",") if item.strip()
        }
    }
    bystanders = [tenant_id for tenant_id in selected if tenant_id != target_tenant]
    if not bystanders or target_tenant not in selected:
        ctx.check(
            "fault-isolation",
            status=INCONCLUSIVE,
            reason="故障租户或旁观租户配置不足，至少需要 1 个旁观租户",
            detail=_detail({"target_tenant": target_tenant, "bystanders": bystanders}),
        )
        return

    clients = {
        tenant_id: EchoMemHTTP(
            base_url,
            spec.auth_key,
            tenant_id=spec.tenant_id,
            user_id=spec.user_id,
            account_id=spec.account_id,
            agent_id=spec.agent_id,
            auth_header=auth_header,
        )
        for tenant_id, spec in selected.items()
    }
    sessions = {
        tenant_id: clients[tenant_id].open_session(
            tenant_id, f"pr397-fault-isolation-{tenant_id}"
        )[0]
        for tenant_id in selected
    }
    before = sample_search(
        clients, sessions, count=samples, workers=workers,
        timeout_s=timeout_s, phase="before",
        queries=params.get("queries"),
    )
    enable = control(
        {"endpoint": endpoint, "command": command},
        action="enable", target_tenant=target_tenant, timeout_s=control_timeout_s,
        **control_args,
    )
    during: dict[str, Any] = {}
    disable: dict[str, Any] = {
        "status": INCONCLUSIVE,
        "reason": "故障尚未启用，未执行恢复动作",
    }
    try:
        if enable.get("status") == PASS:
            during = sample_search(
                clients, sessions, count=samples, workers=workers,
                timeout_s=timeout_s, phase="during",
                queries=params.get("queries"),
            )
    finally:
        # 无论采样是否抛异常，故障结束后都必须恢复真实依赖。
        disable = control(
            {"endpoint": endpoint, "command": command},
            action="disable", target_tenant=target_tenant, timeout_s=control_timeout_s,
            **control_args,
        )

    degradations: dict[str, float] = {}
    for tenant_id in bystanders:
        baseline = (before.get("by_tenant", {}).get(tenant_id) or {}).get("p95_s")
        degraded = (during.get("by_tenant", {}).get(tenant_id) or {}).get("p95_s")
        if baseline and degraded is not None:
            degradations[tenant_id] = (float(degraded) - float(baseline)) / float(baseline)
    bystander_p95_degradation = max(degradations.values(), default=None)
    after = sample_search(clients, sessions, count=samples, workers=workers,
                          timeout_s=timeout_s, phase="after", queries=params.get("queries"))
    target_before = before.get("by_tenant", {}).get(target_tenant, {})
    target_during = during.get("by_tenant", {}).get(target_tenant, {})
    fault_observed = (
        any(row.get("status_code") in (429, 503) for row in target_during.get("rows", []))
        if control_args["fault_type"] == "reject"
        else bool(target_before.get("median_s") is not None
                  and target_during.get("median_s") is not None
                  and target_during["median_s"] - target_before["median_s"]
                  >= control_args["delay_ms"] / 2000)
    )
    healthy_bystanders = all(
        block.get("by_tenant", {}).get(t, {}).get("succeeded", 0) == samples
        for block in (before, during, after) for t in bystanders
    )
    recovered_target = after.get("by_tenant", {}).get(target_tenant, {}).get("succeeded", 0) == samples
    baseline_healthy = all(before.get("by_tenant", {}).get(t, {}).get("succeeded", 0) == samples
                           for t in selected)

    ctx.check(
        "fault-control-enable",
        status=enable.get("status", INCONCLUSIVE),
        reason=enable.get("reason") or f"enable control: {enable.get('backend', '')}",
        elapsed_s=enable.get("elapsed_s"),
        detail=_detail(enable),
    )
    ctx.check(
        "fault-control-disable",
        status=disable.get("status", INCONCLUSIVE),
        reason=disable.get("reason") or f"disable control: {disable.get('backend', '')}",
        elapsed_s=disable.get("elapsed_s"),
        detail=_detail(disable),
    )

    complete = (
        enable.get("status") == PASS
        and disable.get("status") == PASS
        and len(degradations) == len(bystanders)
        and fault_observed
    )
    if not baseline_healthy:
        status, reason = INCONCLUSIVE, "故障注入前基线已有错误或降级，保留数据但不能归因于单租户故障"
    elif not complete:
        status, reason = INCONCLUSIVE, "故障控制或旁观租户前后 Search P95 证据不完整"
    elif recovered_target and healthy_bystanders and bystander_p95_degradation is not None and bystander_p95_degradation <= 0.20:
        status, reason = PASS, "旁观租户 Search P95 劣化不超过 20%"
    else:
        status, reason = FAIL, "旁观租户延迟/质量未达标，或目标租户未恢复；详见逐租户数据"

    ctx.check(
        "fault-isolation",
        status=status,
        reason=reason,
        detail=_detail({
            "target_tenant": target_tenant,
            "fault_observed": fault_observed,
            "healthy_bystanders": healthy_bystanders,
            "baseline_healthy": baseline_healthy,
            "samples_per_tenant": samples,
            "before": before,
            "during": during,
            "after": after,
            "bystanders": bystanders,
            "fault_recovered": disable.get("status") == PASS and recovered_target,
            "bystander_p95_degradation": bystander_p95_degradation,
            "degradation_by_tenant": degradations,
            "p95_before_by_tenant": {
                t: (before.get("by_tenant", {}).get(t) or {}).get("p95_s")
                for t in bystanders
            },
            "p95_during_by_tenant": {
                t: (during.get("by_tenant", {}).get(t) or {}).get("p95_s")
                for t in bystanders
            },
        }),
    )

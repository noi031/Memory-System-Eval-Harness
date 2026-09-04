"""Preflight real tenant identities before a stress suite starts.

对每个独立租户做真实 HTTP open_session 预检（带各自 auth key），验证压测
开始前租户凭据能打开真实会话。认证是每租户独立的只读检查，并发执行
（max_workers=min(8, len)）让 N 租户预检耗时由最慢凭据决定而非总和。
每个租户一条 ``ctx.check(tenant_id, PASS/FAIL)``；tenant-config 加载失败或
全部租户传输失败时一条 ``ctx.check('auth_preflight', INCONCLUSIVE, ...)``。

配置经 ``ctx.params`` 读取（键名与原 CLI 参数同名）：``tenant_config`` /
``tenant_count`` / ``timeout_s`` / ``auth_header``。
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from performance.ctx import Ctx
from performance.targets.echomem.probes._client import (
    EchoMemHTTP,
    TenantSpec,
    load_tenant_specs,
)

PASS = "PASS"
FAIL = "FAIL"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
INCONCLUSIVE = "INCONCLUSIVE"


def key_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else ""


def open_session(
    base_url: str,
    tenant: TenantSpec,
    *,
    timeout_s: float,
    auth_header: str,
) -> dict[str, Any]:
    client = EchoMemHTTP(
        base_url,
        tenant.auth_key,
        timeout_s=timeout_s,
        tenant_id=tenant.tenant_id,
        user_id=tenant.user_id,
        account_id=tenant.account_id,
        agent_id=tenant.agent_id,
        auth_header=auth_header,
    )
    result = client.request("POST", "/api/sessions/open", {
        "agent_id": tenant.agent_id,
        "metadata": {
            "title": "stress-auth-preflight",
            "account_id": tenant.account_id or tenant.tenant_id,
            "user_id": tenant.user_id or f"stress-{tenant.tenant_id}",
            "tenant_id": tenant.tenant_id,
        },
    }, timeout_s=timeout_s)
    payload = result.payload if isinstance(result.payload, dict) else {}
    error = result.error
    if not error and result.status_code is not None and result.status_code >= 300:
        reason = str(
            payload.get("detail")
            or payload.get("message")
            or payload.get("error")
            or payload.get("code")
            or ""
        )[:300]
        error = f"HTTP {result.status_code}" + (f": {reason}" if reason else "")
    return {
        "tenant_id": tenant.tenant_id,
        "auth_key_sha256_12": key_fingerprint(tenant.auth_key),
        "http_status": result.status_code,
        "elapsed_s": round(result.elapsed_s, 3),
        "session_opened": result.status_code is not None and result.status_code < 300,
        "error": error,
    }


def run(ctx: Ctx) -> None:
    params = ctx.params
    tenant_config = str(params.get("tenant_config") or "")
    if not tenant_config:
        ctx.check(
            "auth_preflight",
            status=INCONCLUSIVE,
            reason="no tenant_config parameter; tenant auth preflight was not exercised",
        )
        return
    timeout_s = max(0.1, float(params.get("timeout_s", 5.0)))
    tenant_count = max(0, int(params.get("tenant_count", 0)))
    auth_header = str(params.get("auth_header") or "X-Auth-Key")

    try:
        tenants = load_tenant_specs(tenant_config, tenant_count=tenant_count)
    except (OSError, ValueError) as exc:
        ctx.check(
            "auth_preflight",
            status=INCONCLUSIVE,
            reason=f"tenant config unavailable: {exc}",
        )
        return

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(tenants)))) as executor:
        results = list(
            executor.map(
                lambda tenant: open_session(
                    ctx.base_url, tenant, timeout_s=timeout_s, auth_header=auth_header
                ),
                tenants,
            )
        )

    if all(item["http_status"] is None for item in results):
        ctx.check(
            "auth_preflight",
            status=INCONCLUSIVE,
            reason="all tenant credentials failed at transport level; environment unreachable",
            detail=json.dumps(
                [
                    {
                        "tenant_id": item["tenant_id"],
                        "auth_key_sha256_12": item["auth_key_sha256_12"],
                        "error": item["error"],
                    }
                    for item in results
                ],
                ensure_ascii=False,
            )[:500],
        )
        return

    for item in results:
        ctx.check(
            item["tenant_id"],
            status=PASS if item["session_opened"] else FAIL,
            reason="" if item["session_opened"] else (item["error"] or "open session failed"),
            elapsed_s=item.get("elapsed_s"),
            detail=json.dumps(
                {
                    "auth_key_sha256_12": item["auth_key_sha256_12"],
                    "http_status": item["http_status"],
                    "session_opened": item["session_opened"],
                    "error": item["error"],
                },
                ensure_ascii=False,
            )[:500],
        )

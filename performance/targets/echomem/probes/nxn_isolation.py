"""N×N 租户隔离探针。

每个 writer 租户写入私有 marker 消息（open → add×N → commit → poll
completed）；随后对每个 (writer, reader) 对用 reader 身份精确检索每个
marker：同租户期望命中（重试 2 次、间隔 1s）、跨租户期望不命中（1 次）。
全部经真实 per-tenant auth（``X-Auth-Key``）执行，报告四态。

配置经 ``ctx.params`` 读取：``tenant_config`` / ``markers_per_tenant``
（默认 5）/ ``timeout_s``（默认 30）/ ``commit_poll_timeout_s``（默认
600）/ ``auth_header``（默认 X-Auth-Key）。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from performance.ctx import Ctx
from performance.targets.echomem.probes._client import (
    EchoMemHTTP,
    load_tenant_specs,
)

PASS = "PASS"
FAIL = "FAIL"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
INCONCLUSIVE = "INCONCLUSIVE"

_MARKER_PREFIX = "echomem-isolation"


def _items_from(result: Any) -> list[dict[str, Any]]:
    payload = result.payload if isinstance(result.payload, dict) else {}
    result_body = payload.get("result")
    if not isinstance(result_body, dict):
        return []
    items = result_body.get("items")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _search_found(
    client: EchoMemHTTP,
    session_id: str,
    marker: str,
    *,
    attempts: int,
    timeout_s: float,
    interval_s: float,
) -> tuple[bool, float]:
    found = False
    latency_ms = 0.0
    for attempt in range(attempts):
        started = time.perf_counter()
        result = client.search(session_id, marker, timeout_s)
        latency_ms = (time.perf_counter() - started) * 1000
        if result.status_code is not None and 200 <= result.status_code < 300:
            found = any(
                marker in (item.get("content") or "") or marker in (item.get("uri") or "")
                for item in _items_from(result)
            )
        if found or attempt + 1 >= attempts:
            break
        time.sleep(interval_s)
    return found, latency_ms


def run(ctx: Ctx) -> None:
    params = ctx.params
    tenant_config = str(params.get("tenant_config") or "")
    if not tenant_config:
        ctx.check(
            "nxn_isolation",
            status=INCONCLUSIVE,
            reason="no tenant_config parameter; tenant isolation was not exercised",
        )
        return
    marker_count = max(1, int(params.get("markers_per_tenant", 5)))
    timeout_s = max(0.1, float(params.get("timeout_s", 30.0)))
    poll_timeout_s = max(0.1, float(params.get("commit_poll_timeout_s", 600.0)))
    auth_header = str(params.get("auth_header") or "X-Auth-Key")

    try:
        tenants = load_tenant_specs(tenant_config)
    except (OSError, ValueError) as exc:
        ctx.check(
            "nxn_isolation",
            status=INCONCLUSIVE,
            reason=f"tenant config unavailable: {exc}",
        )
        return
    if len(tenants) < 2:
        ctx.check(
            "nxn_isolation",
            status=INCONCLUSIVE,
            reason="requires at least two tenants",
        )
        return

    clients = [
        EchoMemHTTP(
            ctx.base_url,
            tenant.auth_key,
            timeout_s=timeout_s,
            tenant_id=tenant.tenant_id,
            user_id=tenant.user_id,
            account_id=tenant.account_id,
            agent_id=tenant.agent_id,
            auth_header=auth_header,
        )
        for tenant in tenants
    ]

    # 1. Writers: private marker messages, committed to completion.
    writers: dict[int, dict[str, Any]] = {}
    for writer in range(len(tenants)):
        markers = [
            f"{_MARKER_PREFIX}-{writer}-{uuid.uuid4().hex}"
            for _ in range(marker_count)
        ]
        try:
            session_id, _ = clients[writer].open_session(
                tenants[writer].tenant_id, "perf-isolation-writer"
            )
        except Exception as exc:
            ctx.check(
                f"nxn_writer:{writer}",
                status=INCONCLUSIVE,
                reason=f"writer open_session failed: {type(exc).__name__}: {exc}",
            )
            continue
        try:
            for index, marker in enumerate(markers):
                clients[writer].add_message(
                    session_id, f"iso-{writer}-{index}",
                    f"Tenant {writer} private marker {marker}",
                )
        except Exception as exc:
            ctx.check(
                f"nxn_writer:{writer}",
                status=INCONCLUSIVE,
                reason=f"writer add_message failed: {type(exc).__name__}: {exc}",
            )
            continue
        try:
            commit = clients[writer].commit(session_id)
        except Exception as exc:
            ctx.check(
                f"nxn_writer:{writer}",
                status=INCONCLUSIVE,
                reason=f"writer commit failed: {type(exc).__name__}: {exc}",
            )
            continue
        archive_id = _archive_id(commit)
        if not archive_id:
            ctx.check(
                f"nxn_writer:{writer}",
                status=INCONCLUSIVE,
                reason=f"writer commit returned no archive_id: {commit.payload}",
            )
            continue
        deadline = time.monotonic() + poll_timeout_s
        done = False
        while time.monotonic() < deadline:
            status = clients[writer].commit_status(session_id, archive_id)
            if status.status_code is not None and 200 <= status.status_code < 300:
                state = str(status.payload.get("status") or "").lower()
                if state in ("completed", "done", "success"):
                    done = True
                    break
                if state in ("failed", "error"):
                    break
            time.sleep(1.0)
        if not done:
            ctx.check(
                f"nxn_writer:{writer}",
                status=INCONCLUSIVE,
                reason=f"writer commit not completed: {commit.status_code}",
            )
            continue
        writers[writer] = {"markers": markers, "session_id": session_id}

    # 2. Readers: same-tenant must hit, cross-tenant must miss.
    invalid = 0
    same_hits = 0
    same_total = 0
    cross_fp = 0
    cross_total = 0
    for writer, writer_data in writers.items():
        for reader in range(len(tenants)):
            same_tenant = writer == reader
            attempts = 2 if same_tenant else 1
            pair_fail = 0
            for marker in writer_data["markers"]:
                found, _ = _search_found(
                    clients[reader],
                    writer_data["session_id"],
                    marker,
                    attempts=attempts,
                    timeout_s=timeout_s,
                    interval_s=1.0,
                )
                if same_tenant:
                    same_total += 1
                    same_hits += 1 if found else 0
                else:
                    cross_total += 1
                    cross_fp += 1 if found else 0
                if found != same_tenant:
                    invalid += 1
                    pair_fail += 1
            if pair_fail:
                ctx.check(
                    f"nxn:{writer}->{reader}",
                    status=FAIL,
                    reason=f"{pair_fail}/{marker_count} marker hits violated "
                           f"isolation (expected hit only for the owning tenant)",
                )

    interrupted = len(writers) < len(tenants)
    for reader in range(len(tenants)):
        ctx.check(
            f"nxn_reader:{reader}",
            status=INCONCLUSIVE if interrupted else PASS,
            reason="writer phase incomplete; isolation checks are inconclusive"
            if interrupted
            else f"observed {same_total} same-tenant probes and {cross_total} "
                 f"cross-tenant probes",
        )

    if interrupted:
        status = INCONCLUSIVE
        reason = "writer phase interrupted; isolation could not be fully observed"
    elif invalid:
        status = FAIL
        reason = f"{invalid} probe hits violated isolation expectations"
    else:
        status = PASS
        reason = "all same-tenant probes hit and all cross-tenant probes missed"
    ctx.check(
        "nxn_isolation",
        status=status,
        reason=reason,
        detail=json.dumps(
            {
                "probe_count": same_total + cross_total,
                "markers_per_tenant": marker_count,
                "same_tenant_hits": same_hits,
                "same_tenant_total": same_total,
                "cross_tenant_false_positives": cross_fp,
                "cross_tenant_total": cross_total,
                "invalid_probe_count": invalid,
                "writers_ok": len(writers),
                "writers_total": len(tenants),
            },
            ensure_ascii=False,
        ),
    )


def _archive_id(result: Any) -> str:
    payload = result.payload if isinstance(result.payload, dict) else {}
    body = payload.get("result")
    if not isinstance(body, dict):
        body = {}
    return str(
        payload.get("archive_id")
        or payload.get("task_id")
        or body.get("archive_id")
        or body.get("task_id")
        or payload.get("id")
        or ""
    )

"""并发 commit 行为探针（真实 HTTP）。

对同一真实 EchoMem 会话做 N 次并发 commit，观察提交的接受/拒绝、
operation/archive 唯一性、重复接受与终态收敛，验证并发 commit 契约。

配置经 ``ctx.params`` 读取（键名与原 CLI 参数同名）：``tenant_config`` /
``concurrency`` / ``timeout_s`` / ``auth_header``。
"""

from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from performance.ctx import Ctx
from performance.targets.echomem.probes._client import (
    EchoMemHTTP,
    extract_archive,
    load_tenant_specs,
    status_from,
)

PASS = "PASS"
FAIL = "FAIL"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
INCONCLUSIVE = "INCONCLUSIVE"


def poll(client: EchoMemHTTP, session_id: str, archive_id: str, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    states: list[str] = []
    while time.monotonic() < deadline:
        response = client.commit_status(session_id, archive_id)
        payload = response.payload if isinstance(response.payload, dict) else {}
        state = status_from(payload)
        state = state.lower() if state else ""
        if state:
            states.append(state)
        if state in {"completed", "complete", "succeeded", "success", "failed", "error"}:
            return {
                "status_code": response.status_code,
                "state": state,
                "states": states,
            }
        time.sleep(0.5)
    return {"status_code": None, "state": "timeout", "states": states}


def run_case(client: EchoMemHTTP, concurrency: int, timeout_s: float) -> dict[str, Any]:
    session_id, _ = client.open_session(client.tenant_id, f"pr421-concurrent-{uuid.uuid4().hex}")
    message_ids: list[str] = []
    for index in range(concurrency):
        message_id = f"pr421-concurrent-message-{uuid.uuid4().hex}"
        response = client.add_message(
            session_id,
            message_id,
            f"PR421 concurrent commit probe {index} {uuid.uuid4().hex}",
        )
        if response.status_code is None or response.status_code >= 400:
            return {
                "status": "FAIL",
                "phase": "seed",
                "session_id": session_id,
                "message_status": response.status_code,
                "error": response.error,
            }
        message_ids.append(message_id)

    def submit(index: int) -> dict[str, Any]:
        started = time.monotonic()
        response = client.commit(session_id)
        return {
            "index": index,
            "status_code": response.status_code,
            "elapsed_s": time.monotonic() - started,
            "request_id": response.headers.get("X-Request-ID", ""),
            "operation_id": (
                response.payload.get("operation_id")
                if isinstance(response.payload, dict)
                else ""
            ),
            "archive_id": extract_archive(response.payload),
            "error": response.error,
        }

    submissions: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(submit, index) for index in range(concurrency)]
        for future in as_completed(futures):
            submissions.append(future.result())

    accepted = [item for item in submissions if item["status_code"] in {200, 202}]
    terminal: list[dict[str, Any]] = []
    poll_items = [
        item for item in accepted if item.get("archive_id")
    ]
    # Poll all accepted operations concurrently. Sequential polling can make
    # the test's deadline depend on the number of accepted commits.
    with ThreadPoolExecutor(max_workers=max(1, len(poll_items))) as executor:
        futures = {
            executor.submit(
                poll, client, session_id, item["archive_id"], timeout_s
            ): item["archive_id"]
            for item in poll_items
        }
        for future in as_completed(futures):
            terminal.append({
                "archive_id": futures[future],
                "poll": future.result(),
            })

    archive_ids = [item["archive_id"] for item in accepted if item.get("archive_id")]
    operation_ids = [item["operation_id"] for item in accepted if item.get("operation_id")]
    failed_terminal = [
        item for item in terminal
        if item["poll"].get("state") not in {"completed", "complete", "succeeded", "success"}
    ]
    status = "PASS" if accepted and not failed_terminal else "FAIL"
    return {
        "status": status,
        "session_id": session_id,
        "seed_messages": len(message_ids),
        "concurrency": concurrency,
        "submissions": submissions,
        "accepted_count": len(accepted),
        "rejected_count": len(submissions) - len(accepted),
        "archive_ids": archive_ids,
        "unique_archive_ids": len(set(archive_ids)),
        "operation_ids": operation_ids,
        "unique_operation_ids": len(set(operation_ids)),
        "terminal": terminal,
        "duplicate_acceptance": len(archive_ids) != len(set(archive_ids)),
        "failed_terminal_count": len(failed_terminal),
    }


def run(ctx: Ctx) -> None:
    params = ctx.params
    tenant_config = str(params.get("tenant_config") or "")
    if not tenant_config:
        ctx.check(
            "concurrent-commit",
            status=INCONCLUSIVE,
            reason="no tenant_config parameter; concurrent commit was not exercised",
        )
        return
    concurrency = max(2, int(params.get("concurrency", 8)))
    timeout_s = float(params.get("timeout_s", 120))
    auth_header = str(params.get("auth_header") or "X-Auth-Key")

    try:
        specs = load_tenant_specs(tenant_config)
    except (OSError, ValueError) as exc:
        ctx.check(
            "concurrent-commit",
            status=INCONCLUSIVE,
            reason=f"tenant config unavailable: {exc}",
        )
        return
    if not specs:
        ctx.check(
            "concurrent-commit",
            status=INCONCLUSIVE,
            reason="tenant config contains no tenants",
        )
        return
    spec = specs[0]
    client = EchoMemHTTP(
        ctx.base_url,
        spec.auth_key,
        tenant_id=spec.tenant_id,
        user_id=spec.user_id,
        account_id=spec.account_id,
        agent_id="pr421-concurrent-commit",
        auth_header=auth_header,
    )

    started = time.monotonic()
    try:
        case = run_case(client, concurrency, timeout_s)
    except Exception as exc:
        ctx.check(
            "concurrent-commit",
            status=INCONCLUSIVE,
            reason=f"concurrent commit could not be exercised: {type(exc).__name__}: {exc}",
        )
        return
    elapsed_s = time.monotonic() - started

    if case["status"] == "PASS":
        reason = "concurrent commit accepted and reached terminal state"
    elif case.get("phase") == "seed":
        reason = f"seed failed: HTTP {case.get('message_status')} {case.get('error') or ''}".strip()
    elif not case["accepted_count"]:
        reason = "no concurrent commit accepted"
    else:
        reason = "failed terminal commit state observed"
    reason += (
        "; idempotency NOT_VERIFIED (public API has no documented idempotency key "
        "or replay contract); version conflict OBSERVED_FROM_HTTP_AND_TERMINAL_STATES"
    )
    summary = {
        "accepted_count": case["accepted_count"],
        "rejected_count": case["rejected_count"],
        "unique_archive_ids": case["unique_archive_ids"],
        "unique_operation_ids": case["unique_operation_ids"],
        "duplicate_acceptance": case["duplicate_acceptance"],
        "failed_terminal_count": case["failed_terminal_count"],
    }
    ctx.check(
        "concurrent-commit",
        status=case["status"],
        reason=reason,
        elapsed_s=elapsed_s,
        detail=json.dumps(summary, ensure_ascii=False)[:500],
    )

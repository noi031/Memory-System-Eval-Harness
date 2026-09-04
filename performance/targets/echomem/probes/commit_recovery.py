"""探测 EchoMem 容器在 Commit 操作中途被 kill 后的恢复能力（真实 HTTP）。

使用真实 HTTP 服务与真实配置模型。有意保持保守：丢失 Commit 响应、或缺少
message-set/cursor 端点，记录为 INCONCLUSIVE 而非推断为成功。

配置经 ``ctx.params`` 读取（键名与原 CLI 参数同名）：``health_url`` /
``container`` / ``tenant_config`` / ``tenant`` / ``kill_delay_s`` /
``messages`` / ``content_chars`` / ``health_timeout_s`` /
``recovery_timeout_s`` / ``poll_s`` / ``require_accepted_202`` /
``accepted_wait_s`` / ``idempotency_key``。``base_url`` 取 ``ctx.base_url``。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from performance.ctx import Ctx
from performance.targets.echomem.probes._client import EchoMemHTTP, extract_message
from performance.targets.echomem.probes.cursor_reconcile import values_from_payload

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detail(fields: dict[str, Any]) -> str:
    return json.dumps(fields, ensure_ascii=False)[:500]


def load_tenant(path: Path, tenant_id: str) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data.get("tenants", []):
        if str(item.get("tenant_id")) == tenant_id:
            direct_key = str(item.get("auth_key") or "").strip()
            env_name = str(item.get("auth_key_env") or "").strip()
            return {
                "tenant_id": str(item["tenant_id"]),
                "user_id": str(item.get("user_id") or f"stress-{tenant_id}"),
                "auth_key": direct_key or os.environ.get(env_name, ""),
            }
    raise RuntimeError(f"tenant not found: {tenant_id}")


def health(url: str, timeout_s: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = {"raw": body[-1000:]}
            return {
                "healthy": 200 <= response.status < 300,
                "status_code": response.status,
                "elapsed_s": time.monotonic() - started,
                "payload": payload,
            }
    except (OSError, urllib.error.URLError) as exc:
        return {
            "healthy": False,
            "status_code": None,
            "elapsed_s": time.monotonic() - started,
            "error": str(exc),
        }


def archive_ids_from_payload(payload: dict[str, Any]) -> set[str]:
    """Collect archive IDs from summaries without assuming one response shape."""
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"archive_id", "archiveId", "commit_id", "commitId"} and item:
                    found.add(str(item))
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return found


def decode_fs_read_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the JSON document returned by EchoMem's /fs/read endpoint."""
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    text = result.get("text")
    if not isinstance(text, str) or not text:
        return payload
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return payload
    return decoded if isinstance(decoded, dict) else payload


def _docker_engine_post(path: str) -> tuple[int, str]:
    """POST to the mounted Docker Engine socket using only the stdlib."""
    request = (
        f"POST {path} HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "Content-Length: 0\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(15.0)
            client.connect("/var/run/docker.sock")
            client.sendall(request)
            response = b""
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                response += chunk
        status_line = response.split(b"\r\n", 1)[0].decode(
            "ascii", errors="replace"
        )
        code = int(status_line.split()[1])
        return code, "" if 200 <= code < 300 else status_line
    except (OSError, ValueError, IndexError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def kill_and_start(container: str, restart_wait_s: float) -> dict[str, Any]:
    docker_cli = shutil.which("docker")
    killed = None
    if docker_cli:
        try:
            killed = subprocess.run(
                [docker_cli, "kill", "--signal", "KILL", container],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            # Some runner images contain a stale or non-executable docker
            # shim. Fall back to the mounted Engine socket in that case.
            docker_cli = None
    if not docker_cli:
        # The web runner may have the Docker socket mounted without the CLI.
        # Use the Engine HTTP API directly so recovery needs no extra binary.
        encoded = quote(container, safe="")
        kill_code, kill_error = _docker_engine_post(
            f"/containers/{encoded}/kill?signal=KILL"
        )
    result: dict[str, Any] = {
        "kill_returncode": (
            killed.returncode
            if docker_cli and killed is not None
            else (0 if 200 <= kill_code < 300 else 1)
        ),
        "kill_stderr": (
            killed.stderr[-2000:]
            if docker_cli and killed is not None
            else kill_error[-2000:]
        ),
        "killed_at": now(),
        "control_backend": "docker-cli" if docker_cli else "docker-engine-api",
    }
    if result["kill_returncode"] != 0:
        return result
    started = None
    if docker_cli:
        try:
            started = subprocess.run(
                [docker_cli, "start", container],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            docker_cli = None
    if not docker_cli:
        encoded = quote(container, safe="")
        start_code, start_error = _docker_engine_post(
            f"/containers/{encoded}/start"
        )
    result.update(
        {
            "start_returncode": (
                started.returncode
                if docker_cli and started is not None
                else (0 if 200 <= start_code < 300 else 1)
            ),
            "start_stderr": (
                started.stderr[-2000:]
                if docker_cli and started is not None
                else start_error[-2000:]
            ),
            "restart_at": now(),
        }
    )
    if restart_wait_s > 0:
        time.sleep(restart_wait_s)
    return result


def recovery_control_ok(control: dict[str, Any]) -> bool:
    """Return true only when both the real kill and restart succeeded."""
    return (
        control.get("kill_returncode") == 0
        and control.get("start_returncode") == 0
    )


def run(ctx: Ctx) -> None:
    params = ctx.params
    base_url = ctx.base_url
    health_url = str(params.get("health_url") or "") or base_url.rstrip("/") + "/health"
    container = str(params.get("container") or "")
    tenant_config = str(params.get("tenant_config") or "")
    tenant = str(params.get("tenant") or "stress-a")
    kill_delay_s = float(params.get("kill_delay_s", 0.5))
    message_count = int(params.get("messages", 12))
    content_chars = int(params.get("content_chars", 2500))
    health_timeout_s = float(params.get("health_timeout_s", 5.0))
    recovery_timeout_s = float(params.get("recovery_timeout_s", 180.0))
    poll_s = float(params.get("poll_s", 2.0))
    require_accepted_202 = str(
        params.get("require_accepted_202") or ""
    ).lower() in ("1", "true", "yes", "on")
    accepted_wait_s = float(params.get("accepted_wait_s", 10.0))
    configured_key = str(params.get("idempotency_key") or "")
    started = time.monotonic()

    before = health(health_url, health_timeout_s)
    if not before["healthy"]:
        ctx.check(
            "health-before",
            status=INCONCLUSIVE,
            reason="service was not healthy before probe",
            elapsed_s=before.get("elapsed_s"),
            detail=_detail({
                "health_url": health_url,
                "status_code": before.get("status_code"),
                "error": before.get("error", ""),
            }),
        )
        return
    ctx.check(
        "health-before",
        status=PASS,
        reason="service was healthy before probe",
        elapsed_s=before.get("elapsed_s"),
        detail=_detail({"health_url": health_url, "status_code": before.get("status_code")}),
    )

    if not container:
        ctx.check(
            "commit-recovery",
            status=INCONCLUSIVE,
            reason="no container configured; recovery was not externally exercised",
            elapsed_s=time.monotonic() - started,
        )
        return
    if not tenant_config:
        ctx.check(
            "commit-recovery",
            status=INCONCLUSIVE,
            reason="tenant_config is required; the recovery probe could not authenticate",
            elapsed_s=time.monotonic() - started,
        )
        return
    try:
        identity = load_tenant(Path(tenant_config), tenant)
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        ctx.check(
            "commit-recovery",
            status=INCONCLUSIVE,
            reason=f"tenant config could not be loaded: {exc}",
            elapsed_s=time.monotonic() - started,
            detail=_detail({"tenant_config": tenant_config, "tenant": tenant}),
        )
        return

    client = EchoMemHTTP(
        base_url,
        auth_key=identity["auth_key"],
        timeout_s=max(health_timeout_s, 60.0),
        tenant_id=identity["tenant_id"],
        user_id=identity["user_id"],
        account_id=identity["tenant_id"],
        agent_id="pr421-commit-recovery",
    )
    session_id, _ = client.open_session(tenant, f"pr421-recovery-{uuid.uuid4().hex[:10]}")
    marker = f"pr421-recovery-marker-{uuid.uuid4().hex}"
    idempotency_key = configured_key or f"pr421-recovery-commit-{uuid.uuid4().hex}"
    client_message_ids: list[str] = []
    message_records: list[dict[str, Any]] = []
    for index in range(max(1, message_count)):
        client_message_id = f"recovery-{uuid.uuid4().hex}"
        client_message_ids.append(client_message_id)
        response = client.add_message(
            session_id,
            client_message_id,
            (
                f"Real Commit recovery probe {marker}; message {index}. "
                + ("payload-" + marker + " ") * max(1, content_chars // (len(marker) + 9))
            )[: max(64, content_chars)],
        )
        if response.status_code is None or response.status_code >= 400:
            ctx.check(
                "commit-recovery",
                status=FAIL,
                reason="message setup failed",
                elapsed_s=time.monotonic() - started,
                detail=_detail({
                    "session_id": session_id,
                    "setup_status_code": response.status_code,
                    "setup_payload": response.payload,
                }),
            )
            return
        server_message = extract_message(response.payload)
        message_records.append({
            "client_request_id": client_message_id,
            "server_message_id": server_message.get("id", ""),
            "response": response.payload,
        })

    commit_box: dict[str, Any] = {}
    commit_response_ready = threading.Event()

    def submit() -> None:
        try:
            response = client.commit(session_id, idempotency_key=idempotency_key)
            commit_box["status_code"] = response.status_code
            commit_box["payload"] = response.payload
            commit_box["error"] = response.error
        except BaseException as exc:  # the process may be killed during the request
            commit_box["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            commit_response_ready.set()

    commit_thread = threading.Thread(target=submit, daemon=True)
    commit_started = time.monotonic()
    commit_thread.start()
    # The target contract is "accepted as 202, then crash".  Waiting for the
    # response before killing avoids accidentally testing a client-side
    # timeout/connection loss instead of recovery of an accepted operation.
    response_ready = commit_response_ready.wait(max(0.0, accepted_wait_s))
    time.sleep(max(0.0, kill_delay_s))
    commit_submitted_at = now()
    commit_request_elapsed_before_kill_s = time.monotonic() - commit_started
    accepted_202 = commit_box.get("status_code") == 202
    control = kill_and_start(container, 0)
    control_ok = recovery_control_ok(control)
    commit_thread.join(timeout=1.0)

    if not control_ok:
        ctx.check(
            "commit-recovery",
            status=INCONCLUSIVE,
            reason=(
                "未成功执行真实 kill-9/start 控制，不能把服务仍然健康 "
                "解释为崩溃恢复通过"
            ),
            elapsed_s=time.monotonic() - started,
            detail=_detail({
                "kill": control,
                "commit_response_before_kill": {
                    "ready": response_ready,
                    "status_code": commit_box.get("status_code"),
                },
                "accepted_202": accepted_202,
                "session_id": session_id,
            }),
        )
        return

    deadline = time.monotonic() + max(1.0, recovery_timeout_s)
    observations = []
    recovered = False
    while time.monotonic() < deadline:
        observation = health(health_url, health_timeout_s)
        observations.append({"at": now(), **observation})
        if observation["healthy"]:
            recovered = True
            break
        time.sleep(max(0.2, poll_s))

    if not recovered:
        ctx.check(
            "commit-recovery",
            status=FAIL,
            reason="service did not recover within timeout",
            elapsed_s=time.monotonic() - started,
            detail=_detail({
                "kill": control,
                "recovered": False,
                "last_observation": observations[-1] if observations else {},
            }),
        )
        return

    payload = commit_box.get("payload") or {}
    commit_payload = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    archive_id = (
        commit_payload.get("archive_id")
        or commit_payload.get("commit_id")
        or commit_payload.get("id")
    )
    # Reconciliation must use EchoMem's durable IDs. The client value is only
    # metadata used to correlate a request; it is not the persisted message ID.
    message_ids = [
        item["server_message_id"]
        for item in message_records
        if item.get("server_message_id")
    ]

    archive_discovery: dict[str, Any] = {}
    # A kill can drop the HTTP response even when EchoMem accepted the
    # operation. Discover the matching archive by its unique marker before
    # declaring the recovery probe inconclusive.
    if not archive_id:
        archives_response = client.request(
            "GET", f"/api/sessions/{session_id}/archives?limit=200"
        )
        candidate_ids = archive_ids_from_payload(archives_response.payload)
        for candidate_id in sorted(candidate_ids):
            candidate = client.get_archive(session_id, candidate_id)
            if marker in json.dumps(candidate.payload, ensure_ascii=False, default=str):
                archive_id = candidate_id
                archive_discovery = {
                    "status_code": archives_response.status_code,
                    "candidate_archive_ids": sorted(candidate_ids),
                    "matched_archive_id": archive_id,
                }
                break
        if not archive_id:
            archive_discovery = {
                "status_code": archives_response.status_code,
                "candidate_archive_ids": sorted(candidate_ids),
                "matched_archive_id": "",
            }

    if not archive_id:
        ctx.check(
            "commit-recovery",
            status=INCONCLUSIVE,
            reason=(
                "service recovered but the Commit response was lost and no "
                "archive containing the unique marker could be identified"
            ),
            elapsed_s=time.monotonic() - started,
            detail=_detail({
                "kill": control,
                "recovered": True,
                "archive_discovery": archive_discovery,
            }),
        )
        return

    replay_response = client.commit(session_id, idempotency_key=idempotency_key)
    replay_payload = replay_response.payload if isinstance(replay_response.payload, dict) else {}
    replay_result = (
        replay_payload.get("result")
        if isinstance(replay_payload.get("result"), dict)
        else replay_payload
    )
    replay_archive_id = (
        replay_result.get("archive_id")
        or replay_result.get("commit_id")
        or replay_result.get("id")
    )
    replayed = bool(replay_result.get("replayed")) if isinstance(replay_result, dict) else False
    idempotency_status = (
        PASS
        if replayed and str(replay_archive_id or "") == str(archive_id)
        else FAIL
    )
    idempotency_reason = (
        "same idempotency key returned the same archive with replayed=true"
        if idempotency_status == PASS
        else "same-key Commit replay did not return the original archive with replayed=true"
    )

    terminal = []
    deadline = time.monotonic() + max(1.0, recovery_timeout_s)
    while time.monotonic() < deadline:
        response = client.commit_status(session_id, str(archive_id))
        status_payload = response.payload
        for key in ("result", "status"):
            if isinstance(status_payload, dict) and isinstance(status_payload.get(key), dict):
                status_payload = status_payload[key]
        raw_state = (
            status_payload.get("status") or status_payload.get("state")
            if isinstance(status_payload, dict)
            else None
        )
        state = raw_state if isinstance(raw_state, str) else None
        terminal.append({
            "at": now(),
            "status_code": response.status_code,
            "state": state,
            "payload": response.payload,
            "error": response.error,
        })
        if state in {"completed", "failed", "error", "cancelled"}:
            break
        time.sleep(max(0.2, poll_s))
    final_state = terminal[-1].get("state") if terminal else None

    history = client.get_history(session_id, limit=200)
    memories = client.get_commit_memories(session_id, str(archive_id))

    expected_message_ids = set(message_ids)
    source_payloads: dict[str, dict[str, Any]] = {
        "history": history.payload if isinstance(history.payload, dict) else {},
        "archive": (
            client.get_archive(session_id, str(archive_id)).payload
            if archive_id
            else {}
        ),
        "commit_memories": memories.payload if isinstance(memories.payload, dict) else {},
    }
    cursor_response = client.fs_read(
        f"echo://sessions/{session_id}/current/commit_cursor.json"
    )
    source_payloads["commit_cursor"] = (
        decode_fs_read_payload(cursor_response.payload)
        if isinstance(cursor_response.payload, dict)
        else {}
    )
    source_ids = {
        source: sorted(values_from_payload(source_payload)[0])
        for source, source_payload in source_payloads.items()
    }
    observed_ids = set().union(*(set(ids) for ids in source_ids.values()))
    missing_ids = sorted(expected_message_ids - observed_ids)
    complete_sources = [
        source for source, ids in source_ids.items()
        if expected_message_ids and expected_message_ids <= set(ids)
    ]
    reconciliation_status = (
        PASS
        if expected_message_ids and not missing_ids
        else INCONCLUSIVE
        if not expected_message_ids
        else FAIL
    )

    cursor_status = (
        PASS
        if expected_message_ids and expected_message_ids <= set(source_ids["commit_cursor"])
        else INCONCLUSIVE
        if not expected_message_ids or cursor_response.status_code == 404
        else FAIL
    )
    cursor_reason = (
        "all server-assigned message IDs were present in commit_cursor.json"
        if cursor_status == PASS
        else "cursor endpoint unavailable or did not expose all server-assigned message IDs"
    )

    status = (
        FAIL
        if idempotency_status == FAIL
        else PASS
        if (
            final_state == "completed"
            and reconciliation_status == PASS
            and idempotency_status == PASS
            and history.status_code
            and history.status_code < 400
        )
        else FAIL
        if final_state in {"failed", "error", "cancelled"}
        else INCONCLUSIVE
    )
    if require_accepted_202 and not accepted_202:
        status = FAIL if commit_box.get("status_code") else INCONCLUSIVE
        reason = (
            "Commit 未在崩溃前返回 HTTP 202，不能证明已接受的异步操作可恢复"
            if commit_box.get("status_code")
            else "崩溃前未收到 Commit 响应，无法证明该操作曾返回 HTTP 202"
        )
    else:
        reason = (
            "same idempotency key returned the original archive with replayed=true, "
            "and all server-assigned message IDs were found in durable readback"
            if status == PASS
            else "same idempotency key returned the original archive but replayed was false"
            if idempotency_status == FAIL
            else "Commit did not reach a terminal completed state within the recovery window"
        )

    elapsed = time.monotonic() - started
    ctx.check(
        "commit-recovery",
        status=status,
        reason=reason,
        elapsed_s=elapsed,
        detail=_detail({
            "kill": control,
            "recovered": recovered,
            "commit_terminal": terminal,
            "archive_discovery": archive_discovery,
            "commit_submitted_at": commit_submitted_at,
            "commit_request_elapsed_before_kill_s": commit_request_elapsed_before_kill_s,
            "commit_response_before_kill": {
                "ready": response_ready,
                "status_code": commit_box.get("status_code"),
            },
            "accepted_202": accepted_202,
            "idempotency_key": idempotency_key,
            "session_id": session_id,
            "archive_id": archive_id,
        }),
    )
    ctx.check(
        "message-reconciliation",
        status=reconciliation_status,
        reason=(
            "all server-assigned message IDs were found in durable readback"
            if reconciliation_status == PASS
            else "no server-assigned message IDs to reconcile"
            if reconciliation_status == INCONCLUSIVE
            else "some server-assigned message IDs were missing from durable readback"
        ),
        elapsed_s=elapsed,
        detail=_detail({
            "expected_server_message_ids": sorted(expected_message_ids),
            "observed_by_source": source_ids,
            "missing_server_message_ids": missing_ids,
            "complete_sources": complete_sources,
            "cursor_status_code": cursor_response.status_code,
        }),
    )
    ctx.check(
        "idempotency-replay",
        status=idempotency_status,
        reason=idempotency_reason,
        elapsed_s=elapsed,
        detail=_detail({
            "status_code": replay_response.status_code,
            "archive_id": replay_archive_id,
            "replayed": replayed,
            "same_archive": str(replay_archive_id or "") == str(archive_id),
            "error": replay_response.error,
        }),
    )
    ctx.check(
        "cursor-reconciliation",
        status=cursor_status,
        reason=cursor_reason,
        elapsed_s=elapsed,
        detail=_detail({
            "expected_server_message_ids": sorted(expected_message_ids),
            "observed_by_cursor": sorted(source_ids["commit_cursor"]),
            "cursor_status_code": cursor_response.status_code,
        }),
    )

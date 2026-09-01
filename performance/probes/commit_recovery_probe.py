#!/usr/bin/env python3
"""Probe Commit recovery when the EchoMem container is killed mid-operation.

This uses the real HTTP service and the real configured model.  It is
intentionally conservative: losing the Commit response or lacking a
message-set/cursor endpoint is recorded as inconclusive instead of inferred
as success.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ._client import EchoMemHTTP
except ImportError:
    from _client import EchoMemHTTP


PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_tenant(path: Path, tenant_id: str) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data.get("tenants", []):
        if str(item.get("tenant_id")) == tenant_id:
            return {
                "tenant_id": str(item["tenant_id"]),
                "user_id": str(item.get("user_id") or f"stress-{tenant_id}"),
                "auth_key": os.environ.get(str(item.get("auth_key_env") or ""), ""),
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


def kill_and_start(container: str, restart_wait_s: float) -> dict[str, Any]:
    killed = subprocess.run(
        ["docker", "kill", "--signal", "KILL", container],
        capture_output=True,
        text=True,
        check=False,
    )
    result: dict[str, Any] = {
        "kill_returncode": killed.returncode,
        "kill_stderr": killed.stderr[-2000:],
        "killed_at": now(),
    }
    if killed.returncode != 0:
        return result
    started = subprocess.run(
        ["docker", "start", container],
        capture_output=True,
        text=True,
        check=False,
    )
    result.update(
        {
            "start_returncode": started.returncode,
            "start_stderr": started.stderr[-2000:],
            "restart_at": now(),
        }
    )
    if restart_wait_s > 0:
        time.sleep(restart_wait_s)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--health-url", default="")
    parser.add_argument("--container", required=True)
    parser.add_argument("--tenant-config", required=True, type=Path)
    parser.add_argument("--tenant", default="stress-a")
    parser.add_argument("--kill-delay-s", type=float, default=0.5)
    parser.add_argument("--messages", type=int, default=12)
    parser.add_argument("--content-chars", type=int, default=2500)
    parser.add_argument("--health-timeout-s", type=float, default=5.0)
    parser.add_argument("--recovery-timeout-s", type=float, default=180.0)
    parser.add_argument("--poll-s", type=float, default=2.0)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    started_at = now()
    health_url = args.health_url or args.base_url.rstrip("/") + "/health"
    tenant = load_tenant(args.tenant_config, args.tenant)
    client = EchoMemHTTP(
        args.base_url,
        auth_key=tenant["auth_key"],
        timeout_s=max(args.health_timeout_s, 60.0),
        tenant_id=tenant["tenant_id"],
        user_id=tenant["user_id"],
        account_id=tenant["tenant_id"],
        agent_id="pr421-commit-recovery",
    )
    result: dict[str, Any] = {
        "started_at": started_at,
        "finished_at": "",
        "base_url": args.base_url,
        "container": args.container,
        "tenant": args.tenant,
        "real_http": True,
        "mock_model": False,
        "kill_delay_s": args.kill_delay_s,
    }

    before = health(health_url, args.health_timeout_s)
    result["health_before"] = before
    if not before["healthy"]:
        result.update({"status": INCONCLUSIVE, "reason": "service was not healthy before probe"})
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 2

    session_id, _ = client.open_session(args.tenant, f"pr421-recovery-{uuid.uuid4().hex[:10]}")
    marker = f"pr421-recovery-marker-{uuid.uuid4().hex}"
    message_ids = []
    for index in range(max(1, args.messages)):
        message_id = f"recovery-{uuid.uuid4().hex}"
        message_ids.append(message_id)
        response = client.add_message(
            session_id,
            message_id,
            (
                f"Real Commit recovery probe {marker}; message {index}. "
                + ("payload-" + marker + " ") * max(1, args.content_chars // (len(marker) + 9))
            )[: max(64, args.content_chars)],
        )
        if response.status_code is None or response.status_code >= 400:
            result.update({
                "status": FAIL,
                "reason": "message setup failed",
                "session_id": session_id,
                "setup_response": response.payload,
                "setup_status_code": response.status_code,
            })
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False))
            return 2

    commit_box: dict[str, Any] = {}

    def submit() -> None:
        try:
            response = client.commit(session_id)
            commit_box["status_code"] = response.status_code
            commit_box["payload"] = response.payload
            commit_box["error"] = response.error
        except BaseException as exc:  # the process may be killed during the request
            commit_box["error"] = f"{type(exc).__name__}: {exc}"

    commit_thread = threading.Thread(target=submit, daemon=True)
    commit_started = time.monotonic()
    commit_thread.start()
    time.sleep(max(0.0, args.kill_delay_s))
    result["commit_submitted_at"] = now()
    result["commit_request_elapsed_before_kill_s"] = time.monotonic() - commit_started
    result["container_control"] = kill_and_start(args.container, 0)
    commit_thread.join(timeout=1.0)
    result["commit_response"] = dict(commit_box)

    deadline = time.monotonic() + max(1.0, args.recovery_timeout_s)
    observations = []
    recovered = False
    while time.monotonic() < deadline:
        observation = health(health_url, args.health_timeout_s)
        observations.append({"at": now(), **observation})
        if observation["healthy"]:
            recovered = True
            break
        time.sleep(max(0.2, args.poll_s))
    result["health_after"] = observations
    result["recovered"] = recovered

    payload = commit_box.get("payload") or {}
    commit_payload = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    archive_id = (
        commit_payload.get("archive_id")
        or commit_payload.get("commit_id")
        or commit_payload.get("id")
    )
    result["session_id"] = session_id
    result["marker"] = marker
    result["message_ids"] = message_ids
    result["archive_id"] = archive_id
    result["commit_response_result"] = commit_payload

    if not recovered:
        result.update({"status": FAIL, "reason": "service did not recover within timeout"})
    elif not archive_id:
        result.update({
            "status": INCONCLUSIVE,
            "reason": "Commit response was lost before archive_id was observed; replay cannot be identified",
        })
    else:
        terminal = []
        deadline = time.monotonic() + max(1.0, args.recovery_timeout_s)
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
            time.sleep(max(0.2, args.poll_s))
        result["commit_terminal"] = terminal
        final_state = terminal[-1].get("state") if terminal else None
        history = client.get_history(session_id, limit=200)
        memories = client.get_commit_memories(session_id, str(archive_id))
        result["history_observation"] = {
            "status_code": history.status_code,
            "payload": history.payload,
            "error": history.error,
        }
        result["commit_memories_observation"] = {
            "status_code": memories.status_code,
            "payload": memories.payload,
            "error": memories.error,
        }
        result["cursor_reconciliation"] = {
            "status": INCONCLUSIVE,
            "reason": "EchoMem cursor/message-set export endpoint was not configured",
        }
        result["status"] = (
            PASS
            if final_state == "completed" and history.status_code and history.status_code < 400
            else FAIL
            if final_state in {"failed", "error", "cancelled"}
            else INCONCLUSIVE
        )
        result["reason"] = (
            "service recovered and Commit reached terminal state, but exact replay/idempotency is not provable"
            if result["status"] == PASS
            else "Commit did not reach a terminal completed state within the recovery window"
        )

    result["finished_at"] = now()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Execute explicit real fault controls and retain a tamper-evident timeline.

The harness never simulates a dependency failure. A control is either supplied
by the deployment (command, HTTP endpoint, or Docker container) or the case is
reported as INCONCLUSIVE. An explicit HTTP 404 is the only evidence that an
HTTP control endpoint is not implemented.

Config is read via ``ctx.params`` (keys same as original CLI params): ``kind`` /
``command`` / ``endpoint`` / ``action`` / ``container`` / ``signal`` /
``timeout_s``.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from performance.ctx import Ctx

NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_control(params: dict[str, Any]) -> dict[str, Any]:
    started_at = now()
    started = time.monotonic()
    command = params.get("command", os.getenv("STRESS_FAULT_COMMAND", ""))
    endpoint = params.get("endpoint", os.getenv("STRESS_FAULT_ENDPOINT", ""))
    container = params.get("container", "")
    signal = params.get("signal", "KILL")
    action = params.get("action", "")
    timeout_s = float(params.get("timeout_s", 30.0))
    if not command and not endpoint and not container:
        return {
            "status": INCONCLUSIVE,
            "reason": "no real fault control supplied",
            "started_at": started_at,
            "finished_at": now(),
            "elapsed_s": 0.0,
        }
    try:
        if command:
            completed = subprocess.run(
                command, shell=True, text=True, capture_output=True,
                timeout=timeout_s, check=False,
            )
            result = {
                "control": "command",
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
            status = (
                PASS
                if completed.returncode == 0
                else INCONCLUSIVE
                if completed.returncode == 127
                else FAIL
            )
        elif container:
            completed = subprocess.run(
                ["docker", "kill", "--signal", signal, container],
                text=True, capture_output=True, timeout=timeout_s, check=False,
            )
            result = {
                "control": "docker",
                "container": container,
                "signal": signal,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
            status = (
                PASS
                if completed.returncode == 0
                else INCONCLUSIVE
                if completed.returncode == 127
                else FAIL
            )
        else:
            request = urllib.request.Request(
                endpoint,
                data=json.dumps({"action": action}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                result = {
                    "control": "http",
                    "endpoint": endpoint,
                    "status_code": response.status,
                    "body": response.read().decode(errors="replace")[-4000:],
                }
            status = (
                PASS
                if 200 <= result["status_code"] < 300
                else NOT_IMPLEMENTED
                if result["status_code"] == 404
                else FAIL
            )
    except urllib.error.HTTPError as exc:
        result = {
            "control": "http",
            "endpoint": endpoint,
            "status_code": exc.code,
            "body": exc.read().decode(errors="replace")[-4000:],
        }
        status = NOT_IMPLEMENTED if exc.code == 404 else FAIL
    except FileNotFoundError as exc:
        result = {"error": str(exc)}
        status = INCONCLUSIVE
    except (OSError, urllib.error.URLError, subprocess.TimeoutExpired) as exc:
        result, status = {"error": str(exc)}, FAIL
    result.update({
        "status": status,
        "started_at": started_at,
        "finished_at": now(),
        "elapsed_s": time.monotonic() - started,
    })
    return result


def run(ctx: Ctx) -> None:
    params = ctx.params
    kind = str(params.get("kind") or "")
    if not kind:
        ctx.check(
            "fault injection",
            status=INCONCLUSIVE,
            reason="no fault kind configured",
        )
        return
    result = run_control(params)
    detail = json.dumps(
        {
            key: value
            for key, value in result.items()
            if key not in ("status", "reason", "elapsed_s")
        },
        ensure_ascii=False,
    )[:500]
    ctx.check(
        kind,
        status=result["status"],
        reason=result.get("reason") or (
            "fault control completed successfully"
            if result["status"] == PASS
            else "fault control did not complete successfully"
        ),
        elapsed_s=result.get("elapsed_s"),
        detail=detail,
    )

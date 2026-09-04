"""探测 EchoMem 进程/容器被 kill -9 后的恢复能力（真实 HTTP）。

对目标进程（``pid``）或容器（``container``）发送 SIGKILL，可选执行
``restart_command`` 拉起进程，然后轮询 health 直到恢复或超时。无 pid /
container 时 INCONCLUSIVE；docker 命令不可用（返回码 127）INCONCLUSIVE。

配置经 ``ctx.params`` 读取（键名与原 CLI 参数同名）：``health_url`` /
``pid`` / ``container`` / ``restart_command`` / ``wait_s`` / ``poll_s`` /
``auth_key`` / ``auth_key_env``。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from performance.ctx import Ctx

PASS = "PASS"
FAIL = "FAIL"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
INCONCLUSIVE = "INCONCLUSIVE"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def health(url: str, timeout: float, auth_key: str = "") -> tuple[bool, int | None, str]:
    headers = {}
    if auth_key:
        headers["X-Auth-Key"] = auth_key
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300, response.status, response.read().decode(errors="replace")[-1000:]
    except (OSError, urllib.error.URLError) as exc:
        return False, None, str(exc)


def _detail(fields: dict[str, Any]) -> str:
    return json.dumps(fields, ensure_ascii=False)[:500]


def run(ctx: Ctx) -> None:
    params = ctx.params
    auth_key = str(params.get("auth_key") or "")
    if not auth_key:
        auth_key = os.getenv(str(params.get("auth_key_env") or "ECHOMEM_AUTH_KEY"), "")
    health_url = str(params.get("health_url") or (ctx.base_url + "/health"))
    pid = int(params.get("pid", 0))
    container = str(params.get("container") or "")
    restart_command = str(params.get("restart_command") or os.getenv("ECHOMEM_RESTART_COMMAND", ""))
    wait_s = max(0.1, float(params.get("wait_s", 120)))
    poll_s = max(0.1, float(params.get("poll_s", 2)))

    if not pid and not container:
        ctx.check(
            "recovery",
            status=INCONCLUSIVE,
            reason="pid or container is required; recovery was not externally exercised",
        )
        return

    before = health(health_url, 5, auth_key)
    ctx.check(
        "health before kill",
        status=PASS if before[0] else FAIL,
        reason="health endpoint is up before kill" if before[0] else (
            f"health endpoint is down before kill: {before[2]}"
        ),
        detail=_detail({"health_url": health_url, "before_health": before[0]}),
    )

    kill_result: dict[str, Any] = {}
    try:
        if container:
            killed = subprocess.run(
                ["docker", "kill", "--signal", "KILL", container],
                capture_output=True,
                text=True,
                check=False,
            )
            if killed.returncode != 0:
                kill_result = {
                    "control": "docker",
                    "returncode": killed.returncode,
                    "stderr": killed.stderr[-2000:],
                }
                ctx.check(
                    "recovery",
                    status=INCONCLUSIVE if killed.returncode == 127 else FAIL,
                    reason="container kill command is unavailable or failed",
                    detail=_detail({"kill": kill_result, "observations": [], "recovery_time_s": None}),
                )
                return
            kill_result = {
                "control": "docker",
                "returncode": killed.returncode,
                "stderr": killed.stderr[-2000:],
            }
        else:
            os.kill(pid, signal.SIGKILL)
            kill_result = {"control": "pid", "pid": pid, "signal": "SIGKILL"}
    except FileNotFoundError as exc:
        ctx.check(
            "recovery",
            status=INCONCLUSIVE,
            reason=f"kill control is unavailable: {exc}",
            detail=_detail({"kill": {}, "observations": [], "recovery_time_s": None}),
        )
        return

    restart = None
    if restart_command:
        restart = subprocess.Popen(restart_command, shell=True, start_new_session=True)

    deadline = time.monotonic() + wait_s
    observations: list[dict[str, Any]] = []
    recovered = False
    while time.monotonic() < deadline:
        observation = health(health_url, 5, auth_key)
        observations.append({"at": now(), "healthy": observation[0], "status_code": observation[1]})
        if observation[0]:
            recovered = True
            break
        time.sleep(poll_s)

    recovery_time_s = (len(observations) - 1) * poll_s if recovered else None
    status = PASS if before[0] and recovered else FAIL
    if status == PASS:
        reason = "recovered after kill"
    elif not recovered:
        reason = "service did not recover within the wait window"
    else:
        reason = "service was not healthy before kill"
    summary: dict[str, Any] = {
        "kill": kill_result,
        "observations": observations,
        "recovery_time_s": recovery_time_s,
        "restart_command_supplied": bool(restart_command),
    }
    if restart is not None and restart.poll() is not None:
        summary["restart_returncode"] = restart.returncode
    ctx.check(
        "recovery",
        status=status,
        reason=reason,
        detail=_detail(summary),
    )
    ctx.check(
        "health after kill",
        status=PASS if recovered else FAIL,
        reason="health endpoint is up after the recovery window" if recovered else (
            "health endpoint is still down after the recovery window"
        ),
        detail=_detail({
            "recovery_time_s": recovery_time_s,
            "last_observations": observations[-3:],
        }),
    )

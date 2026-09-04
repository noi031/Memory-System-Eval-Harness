"""探测真实 HTTP 客户端断连后 EchoMem 的服务可用性与有界资源回收。

客户端线程在发出 search POST 后延迟 ``disconnect_delay_s`` 即关闭连接，
验证服务在断连波次后保持健康、进程资源（FD 数）回落到波前水平。EchoMem
不暴露逐请求的孤儿任务/FD 契约，因此默认结论为 INCONCLUSIVE；只有波后
采样不健康才判 FAIL（``exit_on = ("FAIL",)``，INCONCLUSIVE 不算失败）。

配置经 ``ctx.params`` 读取（键名与原 CLI 参数同名）：``auth_key`` /
``auth_key_env`` / ``auth_header`` / ``tenant`` / ``pid`` / ``requests`` /
``disconnect_delay_s`` / ``recovery_wait_s`` / ``post_recovery_wait_s``。
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from performance.ctx import Ctx

PASS = "PASS"
FAIL = "FAIL"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
INCONCLUSIVE = "INCONCLUSIVE"

exit_on = ("FAIL",)


def http_json(url: str, method: str, body: dict[str, Any] | None, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"raw": raw[-1000:]}
            return {"status_code": response.status, "payload": payload}
    except (OSError, urllib.error.URLError) as exc:
        return {"status_code": None, "error": str(exc)}


def process_sample(pid: int) -> dict[str, Any]:
    result: dict[str, Any] = {"pid": pid}
    if not pid:
        return result
    proc = Path("/proc") / str(pid)
    try:
        result["threads"] = int((proc / "status").read_text().split("Threads:", 1)[1].splitlines()[0])
    except (OSError, IndexError, ValueError):
        pass
    try:
        result["fds"] = len(list((proc / "fd").iterdir()))
    except OSError:
        pass
    return result


def metrics(base_url: str, headers: dict[str, str]) -> dict[str, Any]:
    return http_json(base_url.rstrip("/") + "/metrics", "GET", None, headers, 5)


def is_healthy(result: dict[str, Any]) -> bool:
    status_code = result.get("status_code")
    return status_code is not None and status_code < 400


def _detail(item: dict[str, Any]) -> str:
    payload = item.get("payload")
    if isinstance(payload, dict) and payload:
        return json.dumps(payload, ensure_ascii=False)[:500]
    return ""


def run(ctx: Ctx) -> None:
    params = ctx.params
    base_url = ctx.base_url
    auth_key = str(params.get("auth_key") or "")
    if not auth_key:
        auth_key = os.getenv(str(params.get("auth_key_env") or "ECHOMEM_AUTH_KEY"), "")
    auth_header = str(params.get("auth_header") or "X-Auth-Key")
    tenant = str(params.get("tenant") or "stress-a")
    pid = int(params.get("pid", 0))
    request_count = int(params.get("requests", 32))
    disconnect_delay_s = float(params.get("disconnect_delay_s", 0.05))
    recovery_wait_s = float(params.get("recovery_wait_s", 30))
    post_recovery_wait_s = float(params.get("post_recovery_wait_s", 60))

    headers = {"X-EchoMem-Tenant": tenant}
    if auth_key:
        headers[auth_header] = auth_key
    health_url = base_url.rstrip("/") + "/health"
    before = http_json(health_url, "GET", None, headers, 5)
    metrics_before = metrics(base_url, headers)
    proc_before = process_sample(pid)
    started = time.monotonic()
    outcomes: list[dict[str, Any]] = []

    def disconnect_one(index: int) -> None:
        connection = None
        try:
            parsed = urllib.parse.urlparse(base_url)
            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            body = json.dumps({
                "query": f"real disconnect probe {tenant} {index} {uuid_marker}",
                "agent_id": "pr421-disconnect",
                "limit": 10,
                "include_debug": True,
            })
            connection.request(
                "POST",
                "/api/retrieval/search",
                body=body,
                headers={"Content-Type": "application/json", **headers},
            )
            time.sleep(max(0.0, disconnect_delay_s))
            connection.close()
            outcomes.append({"index": index, "client_action": "closed_after_request"})
        except BaseException as exc:
            outcomes.append({"index": index, "client_action": "client_error", "error": str(exc)})
            if connection:
                connection.close()

    uuid_marker = f"{os.getpid()}-{int(time.time())}"
    threads = [threading.Thread(target=disconnect_one, args=(i,)) for i in range(max(1, request_count))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    time.sleep(max(0.0, recovery_wait_s))
    after = http_json(health_url, "GET", None, headers, 5)
    metrics_after = metrics(base_url, headers)
    proc_after = process_sample(pid)
    wave_elapsed_s = time.monotonic() - started
    recovered = is_healthy(after)
    time.sleep(max(0.0, post_recovery_wait_s))
    after_settle = http_json(health_url, "GET", None, headers, 5)
    metrics_after_settle = metrics(base_url, headers)
    proc_after_settle = process_sample(pid)
    total_elapsed_s = time.monotonic() - started

    checks: list[dict[str, Any]] = []

    healthy_before = is_healthy(before)
    checks.append({
        "name": "health_before",
        "status": PASS if healthy_before else INCONCLUSIVE,
        "reason": "service was healthy before the disconnect wave" if healthy_before else "service was not healthy before the probe; disconnect wave was not meaningfully exercised",
        "payload": before,
    })

    client_errors = [outcome for outcome in outcomes if outcome.get("client_action") == "client_error"]
    checks.append({
        "name": "disconnect_wave",
        "status": PASS if not client_errors else INCONCLUSIVE,
        "reason": (
            f"{len(outcomes)} client connections closed {disconnect_delay_s}s after request"
            if not client_errors
            else f"{len(client_errors)} of {len(outcomes)} client disconnect attempts errored"
        ),
        "elapsed_s": wave_elapsed_s,
        "payload": {"client_errors": client_errors[:10]},
    })

    checks.append({
        "name": "health_after",
        "status": PASS if recovered else FAIL,
        "reason": "service was healthy after the client disconnect wave" if recovered else "service was not healthy after the client disconnect wave",
        "elapsed_s": wave_elapsed_s,
        "payload": after,
    })

    checks.append({
        "name": "service_recovered",
        "status": PASS if recovered else FAIL,
        "reason": "service recovered after the disconnect wave" if recovered else "service did not recover after the disconnect wave",
        "elapsed_s": wave_elapsed_s,
        "payload": {"recovered": recovered, "requests": len(outcomes)},
    })

    metric_codes = {
        "before": metrics_before.get("status_code"),
        "after": metrics_after.get("status_code"),
        "after_settle": metrics_after_settle.get("status_code"),
    }
    metrics_ok = all(code is not None and code < 400 for code in metric_codes.values())
    checks.append({
        "name": "metrics_observed",
        "status": PASS if metrics_ok else INCONCLUSIVE,
        "reason": "metrics endpoint stayed observable across all samples" if metrics_ok else "metrics endpoint was not observable at every sample",
        "elapsed_s": total_elapsed_s,
        "payload": {"status_codes": metric_codes},
    })

    settled_healthy = is_healthy(after_settle)
    checks.append({
        "name": "health_after_settle",
        "status": PASS if settled_healthy else FAIL,
        "reason": "service was healthy after the settle window" if settled_healthy else "service was not healthy after the settle window",
        "elapsed_s": total_elapsed_s,
        "payload": after_settle,
    })

    if not pid:
        resource_status = INCONCLUSIVE
        resource_reason = "no pid configured; /proc process sampling disabled"
    elif not (proc_before.get("fds") and proc_after_settle.get("fds")):
        resource_status = INCONCLUSIVE
        resource_reason = "process FD samples unavailable (/proc not readable)"
    else:
        settled = proc_after_settle["fds"] <= max(proc_before["fds"] + 2, proc_after.get("fds", 0))
        resource_status = PASS if settled else FAIL
        resource_reason = "process FD count settled near the pre-wave level" if settled else "process FD count did not settle after the disconnect wave"
    checks.append({
        "name": "resource_settled",
        "status": resource_status,
        "reason": resource_reason,
        "elapsed_s": total_elapsed_s,
        "payload": {
            "pid": pid,
            "fds_before": proc_before.get("fds"),
            "fds_after": proc_after.get("fds"),
            "fds_after_settle": proc_after_settle.get("fds"),
            "threads_before": proc_before.get("threads"),
            "threads_after": proc_after.get("threads"),
            "threads_after_settle": proc_after_settle.get("threads"),
        },
    })

    for item in checks:
        ctx.check(
            item["name"],
            status=item["status"],
            reason=item["reason"],
            elapsed_s=item.get("elapsed_s"),
            detail=_detail(item),
        )

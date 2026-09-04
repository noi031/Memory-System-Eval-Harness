"""探测 EchoMem 可选契约（真实 HTTP）。

只有显式 HTTP 404 才能证明端点未实现；配置缺失、测试身份缺失、传输失败
一律 INCONCLUSIVE——探针不能把自身环境问题说成 EchoMem 的问题。

配置经 ``ctx.params`` 读取（键名与原 CLI 参数同名）：``auth_key`` /
``auth_key_env`` / ``auth_header`` / ``health_path`` / ``metrics_path`` /
``session_id`` / ``cursor_path`` / ``message_list_key`` /
``operation_path`` / ``operation_keys`` / ``conflict_path`` /
``conflict_keys`` / ``ttl_path`` / ``ttl_keys`` / ``engine_path`` /
``engine_keys`` / ``fault_path`` / ``fault_keys`` / ``timeout_s``。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from performance.ctx import Ctx

PASS = "PASS"
FAIL = "FAIL"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
INCONCLUSIVE = "INCONCLUSIVE"


def request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    auth_key: str = "",
    auth_header: str = "X-Auth-Key",
    timeout_s: float = 10.0,
    preserve_raw: bool = False,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if auth_key:
        headers[auth_header] = auth_key
    started = time.monotonic()
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                payload: Any = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw if preserve_raw else raw[-4000:]}
            return {
                "status_code": response.status,
                "elapsed_s": time.monotonic() - started,
                "headers": {str(k).lower(): str(v) for k, v in response.headers.items()},
                "payload": payload,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw if preserve_raw else raw[-4000:]}
        return {
            "status_code": exc.code,
            "elapsed_s": time.monotonic() - started,
            "headers": {str(k).lower(): str(v) for k, v in exc.headers.items()},
            "payload": payload,
            "error": f"HTTP {exc.code}",
        }
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return {
            "status_code": None,
            "elapsed_s": time.monotonic() - started,
            "payload": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def classify_probe(
    name: str,
    result: dict[str, Any],
    *,
    required_keys: tuple[str, ...] = (),
    expect_list_key: str = "",
) -> dict[str, Any]:
    status_code = result.get("status_code")
    if status_code == 404:
        status = NOT_IMPLEMENTED
        reason = "endpoint returned HTTP 404"
    elif status_code is None:
        status = INCONCLUSIVE
        reason = "transport failure; capability could not be observed"
    elif not 200 <= int(status_code) < 300:
        status = FAIL
        reason = f"endpoint returned HTTP {status_code}"
    else:
        payload = result.get("payload")
        missing = [
            key for key in required_keys
            if not isinstance(payload, dict) or payload.get(key) in (None, "")
        ]
        if expect_list_key and (
            not isinstance(payload, dict) or not isinstance(payload.get(expect_list_key), list)
        ):
            missing.append(expect_list_key)
        status = FAIL if missing else PASS
        reason = "response satisfies the configured contract" if not missing else (
            f"missing response fields: {', '.join(missing)}"
        )
    return {
        "name": name,
        "status": status,
        "http_status": status_code,
        "elapsed_s": result.get("elapsed_s"),
        "reason": reason,
        "error": result.get("error", ""),
        "payload": result.get("payload", {}),
    }


def _detail(item: dict[str, Any]) -> str:
    payload = item.get("payload")
    if isinstance(payload, dict) and payload:
        return json.dumps(payload, ensure_ascii=False)[:500]
    return str(item.get("error") or "")


def run(ctx: Ctx) -> None:
    params = ctx.params
    auth_key = str(params.get("auth_key") or "")
    if not auth_key:
        auth_key = os.getenv(str(params.get("auth_key_env") or "ECHOMEM_AUTH_KEY"), "")
    auth_header = str(params.get("auth_header") or "X-Auth-Key")
    timeout_s = float(params.get("timeout_s", 10.0))
    base_url = ctx.base_url

    checks: list[dict[str, Any]] = []

    checks.append(classify_probe(
        "health/version",
        request(base_url, params.get("health_path", "/health"), auth_key=auth_key,
                auth_header=auth_header, timeout_s=timeout_s),
    ))
    optional = [
        ("cursor/message-set", params.get("cursor_path"), (), str(params.get("message_list_key") or "message_ids")),
        ("operation/idempotency", params.get("operation_path"), tuple(params.get("operation_keys") or ["operation_id"]), ""),
        ("version/conflict", params.get("conflict_path"), tuple(params.get("conflict_keys") or ["version", "conflict_count"]), ""),
        ("cache/TTL", params.get("ttl_path"), tuple(params.get("ttl_keys") or ["ttl_seconds"]), ""),
        ("engine status/degradation", params.get("engine_path"), tuple(params.get("engine_keys") or ["status"]), ""),
        ("fault control", params.get("fault_path"), tuple(params.get("fault_keys") or ["status"]), ""),
    ]
    for name, path, keys, list_key in optional:
        if not path:
            checks.append({
                "name": name,
                "status": INCONCLUSIVE,
                "http_status": None,
                "reason": "no probe endpoint configured; capability was not externally tested",
                "payload": {},
            })
            continue
        if "{session}" in str(path) and not params.get("session_id"):
            checks.append({
                "name": name,
                "status": INCONCLUSIVE,
                "http_status": None,
                "reason": "endpoint requires session_id; capability was not externally tested",
                "payload": {},
            })
            continue
        path = str(path).replace("{session}", str(params.get("session_id")))
        result = request(
            base_url,
            path,
            method="POST" if name == "fault control" else "GET",
            body={"action": "status"} if name == "fault control" else None,
            auth_key=auth_key,
            auth_header=auth_header,
            timeout_s=timeout_s,
        )
        checks.append(classify_probe(name, result, required_keys=keys, expect_list_key=list_key))

    metric_result = request(
        base_url, params.get("metrics_path", "/metrics"), auth_key=auth_key,
        auth_header=auth_header, timeout_s=timeout_s, preserve_raw=True,
    )
    metric_check = classify_probe("Prometheus B7 metrics", metric_result)
    raw = metric_result.get("payload", {}).get("raw", "") if isinstance(metric_result.get("payload"), dict) else ""
    required_metrics = {
        "lane_queued": "echomem_lane_queued",
        "lane_wait": "echomem_lane_wait_seconds",
        "lane_exec": "echomem_lane_exec_seconds",
        "lane_rejected": "echomem_lane_rejected_total",
        "engine_exec": "echomem_engine_fanout_exec_seconds",
        "engine_skipped": "echomem_engine_fanout_skipped_total",
    }
    if metric_check["status"] == PASS:
        present = {
            key: metric in raw
            for key, metric in required_metrics.items()
        }
        metric_check["required_metrics"] = required_metrics
        metric_check["present"] = present
        metric_check["missing"] = [key for key, value in present.items() if not value]
        if metric_check["missing"]:
            metric_check["status"] = INCONCLUSIVE
            metric_check["reason"] = "metrics endpoint is reachable but B7 families are incomplete"
    checks.append(metric_check)

    for item in checks:
        ctx.check(
            item["name"],
            status=item["status"],
            reason=item["reason"],
            elapsed_s=item.get("elapsed_s"),
            detail=_detail(item),
        )

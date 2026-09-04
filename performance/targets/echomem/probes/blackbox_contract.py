"""Probe EchoMem's existing black-box contracts after a real stress run.

The harness does not require a new EchoMem endpoint.  It reuses a session and
archive recorded by the runner, then checks the public history/archive/status
APIs, the existing commit cursor file, and Prometheus metrics.

Config comes from ``ctx.params`` (keys match the original CLI flags):
``commit_csv`` / ``tenant`` / ``auth_key`` / ``auth_key_env`` /
``auth_header`` / ``metrics_path`` / ``cursor_uri_template`` /
``cursor_url_template`` / ``timeout_s``; the target base URL is
``ctx.base_url``.
"""

from __future__ import annotations

import csv
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

from performance.ctx import Ctx

PASS = "PASS"
FAIL = "FAIL"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
INCONCLUSIVE = "INCONCLUSIVE"


def request(
    base_url: str,
    path: str,
    *,
    auth_key: str,
    auth_header: str,
    timeout_s: float,
    preserve_raw: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    url = path if path.startswith(("http://", "https://")) else base_url.rstrip("/") + path
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", auth_header: auth_key}
        if auth_key
        else {"Accept": "application/json"},
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
                "elapsed_s": round(time.monotonic() - started, 6),
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
            "elapsed_s": round(time.monotonic() - started, 6),
            "payload": payload,
            "error": f"HTTP {exc.code}",
        }
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return {
            "status_code": None,
            "elapsed_s": round(time.monotonic() - started, 6),
            "payload": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def classify(name: str, result: dict[str, Any], *, allow_empty: bool = True) -> dict[str, Any]:
    code = result.get("status_code")
    if code == 404:
        status, reason = NOT_IMPLEMENTED, "EchoMem 明确返回 HTTP 404"
    elif code is None:
        status, reason = INCONCLUSIVE, "传输失败，无法判断 EchoMem 是否支持该能力"
    elif not 200 <= int(code) < 300:
        status, reason = FAIL, f"接口返回 HTTP {code}"
    elif allow_empty or result.get("payload") not in ({}, None):
        status, reason = PASS, "接口可通过真实 HTTP 访问"
    else:
        status, reason = INCONCLUSIVE, "接口返回为空，无法判断响应契约"
    return {
        "name": name,
        "status": status,
        "http_status": code,
        "elapsed_s": result.get("elapsed_s"),
        "reason": reason,
        "error": result.get("error", ""),
        "payload_keys": sorted(result["payload"]) if isinstance(result.get("payload"), dict) else [],
    }


def first_completed(path: Path, tenant: str = "") -> dict[str, str]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        if tenant and str(row.get("tenant") or "") != tenant:
            continue
        if (
            str(row.get("status") or "").lower()
            in {"completed", "complete", "success", "succeeded"}
        ):
            return row
    return {}


def _detail(item: dict[str, Any]) -> str:
    parts: dict[str, Any] = {}
    for key in ("session_id", "archive_id", "http_status", "payload_keys", "error", "missing"):
        value = item.get(key)
        if value not in (None, "", [], {}):
            parts[key] = value
    if not parts:
        return ""
    return json.dumps(parts, ensure_ascii=False)[:500]


def run(ctx: Ctx) -> None:
    params = ctx.params
    auth_key = str(params.get("auth_key") or "")
    if not auth_key:
        auth_key = os.getenv(str(params.get("auth_key_env") or "ECHOMEM_AUTH_KEY"), "")
    auth_header = str(params.get("auth_header") or "X-Auth-Key")
    metrics_path = str(params.get("metrics_path") or "/metrics")
    cursor_uri_template = str(
        params.get("cursor_uri_template")
        or "echo://sessions/{session}/current/commit_cursor.json"
    )
    cursor_url_template = str(params.get("cursor_url_template") or "")
    timeout_s = float(params.get("timeout_s", 10.0))
    base_url = ctx.base_url

    row = first_completed(
        Path(str(params.get("commit_csv") or "")),
        str(params.get("tenant") or ""),
    )
    session = str(row.get("session_id") or "")
    archive = str(row.get("archive_id") or "")
    checks: list[dict[str, Any]] = []

    if not session:
        ctx.check(
            "blackbox-contract",
            status=INCONCLUSIVE,
            reason="no completed commit in csv",
        )
        return
    if not archive:
        # Older runner artifacts did not persist archive_id. Keep probing the
        # session-scoped contracts, but make the missing operation identity
        # explicit instead of discarding otherwise useful evidence.
        checks.append({
            "name": "commit_identity",
            "status": INCONCLUSIVE,
            "http_status": None,
            "reason": "旧版 commit_results.csv 未记录 archive_id，无法定位单次 commit_status",
            "payload_keys": [],
        })

    paths = [
        ("history", f"/api/sessions/{quote(session, safe='')}/history?limit=200"),
        ("archives", f"/api/sessions/{quote(session, safe='')}/archives?limit=200"),
    ]
    if archive:
        paths.extend([
            (
                "commit_status",
                f"/api/sessions/{quote(session, safe='')}/commits/{quote(archive, safe='')}",
            ),
            (
                "commit_memories",
                f"/api/sessions/{quote(session, safe='')}/commits/{quote(archive, safe='')}/memories",
            ),
        ])
    for name, path in paths:
        checks.append(
            classify(
                name,
                request(
                    base_url,
                    path,
                    auth_key=auth_key,
                    auth_header=auth_header,
                    timeout_s=timeout_s,
                ),
            )
        )

    if cursor_url_template:
        cursor_url = cursor_url_template.format(session=session, archive=archive)
        cursor_result = request(
            base_url,
            cursor_url,
            auth_key=auth_key,
            auth_header=auth_header,
            timeout_s=timeout_s,
        )
    else:
        cursor_uri = cursor_uri_template.format(session=session, archive=archive)
        cursor_result = request(
            base_url,
            f"/fs/read?uri={quote(cursor_uri, safe=':/')}",
            auth_key=auth_key,
            auth_header=auth_header,
            timeout_s=timeout_s,
        )
    checks.append(classify("commit_cursor", cursor_result))

    metrics = request(
        base_url,
        metrics_path,
        auth_key=auth_key,
        auth_header=auth_header,
        timeout_s=timeout_s,
        preserve_raw=True,
    )
    metric = classify("metrics", metrics)
    metric_text = ""
    payload = metrics.get("payload")
    if isinstance(payload, dict):
        metric_text = str(payload.get("raw") or "")
    required = {
        "lane_queued": "echomem_lane_queued",
        "lane_wait": "echomem_lane_wait_seconds",
        "lane_exec": "echomem_lane_exec_seconds",
        "lane_rejected": "echomem_lane_rejected_total",
        "engine_exec": "echomem_engine_fanout_exec_seconds",
        "engine_skipped": "echomem_engine_fanout_skipped_total",
    }
    if metric.get("status") == PASS:
        metric["present"] = {key: value in metric_text for key, value in required.items()}
        metric["missing"] = [key for key, present in metric["present"].items() if not present]
        if metric["missing"]:
            metric["status"] = INCONCLUSIVE
            metric["reason"] = "metrics 可访问，但 PR449 B7/fan-out 指标族不完整"
    checks.append(metric)

    for item in checks:
        item["session_id"] = session
        item["archive_id"] = archive
        ctx.check(
            item["name"],
            status=item["status"],
            reason=item["reason"],
            elapsed_s=item.get("elapsed_s"),
            detail=_detail(item),
        )

"""Reconcile accepted Commit operations with a real cursor/message-set API.

对已受理（completed）的 Commit 记录做对账：读 cursor（``cursor_url_template``
直取，或经 EchoMem ``/fs/read`` 读持久 cursor），并在 ``base_url`` 下复查
history / archives / archive / commit_status / commit_memories 五个只读源，
用 :func:`values_from_payload` 抽取 message / archive / operation 身份与 CSV
期望比对。每个 session 一条断言；无 completed Commit 证据或未配置任何对账
端点时 INCONCLUSIVE，cursor 端点 HTTP 404 视为 NOT_IMPLEMENTED。

配置经 ``ctx.params`` 读取（键名与原 CLI 参数同名）：``commit_csv``（必填）/
``cursor_url_template`` / ``cursor_uri_template`` / ``auth_key`` /
``auth_key_env`` / ``auth_header`` / ``timeout_s``。``base_url`` 取
``ctx.base_url``。
"""

from __future__ import annotations

import csv
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

from performance.ctx import Ctx

NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def read_commits(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def fetch(url: str, key: str, header: str, timeout: float) -> tuple[int | None, dict[str, Any], str]:
    request = urllib.request.Request(url, headers={header: key} if key else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode(errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {}
            return response.status, payload if isinstance(payload, dict) else {}, raw[-4000:]
    except (OSError, urllib.error.URLError) as exc:
        return None, {}, str(exc)


def fetch_existing_cursor(
    base_url: str,
    session: str,
    key: str,
    header: str,
    timeout: float,
    uri_template: str,
) -> tuple[int | None, dict[str, Any], str]:
    """Read the durable cursor through EchoMem's existing /fs/read API."""
    uri = uri_template.format(session=session)
    url = f"{base_url.rstrip('/')}/fs/read?uri={quote(uri, safe=':/')}"
    code, payload, raw = fetch(url, key, header, timeout)
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    text = result.get("text")
    if isinstance(text, str):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return code, {}, raw
        return code, decoded if isinstance(decoded, dict) else {}, raw
    return code, payload, raw


def values_from_payload(payload: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    """Extract message, archive and operation identities without assuming one schema."""
    message_ids: set[str] = set()
    archive_ids: set[str] = set()
    operation_ids: set[str] = set()

    def visit(value: Any, list_context: str = "") -> None:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and list_context in {
                    "messages",
                    "items",
                    "message_ids",
                    "committed_message_ids",
                    "source_turn_ids",
                }:
                    message = item.get("message_id") or item.get("messageId") or item.get("id")
                    if message:
                        message_ids.add(str(message))
                    archive = item.get("archive_id") or item.get("archiveId")
                    operation = item.get("operation_id") or item.get("operationId")
                    if archive:
                        archive_ids.add(str(archive))
                    if operation:
                        operation_ids.add(str(operation))
                elif not isinstance(item, (dict, list)) and list_context in {
                    "message_ids",
                    "messages",
                    "items",
                    "committed_message_ids",
                    "source_turn_ids",
                }:
                    if item not in (None, ""):
                        message_ids.add(str(item))
                visit(item, list_context)
            return
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            normalized = str(key)
            if normalized in {"archive_id", "archiveId"} and item not in (None, ""):
                archive_ids.add(str(item))
            elif normalized in {"operation_id", "operationId"} and item not in (None, ""):
                operation_ids.add(str(item))
            if normalized in {
                "message_ids",
                "messages",
                "items",
                "committed_message_ids",
                "source_turn_ids",
            }:
                visit(item, normalized)
            else:
                visit(item, "")

    visit(payload)
    return message_ids, archive_ids, operation_ids


def run(ctx: Ctx) -> None:
    params = ctx.params
    commit_csv = params.get("commit_csv")
    if not commit_csv:
        ctx.check(
            "reconcile",
            status=INCONCLUSIVE,
            reason="commit_csv is required; no Commit evidence to reconcile",
        )
        return
    auth_key = str(params.get("auth_key") or "")
    if not auth_key:
        auth_key = os.getenv(str(params.get("auth_key_env") or "ECHOMEM_AUTH_KEY"), "")
    auth_header = str(params.get("auth_header") or "X-Auth-Key")
    timeout_s = float(params.get("timeout_s", 10))
    cursor_url_template = str(params.get("cursor_url_template") or "")
    cursor_uri_template = str(
        params.get("cursor_uri_template")
        or "echo://sessions/{session}/current/commit_cursor.json"
    )
    base_url = ctx.base_url

    commits = [
        row for row in read_commits(Path(commit_csv))
        if row.get("status", "").lower() in {"completed", "complete", "transcommit", "succeeded", "success"}
    ]
    if not commits:
        ctx.check(
            "reconcile",
            status=INCONCLUSIVE,
            reason="no completed Commit evidence to reconcile",
        )
        return
    if not cursor_url_template and not base_url:
        ctx.check(
            "reconcile",
            status=INCONCLUSIVE,
            reason="no cursor endpoint or EchoMem base URL configured",
            detail=json.dumps({"accepted_commits": len(commits)}, ensure_ascii=False),
        )
        return

    for row in commits:
        session = row.get("session_id", "")
        if cursor_url_template:
            url = cursor_url_template.format(
                session=session, archive=row.get("archive_id", "")
            )
            code, payload, raw = fetch(url, auth_key, auth_header, timeout_s)
            source_payloads = {"cursor_endpoint": payload}
        else:
            code, payload, raw = fetch_existing_cursor(
                base_url,
                session,
                auth_key,
                auth_header,
                timeout_s,
                cursor_uri_template,
            )
            source_payloads = {"commit_cursor": payload}

        # EchoMem's stable public read APIs are the primary reconciliation
        # evidence. The cursor is an additional durable index, not the only
        # way to prove that a committed message survived.
        if base_url:
            archive_id = row.get("archive_id", "")
            for source, path in (
                ("history", f"/api/sessions/{session}/history?limit=200"),
                ("archives", f"/api/sessions/{session}/archives?limit=200"),
                ("archive", f"/api/sessions/{session}/archives/{archive_id}" if archive_id else ""),
                ("commit_status", f"/api/sessions/{session}/commits/{archive_id}" if archive_id else ""),
                ("commit_memories", f"/api/sessions/{session}/commits/{archive_id}/memories" if archive_id else ""),
            ):
                if not path:
                    continue
                source_code, source_payload, source_raw = fetch(
                    base_url.rstrip("/") + path,
                    auth_key,
                    auth_header,
                    timeout_s,
                )
                source_payloads[source] = {
                    "_http_status": source_code,
                    "_raw": source_raw,
                    **source_payload,
                }

        actual_sets: dict[str, set[str]] = {}
        archives: set[str] = set()
        operations: set[str] = set()
        source_statuses: dict[str, int | None] = {}
        for source, source_payload in source_payloads.items():
            actual_sets[source], source_archives, source_operations = values_from_payload(source_payload)
            archives.update(source_archives)
            operations.update(source_operations)
            source_statuses[source] = source_payload.get("_http_status")
        actual = set().union(*actual_sets.values()) if actual_sets else set()
        if not actual and not archives and not operations:
            ctx.check(
                session or "reconcile",
                status=NOT_IMPLEMENTED if code == 404 else INCONCLUSIVE,
                reason=(
                    "cursor endpoint returned HTTP 404"
                    if code == 404
                    else "read-only responses were reachable but contained no externally parseable identities"
                ),
                detail=json.dumps(
                    {"http_status": code, "raw": raw, "source_statuses": source_statuses},
                    ensure_ascii=False,
                )[:500],
            )
            continue
        raw_expected = row.get("message_ids", "")
        try:
            parsed_expected = json.loads(raw_expected) if raw_expected else []
        except json.JSONDecodeError:
            parsed_expected = [item.strip(" '\"") for item in raw_expected.strip("[]").split(",") if item.strip()]
        expected = {str(item) for item in parsed_expected}
        expected_archive = str(row.get("archive_id") or "")
        expected_operation = str(row.get("operation_id") or "")
        missing = sorted(expected - actual)
        # /history is cumulative by contract, so older committed messages are
        # expected there. A single archive/cursor is scoped to this Commit and
        # can be checked for exact membership.
        scoped_ids = set().union(
            actual_sets.get("archive", set()),
            actual_sets.get("commit_cursor", set()),
            actual_sets.get("cursor_endpoint", set()),
        )
        unexpected = sorted(scoped_ids - expected) if scoped_ids else []
        duplicate_count = max(0, len(parsed_expected) - len(expected))
        archive_ok = not expected_archive or expected_archive in archives
        operation_ok = not expected_operation or expected_operation in operations
        status = PASS if not missing and not unexpected and archive_ok and operation_ok else FAIL
        if status == PASS:
            reason = f"session {session}: commit reconciled"
        else:
            mismatches = []
            if missing:
                mismatches.append(f"{len(missing)} expected message(s) missing")
            if unexpected:
                mismatches.append(f"{len(unexpected)} unexpected identity(ies)")
            if not archive_ok:
                mismatches.append("archive identity mismatch")
            if not operation_ok:
                mismatches.append("operation identity mismatch")
            reason = f"session {session}: " + "; ".join(mismatches)
        ctx.check(
            session or "reconcile",
            status=status,
            reason=reason,
            detail=json.dumps(
                {
                    "http_status": code,
                    "expected": len(expected),
                    "actual": len(actual),
                    "missing": missing,
                    "unexpected": unexpected,
                    "duplicate": duplicate_count,
                    "archive_match": archive_ok,
                    "operation_match": operation_ok,
                    "source_statuses": source_statuses,
                },
                ensure_ascii=False,
            )[:500],
        )

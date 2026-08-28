#!/usr/bin/env python3
"""Reconcile accepted Commit operations with a real cursor/message-set API."""

from __future__ import annotations

import argparse
import csv
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
PASS = "PASS"
FAIL = "FAIL"


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


def reconcile(args: argparse.Namespace) -> dict[str, Any]:
    commits = [
        row for row in read_commits(args.commit_csv)
        if row.get("status", "").lower() in {"completed", "complete", "transcommit", "succeeded", "success"}
    ]
    if not args.cursor_url_template:
        return {"status": NOT_IMPLEMENTED, "reason": "cursor URL template is not configured", "accepted_commits": len(commits)}
    if not commits:
        return {"status": NOT_IMPLEMENTED, "reason": "no completed Commit evidence to reconcile"}
    checks = []
    for row in commits:
        session = row.get("session_id", "")
        url = args.cursor_url_template.format(session=session, archive=row.get("archive_id", ""))
        code, payload, raw = fetch(url, args.auth_key, args.auth_header, args.timeout_s)
        messages = payload.get("message_ids") or payload.get("messages") or payload.get("items")
        if not isinstance(messages, list):
            checks.append({"session_id": session, "status": NOT_IMPLEMENTED, "http_status": code, "raw": raw})
            continue
        raw_expected = row.get("message_ids", "")
        try:
            parsed_expected = json.loads(raw_expected) if raw_expected else []
        except json.JSONDecodeError:
            parsed_expected = [item.strip(" '\"") for item in raw_expected.strip("[]").split(",") if item.strip()]
        expected = {str(item) for item in parsed_expected}
        actual = {str(item.get("message_id") if isinstance(item, dict) else item) for item in messages}
        checks.append({
            "session_id": session,
            "archive_id": row.get("archive_id", ""),
            "http_status": code,
            "expected_message_count": len(expected),
            "actual_message_count": len(actual),
            "missing": sorted(expected - actual),
            "unexpected": sorted(actual - expected),
            "status": PASS if expected <= actual else FAIL,
        })
    if any(item["status"] == FAIL for item in checks):
        status = FAIL
    elif any(item["status"] == NOT_IMPLEMENTED for item in checks):
        status = NOT_IMPLEMENTED
    else:
        status = PASS
    return {"status": status, "accepted_commits": len(commits), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit-csv", required=True, type=Path)
    parser.add_argument("--cursor-url-template", default="")
    parser.add_argument("--auth-key", default="")
    parser.add_argument("--auth-header", default="X-API-Key")
    parser.add_argument("--timeout-s", type=float, default=10)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = reconcile(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())

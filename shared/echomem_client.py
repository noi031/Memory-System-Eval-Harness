"""EchoMem HTTP client with commit polling, search, and built-in logging."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("echomem_client")


@dataclass
class CommitResult:
    """Result of a session commit + poll cycle."""

    session_id: str
    archive_id: str
    status: str  # completed | failed | timeout
    elapsed_s: float
    polls: int
    error: str = ""


@dataclass
class SearchResult:
    """A single search hit from EchoMem."""

    uri: str
    score: float
    content: str = ""
    memory_type: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SearchResult":
        return cls(
            uri=d.get("uri", ""),
            score=float(d.get("score", 0.0)),
            content=d.get("content", d.get("text", "")),
            memory_type=d.get("memory_type", d.get("type", "")),
        )


class EchoMemClient:
    """Thin async HTTP client for EchoMem's REST API.

    Handles session open/message/commit/search with retry, logging, and
    commit-status polling.  Uses urllib so there are zero third-party deps.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8010",
        auth_key: str = "",
        account: str = "default",
        user_id: str = "default",
        agent_id: str = "default",
        workspace: str = "",
        timeout_s: float = 60.0,
        max_retries: int = 3,
        retry_backoff_s: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_key = auth_key
        self.account = account
        self.user_id = user_id
        self.agent_id = agent_id
        self.workspace = workspace
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s
        self._log = logging.getLogger("echomem_client")

    # -- low-level HTTP -------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.auth_key:
            h["X-Auth-Key"] = self.auth_key
        return h

    def _post(self, path: str, body: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        return self._do_request(req)

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        return self._do_request(req)

    def _do_request(self, req: urllib.request.Request) -> dict[str, Any]:
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    raw = resp.read().decode("utf-8")
                    if not raw:
                        return {}
                    return json.loads(raw)
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                last_err = e
                self._log.warning(
                    "HTTP %s %s -> %d %s (attempt %d/%d)",
                    req.method, req.full_url, e.code, body, attempt, self.max_retries,
                )
                if e.code >= 500 and attempt < self.max_retries:
                    time.sleep(self.retry_backoff_s * attempt)
                else:
                    raise
            except Exception as e:
                last_err = e
                self._log.warning(
                    "Request %s failed: %s (attempt %d/%d)",
                    req.full_url, e, attempt, self.max_retries,
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_s * attempt)
                else:
                    raise
        raise RuntimeError(f"request failed after {self.max_retries} retries: {last_err}")

    # -- session lifecycle -----------------------------------------------

    def open_session(self, title: str = "") -> str:
        """Create a new session, return its id."""
        body: dict[str, Any] = {
            "agent_id": self.agent_id,
        }
        if title:
            body["title"] = title
        if self.workspace:
            body["workspace"] = self.workspace
        resp = self._post("/api/sessions/open", body)
        sid = resp.get("session_id") or resp.get("id") or ""
        if not sid:
            scope = resp.get("scope", {})
            if isinstance(scope, dict):
                sid = scope.get("session_id") or ""
        if not sid:
            raise RuntimeError(f"open_session returned no id: {resp}")
        self._log.info("opened session %s (%s)", sid, title)
        return sid

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        created_at: str = "",
        role_id: str = "",
    ) -> dict[str, Any]:
        """Append one message to a session."""
        body: dict[str, Any] = {
            "role": role,
            "content": content,
        }
        if created_at:
            body["created_at"] = created_at
        if role_id:
            body["role_id"] = role_id
        return self._post(f"/api/sessions/{session_id}/messages", body)

    def commit_session(self, session_id: str, keep_recent_count: int = 0) -> str:
        """Commit a session, return the archive_id."""
        body: dict[str, Any] = {}
        resp = self._post(f"/api/sessions/{session_id}/commit", body)
        aid = resp.get("archive_id") or resp.get("task_id") or ""
        if not aid:
            result = resp.get("result", {})
            if isinstance(result, dict):
                aid = result.get("archive_id") or result.get("task_id") or ""
        if not aid:
            aid = resp.get("id", "")
        self._log.info("committed session %s -> archive %s", session_id, aid)
        return aid

    def commit_status(self, session_id: str, archive_id: str) -> dict[str, Any]:
        """Poll commit status. Returns the raw status dict."""
        return self._get(f"/api/sessions/{session_id}/commits/{archive_id}")

    def poll_commit(
        self,
        session_id: str,
        archive_id: str,
        timeout_s: float = 600.0,
        poll_interval_s: float = 2.0,
    ) -> CommitResult:
        """Poll commit until completed/failed or timeout."""
        start = time.monotonic()
        polls = 0
        while True:
            polls += 1
            elapsed = time.monotonic() - start
            if timeout_s > 0 and elapsed > timeout_s:
                self._log.warning(
                    "commit poll timeout: session=%s archive=%s (%.1fs, %d polls)",
                    session_id, archive_id, elapsed, polls,
                )
                return CommitResult(session_id, archive_id, "timeout", elapsed, polls)

            try:
                resp = self.commit_status(session_id, archive_id)
            except Exception as e:
                self._log.warning(
                    "commit status poll error (poll %d): %s", polls, e,
                )
                # If the endpoint doesn't exist (404), treat as completed
                # with a warning – some EchoMem versions don't have this API.
                if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                    self._log.warning(
                        "commit status endpoint returned 404 – "
                        "treating as completed (API may not exist in this version)"
                    )
                    return CommitResult(
                        session_id, archive_id, "completed", elapsed, polls,
                        error="404 – endpoint not found, assumed completed",
                    )
                time.sleep(poll_interval_s)
                continue

            # EchoMem may return {"status": "completed"} or {"status": {"status": "completed", ...}}
            raw_status = resp.get("status")
            if isinstance(raw_status, dict):
                status = (
                    raw_status.get("status")
                    or raw_status.get("stage")
                    or raw_status.get("state")
                    or ""
                ).lower()
            else:
                status = (
                    raw_status
                    or resp.get("stage")
                    or resp.get("state")
                    or ""
                ).lower()

            if status in ("completed", "done", "success"):
                self._log.info(
                    "commit completed: session=%s archive=%s (%.1fs, %d polls)",
                    session_id, archive_id, elapsed, polls,
                )
                return CommitResult(session_id, archive_id, "completed", elapsed, polls)

            if status in ("failed", "error"):
                self._log.error(
                    "commit failed: session=%s archive=%s status=%s",
                    session_id, archive_id, status,
                )
                error_msg = status
                if isinstance(raw_status, dict):
                    error_msg = raw_status.get("error", status)
                else:
                    error_msg = resp.get("error", status)
                return CommitResult(
                    session_id, archive_id, "failed", elapsed, polls,
                    error=error_msg,
                )

            time.sleep(poll_interval_s)

    # -- retrieval ------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
        session_id: str = "",
        agent_id: str = "",
    ) -> list[SearchResult]:
        """Search EchoMem for memory items."""
        body: dict[str, Any] = {
            "query": query,
            "agent_id": agent_id or self.agent_id,
            "limit": top_k,
        }
        if session_id:
            body["session_id"] = session_id
        resp = self._post("/api/retrieval/search", body)
        items = resp.get("result", {}).get("items", []) if "result" in resp else resp.get("items", [])
        return [SearchResult.from_dict(item) for item in items]

    # -- utility --------------------------------------------------------

    def close(self) -> None:
        """No persistent connection to close; kept for API symmetry."""
        pass

    def __enter__(self) -> "EchoMemClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

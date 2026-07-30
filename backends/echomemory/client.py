"""EchoMemory HTTP backend client with commit polling and retrieval."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from backends.types import CommitResult, SearchResult

logger = logging.getLogger("echomem_client")


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

    def _post(
        self,
        path: str,
        body: dict | None = None,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        return self._do_request(req, timeout_s=timeout_s)

    def _get(
        self,
        path: str,
        query: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        return self._do_request(req, timeout_s=timeout_s)

    def _do_request(
        self,
        req: urllib.request.Request,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        last_err: Exception | None = None
        request_timeout = self.timeout_s if timeout_s is None else max(0.001, timeout_s)
        deadline = time.monotonic() + request_timeout
        for attempt in range(1, self.max_retries + 1):
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"request deadline exceeded after {request_timeout:g}s")
                with urllib.request.urlopen(req, timeout=min(self.timeout_s, remaining)) as resp:
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
                finally:
                    e.close()
                last_err = e
                self._log.warning(
                    "HTTP %s %s -> %d %s (attempt %d/%d)",
                    req.method, req.full_url, e.code, body, attempt, self.max_retries,
                )
                if e.code >= 500 and attempt < self.max_retries:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            f"request deadline exceeded after {request_timeout:g}s"
                        ) from e
                    time.sleep(min(self.retry_backoff_s * attempt, remaining))
                else:
                    raise
            except Exception as e:
                last_err = e
                self._log.warning(
                    "Request %s failed: %s (attempt %d/%d)",
                    req.full_url, e, attempt, self.max_retries,
                )
                if attempt < self.max_retries:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            f"request deadline exceeded after {request_timeout:g}s"
                        ) from e
                    time.sleep(min(self.retry_backoff_s * attempt, remaining))
                else:
                    raise
        raise RuntimeError(f"request failed after {self.max_retries} retries: {last_err}")

    # -- session lifecycle -----------------------------------------------

    def health(self) -> dict[str, Any]:
        """Verify that the EchoMem HTTP service is reachable."""
        return self._get("/health")

    def provision_isolated_identity(self, label: str) -> dict[str, str]:
        """Create a tenant/user/key and switch this client to that identity."""
        tenant_response = self._post("/api/auth/tenants", {"name": label})
        tenant = tenant_response.get("tenant", {})
        tenant_id = str(tenant.get("tenant_id") or "") if isinstance(tenant, dict) else ""
        if not tenant_id:
            raise RuntimeError(f"tenant provisioning returned no tenant id: {tenant_response}")

        user_response = self._post(f"/api/auth/tenants/{tenant_id}/users", {})
        user = user_response.get("user", {})
        user_id = str(user.get("user_id") or "") if isinstance(user, dict) else ""
        if not user_id:
            raise RuntimeError(f"user provisioning returned no user id: {user_response}")

        key_response = self._post(
            f"/api/auth/tenants/{tenant_id}/users/{user_id}/key",
            {},
        )
        auth_key = str(key_response.get("auth_key") or "")
        if not auth_key:
            raise RuntimeError(f"key provisioning returned no auth key: {key_response}")

        self.auth_key = auth_key
        self.account = tenant_id
        self.user_id = user_id
        return {"tenant_id": tenant_id, "user_id": user_id}

    def delete_current_identity(self) -> None:
        """Delete the tenant selected by the current auth key."""
        response = self._post("/api/auth/account/delete", {})
        if str(response.get("status") or "").lower() != "deleted":
            raise RuntimeError(f"account deletion was not confirmed: {response}")

    def open_session(self, title: str = "") -> str:
        """Create a new session, return its id."""
        body: dict[str, Any] = {
            "agent_id": self.agent_id,
            "metadata": {
                "title": title,
                "account_id": self.account,
                "user_id": self.user_id,
            },
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
        metadata: dict[str, Any] = {}
        if created_at:
            body["created_at"] = created_at
            metadata["created_at"] = created_at
        if role_id:
            body["role_id"] = role_id
            body["name"] = role_id
            metadata["role_id"] = role_id
        if metadata:
            body["metadata"] = metadata
        return self._post(f"/api/sessions/{session_id}/messages", body)

    def commit_session(self, session_id: str, keep_recent_count: int = 0) -> str:
        """Commit a session, return the archive_id."""
        body: dict[str, Any] = {
            "metadata": {"keep_recent_count": int(keep_recent_count or 0)}
        }
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
        """Poll commit until completed/failed or timeout.

        timeout_s=0 means wait indefinitely.
        """
        if not archive_id:
            self._log.error(
                "poll_commit called with empty archive_id – commit did not return "
                "an archive id, cannot poll status"
            )
            return CommitResult(
                session_id, archive_id, "failed", 0.0, 0,
                error="empty archive_id – commit returned no archive id",
            )

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
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500 and e.code not in (408, 409, 425, 429):
                    self._log.error(
                        "commit status returned terminal HTTP %d: session=%s archive=%s",
                        e.code, session_id, archive_id,
                    )
                    return CommitResult(
                        session_id, archive_id, "failed", elapsed, polls,
                        error=f"HTTP {e.code} while polling commit",
                    )
                # Other HTTP errors: retry
                self._log.warning(
                    "commit status poll error (poll %d): %s", polls, e,
                )
                time.sleep(poll_interval_s)
                continue
            except Exception as e:
                # Network errors, connection refused, etc.: retry
                self._log.warning(
                    "commit status poll error (poll %d): %s", polls, e,
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
        timeout_s: float | None = None,
    ) -> list[SearchResult]:
        """Search EchoMem for memory items."""
        body: dict[str, Any] = {
            "query": query,
            "agent_id": agent_id or self.agent_id,
            "limit": top_k,
            "include_explain": False,
            "include_debug": True,
        }
        if session_id:
            body["session_id"] = session_id
        resp = self._post("/api/retrieval/search", body, timeout_s=timeout_s)
        items = resp.get("result", {}).get("items", []) if "result" in resp else resp.get("items", [])
        return [SearchResult.from_dict(item) for item in items]

    def fs_read(self, uri: str, *, timeout_s: float | None = None) -> str:
        """Read a public EchoMem filesystem URI."""
        response = self._get("/fs/read", {"uri": uri}, timeout_s=timeout_s)
        result = response.get("result") or {}
        return str(
            response.get("content")
            or response.get("text")
            or (result.get("content") if isinstance(result, dict) else "")
            or (result.get("text") if isinstance(result, dict) else "")
            or ""
        )

    def fs_list(
        self,
        uri: str,
        *,
        recursive: bool = False,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """List public EchoMemory filesystem entries."""
        if recursive:
            return self.fs_glob(
                uri.rstrip("/") + "/**",
                timeout_s=timeout_s,
            )
        response = self._get(
            "/fs/ls",
            {"uri": uri},
            timeout_s=timeout_s,
        )
        result = response.get("result")
        entries = (
            result.get("entries")
            if isinstance(result, dict)
            else response.get("entries")
        )
        return [
            dict(item)
            for item in (entries or [])
            if isinstance(item, dict)
        ]

    def fs_glob(
        self,
        pattern: str,
        *,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """Find public EchoMemory filesystem entries by glob pattern."""
        response = self._post(
            "/fs/glob",
            {"pattern": pattern},
            timeout_s=timeout_s,
        )
        result = response.get("result")
        entries = (
            result.get("entries")
            if isinstance(result, dict)
            else response.get("entries")
        )
        return [
            dict(item)
            for item in (entries or [])
            if isinstance(item, dict)
        ]

    # -- utility --------------------------------------------------------

    def close(self) -> None:
        """No persistent connection to close; kept for API symmetry."""
        pass

    def __enter__(self) -> "EchoMemClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

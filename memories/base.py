"""Memory plugin contracts, shared HTTP transport, and validation.

This module defines the plugin protocol that every memory system under test
implements, plus a shared HTTP client base class that concrete clients
(EchoMem, OpenViking, ...) inherit to avoid duplicating urllib plumbing.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


# ------------------------------------------------------------------ #
#  Result types                                                       #
# ------------------------------------------------------------------ #

@dataclass
class CommitResult:
    """Outcome of a session commit + poll cycle."""

    session_id: str
    archive_id: str
    status: str
    elapsed_s: float
    polls: int
    error: str = ""


@dataclass
class SearchResult:
    """Normalized memory item returned by search."""

    uri: str
    score: float
    content: str = ""
    memory_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchResult":
        return cls(
            uri=(
                data.get("uri")
                or data.get("evidence_uri")
                or data.get("source_uri")
                or data.get("path")
                or data.get("id")
                or ""
            ),
            score=float(data.get("score", 0.0)),
            content=(
                data.get("content")
                or data.get("text")
                or data.get("preview")
                or data.get("abstract")
                or data.get("overview")
                or data.get("summary")
                or ""
            ),
            memory_type=(
                data.get("memory_type")
                or data.get("type")
                or data.get("kind")
                or ""
            ),
            metadata=dict(data),
        )

    def to_dict(self) -> dict[str, Any]:
        """Preserve native evidence metadata while exposing normalized fields."""
        return {
            **self.metadata,
            "uri": self.uri,
            "score": self.score,
            "content": self.content,
            "memory_type": self.memory_type,
        }


# ------------------------------------------------------------------ #
#  Plugin metadata                                                    #
# ------------------------------------------------------------------ #

@dataclass(frozen=True)
class MemoryCapability:
    name: str
    description: str


@dataclass(frozen=True)
class MemoryDescriptor:
    id: str
    name: str
    status: str
    description: str
    capabilities: tuple[MemoryCapability, ...] = field(default_factory=tuple)

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["capabilities"] = [asdict(item) for item in self.capabilities]
        return payload


# ------------------------------------------------------------------ #
#  Plugin interface                                                   #
# ------------------------------------------------------------------ #

class MemoryPlugin(ABC):
    """Abstract memory plugin interface.

    The evaluation platform calls only these four methods:

    1. add_arguments(parser) -- declare CLI args (classmethod, before parse)
    2. setup(config) -- create self.client, provision isolated identity
    3. inject_memories(memories, session_id) -- open/add/commit/poll
    4. teardown() -- release resources, delete ephemeral identities

    Identity isolation, client construction, and session lifecycle are
    internal to each plugin. Plugins expose self.client (public) for
    retrieval and complex import operations that benchmark code performs
    directly on the memory backend.
    """

    descriptor: MemoryDescriptor

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Add plugin-specific CLI arguments to *parser*. Default: no-op."""

    @abstractmethod
    def setup(self, config: dict) -> None:
        """Create self.client and provision an isolated identity.

        config is a flat dict of all CLI args. Each plugin reads the
        fields it cares about (connection params, benchmark_name, run_id)
        and ignores the rest. If benchmark_name/run_id are absent, identity
        isolation is skipped (test/validation scenarios).
        """

    @abstractmethod
    def inject_memories(self, memories: list[dict], session_id: str = "") -> str:
        """Inject background memories into the memory system.

        Opens a session (or reuses session_id), adds each memory as a
        user message, commits, and polls until extraction completes.

        Each dict has at least "text"; "time" (if present) is passed as
        created_at. Returns the session_id used.
        """

    def teardown(self) -> None:
        """Release resources. Default: no-op."""


# ------------------------------------------------------------------ #
#  Client protocol                                                     #
# ------------------------------------------------------------------ #

class MemoryClient(Protocol):
    def search(
        self,
        query: str,
        top_k: int = 10,
        session_id: str = "",
        agent_id: str = "",
        timeout_s: float | None = None,
    ) -> list[SearchResult]:
        """Retrieve memory relevant to a query."""

    def fs_read(self, uri: str, *, timeout_s: float | None = None) -> str:
        """Read full content for a memory URI."""

    def fs_list(
        self,
        uri: str,
        *,
        recursive: bool = False,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """List memory filesystem entries."""

    def fs_glob(
        self,
        pattern: str,
        *,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """Find memory filesystem entries by glob pattern."""


# ------------------------------------------------------------------ #
#  Shared HTTP transport                                               #
# ------------------------------------------------------------------ #

class BaseHTTPMemoryClient(ABC):
    """Shared HTTP transport for memory clients using urllib.

    Provides _post, _get, _do_request (with retry and deadline handling),
    and poll_commit (template method).  Subclasses implement _headers()
    and the session/search/fs methods specific to their API.
    """

    def __init__(
        self,
        base_url: str,
        *,
        account: str = "default",
        user_id: str = "default",
        agent_id: str = "default",
        workspace: str = "",
        timeout_s: float = 60.0,
        max_retries: int = 3,
        retry_backoff_s: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.account = account
        self.user_id = user_id
        self.agent_id = agent_id
        self.workspace = workspace
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s
        self._log = logging.getLogger(self.__class__.__name__)

    # -- abstract hooks -------------------------------------------------

    @abstractmethod
    def _headers(self) -> dict[str, str]:
        """Return HTTP headers for requests."""

    # -- low-level HTTP -------------------------------------------------

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

    # -- commit polling (template method) -------------------------------

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
                resp = self._fetch_commit_status(session_id, archive_id)
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
                self._log.warning(
                    "commit status poll error (poll %d): %s", polls, e,
                )
                time.sleep(poll_interval_s)
                continue
            except Exception as e:
                self._log.warning(
                    "commit status poll error (poll %d): %s", polls, e,
                )
                time.sleep(poll_interval_s)
                continue

            status = self._parse_commit_status(resp)

            if status in ("completed", "done", "success"):
                self._log.info(
                    "commit completed: session=%s archive=%s (%.1fs, %d polls)",
                    session_id, archive_id, elapsed, polls,
                )
                return CommitResult(session_id, archive_id, "completed", elapsed, polls)

            if status in self._commit_failed_statuses():
                self._log.error(
                    "commit failed: session=%s archive=%s status=%s",
                    session_id, archive_id, status,
                )
                error_msg = self._extract_commit_error(resp, status)
                return CommitResult(
                    session_id, archive_id, "failed", elapsed, polls,
                    error=error_msg,
                )

            time.sleep(poll_interval_s)

    def _fetch_commit_status(self, session_id: str, archive_id: str) -> dict[str, Any]:
        """Fetch commit status from the backend. Override for different endpoints."""
        raise NotImplementedError

    def _parse_commit_status(self, resp: dict[str, Any]) -> str:
        """Extract a lowercase status string from the poll response."""
        return str(
            resp.get("status")
            or resp.get("stage")
            or resp.get("state")
            or ""
        ).lower()

    def _commit_failed_statuses(self) -> tuple[str, ...]:
        return ("failed", "error")

    def _extract_commit_error(self, resp: dict[str, Any], status: str) -> str:
        return resp.get("error", status)

    # -- utility --------------------------------------------------------

    def close(self) -> None:
        """No persistent connection to close; kept for API symmetry."""
        pass

    def __enter__(self) -> "BaseHTTPMemoryClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# ------------------------------------------------------------------ #
#  Validation                                                          #
# ------------------------------------------------------------------ #

REQUIRED_CAPABILITIES = (
    "session_write",
    "commit_session",
    "relevant_memory",
    "content_read",
)


def validate_memory(plugin: MemoryPlugin) -> dict[str, Any]:
    capabilities = tuple(item.name for item in plugin.descriptor.capabilities)
    missing = tuple(name for name in REQUIRED_CAPABILITIES if name not in capabilities)
    return {
        "memory_id": plugin.descriptor.id,
        "status": "ok" if not missing else "fail",
        "ok": not missing,
        "required_capabilities": REQUIRED_CAPABILITIES,
        "capabilities": capabilities,
        "missing_required_capabilities": missing,
    }

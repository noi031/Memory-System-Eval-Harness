"""Read-only session scoping for reused LoCoMo memory accounts."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


def _session_id(uri: str) -> str:
    marker = "/sessions/"
    if marker not in uri:
        return ""
    return uri.split(marker, 1)[1].split("/", 1)[0]


class SessionPrefixMemoryClient:
    """Hide session-backed memory outside an explicit session-id prefix."""

    def __init__(self, client, session_prefix: str):
        prefix = str(session_prefix or "").strip()
        if not prefix:
            raise ValueError("session prefix must not be empty")
        self._client = client
        self.session_prefix = prefix

    def __getattr__(self, name: str):
        return getattr(self._client, name)

    def _allowed_uri(self, uri: str) -> bool:
        session_id = _session_id(str(uri or ""))
        return not session_id or session_id.startswith(self.session_prefix)

    def search(self, *args, **kwargs):
        return [
            item
            for item in self._client.search(*args, **kwargs)
            if self._allowed_uri(getattr(item, "uri", ""))
        ]

    def fs_read(self, uri: str, **kwargs) -> str:
        if not self._allowed_uri(uri):
            raise ValueError(
                f"session URI is outside configured prefix {self.session_prefix!r}"
            )
        return self._client.fs_read(uri, **kwargs)

    def fs_list(self, *args, **kwargs) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self._client.fs_list(*args, **kwargs)
            if self._allowed_uri(str(entry.get("uri") or ""))
        ]

    def fs_glob(self, *args, **kwargs) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self._client.fs_glob(*args, **kwargs)
            if self._allowed_uri(str(entry.get("uri") or ""))
        ]


class ExcludingMemoryFilesClient:
    """Hide and reject explicitly excluded filesystem leaf names."""

    def __init__(self, client, filenames: list[str] | tuple[str, ...]):
        excluded = {
            str(filename or "").strip()
            for filename in filenames
            if str(filename or "").strip()
        }
        if not excluded:
            raise ValueError("excluded filenames must not be empty")
        self._client = client
        self.excluded_filenames = frozenset(excluded)

    def __getattr__(self, name: str):
        return getattr(self._client, name)

    def _allowed_uri(self, uri: str) -> bool:
        return PurePosixPath(str(uri or "").rstrip("/")).name not in (
            self.excluded_filenames
        )

    def fs_read(self, uri: str, **kwargs) -> str:
        if not self._allowed_uri(uri):
            raise ValueError(
                f"memory file is excluded by access policy: {uri}"
            )
        return self._client.fs_read(uri, **kwargs)

    def fs_list(self, *args, **kwargs) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self._client.fs_list(*args, **kwargs)
            if self._allowed_uri(str(entry.get("uri") or ""))
        ]

    def fs_glob(self, *args, **kwargs) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self._client.fs_glob(*args, **kwargs)
            if self._allowed_uri(str(entry.get("uri") or ""))
        ]

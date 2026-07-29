"""Backend-neutral result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommitResult:
    session_id: str
    archive_id: str
    status: str
    elapsed_s: float
    polls: int
    error: str = ""


@dataclass
class SearchResult:
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

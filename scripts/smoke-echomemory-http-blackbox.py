#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for candidate in (ROOT, SCRIPTS):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

from echomemory_memory_qa import echomemory_retrieve, search_overview_enrichment_hits


class FakeHTTPSDK:
    _compat_layout = "http"

    def __init__(self) -> None:
        self.search_queries: list[str] = []
        self.read_uris: list[str] = []

    def _ctx(self, **kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)

    async def search(
        self,
        query: str,
        *,
        ctx: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
    ) -> Any:
        self.search_queries.append(query)
        items = [
            {
                "source_uri": "graph://native/graph-result",
                "evidence_uri": "echo://engine/echo0_plugin/sessions/session-a/graph/result-1",
                "source": "echo0_plugin",
                "kind": "graph",
                "confidence": 0.01,
                "content": "native graph result",
            },
            {
                "source_uri": "atom://native/atom-result",
                "evidence_uri": "echo://engine/echo0_plugin/sessions/session-a/atoms/result-2",
                "source": "echo0_plugin",
                "kind": "atomic",
                "confidence": 0.99,
                "content": "native atom result",
            },
        ]
        return SimpleNamespace(items=items)

    async def fs_read(self, uri: str, *, ctx: dict[str, Any] | None = None) -> dict[str, str]:
        self.read_uris.append(uri)
        return {"content": "# Overview\nHTTP-only overview content"}


def args_for(*, overview: bool) -> argparse.Namespace:
    return argparse.Namespace(
        account="blackbox-smoke",
        user_id="user",
        agent_id="agent",
        import_session_id="",
        top_k=25,
        evidence_policy="blackbox",
        search_overview_enrichment=overview,
        exclude_session_summaries=False,
    )


async def main() -> None:
    sdk = FakeHTTPSDK()
    args = args_for(overview=False)
    raw_query = "Which exact memory should be returned?"
    hits, error, timing = await echomemory_retrieve(args, sdk, raw_query)

    assert not error, error
    assert sdk.search_queries == [raw_query], sdk.search_queries
    assert [item["uri"] for item in hits] == [
        "graph://native/graph-result",
        "atom://native/atom-result",
    ]
    assert timing["platform_retrieval_postprocess_enabled"] is False
    assert timing["native_http_selected_count"] == 2

    overview_hits, overview_audit = await search_overview_enrichment_hits(args, sdk, hits)
    assert overview_hits == []
    assert sdk.read_uris == []
    assert overview_audit["http_read_count"] == 0

    args.search_overview_enrichment = True
    overview_hits, overview_audit = await search_overview_enrichment_hits(args, sdk, hits)
    expected_uri = "echo://engine/echo0_plugin/sessions/session-a/overview.md"
    assert sdk.read_uris == [expected_uri], sdk.read_uris
    assert overview_audit["candidate_uris"] == [expected_uri]
    assert overview_audit["hit_uris"] == [expected_uri]
    assert len(overview_hits) == 1
    assert overview_hits[0]["backend"] == "echomemory_http_fs_read"

    print("EchoMemory HTTP black-box smoke passed")


if __name__ == "__main__":
    asyncio.run(main())

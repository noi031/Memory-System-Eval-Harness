"""Tests for the openviking_mcp agent plugin: tool schema + _execute_tool."""

from __future__ import annotations

import json
import unittest
from typing import Any

from plugins.openviking_mcp.runtime import MEMORY_TOOLS, _execute_tool
from backends.memory_types import SearchResult


class _MockMemoryClient:
    """Minimal MemoryClient for testing _execute_tool without a server."""

    def __init__(
        self,
        search_results: list[SearchResult] | None = None,
        read_content: str = "",
        list_entries: list[dict[str, Any]] | None = None,
        glob_entries: list[dict[str, Any]] | None = None,
    ):
        self._search_results = search_results or []
        self._read_content = read_content
        self._list_entries = list_entries or []
        self._glob_entries = glob_entries or []

    def search(self, query, top_k=10, session_id="", agent_id="", timeout_s=None):
        return self._search_results

    def fs_read(self, uri, *, timeout_s=None):
        return self._read_content

    def fs_list(self, uri, *, recursive=False, timeout_s=None):
        return self._list_entries

    def fs_glob(self, pattern, *, timeout_s=None):
        return self._glob_entries


class ToolSchemaTests(unittest.TestCase):
    """Verify tool definitions have valid OpenAI function-calling structure."""

    def test_four_tools_defined(self) -> None:
        names = [t["function"]["name"] for t in MEMORY_TOOLS]
        self.assertEqual(
            {"memory_search", "memory_read", "memory_list", "memory_glob"},
            set(names),
        )

    def test_each_tool_has_required_fields(self) -> None:
        for tool in MEMORY_TOOLS:
            self.assertEqual("function", tool["type"])
            func = tool["function"]
            self.assertIn("name", func)
            self.assertIn("description", func)
            self.assertIn("parameters", func)
            params = func["parameters"]
            self.assertEqual("object", params["type"])

    def test_memory_search_requires_query(self) -> None:
        tool = next(t for t in MEMORY_TOOLS if t["function"]["name"] == "memory_search")
        self.assertIn("query", tool["function"]["parameters"]["required"])
        self.assertIn("limit", tool["function"]["parameters"]["properties"])

    def test_memory_read_requires_uris(self) -> None:
        tool = next(t for t in MEMORY_TOOLS if t["function"]["name"] == "memory_read")
        self.assertIn("uris", tool["function"]["parameters"]["required"])


class ExecuteToolTests(unittest.TestCase):
    """Test _execute_tool dispatches to the correct MemoryClient method."""

    def test_memory_search_returns_json(self) -> None:
        client = _MockMemoryClient(
            search_results=[
                SearchResult(
                    uri="viking://user/memories/item1",
                    score=0.95,
                    content="Hello world",
                    memory_type="fact",
                ),
            ]
        )
        result = _execute_tool(
            client, "memory_search", {"query": "hello", "limit": 5}, timeout_s=10
        )
        items = json.loads(result)
        self.assertEqual(1, len(items))
        self.assertEqual("viking://user/memories/item1", items[0]["uri"])
        self.assertEqual(0.95, items[0]["score"])
        self.assertEqual("Hello world", items[0]["preview"])

    def test_memory_search_empty_query(self) -> None:
        client = _MockMemoryClient()
        result = _execute_tool(client, "memory_search", {"query": ""}, timeout_s=10)
        self.assertIn("empty query", result.lower())

    def test_memory_search_no_results(self) -> None:
        client = _MockMemoryClient(search_results=[])
        result = _execute_tool(
            client, "memory_search", {"query": "nothing"}, timeout_s=10
        )
        self.assertEqual("No results found", result)

    def test_memory_read_returns_content(self) -> None:
        client = _MockMemoryClient(read_content="Full content here")
        result = _execute_tool(
            client, "memory_read", {"uris": "viking://user/memories/1"}, timeout_s=10
        )
        data = json.loads(result)
        self.assertEqual("Full content here", data["viking://user/memories/1"])

    def test_memory_read_multiple_uris(self) -> None:
        client = _MockMemoryClient(read_content="content")
        result = _execute_tool(
            client,
            "memory_read",
            {"uris": "viking://a, viking://b"},
            timeout_s=10,
        )
        data = json.loads(result)
        self.assertEqual(2, len(data))

    def test_memory_read_empty_uris(self) -> None:
        client = _MockMemoryClient()
        result = _execute_tool(client, "memory_read", {"uris": ""}, timeout_s=10)
        self.assertIn("No URIs", result)

    def test_memory_list_returns_entries(self) -> None:
        client = _MockMemoryClient(
            list_entries=[{"uri": "viking://a", "name": "a", "is_dir": False}]
        )
        result = _execute_tool(
            client, "memory_list", {"uri": "viking://user/memories/"}, timeout_s=10
        )
        entries = json.loads(result)
        self.assertEqual(1, len(entries))

    def test_memory_list_no_entries(self) -> None:
        client = _MockMemoryClient(list_entries=[])
        result = _execute_tool(
            client, "memory_list", {"uri": "viking://empty/"}, timeout_s=10
        )
        self.assertIn("No entries", result)

    def test_memory_glob_returns_uris(self) -> None:
        client = _MockMemoryClient(
            glob_entries=[
                {"uri": "viking://user/memories/file1.md"},
                {"uri": "viking://user/memories/file2.md"},
            ]
        )
        result = _execute_tool(
            client, "memory_glob", {"pattern": "viking://**/*.md"}, timeout_s=10
        )
        self.assertIn("Found 2 entries", result)
        self.assertIn("file1.md", result)

    def test_unknown_tool(self) -> None:
        client = _MockMemoryClient()
        result = _execute_tool(client, "unknown_tool", {}, timeout_s=10)
        self.assertIn("Unknown tool", result)


if __name__ == "__main__":
    unittest.main()

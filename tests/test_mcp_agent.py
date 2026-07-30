"""Tests for the echomem_mcp agent plugin: McpClient SSE parsing + tool schema."""

from __future__ import annotations

import json
import unittest

from agents.echomem_mcp.mcp_client import McpClient
from agents.echomem_mcp.runtime import MCP_TOOLS


class SseParsingTests(unittest.TestCase):
    """Test SSE parsing logic in McpClient._parse_sse."""

    def test_parses_single_message_event(self) -> None:
        sse = (
            "event: message\r\n"
            'data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"hello"}],"isError":false}}\r\n'
        )
        result = McpClient._parse_sse(sse)
        self.assertIsNotNone(result)
        self.assertEqual(1, result["id"])
        self.assertEqual("hello", result["result"]["content"][0]["text"])

    def test_parses_multiline_sse(self) -> None:
        sse = (
            ": keepalive\r\n"
            "\r\n"
            "event: message\r\n"
            'data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"world"}]}}\r\n'
        )
        result = McpClient._parse_sse(sse)
        self.assertIsNotNone(result)
        self.assertEqual("world", result["result"]["content"][0]["text"])

    def test_returns_none_for_no_data(self) -> None:
        result = McpClient._parse_sse("event: ping\r\n\r\n")
        self.assertIsNone(result)

    def test_returns_none_for_empty(self) -> None:
        result = McpClient._parse_sse("")
        self.assertIsNone(result)


class ToolSchemaTests(unittest.TestCase):
    """Verify MCP tool definitions have valid OpenAI function-calling structure."""

    def test_four_tools_defined(self) -> None:
        names = [t["function"]["name"] for t in MCP_TOOLS]
        self.assertEqual(
            {"memory_query", "read", "list", "glob"},
            set(names),
        )

    def test_each_tool_has_required_fields(self) -> None:
        for tool in MCP_TOOLS:
            self.assertEqual("function", tool["type"])
            func = tool["function"]
            self.assertIn("name", func)
            self.assertIn("description", func)
            self.assertIn("parameters", func)
            params = func["parameters"]
            self.assertEqual("object", params["type"])

    def test_memory_query_requires_query(self) -> None:
        tool = next(t for t in MCP_TOOLS if t["function"]["name"] == "memory_query")
        self.assertIn("query", tool["function"]["parameters"]["required"])
        self.assertIn("limit", tool["function"]["parameters"]["properties"])

    def test_read_requires_uris(self) -> None:
        tool = next(t for t in MCP_TOOLS if t["function"]["name"] == "read")
        self.assertIn("uris", tool["function"]["parameters"]["required"])


class McpClientConstructionTests(unittest.TestCase):
    """Test McpClient initialization without network calls."""

    def test_strips_trailing_slash(self) -> None:
        client = McpClient("http://127.0.0.1:8001/", auth_key="key123")
        self.assertEqual("http://127.0.0.1:8001", client.base_url)
        self.assertEqual("key123", client.auth_key)

    def test_no_session_before_initialize(self) -> None:
        client = McpClient("http://127.0.0.1:8001")
        self.assertIsNone(client._session_id)

    def test_call_tool_requires_session(self) -> None:
        client = McpClient("http://127.0.0.1:8001")
        with self.assertRaises(RuntimeError):
            client.call_tool("memory_query", {"query": "test"})


if __name__ == "__main__":
    unittest.main()

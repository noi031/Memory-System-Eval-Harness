"""Minimal MCP Streamable HTTP client (stdlib only).

Connects to an EchoMem MCP server (FastMCP + Streamable HTTP transport).
The protocol requires an ``initialize`` handshake to obtain a session ID,
then ``tools/call`` requests can be issued.  Responses are SSE streams;
this client parses the first ``event: message`` data payload.

No third-party dependencies -- uses ``urllib`` for HTTP and manual SSE
line parsing.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("eval.echomem_mcp.mcp_client")

_PROTOCOL_VERSION = "2025-06-18"


class McpClient:
    """MCP client over Streamable HTTP.

    Usage::

        client = McpClient("http://127.0.0.1:8001", auth_key="...")
        client.initialize()
        result_text = client.call_tool("memory_query", {"query": "hello", "limit": 5})
        client.close()
    """

    def __init__(self, base_url: str, auth_key: str = "", timeout_s: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.auth_key = auth_key
        self.timeout_s = timeout_s
        self._session_id: str | None = None
        self._req_id = 0

    # -- public API -----------------------------------------------------

    def initialize(self, timeout_s: float | None = None) -> None:
        """Send ``initialize`` request and capture the session ID."""
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "eval-harness", "version": "1.0"},
            },
        }
        result, headers = self._post(body, include_session=False, timeout_s=timeout_s)
        # FastMCP returns the session ID in the SSE response headers
        self._session_id = (
            headers.get("Mcp-Session-Id")
            or headers.get("mcp-session-id")
        )
        if not self._session_id:
            raise RuntimeError(
                f"MCP initialize did not return a session ID; headers={dict(headers)}"
            )
        logger.debug("MCP session initialized: %s", self._session_id)

        # Send the initialized notification (good MCP practice)
        notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        self._post(notif, is_notification=True, timeout_s=timeout_s)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        timeout_s: float | None = None,
    ) -> str:
        """Call an MCP tool by name and return the text result.

        Returns the ``content[0].text`` field from the tool response.
        Raises ``RuntimeError`` if the tool call fails or the server
        returns an error.
        """
        if not self._session_id:
            raise RuntimeError("MCP session not initialized; call initialize() first")

        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        result, _ = self._post(body, include_session=True, timeout_s=timeout_s)
        if result is None:
            return ""

        if result.get("isError"):
            content = result.get("content", [])
            err_text = content[0].get("text", "") if content else str(result)
            raise RuntimeError(f"MCP tool '{name}' returned error: {err_text}")

        content = result.get("content", [])
        if content and isinstance(content, list):
            return content[0].get("text", "")
        return ""

    def close(self) -> None:
        """No persistent connection to close; session expires server-side."""
        pass

    # -- internals ------------------------------------------------------

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _build_headers(self, include_session: bool, is_notification: bool = False) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.auth_key:
            headers["X-Auth-Key"] = self.auth_key
        if include_session and self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
            headers["mcp-protocol-version"] = _PROTOCOL_VERSION
        return headers

    def _post(
        self,
        body: dict[str, Any],
        *,
        include_session: bool = True,
        is_notification: bool = False,
        timeout_s: float | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """POST a JSON-RPC request and parse the SSE response.

        Returns ``(parsed_result, response_headers)``.  For notifications,
        ``parsed_result`` is ``None`` (server returns 202 with no body).
        """
        url = f"{self.base_url}/mcp"
        data = json.dumps(body).encode("utf-8")
        headers = self._build_headers(include_session, is_notification)
        request_timeout = self.timeout_s if timeout_s is None else max(0.001, timeout_s)

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=request_timeout) as resp:
                resp_headers = {k: v for k, v in resp.headers.items()}
                raw = resp.read()
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            raise RuntimeError(f"MCP HTTP {e.code}: {body_text}") from e

        if is_notification:
            # Notifications get HTTP 202 with no SSE body
            return None, resp_headers

        # Parse SSE: find the first "data:" line and JSON-decode it
        result = self._parse_sse(raw.decode("utf-8", errors="replace"))
        if result is None:
            raise RuntimeError(f"MCP response had no SSE data: {raw[:500]!r}")

        # The SSE data is a JSON-RPC response; extract the "result" or "error"
        if "error" in result:
            err = result["error"]
            raise RuntimeError(f"MCP JSON-RPC error: {err}")

        return result.get("result"), resp_headers

    @staticmethod
    def _parse_sse(text: str) -> dict[str, Any] | None:
        """Parse an SSE stream and return the first ``data:`` payload as JSON."""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    try:
                        return json.loads(payload)
                    except json.JSONDecodeError:
                        logger.warning("MCP SSE data is not valid JSON: %s", payload[:200])
                        continue
        return None

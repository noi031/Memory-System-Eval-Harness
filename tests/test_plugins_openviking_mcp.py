"""Tests for plugins.openviking_mcp.plugin and runtime gaps.

Covers OpenVikingMCPPlugin (plugin.py) comprehensively and supplements
test_openviking_mcp_agent.py for runtime.py gaps: _SYSTEM_PROMPT, MEMORY_TOOLS
structural details, _execute_tool edge cases, and concurrency safety (no
agent_id in search calls).
"""

from __future__ import annotations

import argparse
import inspect
import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from plugins.openviking_mcp.plugin import OpenVikingMCPPlugin
from plugins.openviking_mcp.runtime import MEMORY_TOOLS, _SYSTEM_PROMPT, _execute_tool
from backends.memory_types import CommitResult, SearchResult
from shared.llm_client import LLMResponse, LLMToolResponse


# ======================================================================
#  Test helpers
# ======================================================================

class _FakeLLM:
    """Fake LLMClient with configurable response sequences."""

    def __init__(
        self,
        tool_responses: list[LLMToolResponse] | None = None,
        chat_response: LLMResponse | None = None,
    ):
        self._tool_responses = list(tool_responses or [])
        self._chat_response = chat_response
        self.tool_calls: list[tuple] = []
        self.chat_calls: list[tuple] = []

    def chat_with_tools(
        self, messages, tools, *, timeout_s=None, tool_choice="auto",
    ) -> LLMToolResponse:
        self.tool_calls.append((messages, tools, timeout_s))
        if self._tool_responses:
            return self._tool_responses.pop(0)
        return LLMToolResponse(content="", error="exhausted")

    def chat(self, messages, *, timeout_s=None) -> LLMResponse:
        self.chat_calls.append((messages, timeout_s))
        if self._chat_response is not None:
            return self._chat_response
        return LLMResponse(
            content="", prompt_tokens=0, completion_tokens=0, elapsed_s=0.0,
        )


def _make_plugin(**overrides) -> OpenVikingMCPPlugin:
    """Build a plugin with pre-set internal state, bypassing setup()."""
    p = OpenVikingMCPPlugin()
    p._max_iterations = overrides.pop("max_iterations", 10)
    p._search_limit = overrides.pop("search_limit", 8)
    p._tool_calling = overrides.pop("tool_calling", True)
    p._search_in_tools = overrides.pop("search_in_tools", True)
    p._manual_search = overrides.pop("manual_search", True)
    p._top_k = overrides.pop("top_k", 25)
    p._memory_budget_chars = overrides.pop("memory_budget_chars", 0)
    p._question_timeout_s = overrides.pop("question_timeout_s", 0.0)
    p._commit_timeout_s = overrides.pop("commit_timeout_s", 0.0)
    p._commit_poll_interval_s = overrides.pop("commit_poll_interval_s", 2.0)
    p._llm = overrides.pop("llm", _FakeLLM())
    p.memory_client = overrides.pop("memory_client", MagicMock())
    return p


def _setup_config(**overrides) -> dict[str, Any]:
    """Minimal config dict for setup() tests."""
    cfg: dict[str, Any] = {
        "llm_base_url": "http://llm:8080",
        "llm_api_key": "k",
        "llm_model": "m",
        "llm_temperature": 0.5,
        "llm_max_tokens": 1024,
        "llm_timeout_s": 60.0,
        "llm_retries": 2,
        "echomem_url": "http://ov:19080",
        "echomem_auth_key": "ovk",
        "account": "acc",
        "user_id": "uid",
        "agent_id": "aid",
        "workspace": "/ws",
        "timeout_s": 30.0,
        "max_retries": 2,
        "ov_max_iterations": 5,
        "ov_search_limit": 4,
        "tool_calling": True,
        "search_in_tools": True,
        "manual_search": True,
        "top_k": 10,
        "memory_budget_chars": 1000,
        "question_timeout_s": 60.0,
        "commit_timeout_s": 30.0,
        "commit_poll_interval_s": 1.0,
    }
    cfg.update(overrides)
    return cfg


# ======================================================================
#  runtime.py — _SYSTEM_PROMPT
# ======================================================================

class SystemPromptTests(unittest.TestCase):
    """Verify _SYSTEM_PROMPT content (not covered by existing tests)."""

    def test_is_non_empty_string(self) -> None:
        self.assertIsInstance(_SYSTEM_PROMPT, str)
        self.assertTrue(_SYSTEM_PROMPT.strip())

    def test_mentions_memory_tools(self) -> None:
        self.assertIn("memory_search", _SYSTEM_PROMPT)
        self.assertIn("memory_read", _SYSTEM_PROMPT)


# ======================================================================
#  runtime.py — MEMORY_TOOLS structural gaps
# ======================================================================

class MemoryToolsSupplementTests(unittest.TestCase):
    """Verify tool definition details not covered by existing tests."""

    def test_memory_list_requires_uri(self) -> None:
        tool = next(t for t in MEMORY_TOOLS if t["function"]["name"] == "memory_list")
        self.assertIn("uri", tool["function"]["parameters"]["required"])
        self.assertIn("recursive", tool["function"]["parameters"]["properties"])

    def test_memory_glob_requires_pattern(self) -> None:
        tool = next(t for t in MEMORY_TOOLS if t["function"]["name"] == "memory_glob")
        self.assertIn("pattern", tool["function"]["parameters"]["required"])

    def test_each_description_non_empty(self) -> None:
        for tool in MEMORY_TOOLS:
            with self.subTest(tool=tool["function"]["name"]):
                self.assertTrue(tool["function"]["description"].strip())

    def test_each_tool_has_required_list(self) -> None:
        for tool in MEMORY_TOOLS:
            with self.subTest(tool=tool["function"]["name"]):
                self.assertIsInstance(
                    tool["function"]["parameters"]["required"], list,
                )


# ======================================================================
#  runtime.py — _execute_tool edge cases (supplement existing tests)
# ======================================================================

class ExecuteToolSupplementTests(unittest.TestCase):
    """Test _execute_tool paths not covered by test_openviking_mcp_agent.py."""

    # -- memory_search --------------------------------------------------

    def test_memory_search_includes_type_field(self) -> None:
        client = MagicMock()
        client.search.return_value = [
            SearchResult(uri="u1", score=0.9, content="c", memory_type="episode"),
        ]
        result = _execute_tool(client, "memory_search", {"query": "q"}, timeout_s=10)
        items = json.loads(result)
        self.assertEqual("episode", items[0]["type"])

    def test_memory_search_uses_search_limit_default(self) -> None:
        client = MagicMock()
        client.search.return_value = []
        _execute_tool(
            client, "memory_search", {"query": "q"}, timeout_s=10, search_limit=3,
        )
        client.search.assert_called_once_with("q", top_k=3, timeout_s=10)

    def test_memory_search_limit_arg_overrides_search_limit(self) -> None:
        client = MagicMock()
        client.search.return_value = []
        _execute_tool(
            client, "memory_search", {"query": "q", "limit": 20},
            timeout_s=5, search_limit=3,
        )
        client.search.assert_called_once_with("q", top_k=20, timeout_s=5)

    def test_memory_search_rounds_score_to_4_decimals(self) -> None:
        client = MagicMock()
        client.search.return_value = [
            SearchResult(uri="u", score=0.123456789, content="c", memory_type="f"),
        ]
        result = _execute_tool(client, "memory_search", {"query": "q"}, timeout_s=10)
        self.assertEqual(0.1235, json.loads(result)[0]["score"])

    def test_memory_search_preview_truncated_to_500(self) -> None:
        client = MagicMock()
        client.search.return_value = [
            SearchResult(uri="u", score=0.5, content="x" * 600, memory_type="f"),
        ]
        result = _execute_tool(client, "memory_search", {"query": "q"}, timeout_s=10)
        self.assertEqual(500, len(json.loads(result)[0]["preview"]))

    def test_memory_search_no_agent_id_passed(self) -> None:
        """Concurrency safety: _execute_tool must not pass agent_id to search."""
        client = MagicMock()
        client.search.return_value = []
        _execute_tool(client, "memory_search", {"query": "q"}, timeout_s=10)
        _, kwargs = client.search.call_args
        self.assertNotIn("agent_id", kwargs)
        self.assertNotIn("session_id", kwargs)

    # -- memory_read ----------------------------------------------------

    def test_memory_read_empty_content_returns_error_message(self) -> None:
        client = MagicMock()
        client.fs_read.return_value = ""
        result = _execute_tool(
            client, "memory_read", {"uris": "viking://a"}, timeout_s=10,
        )
        data = json.loads(result)
        self.assertIn("ERROR", data["viking://a"])

    def test_timeout_passed_to_fs_read(self) -> None:
        client = MagicMock()
        client.fs_read.return_value = "content"
        _execute_tool(client, "memory_read", {"uris": "viking://a"}, timeout_s=42)
        client.fs_read.assert_called_once_with("viking://a", timeout_s=42)

    # -- memory_list ----------------------------------------------------

    def test_memory_list_empty_uri(self) -> None:
        client = MagicMock()
        result = _execute_tool(client, "memory_list", {"uri": ""}, timeout_s=10)
        self.assertIn("No URI", result)

    def test_memory_list_passes_recursive_true(self) -> None:
        client = MagicMock()
        client.fs_list.return_value = [{"uri": "viking://a"}]
        _execute_tool(
            client, "memory_list",
            {"uri": "viking://root/", "recursive": True},
            timeout_s=10,
        )
        client.fs_list.assert_called_once_with(
            "viking://root/", recursive=True, timeout_s=10,
        )

    def test_memory_list_default_recursive_false(self) -> None:
        client = MagicMock()
        client.fs_list.return_value = []
        _execute_tool(
            client, "memory_list", {"uri": "viking://root/"}, timeout_s=10,
        )
        client.fs_list.assert_called_once_with(
            "viking://root/", recursive=False, timeout_s=10,
        )

    # -- memory_glob ----------------------------------------------------

    def test_memory_glob_empty_pattern(self) -> None:
        client = MagicMock()
        result = _execute_tool(client, "memory_glob", {"pattern": ""}, timeout_s=10)
        self.assertIn("No pattern", result)

    def test_memory_glob_no_matches(self) -> None:
        client = MagicMock()
        client.fs_glob.return_value = []
        result = _execute_tool(
            client, "memory_glob", {"pattern": "viking://*.xyz"}, timeout_s=10,
        )
        self.assertIn("No entries found for pattern", result)
        self.assertIn("viking://*.xyz", result)

    def test_memory_glob_filters_entries_without_uri(self) -> None:
        client = MagicMock()
        client.fs_glob.return_value = [
            {"uri": "viking://a.md"},
            {"name": "no-uri-entry"},
            {"uri": ""},
        ]
        result = _execute_tool(
            client, "memory_glob", {"pattern": "viking://*.md"}, timeout_s=10,
        )
        self.assertIn("Found 1 entries", result)
        self.assertIn("viking://a.md", result)

    def test_timeout_passed_to_fs_glob(self) -> None:
        client = MagicMock()
        client.fs_glob.return_value = []
        _execute_tool(
            client, "memory_glob", {"pattern": "viking://*"}, timeout_s=42,
        )
        client.fs_glob.assert_called_once_with("viking://*", timeout_s=42)


# ======================================================================
#  plugin.py — descriptor
# ======================================================================

class PluginDescriptorTests(unittest.TestCase):

    def test_descriptor_id(self) -> None:
        self.assertEqual("openviking_mcp", OpenVikingMCPPlugin.descriptor.id)

    def test_descriptor_name_and_description_non_empty(self) -> None:
        self.assertTrue(OpenVikingMCPPlugin.descriptor.name)
        self.assertTrue(OpenVikingMCPPlugin.descriptor.description)


# ======================================================================
#  plugin.py — add_arguments
# ======================================================================

class PluginArgumentsTests(unittest.TestCase):

    def _make_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser()
        OpenVikingMCPPlugin.add_arguments(parser)
        return parser

    def test_calls_shared_helpers_without_backend_choice(self) -> None:
        with (
            patch("plugins.openviking_mcp.plugin.add_memory_backend_args") as mock_mem,
            patch("plugins.openviking_mcp.plugin.add_qa_args") as mock_qa,
            patch("plugins.openviking_mcp.plugin.add_llm_args") as mock_llm,
        ):
            parser = argparse.ArgumentParser()
            OpenVikingMCPPlugin.add_arguments(parser)
            mock_llm.assert_called_once_with(parser)
            mock_qa.assert_called_once_with(parser)
            mock_mem.assert_called_once_with(parser)
            _, kwargs = mock_mem.call_args
            self.assertNotIn("with_backend_choice", kwargs)

    def test_defaults(self) -> None:
        ns = self._make_parser().parse_args([])
        self.assertEqual(10, ns.ov_max_iterations)
        self.assertEqual(8, ns.ov_search_limit)
        self.assertTrue(ns.tool_calling)
        self.assertTrue(ns.search_in_tools)
        self.assertTrue(ns.manual_search)

    def test_boolean_optional_action_negation(self) -> None:
        ns = self._make_parser().parse_args([
            "--no-tool-calling", "--no-search-in-tools", "--no-manual-search",
        ])
        self.assertFalse(ns.tool_calling)
        self.assertFalse(ns.search_in_tools)
        self.assertFalse(ns.manual_search)

    def test_custom_iterations_and_search_limit(self) -> None:
        ns = self._make_parser().parse_args([
            "--ov-max-iterations", "3", "--ov-search-limit", "20",
        ])
        self.assertEqual(3, ns.ov_max_iterations)
        self.assertEqual(20, ns.ov_search_limit)


# ======================================================================
#  plugin.py — setup
# ======================================================================

class PluginSetupTests(unittest.TestCase):

    @patch("plugins.openviking_mcp.plugin.OpenVikingClient")
    @patch("plugins.openviking_mcp.plugin.LLMClient")
    def test_creates_llm_and_openviking_client(
        self, mock_llm_cls, mock_ov_cls,
    ) -> None:
        plugin = OpenVikingMCPPlugin()
        plugin.setup(_setup_config())
        mock_llm_cls.assert_called_once()
        mock_ov_cls.assert_called_once()

    @patch("plugins.openviking_mcp.plugin.OpenVikingClient")
    @patch("plugins.openviking_mcp.plugin.LLMClient")
    def test_openviking_client_receives_config_values(
        self, mock_llm_cls, mock_ov_cls,
    ) -> None:
        plugin = OpenVikingMCPPlugin()
        plugin.setup(_setup_config(
            echomem_url="http://ov:1234",
            echomem_auth_key="secret",
            account="acc1",
            user_id="uid1",
            agent_id="aid1",
            workspace="/data",
            timeout_s=45.0,
            max_retries=5,
        ))
        _, kwargs = mock_ov_cls.call_args
        self.assertEqual("http://ov:1234", kwargs["base_url"])
        self.assertEqual("secret", kwargs["api_key"])
        self.assertEqual("acc1", kwargs["account"])
        self.assertEqual("uid1", kwargs["user_id"])
        self.assertEqual("aid1", kwargs["agent_id"])
        self.assertEqual("/data", kwargs["workspace"])
        self.assertEqual(45.0, kwargs["timeout_s"])
        self.assertEqual(5, kwargs["max_retries"])

    @patch("plugins.openviking_mcp.plugin.OpenVikingClient")
    @patch("plugins.openviking_mcp.plugin.LLMClient")
    def test_stores_config_flags(self, mock_llm_cls, mock_ov_cls) -> None:
        plugin = OpenVikingMCPPlugin()
        plugin.setup(_setup_config(
            ov_max_iterations=7,
            ov_search_limit=3,
            tool_calling=False,
            search_in_tools=False,
            manual_search=False,
            top_k=15,
            memory_budget_chars=500,
            question_timeout_s=90.0,
        ))
        self.assertEqual(7, plugin._max_iterations)
        self.assertEqual(3, plugin._search_limit)
        self.assertFalse(plugin._tool_calling)
        self.assertFalse(plugin._search_in_tools)
        self.assertFalse(plugin._manual_search)
        self.assertEqual(15, plugin._top_k)
        self.assertEqual(500, plugin._memory_budget_chars)
        self.assertEqual(90.0, plugin._question_timeout_s)

    @patch("plugins.openviking_mcp.plugin.OpenVikingClient")
    @patch("plugins.openviking_mcp.plugin.LLMClient")
    def test_provisions_isolated_identity(
        self, mock_llm_cls, mock_ov_cls,
    ) -> None:
        mock_ov = mock_ov_cls.return_value
        plugin = OpenVikingMCPPlugin()
        plugin.setup(_setup_config(benchmark_name="locomo", run_id="r1"))
        mock_ov.provision_isolated_identity.assert_called_once()
        label = mock_ov.provision_isolated_identity.call_args[0][0]
        self.assertIn("locomo", label)
        self.assertIn("r1", label)

    @patch("plugins.openviking_mcp.plugin.OpenVikingClient")
    @patch("plugins.openviking_mcp.plugin.LLMClient")
    def test_skips_identity_on_resume(self, mock_llm_cls, mock_ov_cls) -> None:
        mock_ov = mock_ov_cls.return_value
        plugin = OpenVikingMCPPlugin()
        plugin.setup(_setup_config(
            benchmark_name="locomo", run_id="r1", resume_qa="existing",
        ))
        mock_ov.provision_isolated_identity.assert_not_called()

    @patch("plugins.openviking_mcp.plugin.OpenVikingClient")
    @patch("plugins.openviking_mcp.plugin.LLMClient")
    def test_skips_identity_without_benchmark_or_run_id(
        self, mock_llm_cls, mock_ov_cls,
    ) -> None:
        mock_ov = mock_ov_cls.return_value
        plugin = OpenVikingMCPPlugin()
        plugin.setup(_setup_config())
        mock_ov.provision_isolated_identity.assert_not_called()


# ======================================================================
#  plugin.py — inject_memories
# ======================================================================

class PluginInjectMemoriesTests(unittest.TestCase):

    def _make_client(
        self, commit_status: str = "completed", commit_error: str = "",
    ) -> MagicMock:
        client = MagicMock()
        client.open_session.return_value = "session-1"
        client.commit_session.return_value = "archive-1"
        client.poll_commit.return_value = CommitResult(
            session_id="session-1",
            archive_id="archive-1",
            status=commit_status,
            elapsed_s=1.0,
            polls=1,
            error=commit_error,
        )
        return client

    def test_open_add_messages_commit_poll(self) -> None:
        client = self._make_client()
        plugin = _make_plugin(memory_client=client)
        sid = plugin.inject_memories([
            {"text": "hello", "time": "2024-01-01T00:00:00"},
            {"text": "world", "time": "2024-01-02T00:00:00"},
        ])
        client.open_session.assert_called_once_with(title="inject")
        self.assertEqual(2, client.add_message.call_count)
        client.commit_session.assert_called_once_with("session-1")
        client.poll_commit.assert_called_once()
        self.assertEqual("session-1", sid)

    def test_skips_open_when_session_id_given(self) -> None:
        client = self._make_client()
        plugin = _make_plugin(memory_client=client)
        sid = plugin.inject_memories(
            [{"text": "msg"}], session_id="existing-session",
        )
        client.open_session.assert_not_called()
        self.assertEqual("existing-session", sid)

    def test_skips_empty_text_memories(self) -> None:
        client = self._make_client()
        plugin = _make_plugin(memory_client=client)
        plugin.inject_memories([
            {"text": ""},
            {"text": None},
            {"text": "real"},
        ])
        self.assertEqual(1, client.add_message.call_count)

    def test_default_backend_is_openviking(self) -> None:
        sig = inspect.signature(OpenVikingMCPPlugin.inject_memories)
        self.assertEqual("openviking", sig.parameters["backend"].default)

    def test_raises_runtime_error_on_commit_failure(self) -> None:
        client = self._make_client(commit_status="failed", commit_error="boom")
        plugin = _make_plugin(memory_client=client)
        with self.assertRaises(RuntimeError) as ctx:
            plugin.inject_memories([{"text": "msg"}])
        self.assertIn("failed", str(ctx.exception))

    def test_passes_commit_timeout_and_interval(self) -> None:
        client = self._make_client()
        plugin = _make_plugin(
            memory_client=client,
            commit_timeout_s=42.0,
            commit_poll_interval_s=0.5,
        )
        plugin.inject_memories([{"text": "msg"}])
        _, kwargs = client.poll_commit.call_args
        self.assertEqual(42.0, kwargs["timeout_s"])
        self.assertEqual(0.5, kwargs["poll_interval_s"])


# ======================================================================
#  plugin.py — create_session
# ======================================================================

class PluginCreateSessionTests(unittest.TestCase):

    def test_returns_counted_session_id(self) -> None:
        plugin = _make_plugin()
        self.assertEqual("openviking_mcp_session_1", plugin.create_session())
        self.assertEqual("openviking_mcp_session_2", plugin.create_session())

    def test_increments_across_multiple_calls(self) -> None:
        plugin = _make_plugin()
        for i in range(1, 4):
            self.assertEqual(
                f"openviking_mcp_session_{i}", plugin.create_session(),
            )


# ======================================================================
#  plugin.py — send_message
# ======================================================================

class PluginSendMessageTests(unittest.TestCase):
    """Test the 3-phase send_message flow and its configurations."""

    # -- Phase C: no tool calling --------------------------------------

    def test_no_tool_calling_single_llm_call(self) -> None:
        """tool_calling=False -> single chat() call, no chat_with_tools."""
        llm = _FakeLLM(chat_response=LLMResponse(
            content="answer", prompt_tokens=10, completion_tokens=5, elapsed_s=0.1,
        ))
        plugin = _make_plugin(tool_calling=False, manual_search=False, llm=llm)
        resp = plugin.send_message("s1", "What is 2+2?")
        self.assertEqual("answer", resp.text)
        self.assertEqual(10, resp.prompt_tokens)
        self.assertEqual(5, resp.completion_tokens)
        self.assertIsNone(resp.error)
        self.assertEqual(1, len(llm.chat_calls))
        self.assertEqual(0, len(llm.tool_calls))

    def test_no_tool_calling_llm_error_propagates(self) -> None:
        llm = _FakeLLM(chat_response=LLMResponse(
            content="", prompt_tokens=0, completion_tokens=0,
            elapsed_s=0, error="timeout",
        ))
        plugin = _make_plugin(tool_calling=False, manual_search=False, llm=llm)
        resp = plugin.send_message("s1", "Q")
        self.assertEqual("timeout", resp.error)

    # -- Phase C: tool calling, immediate answer -----------------------

    def test_tool_calling_immediate_answer(self) -> None:
        """LLM returns no tool_calls on first call."""
        llm = _FakeLLM(tool_responses=[
            LLMToolResponse(
                content="direct answer", prompt_tokens=8,
                completion_tokens=3, elapsed_s=0.1,
            ),
        ])
        plugin = _make_plugin(tool_calling=True, manual_search=False, llm=llm)
        resp = plugin.send_message("s1", "Hi")
        self.assertEqual("direct answer", resp.text)
        self.assertEqual(0, resp.extra["tool_call_count"])
        self.assertEqual(1, resp.extra["iterations"])

    def test_tool_calling_llm_error_propagates(self) -> None:
        llm = _FakeLLM(tool_responses=[
            LLMToolResponse(error="LLM unavailable"),
        ])
        plugin = _make_plugin(tool_calling=True, manual_search=False, llm=llm)
        resp = plugin.send_message("s1", "Q")
        self.assertEqual("LLM unavailable", resp.error)

    # -- Phase C: tool calling, one tool call then answer --------------

    def test_tool_calling_one_tool_call_then_answer(self) -> None:
        llm = _FakeLLM(tool_responses=[
            LLMToolResponse(
                content="",
                tool_calls=[{
                    "id": "tc1",
                    "function": {
                        "name": "memory_search",
                        "arguments": '{"query": "info"}',
                    },
                }],
                prompt_tokens=5, completion_tokens=2, elapsed_s=0.1,
            ),
            LLMToolResponse(
                content="final answer",
                prompt_tokens=15, completion_tokens=5, elapsed_s=0.1,
            ),
        ])
        client = MagicMock()
        client.search.return_value = []
        plugin = _make_plugin(
            tool_calling=True, manual_search=False, llm=llm, memory_client=client,
        )
        resp = plugin.send_message("s1", "Tell me about X")
        self.assertEqual("final answer", resp.text)
        self.assertEqual(1, resp.extra["tool_call_count"])
        self.assertEqual(2, resp.extra["iterations"])
        client.search.assert_called_once()

    def test_memory_search_tool_call_recorded_in_retrieval_items(self) -> None:
        llm = _FakeLLM(tool_responses=[
            LLMToolResponse(
                content="",
                tool_calls=[{
                    "id": "tc1",
                    "function": {
                        "name": "memory_search",
                        "arguments": '{"query": "info"}',
                    },
                }],
                prompt_tokens=1, completion_tokens=1, elapsed_s=0,
            ),
            LLMToolResponse(
                content="answer", prompt_tokens=1, completion_tokens=1, elapsed_s=0,
            ),
        ])
        client = MagicMock()
        client.search.return_value = [
            SearchResult(uri="viking://m1", score=0.8, content="data", memory_type="fact"),
        ]
        plugin = _make_plugin(
            tool_calling=True, manual_search=False, llm=llm, memory_client=client,
        )
        resp = plugin.send_message("s1", "Q")
        tool_items = [
            m for m in resp.memory_items
            if isinstance(m, dict) and m.get("tool") == "memory_search"
        ]
        self.assertEqual(1, len(tool_items))
        self.assertEqual("info", tool_items[0]["query"])

    def test_tool_call_invalid_json_arguments(self) -> None:
        """Invalid JSON in tool_call arguments defaults to empty dict."""
        llm = _FakeLLM(tool_responses=[
            LLMToolResponse(
                content="",
                tool_calls=[{
                    "id": "tc1",
                    "function": {
                        "name": "memory_search",
                        "arguments": "not-json{{",
                    },
                }],
                prompt_tokens=1, completion_tokens=1, elapsed_s=0,
            ),
            LLMToolResponse(
                content="answer", prompt_tokens=1, completion_tokens=1, elapsed_s=0,
            ),
        ])
        client = MagicMock()
        client.search.return_value = []
        plugin = _make_plugin(
            tool_calling=True, manual_search=False, llm=llm, memory_client=client,
        )
        resp = plugin.send_message("s1", "Q")
        self.assertEqual("answer", resp.text)
        self.assertEqual(1, resp.extra["tool_call_count"])
        client.search.assert_not_called()

    def test_tool_call_exception_continues_loop(self) -> None:
        """When _execute_tool raises, error text is used and loop continues."""
        llm = _FakeLLM(tool_responses=[
            LLMToolResponse(
                content="",
                tool_calls=[{
                    "id": "tc1",
                    "function": {
                        "name": "memory_read",
                        "arguments": '{"uris": "viking://a"}',
                    },
                }],
                prompt_tokens=1, completion_tokens=1, elapsed_s=0,
            ),
            LLMToolResponse(
                content="answer", prompt_tokens=1, completion_tokens=1, elapsed_s=0,
            ),
        ])
        client = MagicMock()
        client.fs_read.side_effect = RuntimeError("read failed")
        plugin = _make_plugin(
            tool_calling=True, manual_search=False, llm=llm, memory_client=client,
        )
        resp = plugin.send_message("s1", "Q")
        self.assertEqual("answer", resp.text)
        self.assertEqual(0, resp.extra["tool_call_count"])

    # -- Phase C: max iterations ---------------------------------------

    def test_max_iterations_forces_final_answer(self) -> None:
        max_iter = 2
        tool_call = [{
            "id": "tc",
            "function": {
                "name": "memory_read",
                "arguments": '{"uris": "viking://a"}',
            },
        }]
        llm = _FakeLLM(tool_responses=[
            LLMToolResponse(
                content="", tool_calls=tool_call,
                prompt_tokens=1, completion_tokens=1, elapsed_s=0,
            ),
            LLMToolResponse(
                content="", tool_calls=tool_call,
                prompt_tokens=1, completion_tokens=1, elapsed_s=0,
            ),
            LLMToolResponse(
                content="forced answer",
                prompt_tokens=10, completion_tokens=5, elapsed_s=0,
            ),
        ])
        client = MagicMock()
        client.fs_read.return_value = "content"
        plugin = _make_plugin(
            tool_calling=True, manual_search=False,
            max_iterations=max_iter, llm=llm, memory_client=client,
        )
        resp = plugin.send_message("s1", "Q")
        self.assertEqual("forced answer", resp.text)
        self.assertEqual(max_iter, resp.extra["iterations"])
        self.assertEqual(2, resp.extra["tool_call_count"])
        self.assertEqual(3, len(llm.tool_calls))

    # -- Phase A: manual search ----------------------------------------

    def test_manual_search_failure_continues(self) -> None:
        client = MagicMock()
        client.search.side_effect = ConnectionError("search down")
        llm = _FakeLLM(tool_responses=[
            LLMToolResponse(
                content="answer anyway",
                prompt_tokens=1, completion_tokens=1, elapsed_s=0,
            ),
        ])
        plugin = _make_plugin(
            tool_calling=True, manual_search=True, llm=llm, memory_client=client,
        )
        resp = plugin.send_message("s1", "Q")
        self.assertEqual("answer anyway", resp.text)
        self.assertIsNone(resp.error)

    def test_manual_search_injects_memory_into_messages(self) -> None:
        client = MagicMock()
        client.search.return_value = [
            SearchResult(
                uri="viking://m1", score=0.9,
                content="relevant info", memory_type="fact",
            ),
        ]
        llm = _FakeLLM(tool_responses=[
            LLMToolResponse(
                content="answer", prompt_tokens=1, completion_tokens=1, elapsed_s=0,
            ),
        ])
        plugin = _make_plugin(
            tool_calling=True, manual_search=True,
            memory_budget_chars=10000, llm=llm, memory_client=client,
        )
        resp = plugin.send_message("s1", "Q")
        self.assertTrue(len(resp.memory_items) > 0)
        messages = llm.tool_calls[0][0]
        self.assertEqual("system", messages[0]["role"])
        self.assertIn("### Retrieved memories", messages[1]["content"])
        self.assertEqual("user", messages[2]["role"])

    # -- Phase B: tool list construction --------------------------------

    def test_search_in_tools_false_filters_memory_search(self) -> None:
        llm = _FakeLLM(tool_responses=[
            LLMToolResponse(
                content="answer", prompt_tokens=1, completion_tokens=1, elapsed_s=0,
            ),
        ])
        plugin = _make_plugin(
            tool_calling=True, search_in_tools=False,
            manual_search=False, llm=llm,
        )
        plugin.send_message("s1", "Q")
        tools_passed = llm.tool_calls[0][1]
        tool_names = [t["function"]["name"] for t in tools_passed]
        self.assertNotIn("memory_search", tool_names)
        self.assertIn("memory_read", tool_names)
        self.assertIn("memory_list", tool_names)
        self.assertIn("memory_glob", tool_names)

    # -- Response structure --------------------------------------------

    def test_response_extra_contains_expected_keys(self) -> None:
        llm = _FakeLLM(chat_response=LLMResponse(
            content="ok", prompt_tokens=1, completion_tokens=1, elapsed_s=0,
        ))
        plugin = _make_plugin(tool_calling=False, manual_search=False, llm=llm)
        resp = plugin.send_message("s1", "Q")
        self.assertEqual("openviking_mcp", resp.extra["qa_profile"])
        for key in ("tool_call_count", "iterations", "elapsed_s",
                     "retrieval_latency_s", "llm_latency_s"):
            with self.subTest(key=key):
                self.assertIn(key, resp.extra)

    def test_question_time_prepended_to_user_message(self) -> None:
        llm = _FakeLLM(chat_response=LLMResponse(
            content="ok", prompt_tokens=1, completion_tokens=1, elapsed_s=0,
        ))
        plugin = _make_plugin(tool_calling=False, manual_search=False, llm=llm)
        plugin.send_message("s1", "Q", extra={"question_time": "2024-06-15"})
        messages = llm.chat_calls[0][0]
        user_msg = messages[-1]["content"]
        self.assertIn("Current date: 2024-06-15", user_msg)

    def test_extra_none_does_not_crash(self) -> None:
        llm = _FakeLLM(chat_response=LLMResponse(
            content="ok", prompt_tokens=1, completion_tokens=1, elapsed_s=0,
        ))
        plugin = _make_plugin(tool_calling=False, manual_search=False, llm=llm)
        resp = plugin.send_message("s1", "Q", extra=None)
        self.assertEqual("ok", resp.text)


# ======================================================================
#  Concurrency safety (design intent)
# ======================================================================

class ConcurrencySafetyTests(unittest.TestCase):
    """Verify search calls don't pass agent_id (thread-safety per design.md).

    OpenVikingClient uses urllib with no persistent connections; the client
    instance is shared across threads. Passing agent_id to search() would
    mutate self.agent_id and cause a race. The plugin and _execute_tool
    must therefore omit agent_id from all search calls.
    """

    def test_send_message_manual_search_no_agent_id(self) -> None:
        client = MagicMock()
        client.search.return_value = []
        llm = _FakeLLM(tool_responses=[
            LLMToolResponse(
                content="answer", prompt_tokens=1, completion_tokens=1, elapsed_s=0,
            ),
        ])
        plugin = _make_plugin(
            tool_calling=True, manual_search=True, llm=llm, memory_client=client,
        )
        plugin.send_message("s1", "Q")
        for c in client.search.call_args_list:
            _, kwargs = c
            self.assertNotIn("agent_id", kwargs)

    def test_send_message_tool_search_no_agent_id(self) -> None:
        client = MagicMock()
        client.search.return_value = []
        llm = _FakeLLM(tool_responses=[
            LLMToolResponse(
                content="",
                tool_calls=[{
                    "id": "tc1",
                    "function": {
                        "name": "memory_search",
                        "arguments": '{"query": "q"}',
                    },
                }],
                prompt_tokens=1, completion_tokens=1, elapsed_s=0,
            ),
            LLMToolResponse(
                content="answer", prompt_tokens=1, completion_tokens=1, elapsed_s=0,
            ),
        ])
        plugin = _make_plugin(
            tool_calling=True, manual_search=False, llm=llm, memory_client=client,
        )
        plugin.send_message("s1", "Q")
        for c in client.search.call_args_list:
            _, kwargs = c
            self.assertNotIn("agent_id", kwargs)


if __name__ == "__main__":
    unittest.main()

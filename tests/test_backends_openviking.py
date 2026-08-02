"""Unit tests for backends.openviking.client.OpenVikingClient.

Covers headers, identity isolation, commit-polling hooks, session lifecycle,
dual-domain retrieval, and local-filesystem operations.  No real HTTP server
is used -- _post and _get are monkey-patched with fakes; filesystem tests use
tempfile.TemporaryDirectory.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any

from backends.openviking.client import OpenVikingClient


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _http_error(code: int) -> urllib.error.HTTPError:
    """Build an HTTPError suitable for mocking _get / _post failures."""
    return urllib.error.HTTPError(
        "http://127.0.0.1:19080/test",
        code,
        "Error",
        None,
        io.BytesIO(b"{}"),
    )


def _fake_post(captured: dict[str, Any], resp: dict[str, Any] | list[Any] | None = None):
    """Return a fake _post that records the call and returns *resp*."""

    def _fn(path: str, body: dict | None = None, **kwargs: Any) -> dict[str, Any]:
        captured["path"] = path
        captured["body"] = body
        captured["timeout_s"] = kwargs.get("timeout_s")
        return resp or {}

    return _fn


# --------------------------------------------------------------------------- #
#  _headers()                                                                  #
# --------------------------------------------------------------------------- #

class HeadersTests(unittest.TestCase):
    def test_headers_contains_required_fields(self) -> None:
        client = OpenVikingClient(
            account="acc1", user_id="u1", agent_id="a1", api_key="k1",
        )
        h = client._headers()
        self.assertEqual("application/json", h["Content-Type"])
        self.assertEqual("acc1", h["X-OpenViking-Account"])
        self.assertEqual("u1", h["X-OpenViking-User"])
        self.assertEqual("a1", h["X-OpenViking-Agent"])

    def test_headers_includes_api_key_and_authorization_when_set(self) -> None:
        client = OpenVikingClient(api_key="secret")
        h = client._headers()
        self.assertEqual("secret", h["X-API-Key"])
        self.assertEqual("Bearer secret", h["Authorization"])

    def test_headers_omits_api_key_and_authorization_when_absent(self) -> None:
        client = OpenVikingClient(api_key="")
        h = client._headers()
        self.assertNotIn("X-API-Key", h)
        self.assertNotIn("Authorization", h)

    def test_headers_defaults_empty_fields_to_default(self) -> None:
        client = OpenVikingClient(account="", user_id="", agent_id="")
        h = client._headers()
        self.assertEqual("default", h["X-OpenViking-Account"])
        self.assertEqual("default", h["X-OpenViking-User"])
        self.assertEqual("default", h["X-OpenViking-Agent"])


# --------------------------------------------------------------------------- #
#  auth_key property                                                           #
# --------------------------------------------------------------------------- #

class AuthKeyTests(unittest.TestCase):
    def test_auth_key_returns_api_key(self) -> None:
        client = OpenVikingClient(api_key="my-secret-key")
        self.assertEqual("my-secret-key", client.auth_key)

    def test_auth_key_empty_when_no_api_key(self) -> None:
        client = OpenVikingClient(api_key="")
        self.assertEqual("", client.auth_key)


# --------------------------------------------------------------------------- #
#  provision_isolated_identity / delete_current_identity                       #
# --------------------------------------------------------------------------- #

class ProvisionIdentityTests(unittest.TestCase):
    def test_provision_generates_eval_prefixed_account(self) -> None:
        client = OpenVikingClient()
        identity = client.provision_isolated_identity("mytest")
        account = identity["tenant_id"]
        self.assertTrue(account.startswith("eval-"))
        self.assertRegex(account, r"^eval-mytest-[0-9a-f]{8}$")

    def test_provision_sanitizes_label_special_chars(self) -> None:
        client = OpenVikingClient()
        identity = client.provision_isolated_identity("test label!")
        account = identity["tenant_id"]
        match = re.match(r"^eval-(.+)-[0-9a-f]{8}$", account)
        self.assertIsNotNone(match)
        self.assertEqual("test-label", match.group(1))

    def test_provision_strips_leading_trailing_dashes(self) -> None:
        client = OpenVikingClient()
        identity = client.provision_isolated_identity("---hello---")
        account = identity["tenant_id"]
        match = re.match(r"^eval-(.+)-[0-9a-f]{8}$", account)
        self.assertIsNotNone(match)
        self.assertEqual("hello", match.group(1))

    def test_provision_truncates_long_label_to_60_chars(self) -> None:
        client = OpenVikingClient()
        identity = client.provision_isolated_identity("x" * 100)
        account = identity["tenant_id"]
        match = re.match(r"^eval-(.+)-[0-9a-f]{8}$", account)
        self.assertIsNotNone(match)
        self.assertEqual(60, len(match.group(1)))

    def test_provision_preserves_underscore_and_dot(self) -> None:
        client = OpenVikingClient()
        identity = client.provision_isolated_identity("my_test.label")
        account = identity["tenant_id"]
        match = re.match(r"^eval-(.+)-[0-9a-f]{8}$", account)
        self.assertIsNotNone(match)
        self.assertEqual("my_test.label", match.group(1))

    def test_provision_sets_account_on_client(self) -> None:
        client = OpenVikingClient(account="original")
        identity = client.provision_isolated_identity("eval1")
        self.assertEqual(identity["tenant_id"], client.account)
        self.assertNotEqual("original", client.account)

    def test_provision_returns_tenant_id_and_user_id(self) -> None:
        client = OpenVikingClient(user_id="myuser")
        identity = client.provision_isolated_identity("label")
        self.assertIn("tenant_id", identity)
        self.assertIn("user_id", identity)
        self.assertEqual("myuser", identity["user_id"])
        self.assertEqual(client.account, identity["tenant_id"])

    def test_delete_current_identity_is_noop(self) -> None:
        client = OpenVikingClient(account="acc1", workspace="/tmp/ws")
        original_account = client.account
        client.delete_current_identity()
        self.assertEqual(original_account, client.account)

    def test_delete_current_identity_makes_no_http_calls(self) -> None:
        client = OpenVikingClient()
        calls: list[str] = []
        client._post = lambda *a, **kw: (calls.append("post"), {})[1]  # type: ignore[method-assign]
        client._get = lambda *a, **kw: (calls.append("get"), {})[1]  # type: ignore[method-assign]
        client.delete_current_identity()
        self.assertEqual([], calls)


# --------------------------------------------------------------------------- #
#  Commit-polling hooks                                                        #
# --------------------------------------------------------------------------- #

class CommitPollingHooksTests(unittest.TestCase):
    def test_fetch_commit_status_calls_get_with_task_path(self) -> None:
        client = OpenVikingClient()
        captured: dict[str, Any] = {}

        def fake_get(path: str, query: dict | None = None, **kw: Any) -> dict[str, Any]:
            captured["path"] = path
            captured["query"] = query
            return {"status": "completed"}

        client._get = fake_get  # type: ignore[method-assign]
        resp = client._fetch_commit_status("session1", "task123")
        self.assertEqual("/api/v1/tasks/task123", captured["path"])
        self.assertEqual({"status": "completed"}, resp)

    def test_commit_failed_statuses_contains_all_variants(self) -> None:
        client = OpenVikingClient()
        statuses = client._commit_failed_statuses()
        self.assertIsInstance(statuses, tuple)
        for s in ("failed", "error", "cancelled", "canceled"):
            self.assertIn(s, statuses)

    def test_parse_commit_status_various_formats(self) -> None:
        client = OpenVikingClient()
        cases: list[tuple[dict[str, Any], str]] = [
            ({"result": {"status": "Completed"}}, "completed"),
            ({"result": {"stage": "Processing"}}, "processing"),
            ({"result": {"state": "Done"}}, "done"),
            ({"result": {"status": "COMPLETED"}}, "completed"),
            ({"status": "ok"}, "ok"),
            ({"status": "RUNNING"}, "running"),
            ({}, ""),
        ]
        for resp, expected in cases:
            with self.subTest(resp=resp):
                self.assertEqual(expected, client._parse_commit_status(resp))

    def test_parse_commit_status_result_status_takes_priority(self) -> None:
        client = OpenVikingClient()
        resp = {"result": {"status": "completed", "stage": "processing"}, "status": "ok"}
        self.assertEqual("completed", client._parse_commit_status(resp))

    def test_parse_commit_status_falls_through_to_top_level(self) -> None:
        client = OpenVikingClient()
        resp = {"result": {}, "status": "running"}
        self.assertEqual("running", client._parse_commit_status(resp))

    def test_extract_commit_error_various_formats(self) -> None:
        client = OpenVikingClient()
        cases: list[tuple[dict[str, Any], str, str]] = [
            ({"result": {"error": "boom"}}, "failed", "boom"),
            ({"error": "top error"}, "failed", "top error"),
            ({}, "failed", "failed"),
            ({"result": {}}, "error", "error"),
        ]
        for resp, status, expected in cases:
            with self.subTest(resp=resp, status=status):
                self.assertEqual(expected, client._extract_commit_error(resp, status))

    def test_extract_commit_error_result_error_takes_priority(self) -> None:
        client = OpenVikingClient()
        resp = {"result": {"error": "inner"}, "error": "outer"}
        self.assertEqual("inner", client._extract_commit_error(resp, "failed"))


# --------------------------------------------------------------------------- #
#  health()                                                                     #
# --------------------------------------------------------------------------- #

class HealthTests(unittest.TestCase):
    def test_health_returns_dict_on_success(self) -> None:
        client = OpenVikingClient()
        expected = {"status": "running", "sessions": 3}
        client._get = lambda path, query=None, **kw: expected  # type: ignore[method-assign]
        self.assertEqual(expected, client.health())

    def test_health_tolerates_404(self) -> None:
        client = OpenVikingClient()

        def raise_404(*a: Any, **kw: Any) -> dict[str, Any]:
            raise _http_error(404)

        client._get = raise_404  # type: ignore[method-assign]
        result = client.health()
        self.assertEqual({"status": "ok", "note": "HTTP 404"}, result)

    def test_health_tolerates_405(self) -> None:
        client = OpenVikingClient()

        def raise_405(*a: Any, **kw: Any) -> dict[str, Any]:
            raise _http_error(405)

        client._get = raise_405  # type: ignore[method-assign]
        result = client.health()
        self.assertEqual({"status": "ok", "note": "HTTP 405"}, result)

    def test_health_reraises_500(self) -> None:
        client = OpenVikingClient()

        def raise_500(*a: Any, **kw: Any) -> dict[str, Any]:
            raise _http_error(500)

        client._get = raise_500  # type: ignore[method-assign]
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            client.health()
        self.assertEqual(500, ctx.exception.code)


# --------------------------------------------------------------------------- #
#  open_session()                                                              #
# --------------------------------------------------------------------------- #

class OpenSessionTests(unittest.TestCase):
    def test_open_session_generates_client_side_id(self) -> None:
        client = OpenVikingClient()
        captured: dict[str, Any] = {}
        client._post = _fake_post(captured, {"session_id": "server-123"})  # type: ignore[method-assign]
        result = client.open_session("My Session")
        self.assertEqual("server-123", result)
        self.assertEqual("/api/v1/sessions", captured["path"])

    def test_open_session_posts_session_id_in_body(self) -> None:
        client = OpenVikingClient()
        captured: dict[str, Any] = {}
        client._post = _fake_post(captured)  # type: ignore[method-assign]
        client.open_session("test-title")
        sid = captured["body"]["session_id"]
        self.assertTrue(sid.startswith("eval-"))
        self.assertRegex(sid, r"^eval-test-title-[0-9a-f]{12}$")

    def test_open_session_returns_response_id_when_no_session_id(self) -> None:
        client = OpenVikingClient()
        client._post = _fake_post({}, {"id": "from-id"})  # type: ignore[method-assign]
        self.assertEqual("from-id", client.open_session("t"))

    def test_open_session_returns_generated_when_no_response_id(self) -> None:
        client = OpenVikingClient()
        captured: dict[str, Any] = {}
        client._post = _fake_post(captured, {})  # type: ignore[method-assign]
        result = client.open_session("t")
        sid = captured["body"]["session_id"]
        self.assertEqual(sid, result)

    def test_open_session_sanitizes_title(self) -> None:
        client = OpenVikingClient()
        captured: dict[str, Any] = {}
        client._post = _fake_post(captured)  # type: ignore[method-assign]
        client.open_session("Test Session!")
        sid = captured["body"]["session_id"]
        self.assertIn("Test-Session", sid)

    def test_open_session_uses_session_for_empty_title(self) -> None:
        client = OpenVikingClient()
        captured: dict[str, Any] = {}
        client._post = _fake_post(captured)  # type: ignore[method-assign]
        client.open_session("")
        sid = captured["body"]["session_id"]
        self.assertTrue(sid.startswith("eval-session-"))


# --------------------------------------------------------------------------- #
#  add_message()                                                               #
# --------------------------------------------------------------------------- #

class AddMessageTests(unittest.TestCase):
    def test_add_message_normalizes_role(self) -> None:
        client = OpenVikingClient()
        bodies: list[dict[str, Any]] = []

        def fake_post(path: str, body: dict | None = None, **kw: Any) -> dict[str, Any]:
            bodies.append(body)
            return {}

        client._post = fake_post  # type: ignore[method-assign]
        for role in ("user", "assistant", "system", "tool", "function"):
            with self.subTest(role=role):
                client.add_message("s1", role, "content")
        self.assertEqual("user", bodies[0]["role"])
        self.assertEqual("assistant", bodies[1]["role"])
        self.assertEqual("user", bodies[2]["role"])
        self.assertEqual("user", bodies[3]["role"])
        self.assertEqual("user", bodies[4]["role"])

    def test_add_message_wraps_content_in_parts(self) -> None:
        client = OpenVikingClient()
        captured: dict[str, Any] = {}
        client._post = _fake_post(captured)  # type: ignore[method-assign]
        client.add_message("s1", "user", "hello world", created_at="2024-01-01T00:00:00")
        body = captured["body"]
        self.assertEqual([{"type": "text", "text": "hello world"}], body["parts"])
        self.assertEqual("hello world", body["content"])

    def test_add_message_uses_role_id_when_provided(self) -> None:
        client = OpenVikingClient()
        captured: dict[str, Any] = {}
        client._post = _fake_post(captured)  # type: ignore[method-assign]
        client.add_message("s1", "user", "hello", role_id="custom-id")
        self.assertEqual("custom-id", captured["body"]["role_id"])

    def test_add_message_defaults_role_id_to_role(self) -> None:
        client = OpenVikingClient()
        captured: dict[str, Any] = {}
        client._post = _fake_post(captured)  # type: ignore[method-assign]
        client.add_message("s1", "assistant", "hi")
        self.assertEqual("assistant", captured["body"]["role_id"])

    def test_add_message_uses_provided_created_at(self) -> None:
        client = OpenVikingClient()
        captured: dict[str, Any] = {}
        client._post = _fake_post(captured)  # type: ignore[method-assign]
        client.add_message("s1", "user", "hello", created_at="2024-06-15T10:30:00")
        self.assertEqual("2024-06-15T10:30:00", captured["body"]["created_at"])

    def test_add_message_generates_created_at_when_absent(self) -> None:
        client = OpenVikingClient()
        captured: dict[str, Any] = {}
        client._post = _fake_post(captured)  # type: ignore[method-assign]
        client.add_message("s1", "user", "hello")
        ts = captured["body"]["created_at"]
        self.assertTrue(ts)
        datetime.fromisoformat(ts)  # should not raise

    def test_add_message_posts_to_correct_path(self) -> None:
        client = OpenVikingClient()
        captured: dict[str, Any] = {}
        client._post = _fake_post(captured)  # type: ignore[method-assign]
        client.add_message("sess-42", "user", "hello")
        self.assertEqual("/api/v1/sessions/sess-42/messages", captured["path"])


# --------------------------------------------------------------------------- #
#  commit_session()                                                            #
# --------------------------------------------------------------------------- #

class CommitSessionTests(unittest.TestCase):
    def test_commit_session_returns_task_id_from_various_formats(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({"task_id": "t1"}, "t1"),
            ({"id": "t2"}, "t2"),
            ({"result": {"task_id": "t3"}}, "t3"),
            ({"result": {"id": "t4"}}, "t4"),
        ]
        for resp, expected in cases:
            with self.subTest(resp=resp):
                client = OpenVikingClient()
                client._post = _fake_post({}, resp)  # type: ignore[method-assign]
                self.assertEqual(expected, client.commit_session("sess-1"))

    def test_commit_session_fallback_to_session_id(self) -> None:
        for status in ("accepted", "committed", "ok", "completed", "COMPLETED"):
            with self.subTest(status=status):
                client = OpenVikingClient()
                resp = {"status": status}
                client._post = _fake_post({}, resp)  # type: ignore[method-assign]
                self.assertEqual("sess-1", client.commit_session("sess-1"))

    def test_commit_session_no_fallback_for_unknown_status(self) -> None:
        client = OpenVikingClient()
        client._post = _fake_post({}, {"status": "pending"})  # type: ignore[method-assign]
        self.assertEqual("", client.commit_session("sess-1"))

    def test_commit_session_returns_empty_when_nothing_found(self) -> None:
        client = OpenVikingClient()
        client._post = _fake_post({}, {})  # type: ignore[method-assign]
        self.assertEqual("", client.commit_session("sess-1"))

    def test_commit_session_posts_to_correct_path(self) -> None:
        client = OpenVikingClient()
        captured: dict[str, Any] = {}
        client._post = _fake_post(captured)  # type: ignore[method-assign]
        client.commit_session("sess-42")
        self.assertEqual("/api/v1/sessions/sess-42/commit", captured["path"])


# --------------------------------------------------------------------------- #
#  _search_once()                                                              #
# --------------------------------------------------------------------------- #

class SearchOnceTests(unittest.TestCase):
    def test_search_once_posts_correct_body(self) -> None:
        client = OpenVikingClient()
        captured: dict[str, Any] = {}

        def fake_post(path: str, body: dict | None = None, **kw: Any) -> dict[str, Any]:
            captured["path"] = path
            captured["body"] = body
            captured["timeout_s"] = kw.get("timeout_s")
            return {"items": []}

        client._post = fake_post  # type: ignore[method-assign]
        client._search_once("hello", "viking://user/memories/", 5, 30.0)
        body = captured["body"]
        self.assertEqual("/api/v1/search/find", captured["path"])
        self.assertEqual("hello", body["query"])
        self.assertEqual("viking://user/memories/", body["target_uri"])
        self.assertEqual(5, body["limit"])
        self.assertEqual(0.0, body["score_threshold"])
        self.assertEqual(30.0, captured["timeout_s"])

    def test_search_once_response_formats(self) -> None:
        client = OpenVikingClient()
        item_a = {"uri": "a", "score": 0.1}
        item_b = {"uri": "b", "score": 0.2}
        cases: list[tuple[str, dict[str, Any], int, list[dict[str, Any]]]] = [
            ("list_under_result", {"result": [item_a, item_b]}, 10, [item_a, item_b]),
            ("items_key", {"items": [item_a]}, 10, [item_a]),
            ("results_key", {"results": [item_a]}, 10, [item_a]),
            ("hits_key", {"hits": [item_a]}, 10, [item_a]),
            ("memories_key", {"memories": [item_a]}, 10, [item_a]),
            ("resources_key", {"resources": [item_a]}, 10, [item_a]),
            (
                "merged_memories_resources",
                {"memories": [item_a], "resources": [item_b]},
                10,
                [item_a, item_b],
            ),
            ("nested_result_items", {"result": {"items": [item_a]}}, 10, [item_a]),
            ("empty_resp", {}, 10, []),
            (
                "limit_truncation",
                {"result": [{"uri": str(i)} for i in range(5)]},
                3,
                [{"uri": "0"}, {"uri": "1"}, {"uri": "2"}],
            ),
        ]
        for name, resp, limit, expected in cases:
            with self.subTest(name=name):
                client._post = lambda path, body=None, _r=resp, **kw: _r  # type: ignore[method-assign]
                result = client._search_once("q", "viking://user/memories/", limit, None)
                self.assertEqual(expected, result)

    def test_search_once_tolerates_404(self) -> None:
        client = OpenVikingClient()

        def fake_post(path: str, body: dict | None = None, **kw: Any) -> dict[str, Any]:
            raise _http_error(404)

        client._post = fake_post  # type: ignore[method-assign]
        self.assertEqual([], client._search_once("q", "viking://user/memories/", 5, None))

    def test_search_once_reraises_500(self) -> None:
        client = OpenVikingClient()

        def fake_post(path: str, body: dict | None = None, **kw: Any) -> dict[str, Any]:
            raise _http_error(500)

        client._post = fake_post  # type: ignore[method-assign]
        with self.assertRaises(urllib.error.HTTPError):
            client._search_once("q", "viking://user/memories/", 5, None)


# --------------------------------------------------------------------------- #
#  search()                                                                     #
# --------------------------------------------------------------------------- #

class SearchTests(unittest.TestCase):
    @staticmethod
    def _mock_search_once(client: OpenVikingClient, user_items: list, agent_items: list):
        """Mock _search_once returning *user_items* for user URI, *agent_items* for agent."""
        calls: list[str] = []

        def fake(query: str, target_uri: str, limit: int, timeout_s: Any) -> list[dict[str, Any]]:
            calls.append(target_uri)
            if target_uri == OpenVikingClient.DEFAULT_USER_TARGET_URI:
                return user_items
            return agent_items

        client._search_once = fake  # type: ignore[method-assign]
        return calls

    def test_search_calls_both_domains(self) -> None:
        client = OpenVikingClient()
        calls = self._mock_search_once(client, [], [])
        client.search("query", top_k=5)
        self.assertEqual(2, len(calls))
        self.assertIn(OpenVikingClient.DEFAULT_USER_TARGET_URI, calls)
        self.assertIn(OpenVikingClient.DEFAULT_AGENT_TARGET_URI, calls)

    def test_search_deduplicates_by_uri(self) -> None:
        client = OpenVikingClient()
        self._mock_search_once(
            client,
            [{"uri": "shared", "score": 0.9, "content": "user"}],
            [{"uri": "shared", "score": 0.8, "content": "agent"}],
        )
        results = client.search("query", top_k=10)
        self.assertEqual(1, len(results))
        self.assertEqual(0.9, results[0].score)

    def test_search_deduplicates_by_path(self) -> None:
        client = OpenVikingClient()
        self._mock_search_once(
            client,
            [{"path": "shared", "score": 0.9}],
            [{"path": "shared", "score": 0.8}],
        )
        results = client.search("query", top_k=10)
        self.assertEqual(1, len(results))

    def test_search_deduplicates_by_id(self) -> None:
        client = OpenVikingClient()
        self._mock_search_once(
            client,
            [{"id": "shared", "score": 0.9}],
            [{"id": "shared", "score": 0.8}],
        )
        results = client.search("query", top_k=10)
        self.assertEqual(1, len(results))

    def test_search_sorts_by_score_descending(self) -> None:
        client = OpenVikingClient()
        self._mock_search_once(
            client,
            [
                {"uri": "low", "score": 0.3},
                {"uri": "high", "score": 0.9},
                {"uri": "mid", "score": 0.6},
            ],
            [],
        )
        results = client.search("query", top_k=10)
        self.assertEqual(3, len(results))
        self.assertEqual(0.9, results[0].score)
        self.assertEqual(0.6, results[1].score)
        self.assertEqual(0.3, results[2].score)

    def test_search_truncates_to_top_k(self) -> None:
        client = OpenVikingClient()
        items = [{"uri": f"u{i}", "score": float(i)} for i in range(8)]
        self._mock_search_once(client, items, [])
        results = client.search("query", top_k=3)
        self.assertEqual(3, len(results))

    def test_search_returns_empty_when_no_results(self) -> None:
        client = OpenVikingClient()
        self._mock_search_once(client, [], [])
        self.assertEqual([], client.search("query", top_k=10))

    def test_search_temporarily_sets_agent_id_and_restores(self) -> None:
        client = OpenVikingClient(agent_id="original")
        captured: list[str] = []

        def fake(query: str, target_uri: str, limit: int, timeout_s: Any) -> list[dict[str, Any]]:
            captured.append(client.agent_id)
            return []

        client._search_once = fake  # type: ignore[method-assign]
        client.search("query", agent_id="temp")
        self.assertEqual(["temp", "temp"], captured)
        self.assertEqual("original", client.agent_id)

    def test_search_does_not_change_agent_id_when_not_provided(self) -> None:
        client = OpenVikingClient(agent_id="original")
        client._search_once = lambda *a, **kw: []  # type: ignore[method-assign]
        client.search("query")
        self.assertEqual("original", client.agent_id)

    def test_search_merges_both_domains(self) -> None:
        client = OpenVikingClient()
        self._mock_search_once(
            client,
            [{"uri": "u1", "score": 0.5}],
            [{"uri": "a1", "score": 0.7}],
        )
        results = client.search("query", top_k=10)
        self.assertEqual(2, len(results))
        self.assertEqual(0.7, results[0].score)
        self.assertEqual(0.5, results[1].score)


# --------------------------------------------------------------------------- #
#  _uri_to_local_path / _local_to_uri                                          #
# --------------------------------------------------------------------------- #

class UriMappingTests(unittest.TestCase):
    def test_uri_to_local_path_maps_viking_uri(self) -> None:
        client = OpenVikingClient(workspace="/tmp/ws", account="acc1")
        path = client._uri_to_local_path("viking://user/memories/foo.md")
        expected = Path("/tmp/ws") / "viking" / "acc1" / "user/memories/foo.md"
        self.assertEqual(expected, path)

    def test_uri_to_local_path_returns_none_for_empty_workspace(self) -> None:
        client = OpenVikingClient(workspace="")
        self.assertIsNone(client._uri_to_local_path("viking://user/memories/foo.md"))

    def test_uri_to_local_path_returns_none_for_non_viking_uri(self) -> None:
        client = OpenVikingClient(workspace="/tmp/ws")
        self.assertIsNone(client._uri_to_local_path("http://example.com/foo"))

    def test_uri_to_local_path_returns_none_for_bare_viking_uri(self) -> None:
        client = OpenVikingClient(workspace="/tmp/ws")
        self.assertIsNone(client._uri_to_local_path("viking://"))
        self.assertIsNone(client._uri_to_local_path("viking:///"))

    def test_local_to_uri_maps_path_under_account_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenVikingClient(workspace=tmpdir, account="acc1")
            account_root = Path(tmpdir) / "viking" / "acc1"
            file_path = account_root / "user" / "memories" / "foo.md"
            file_path.parent.mkdir(parents=True)
            file_path.touch()
            uri = client._local_to_uri(file_path)
            self.assertEqual("viking://user/memories/foo.md", uri)

    def test_local_to_uri_returns_empty_for_path_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenVikingClient(workspace=tmpdir, account="acc1")
            outside = Path(tmpdir) / "viking" / "other_account" / "foo.md"
            self.assertEqual("", client._local_to_uri(outside))


# --------------------------------------------------------------------------- #
#  fs_read()                                                                    #
# --------------------------------------------------------------------------- #

class FsReadTests(unittest.TestCase):
    def test_fs_read_reads_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenVikingClient(workspace=tmpdir, account="acc1")
            file_path = Path(tmpdir) / "viking" / "acc1" / "user" / "memories" / "foo.md"
            file_path.parent.mkdir(parents=True)
            file_path.write_text("hello world", encoding="utf-8")
            self.assertEqual("hello world", client.fs_read("viking://user/memories/foo.md"))

    def test_fs_read_returns_empty_for_nonexistent(self) -> None:
        client = OpenVikingClient(workspace="/tmp/ws", account="acc1")
        self.assertEqual("", client.fs_read("viking://nonexistent/file.md"))

    def test_fs_read_returns_empty_for_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenVikingClient(workspace=tmpdir, account="acc1")
            dir_path = Path(tmpdir) / "viking" / "acc1" / "user"
            dir_path.mkdir(parents=True)
            self.assertEqual("", client.fs_read("viking://user"))


# --------------------------------------------------------------------------- #
#  fs_list()                                                                    #
# --------------------------------------------------------------------------- #

class FsListTests(unittest.TestCase):
    def test_fs_list_lists_directory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenVikingClient(workspace=tmpdir, account="acc1")
            root = Path(tmpdir) / "viking" / "acc1" / "memories"
            root.mkdir(parents=True)
            (root / "a.md").write_text("aaa", encoding="utf-8")
            (root / "b.md").write_text("bb", encoding="utf-8")
            (root / "sub").mkdir()

            entries = client.fs_list("viking://memories/")
            names = {e["name"] for e in entries}
            self.assertEqual({"a.md", "b.md", "sub"}, names)

            a_entry = next(e for e in entries if e["name"] == "a.md")
            self.assertFalse(a_entry["is_dir"])
            self.assertEqual(3, a_entry["size"])
            self.assertIn("uri", a_entry)
            self.assertIn("path", a_entry)

            sub_entry = next(e for e in entries if e["name"] == "sub")
            self.assertTrue(sub_entry["is_dir"])
            self.assertEqual(0, sub_entry["size"])

    def test_fs_list_returns_empty_for_nonexistent(self) -> None:
        client = OpenVikingClient(workspace="/tmp/ws", account="acc1")
        self.assertEqual([], client.fs_list("viking://nonexistent/"))

    def test_fs_list_recursive_delegates_to_glob(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenVikingClient(workspace=tmpdir, account="acc1")
            root = Path(tmpdir) / "viking" / "acc1" / "memories"
            root.mkdir(parents=True)
            (root / "a.md").write_text("aaa", encoding="utf-8")
            (root / "sub").mkdir()
            (root / "sub" / "b.md").write_text("bb", encoding="utf-8")

            entries = client.fs_list("viking://memories/", recursive=True)
            names = {e["name"] for e in entries}
            self.assertIn("a.md", names)
            self.assertIn("b.md", names)


# --------------------------------------------------------------------------- #
#  fs_glob()                                                                    #
# --------------------------------------------------------------------------- #

class FsGlobTests(unittest.TestCase):
    def test_fs_glob_matches_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenVikingClient(workspace=tmpdir, account="acc1")
            root = Path(tmpdir) / "viking" / "acc1" / "memories"
            root.mkdir(parents=True)
            (root / "a.md").write_text("aaa", encoding="utf-8")
            (root / "b.md").write_text("bb", encoding="utf-8")

            entries = client.fs_glob("viking://memories/*.md")
            names = [e["name"] for e in entries]
            self.assertEqual(["a.md", "b.md"], names)
            for e in entries:
                self.assertIn("uri", e)
                self.assertIn("path", e)
                self.assertFalse(e["is_dir"])

    def test_fs_glob_returns_empty_for_non_viking_pattern(self) -> None:
        client = OpenVikingClient(workspace="/tmp/ws", account="acc1")
        self.assertEqual([], client.fs_glob("http://example.com/*.md"))

    def test_fs_glob_returns_empty_for_no_workspace(self) -> None:
        client = OpenVikingClient(workspace="")
        self.assertEqual([], client.fs_glob("viking://memories/*.md"))


if __name__ == "__main__":
    unittest.main()

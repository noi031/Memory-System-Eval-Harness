"""EchoAgent HTTP client and typing-prefetch simulation."""

from __future__ import annotations

import json
import logging
import random
import time
import uuid
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def _encode_context_path(context_path: str) -> str:
    return quote(context_path, safe="")


class EchoAgentClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token = ""
        self.user_uuid = ""
        self._context_seq: dict[str, int] = {}

    def _headers(self, json_content: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        timeout: float = 30,
    ) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(body or {}).encode("utf-8") if body else None,
            headers=self._headers(),
            method=method,
        )
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip() else {}

    def login(self) -> None:
        request = Request(
            f"{self.base_url}/v1/auth/login",
            data=json.dumps({
                "username": self.username,
                "password": self.password,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8", "replace"))
            cookie_headers = response.headers.get_all("Set-Cookie") or []
        self.token = str(result.get("access_token") or "")
        if not self.token:
            for cookie_header in cookie_headers:
                if "access_token=" in cookie_header:
                    self.token = cookie_header.split(
                        "access_token=",
                        1,
                    )[1].split(";", 1)[0]
                    break
        if not self.token:
            raise RuntimeError(
                f"登录成功但未获取 token: {list(result.keys())}"
            )
        self.user_uuid = str((result.get("user") or {}).get("id") or "")

    def get_memory_auth_key(self, memory_engine_endpoint: str) -> str:
        request = Request(
            memory_engine_endpoint,
            data=json.dumps({
                "mode": "credential",
                "userId": self.user_uuid,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8", "replace"))
        auth_key = str((result.get("result") or {}).get("authKey") or "")
        if not auth_key:
            raise RuntimeError(f"credential 接口未返回 authKey: {result}")
        return auth_key

    def create_session(
        self,
        title: str = "",
        memory_engine_endpoint: str = "",
    ) -> str:
        result = self._request(
            "POST",
            "/v1/sessions",
            {"title": title or f"test-{uuid.uuid4().hex[:8]}"},
        )
        session_id = str(
            (result.get("data") or result).get("id")
            or result.get("id")
            or ""
        )
        if session_id and memory_engine_endpoint:
            try:
                self._request(
                    "POST",
                    f"/v1/sessions/{session_id}/memory-engine/test",
                    {"endpoint": memory_engine_endpoint},
                )
                self._request(
                    "PUT",
                    f"/v1/sessions/{session_id}/memory-engine",
                    {"enabled": True, "endpoint": memory_engine_endpoint},
                )
            except Exception as exc:
                logging.warning(
                    "启用记忆引擎失败 (session %s): %s",
                    session_id,
                    exc,
                )
        return session_id

    def prefetch_tick(
        self,
        session_id: str,
        context_path: str,
        client_turn_id: str,
        revision: int,
        draft_text: str,
    ) -> dict[str, Any] | None:
        path = (
            f"/v1/sessions/{session_id}/context-paths/"
            f"{_encode_context_path(context_path)}/prefetch/tick"
        )
        try:
            return self._request("POST", path, {
                "clientTurnId": client_turn_id,
                "revision": revision,
                "draftText": draft_text,
            })
        except HTTPError as exc:
            if exc.code == 404:
                logging.debug("prefetch/tick 不存在 (404), 跳过打字模拟")
                return None
            raise
        except Exception as exc:
            logging.debug("prefetch/tick 失败: %s", exc)
            return None

    def prefetch_finalize(
        self,
        session_id: str,
        context_path: str,
        client_turn_id: str,
        full_content: str,
    ) -> dict[str, Any] | None:
        path = (
            f"/v1/sessions/{session_id}/context-paths/"
            f"{_encode_context_path(context_path)}/prefetch/finalize"
        )
        try:
            return self._request("POST", path, {
                "clientTurnId": client_turn_id,
                "fullContent": full_content,
            })
        except HTTPError as exc:
            if exc.code == 404:
                logging.debug("prefetch/finalize 不存在 (404), 跳过")
                return None
            raise
        except Exception as exc:
            logging.debug("prefetch/finalize 失败: %s", exc)
            return None

    def send_message(
        self,
        session_id: str,
        context_path: str,
        content: str,
        prefetch_client_turn_id: str = "",
    ) -> dict[str, Any]:
        key = f"{session_id}:{context_path}"
        after_seq = self._context_seq.get(key, 0)
        path = (
            f"/v1/sessions/{session_id}/context-paths/"
            f"{_encode_context_path(context_path)}/messages"
        )
        result: dict[str, Any] = {}
        for _attempt in range(3):
            body: dict[str, Any] = {
                "content": content,
                "afterSeq": after_seq,
            }
            if prefetch_client_turn_id:
                body["prefetchClientTurnId"] = prefetch_client_turn_id
            result = self._request("POST", path, body)
            data = result.get("data") or result
            server_seq = data.get("latestContextSeq")
            if isinstance(server_seq, int):
                self._context_seq[key] = server_seq
            if (
                data.get("error") in {"CONTEXT_SEQ_OUTDATED", "SEQ_OUTDATED"}
                and isinstance(server_seq, int)
            ):
                after_seq = server_seq
                continue
            return result
        return result

    def stream_reply(
        self,
        session_id: str,
        context_path: str,
        seq: int,
        timeout: float = 300,
    ) -> dict[str, Any]:
        url = (
            f"{self.base_url}/v1/sessions/{session_id}/context-paths/"
            f"{_encode_context_path(context_path)}/streaming?seq={seq}"
        )
        headers = self._headers(json_content=False)
        headers.update({
            "Accept": "text/event-stream",
            "Last-Event-ID": "-1",
        })
        reply_parts: list[str] = []
        ttft_ms: float | None = None
        started = time.monotonic()
        done_event: dict[str, Any] = {}
        with urlopen(Request(url, headers=headers), timeout=timeout) as response:
            raw_buffer = b""
            text_buffer = ""
            while True:
                chunk = response.read(4096)
                if not chunk:
                    break
                raw_buffer += chunk
                try:
                    text = raw_buffer.decode("utf-8")
                    raw_buffer = b""
                except UnicodeDecodeError:
                    text = raw_buffer[:-3].decode("utf-8", errors="replace")
                    raw_buffer = raw_buffer[-3:]
                text_buffer += text
                while "\n\n" in text_buffer:
                    block, text_buffer = text_buffer.split("\n\n", 1)
                    event_type = ""
                    data_lines: list[str] = []
                    for line in block.splitlines():
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:])
                    payload = "\n".join(data_lines)
                    if not payload:
                        continue
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if event_type in {"create", "append"}:
                        if ttft_ms is None:
                            ttft_ms = (time.monotonic() - started) * 1000
                        fragment = data.get("fragment") or data.get("content") or ""
                        reply_parts.append(
                            str(fragment.get("content") or "")
                            if isinstance(fragment, dict)
                            else str(fragment)
                        )
                    elif event_type == "done":
                        done_event = data if isinstance(data, dict) else {}
                        if ttft_ms is None:
                            ttft_ms = (time.monotonic() - started) * 1000
                        return {
                            "reply": "".join(reply_parts),
                            "ttft_ms": ttft_ms,
                            "done_event": done_event,
                        }
                    elif event_type == "error":
                        return {
                            "reply": "".join(reply_parts),
                            "ttft_ms": ttft_ms,
                            "error": str(data),
                            "done_event": {},
                        }
        return {
            "reply": "".join(reply_parts),
            "ttft_ms": ttft_ms,
            "done_event": done_event,
        }

    def get_last_request(
        self,
        session_id: str,
        context_path: str = "/",
    ) -> dict[str, Any]:
        try:
            return self._request(
                "GET",
                f"/v1/sessions/{session_id}/primary-model/last-request"
                f"?contextPath={_encode_context_path(context_path)}",
            )
        except Exception:
            return {}


def simulate_typing(
    client: EchoAgentClient,
    session_id: str,
    context_path: str,
    text: str,
    typing_speed_ms: int = 100,
    jitter_ms: int = 20,
) -> tuple[str, bool]:
    client_turn_id = uuid.uuid4().hex[:12]
    for revision in range(1, len(text) + 1):
        tick_result = client.prefetch_tick(
            session_id,
            context_path,
            client_turn_id,
            revision,
            text[:revision],
        )
        if tick_result is None:
            return "", False
        tick_data = tick_result.get("data") or tick_result
        if not tick_data.get("accepted") and revision == 1:
            return client_turn_id, False
        delay = typing_speed_ms + random.randint(-jitter_ms, jitter_ms)
        time.sleep(max(10, delay) / 1000.0)
    final = client.prefetch_finalize(
        session_id,
        context_path,
        client_turn_id,
        text,
    )
    if final is None:
        return client_turn_id, False
    return client_turn_id, bool((final.get("data") or final).get("accepted"))

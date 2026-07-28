"""LLM client for OpenAI-compatible chat completions (urllib, no third-party deps)."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("llm_client")


@dataclass
class LLMResponse:
    """Result of a single LLM call."""

    content: str
    prompt_tokens: int
    completion_tokens: int
    elapsed_s: float
    error: str = ""


class LLMClient:
    """Synchronous OpenAI-compatible chat completion client.

    Uses urllib so there are zero third-party dependencies.  Designed to be
    called from a ``ThreadPoolExecutor`` for concurrent QA.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "doubao-seed-2.0-pro",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout_s: float = 120.0,
        max_retries: int = 3,
        retry_backoff_s: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s

    def chat(self, messages: list[dict[str, str]]) -> LLMResponse:
        """Call /v1/chat/completions and return the response.

        Args:
            messages: OpenAI-format message list ``[{role, content}, ...]``.

        Returns:
            LLMResponse with content, token usage, and timing.
        """
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        start = time.monotonic()
        last_err: str = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    raw = resp.read().decode("utf-8")
                    obj = json.loads(raw)
                    content = (
                        obj.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    )
                    usage = obj.get("usage", {})
                    elapsed = time.monotonic() - start
                    return LLMResponse(
                        content=content,
                        prompt_tokens=int(usage.get("prompt_tokens", 0)),
                        completion_tokens=int(usage.get("completion_tokens", 0)),
                        elapsed_s=elapsed,
                    )
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                last_err = f"HTTP {e.code}: {body}"
                logger.warning("LLM call failed: %s (attempt %d/%d)", last_err, attempt, self.max_retries)
                if e.code >= 500 and attempt < self.max_retries:
                    time.sleep(self.retry_backoff_s * attempt)
                else:
                    return LLMResponse(
                        content="",
                        prompt_tokens=0,
                        completion_tokens=0,
                        elapsed_s=time.monotonic() - start,
                        error=last_err,
                    )
            except Exception as e:
                last_err = str(e)
                logger.warning("LLM call error: %s (attempt %d/%d)", last_err, attempt, self.max_retries)
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_s * attempt)
                else:
                    return LLMResponse(
                        content="",
                        prompt_tokens=0,
                        completion_tokens=0,
                        elapsed_s=time.monotonic() - start,
                        error=last_err,
                    )
        return LLMResponse(
            content="",
            prompt_tokens=0,
            completion_tokens=0,
            elapsed_s=time.monotonic() - start,
            error=last_err,
        )

    def judge(self, system_prompt: str, user_prompt: str) -> str:
        """Convenience: send a system+user message, return content string."""
        resp = self.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        if resp.error:
            logger.warning("Judge call error: %s", resp.error)
        return resp.content

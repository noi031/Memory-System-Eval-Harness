#!/usr/bin/env python3
"""动态评测脚本: 仿真 EchoAgent+EchoMem 线上真实效果。

两种模式:
  - generate: LLM 生成背景记忆 -> 注入 EchoMem -> 逐轮 QA 测试端到端召回+TTFT
  - replay: 回放数据集对话, 直接注入 EchoMem -> 新会话 QA 测试跨 session 召回

两种模式的注入阶段都直连 EchoMem (open_session -> add_message -> commit -> poll),
不经 EchoAgent, 不触发 LLM 生成。QA 阶段走 EchoAgent 完整管线 (含 prefill/TTFT)。

所有 EchoAgent API 调用都有容错: 接口不存在 (404) 时回退。
例如 prefill/tick 返回 404 则跳过打字模拟, 直接发消息。

用法见 docs/usage.md
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tqdm import tqdm

from shared.eval_base import EvalConfig, EvalRun, add_echomem_args, add_llm_args, add_eval_args, build_config_from_args
from shared.echomem_client import EchoMemClient
from shared.llm_client import LLMClient

_CONFIGS_DIR = Path(__file__).resolve().parent / "configs"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_evaluator_config(path: str) -> dict[str, Any]:
    """加载评测器配置 YAML。文件不存在或解析失败时直接报错。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"评测器配置文件不存在: {p}")
    import yaml
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# EchoAgent HTTP client (with graceful failure / fallback)
# ---------------------------------------------------------------------------

def _encode_context_path(context_path: str) -> str:
    return quote(context_path, safe="")


class EchoAgentClient:
    """EchoAgent 后端 HTTP 客户端, 所有调用都有容错。"""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token: str = ""
        self.user_uuid: str = ""
        self._context_seq: dict[str, int] = {}

    def _headers(self, json_content: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(self, method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body or {}).encode("utf-8") if body else None
        req = Request(url, data=data, headers=self._headers(), method=method)
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}

    def login(self) -> None:
        """登录获取 JWT token。"""
        body = {"username": self.username, "password": self.password}
        data = json.dumps(body).encode("utf-8")
        req = Request(
            f"{self.base_url}/v1/auth/login",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8", "replace"))
        self.token = result.get("access_token") or ""
        if not self.token:
            # 尝试从 cookie 获取
            for cookie_header in resp.headers.get_all("Set-Cookie") or []:
                if "access_token=" in cookie_header:
                    self.token = cookie_header.split("access_token=")[1].split(";")[0]
        if not self.token:
            raise RuntimeError(f"登录成功但未获取 token: {list(result.keys())}")
        user_info = result.get("user") or {}
        self.user_uuid = user_info.get("id") or ""

    def get_memory_auth_key(self, memory_engine_endpoint: str) -> str:
        """通过 echoagent 插件 credential 接口获取与召回一致的 auth_key。

        echoagent 插件用 TenantRegistry 将 EchoAgent 用户 UUID 映射到 EchoMem
        auth_key。注入必须用同一个 auth_key, 否则记忆存到一个身份下, 召回用
        另一个身份查, 永远找不到。
        """
        body = {"mode": "credential", "userId": self.user_uuid}
        data = json.dumps(body).encode("utf-8")
        req = Request(
            memory_engine_endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8", "replace"))
        auth_key = result.get("result", {}).get("authKey", "")
        if not auth_key:
            raise RuntimeError(f"credential 接口未返回 authKey: {result}")
        return auth_key

    def create_session(self, title: str = "", memory_engine_endpoint: str = "") -> str:
        """创建会话, 尝试启用记忆引擎 (失败则忽略)。"""
        result = self._request("POST", "/v1/sessions", {"title": title or f"test-{uuid.uuid4().hex[:8]}"})
        session_id = result.get("data", result).get("id") or result.get("id", "")
        if session_id and memory_engine_endpoint:
            try:
                self._request("POST", f"/v1/sessions/{session_id}/memory-engine/test",
                              {"endpoint": memory_engine_endpoint})
                self._request("PUT", f"/v1/sessions/{session_id}/memory-engine",
                              {"enabled": True, "endpoint": memory_engine_endpoint})
            except Exception as exc:
                logging.warning("启用记忆引擎失败 (session %s): %s", session_id, exc)
        return session_id

    def prefetch_tick(self, session_id: str, context_path: str, client_turn_id: str,
                      revision: int, draft_text: str) -> dict[str, Any] | None:
        """打字模拟 tick。返回 None 表示接口不存在或失败。"""
        path = f"/v1/sessions/{session_id}/context-paths/{_encode_context_path(context_path)}/prefetch/tick"
        try:
            return self._request("POST", path, {
                "clientTurnId": client_turn_id,
                "revision": revision,
                "draftText": draft_text,
            })
        except HTTPError as e:
            if e.code == 404:
                logging.debug("prefetch/tick 不存在 (404), 跳过打字模拟")
                return None
            raise
        except Exception as e:
            logging.debug("prefetch/tick 失败: %s", e)
            return None

    def prefetch_finalize(self, session_id: str, context_path: str, client_turn_id: str,
                          full_content: str) -> dict[str, Any] | None:
        """完成打字模拟。返回 None 表示接口不存在或失败。"""
        path = f"/v1/sessions/{session_id}/context-paths/{_encode_context_path(context_path)}/prefetch/finalize"
        try:
            return self._request("POST", path, {
                "clientTurnId": client_turn_id,
                "fullContent": full_content,
            })
        except HTTPError as e:
            if e.code == 404:
                logging.debug("prefetch/finalize 不存在 (404), 跳过")
                return None
            raise
        except Exception as e:
            logging.debug("prefetch/finalize 失败: %s", e)
            return None

    def send_message(self, session_id: str, context_path: str, content: str,
                     prefetch_client_turn_id: str = "") -> dict[str, Any]:
        """发送消息, 自动处理 seq 冲突重试。"""
        key = f"{session_id}:{context_path}"
        after_seq = self._context_seq.get(key, 0)
        path = f"/v1/sessions/{session_id}/context-paths/{_encode_context_path(context_path)}/messages"
        result = {}
        for attempt in range(3):
            body: dict[str, Any] = {"content": content, "afterSeq": after_seq}
            if prefetch_client_turn_id:
                body["prefetchClientTurnId"] = prefetch_client_turn_id
            result = self._request("POST", path, body)
            data = result.get("data", result)
            server_seq = data.get("latestContextSeq")
            if isinstance(server_seq, int):
                self._context_seq[key] = server_seq
            if data.get("error") in ("CONTEXT_SEQ_OUTDATED", "SEQ_OUTDATED") and isinstance(server_seq, int):
                after_seq = server_seq
                continue
            return result
        return result

    def stream_reply(self, session_id: str, context_path: str, seq: int,
                     timeout: float = 300) -> dict[str, Any]:
        """读取 SSE 流式回复, 返回 {reply, ttft_ms, done_event}。"""
        url = (f"{self.base_url}/v1/sessions/{session_id}/context-paths/"
               f"{_encode_context_path(context_path)}/streaming?seq={seq}")
        headers = self._headers(json_content=False)
        headers["Accept"] = "text/event-stream"
        headers["Last-Event-ID"] = "-1"
        req = Request(url, headers=headers)

        reply_parts: list[str] = []
        ttft_ms: float | None = None
        send_time = time.monotonic()
        done_event: dict[str, Any] = {}

        with urlopen(req, timeout=timeout) as resp:
            raw_buffer = b""
            text_buffer = ""
            while True:
                chunk = resp.read(4096)
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
                    event_block, text_buffer = text_buffer.split("\n\n", 1)
                    event_type = ""
                    data_lines: list[str] = []
                    for line in event_block.splitlines():
                        if line.startswith("event:"):
                            event_type = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[len("data:"):])
                    event_data = "\n".join(data_lines)
                    if not event_data:
                        continue
                    try:
                        data = json.loads(event_data)
                    except json.JSONDecodeError:
                        continue
                    if event_type in ("create", "append"):
                        if ttft_ms is None:
                            ttft_ms = (time.monotonic() - send_time) * 1000
                        fragment = data.get("fragment") or data.get("content") or ""
                        if isinstance(fragment, dict):
                            reply_parts.append(fragment.get("content") or "")
                        else:
                            reply_parts.append(str(fragment))
                    elif event_type == "done":
                        done_event = data if isinstance(data, dict) else {}
                        if ttft_ms is None:
                            ttft_ms = (time.monotonic() - send_time) * 1000
                        return {"reply": "".join(reply_parts), "ttft_ms": ttft_ms, "done_event": done_event}
                    elif event_type == "error":
                        return {"reply": "".join(reply_parts), "ttft_ms": ttft_ms,
                                "error": str(data), "done_event": {}}
        return {"reply": "".join(reply_parts), "ttft_ms": ttft_ms, "done_event": done_event}

    def get_last_request(self, session_id: str, context_path: str = "/") -> dict[str, Any]:
        try:
            path = (f"/v1/sessions/{session_id}/primary-model/last-request"
                    f"?contextPath={_encode_context_path(context_path)}")
            return self._request("GET", path)
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# Typing simulation
# ---------------------------------------------------------------------------

def simulate_typing(
    client: EchoAgentClient,
    session_id: str,
    context_path: str,
    text: str,
    typing_speed_ms: int = 100,
    jitter_ms: int = 20,
) -> tuple[str, bool]:
    """逐字模拟打字。返回 (client_turn_id, committed)。

    如果 prefetch/tick 接口不存在, 返回 ("", False) 表示跳过。
    """
    client_turn_id = uuid.uuid4().hex[:12]
    committed = False
    for i in range(1, len(text) + 1):
        draft = text[:i]
        tick_result = client.prefetch_tick(session_id, context_path, client_turn_id, i, draft)
        if tick_result is None:
            # 接口不存在, 停止打字模拟
            return "", False
        tick_data = tick_result.get("data", tick_result)
        if not tick_data.get("accepted") and i == 1:
            return client_turn_id, False
        delay = typing_speed_ms + random.randint(-jitter_ms, jitter_ms)
        time.sleep(max(10, delay) / 1000.0)

    finalize_result = client.prefetch_finalize(session_id, context_path, client_turn_id, text)
    if finalize_result is not None:
        fin_data = finalize_result.get("data", finalize_result)
        committed = bool(fin_data.get("accepted"))
        return client_turn_id, committed
    return client_turn_id, False


# ---------------------------------------------------------------------------
# Metrics collection
# ---------------------------------------------------------------------------

def collect_round_metrics(
    round_data: dict[str, Any],
    reply_result: dict[str, Any],
    send_time: float,
    prefetch_committed: bool,
    memory_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reply = reply_result.get("reply") or ""
    ttft = reply_result.get("ttft_ms")
    done = reply_result.get("done_event") or {}
    cached_tokens = int(done.get("cachedTokens") or done.get("cached_tokens") or 0)
    prompt_tokens = int(done.get("promptTokens") or done.get("prompt_tokens") or 0)
    return {
        "round_id": round_data.get("id", ""),
        "query": round_data.get("query", ""),
        "reply": reply,
        "reply_length": len(reply),
        "query_length": len(round_data.get("query", "")),
        "ttft_ms": round(ttft, 1) if ttft is not None else None,
        "cached_tokens": cached_tokens,
        "prompt_tokens": prompt_tokens,
        "prefetch_committed": prefetch_committed,
        "is_new_session": bool(round_data.get("new_session")),
        "is_injection": bool(round_data.get("is_injection")),
        "complexity": round_data.get("complexity", ""),
        "ground_facts": round_data.get("ground_facts", []),
        "error": reply_result.get("error", ""),
        "relevant_memory": json.dumps(memory_items or [], ensure_ascii=False),
    }


def compute_summary(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    """计算汇总指标。"""
    query_rounds = [r for r in rounds if not r.get("is_injection")]
    ttft_values = [r["ttft_ms"] for r in query_rounds if r.get("ttft_ms") is not None]
    cached_values = [r["cached_tokens"] for r in query_rounds if r.get("cached_tokens")]
    prompt_values = [r["prompt_tokens"] for r in query_rounds if r.get("prompt_tokens")]
    reply_lengths = [r["reply_length"] for r in query_rounds]
    errors = [r for r in query_rounds if r.get("error")]
    prefetch_committed = [r for r in query_rounds if r.get("prefetch_committed")]
    return {
        "total_queries": len(query_rounds),
        "total_rounds": len(rounds),
        "errors": len(errors),
        "prefetch_committed_count": len(prefetch_committed),
        "avg_ttft_ms": round(sum(ttft_values) / len(ttft_values), 1) if ttft_values else None,
        "median_ttft_ms": round(sorted(ttft_values)[len(ttft_values) // 2], 1) if ttft_values else None,
        "p95_ttft_ms": round(sorted(ttft_values)[int(len(ttft_values) * 0.95)], 1) if len(ttft_values) >= 2 else None,
        "avg_cached_tokens": round(sum(cached_values) / len(cached_values), 1) if cached_values else None,
        "avg_prompt_tokens": round(sum(prompt_values) / len(prompt_values), 1) if prompt_values else None,
        "avg_reply_length": round(sum(reply_lengths) / len(reply_lengths), 1) if reply_lengths else 0,
    }


# ---------------------------------------------------------------------------
# Quality evaluation
# ---------------------------------------------------------------------------

def _config_driven_evaluate(
    llm: LLMClient,
    evaluator_config: dict[str, Any],
    query: str,
    reply: str,
    ground_facts: list[str],
    recalled_memories: str = "",
) -> dict[str, Any]:
    """使用配置驱动的评测器进行单条回复评估。

    返回包含 score / dimension_scores / dimension_info / quality_reason / strengths /
    weaknesses / hallucination_detected / task_completed 等字段, 与 v2 前端格式一致。
    """
    dimensions = evaluator_config.get("dimensions", [])
    prompt_template = evaluator_config.get("evaluate_prompt", "")
    if not prompt_template:
        return {"error": "evaluate_prompt missing in config", "score": 0}

    # 构建维度评标准则文本
    criteria_lines = []
    for i, dim in enumerate(dimensions, 1):
        name = dim.get("display_name", dim.get("name", ""))
        max_score = dim.get("max_score", 0)
        desc = dim.get("description", "")
        criteria_lines.append(f"{i}. {name} (0-{max_score}分): {desc}")
    dimension_criteria = "\n".join(criteria_lines)

    # 渲染 prompt
    prompt = prompt_template.format(
        query=query,
        reply=reply,
        ground_facts="\n".join(f"- {f}" for f in ground_facts) if ground_facts else "N/A",
        recalled_memories=recalled_memories or "N/A",
        dimension_criteria=dimension_criteria,
    )

    resp = llm.chat([
        {"role": "system", "content": "You are a response quality evaluator. Output only valid JSON."},
        {"role": "user", "content": prompt},
    ])

    if resp.error:
        return {"error": resp.error, "score": 0}

    # 构建 dimension_info (display_name + max_score), 所有结果共用
    dimension_info: dict[str, dict[str, Any]] = {}
    for dim in dimensions:
        dimension_info[dim["name"]] = {
            "display_name": dim.get("display_name", dim.get("name", "")),
            "max_score": dim.get("max_score", 0),
        }

    try:
        json_match = re.search(r"\{[\s\S]*\}", resp.content)
        if json_match:
            raw = json.loads(json_match.group())
            # 提取并钳制维度分数
            dim_scores: dict[str, float] = {}
            raw_scores = raw.get("dimension_scores", {})
            for dim in dimensions:
                name = dim["name"]
                max_score = dim["max_score"]
                score = raw_scores.get(name, raw.get(name, 0))
                try:
                    score = float(score)
                except (TypeError, ValueError):
                    score = 0
                dim_scores[name] = min(max(0, score), max_score)
            # 钳制总分到 0-100
            total_score = raw.get("score", 0)
            try:
                total_score = float(total_score)
            except (TypeError, ValueError):
                total_score = 0
            total_score = min(max(0, total_score), 100)
            return {
                "score": total_score,
                "dimension_scores": dim_scores,
                "dimension_info": dimension_info,
                "quality_reason": raw.get("reason", ""),
                "strengths": raw.get("strengths") or [],
                "weaknesses": raw.get("weaknesses") or [],
                "hallucination_detected": raw.get("hallucination_detected"),
                "task_completed": raw.get("task_completed"),
                "matched_facts": raw.get("matched_facts"),
                "total_facts": raw.get("total_facts"),
                "recall_helped": raw.get("recall_helped"),
            }
    except Exception:
        pass
    return {"error": "parse failed", "raw": resp.content[:500], "score": 0,
            "dimension_info": dimension_info}


# ---------------------------------------------------------------------------
# Generate mode
# ---------------------------------------------------------------------------

def run_generate_mode(args, run: EvalRun, client: EchoAgentClient, llm: LLMClient) -> None:
    """Generate 模式: LLM 生成场景, 测试端到端召回+TTFT。"""
    log = run.logger
    log.info("模式: generate (LLM 生成场景)")

    # 加载评测器配置 (用于质量评估)
    evaluator_config_dict = _load_evaluator_config(args.evaluator_config)
    log.info("评测器配置: %s", args.evaluator_config)

    from memory import dynamic_evaluator

    context_path = "/"
    all_rounds: list[dict[str, Any]] = []
    all_facts: dict[str, str] = {}

    evaluator_config = {
        "mode": "dynamic",
        "num_memories": args.num_memories,
        "llm_config": {
            "model": args.scenario_model,
            "base_url": args.scenario_base_url,
            "api_key": args.scenario_api_key,
        },
    }
    if args.user_simulator_config:
        sim_path = Path(args.user_simulator_config)
        if sim_path.is_file():
            evaluator_config["user_simulator_config_yaml"] = sim_path.read_text(encoding="utf-8")
        else:
            evaluator_config["user_simulator_config"] = args.user_simulator_config
    if args.evaluator_config:
        eval_path = Path(args.evaluator_config)
        if eval_path.is_file():
            evaluator_config["evaluator_config_yaml"] = eval_path.read_text(encoding="utf-8")
        else:
            evaluator_config["evaluator_config"] = args.evaluator_config

    evaluator = dynamic_evaluator.MemoryDynamicEvaluator(evaluator_config)

    # 生成背景记忆
    memories_result = evaluator.generate_background_memories()
    memories = memories_result.get("memories", [])
    log.info("theme=%s memories=%d", evaluator.theme, len(memories))
    for fact in memories:
        fid = fact.get("id", "")
        ftext = fact.get("text", "")
        if fid and ftext:
            all_facts[fid] = ftext

    # 注入背景记忆到 EchoMem (不经 EchoAgent, 不触发 LLM 生成)
    echomem = EchoMemClient(
        base_url=args.echomem_url,
        auth_key=args.echomem_auth_key,
        account=args.account,
        user_id=args.user_id,
        agent_id=args.agent_id,
        workspace=args.workspace,
        timeout_s=60.0,
        max_retries=3,
    )
    inject_session_id = echomem.open_session(title=f"generate-{evaluator.theme}")
    for fact in tqdm(memories, desc="注入记忆", unit="mem"):
        text = fact.get("text", "")
        if text:
            echomem.add_message(inject_session_id, "user", text)
    archive_id = echomem.commit_session(inject_session_id)
    commit_result = echomem.poll_commit(
        inject_session_id, archive_id,
        timeout_s=args.commit_timeout_s,
        poll_interval_s=args.commit_poll_interval_s,
    )
    log.info("注入完成: %s (%.1fs, %d polls)",
             commit_result.status, commit_result.elapsed_s, commit_result.polls)

    session_id = ""
    session_count = 0
    previous_queries: list[str] = []
    previous_replies: list[str] = []
    dataset_queries: list[dict[str, Any]] = []

    for round_idx in tqdm(range(args.num_queries), desc="提问", unit="q"):
        query_result = evaluator.generate_next_query({
            "round_index": round_idx,
            "previous_queries": previous_queries,
            "previous_replies": previous_replies,
            "is_new_session": session_id == "",
        })
        query = query_result.get("query", "")
        if not query:
            continue

        round_data = {
            "id": f"r{round_idx}",
            "query": query,
            "ground_facts": query_result.get("ground_facts", []),
            "new_session": query_result.get("new_session_hint", False),
            "complexity": query_result.get("complexity", "simple"),
            "is_injection": False,
        }
        dataset_queries.append({
            "query": query,
            "ground_facts": query_result.get("ground_facts", []),
            "complexity": query_result.get("complexity", "simple"),
            "reasoning": query_result.get("reasoning", ""),
            "new_session_hint": query_result.get("new_session_hint", False),
        })

        # 是否新开 session
        need_new = not session_id
        if not need_new and round_data.get("new_session"):
            if random.random() < args.new_session_ratio:
                need_new = True
        if need_new:
            session_count += 1
            session_id = client.create_session(
                title=f"test-{evaluator.theme}-s{session_count}",
                memory_engine_endpoint=args.memory_engine_endpoint,
            )
            log.info("  新 session: %s", session_id)

        # 打字模拟 (可能因接口不存在而跳过)
        client_turn_id = ""
        prefetch_committed = False
        memory_items: list[dict[str, Any]] = []
        if len(query) > 2:
            client_turn_id, prefetch_committed = simulate_typing(
                client, session_id, context_path, query,
                args.typing_speed_ms, args.typing_jitter_ms,
            )
            if prefetch_committed:
                log.info("  prefetch committed")
                # 尝试获取 memory items
                fin_result = client.prefetch_finalize(session_id, context_path, client_turn_id, query)
                if fin_result:
                    fin_data = fin_result.get("data", fin_result)
                    memory_items = fin_data.get("memoryItems") or []

        # 发送消息
        send_time = time.monotonic()
        try:
            msg_result = client.send_message(session_id, context_path, query, client_turn_id)
            msg_data = msg_result.get("data", msg_result)
            if msg_data.get("error"):
                raise RuntimeError(f"send failed: {msg_data.get('error')} {msg_data.get('message', '')}")
            messages_list = msg_data.get("messages") or []
            seq = 0
            for m in reversed(messages_list):
                if m.get("status") in ("generating", "completed"):
                    seq = m.get("seq", 0)
                    break
            if not seq and messages_list:
                seq = messages_list[-1].get("seq", 0)
            if not seq:
                seq = msg_data.get("latestContextSeq") or 0
        except Exception as exc:
            log.error("  发送失败: %s", exc)
            all_rounds.append({
                "round_id": round_data["id"], "query": query, "reply": "",
                "reply_length": 0, "query_length": len(query), "ttft_ms": None,
                "cached_tokens": 0, "prompt_tokens": 0,
                "prefetch_committed": prefetch_committed,
                "is_new_session": need_new, "is_injection": False,
                "complexity": round_data.get("complexity", ""),
                "ground_facts": round_data.get("ground_facts", []),
                "error": str(exc),
            })
            continue

        # 读取回复
        try:
            reply_result = client.stream_reply(session_id, context_path, seq)
        except Exception as exc:
            reply_result = {"reply": "", "ttft_ms": None, "error": str(exc)}

        metrics = collect_round_metrics(round_data, reply_result, send_time, prefetch_committed, memory_items)
        metrics["session_id"] = session_id
        all_rounds.append(metrics)
        previous_queries.append(query)
        previous_replies.append(metrics.get("reply", ""))
        log.info("  Q[%d] ttft=%sms cached=%d reply_len=%d",
                 round_idx + 1, metrics["ttft_ms"], metrics["cached_tokens"], metrics["reply_length"])

    _save_results(run, all_rounds, all_facts, llm, config={
        "mode": "generate",
        "num_memories": args.num_memories,
        "num_queries": args.num_queries,
        "echoagent_url": args.echoagent_url,
        "evaluator_config": args.evaluator_config,
        "user_simulator_config": args.user_simulator_config,
    }, evaluator_config=evaluator_config_dict,
       theme=evaluator.theme,
       background_memories=memories,
       dataset_queries=dataset_queries,
       inject_session_id=inject_session_id,
       inject_user_id=args.user_id)


# ---------------------------------------------------------------------------
# Replay mode
# ---------------------------------------------------------------------------

def run_replay_mode(args, run: EvalRun, client: EchoAgentClient, llm: LLMClient) -> None:
    """Replay 模式: 回放数据集对话, 先注入再新会话 QA。"""
    log = run.logger
    log.info("模式: replay (回放数据集: %s)", args.dataset)

    # 加载评测器配置 (用于质量评估)
    evaluator_config_dict = _load_evaluator_config(args.evaluator_config)
    log.info("评测器配置: %s", args.evaluator_config)

    from shared.dataset import load_locomo

    jobs, plans = load_locomo(args.dataset, sample_filter=args.dataset_sample)
    log.info("共 %d 个 sample, %d 个 QA 问题", len(plans), len(jobs))

    if args.dataset_limit > 0:
        jobs = jobs[:args.dataset_limit]
        log.info("限制 QA 数量为 %d", len(jobs))

    context_path = "/"
    all_rounds: list[dict[str, Any]] = []
    all_facts: dict[str, str] = {}

    # EchoMem 客户端 (直接注入记忆, 不经 EchoAgent)
    echomem = EchoMemClient(
        base_url=args.echomem_url,
        auth_key=args.echomem_auth_key,
        account=args.account,
        user_id=args.user_id,
        agent_id=args.agent_id,
        workspace=args.workspace,
        timeout_s=60.0,
        max_retries=3,
    )

    for plan_idx, plan in enumerate(tqdm(plans, desc="回放 sample", unit="sample")):
        sample_id = plan.get("sample_id", f"sample_{plan_idx}")
        events = plan.get("events") or []
        if not events:
            continue

        log.info("[sample %d/%d] %s events=%d", plan_idx + 1, len(plans), sample_id, len(events))

        # 注入对话: 直接注入 EchoMem (不经 EchoAgent, 不触发 LLM 生成)
        inject_session = echomem.open_session(title=f"replay-{sample_id}")
        for ev_idx, event in enumerate(tqdm(events, desc=f"  inject", unit="msg", leave=False)):
            text = event.get("text", "")
            if not text:
                continue
            try:
                echomem.add_message(
                    inject_session, "user", text,
                    created_at=event.get("time", ""),
                )
            except Exception as exc:
                log.warning("  注入 %d 失败: %s", ev_idx, exc)
        archive_id = echomem.commit_session(inject_session)
        commit_result = echomem.poll_commit(
            inject_session, archive_id,
            timeout_s=args.commit_timeout_s,
            poll_interval_s=args.commit_poll_interval_s,
        )
        log.info("  注入完成: %s (%.1fs, %d polls)",
                 commit_result.status, commit_result.elapsed_s, commit_result.polls)

        # 在新 session 中进行 QA (测试跨 session 召回)
        qa_session = client.create_session(
            title=f"replay-qa-{sample_id}",
            memory_engine_endpoint=args.memory_engine_endpoint,
        )

        sample_jobs = [j for j in jobs if j.sample_id == sample_id]
        for job_idx, job in enumerate(tqdm(sample_jobs, desc=f"  QA", unit="q", leave=False)):
            query = job.question
            answer = job.answer
            log.info("  [QA %d/%d] %s", job_idx + 1, len(sample_jobs), query[:60])

            client_turn_id = ""
            prefetch_committed = False
            memory_items: list[dict[str, Any]] = []
            if len(query) > 2:
                client_turn_id, prefetch_committed = simulate_typing(
                    client, qa_session, context_path, query,
                    args.typing_speed_ms, args.typing_jitter_ms,
                )

            send_time = time.monotonic()
            try:
                msg_result = client.send_message(qa_session, context_path, query, client_turn_id)
                msg_data = msg_result.get("data", msg_result)
                messages_list = msg_data.get("messages") or []
                seq = 0
                for m in reversed(messages_list):
                    if m.get("status") in ("generating", "completed"):
                        seq = m.get("seq", 0)
                        break
                if not seq and messages_list:
                    seq = messages_list[-1].get("seq", 0)
                if not seq:
                    seq = msg_data.get("latestContextSeq") or 0
            except Exception as exc:
                log.error("  QA 发送失败: %s", exc)
                all_rounds.append({
                    "round_id": job.question_id, "query": query, "reply": "",
                    "reply_length": 0, "query_length": len(query), "ttft_ms": None,
                    "cached_tokens": 0, "prompt_tokens": 0,
                    "prefetch_committed": prefetch_committed,
                    "is_new_session": True, "is_injection": False,
                    "complexity": job.category, "ground_facts": [answer],
                    "error": str(exc),
                })
                continue

            try:
                reply_result = client.stream_reply(qa_session, context_path, seq)
            except Exception as exc:
                reply_result = {"reply": "", "ttft_ms": None, "error": str(exc)}

            round_data = {
                "id": job.question_id,
                "query": query,
                "ground_facts": [answer],
                "new_session": True,
                "is_injection": False,
                "complexity": job.category,
            }
            metrics = collect_round_metrics(round_data, reply_result, send_time, prefetch_committed, memory_items)
            metrics["session_id"] = qa_session
            metrics["question_id"] = job.question_id
            metrics["gold_answer"] = answer
            all_rounds.append(metrics)
            all_facts[job.question_id] = answer
            log.info("    ttft=%sms reply_len=%d", metrics["ttft_ms"], metrics["reply_length"])

    _save_results(run, all_rounds, all_facts, llm, config={
        "mode": "replay",
        "dataset": args.dataset,
        "dataset_sample": args.dataset_sample,
        "dataset_limit": args.dataset_limit,
        "echoagent_url": args.echoagent_url,
        "echomem_url": args.echomem_url,
        "evaluator_config": args.evaluator_config,
    }, evaluator_config=evaluator_config_dict,
       theme="replay",
       inject_user_id=args.user_id)


# ---------------------------------------------------------------------------
# v2-format builders (dataset + quality report)
# ---------------------------------------------------------------------------

def _build_v2_dataset(
    theme: str,
    background_memories: list[dict],
    dataset_queries: list[dict],
    rounds: list[dict],
    inject_session_id: str = "",
    inject_user_id: str = "",
) -> dict[str, Any]:
    """构建 v2 格式数据集, 含对话 turns (user/assistant)。"""
    # 按 session_id 分组 rounds, 构建 conversation
    sessions: dict[str, list[dict]] = {}
    for r in rounds:
        sid = r.get("session_id", "")
        if sid:
            sessions.setdefault(sid, []).append(r)

    conversation: dict[str, Any] = {}
    for sid, session_rounds in sessions.items():
        turns: list[dict[str, Any]] = []
        for r in session_rounds:
            turns.append({
                "round_id": r.get("round_id", ""),
                "speaker": "user",
                "text": r.get("query", ""),
                "ground_facts": r.get("ground_facts", []),
            })
            turns.append({
                "round_id": r.get("round_id", ""),
                "speaker": "assistant",
                "text": r.get("reply", ""),
                "recalled_memories": _safe_json_loads(r.get("relevant_memory", "")),
                "quality_score": r.get("quality_score"),
            })
        conversation[sid] = {
            "session_id": sid,
            "is_new": session_rounds[0].get("is_new_session", False) if session_rounds else False,
            "turns": turns,
        }

    new_session_count = sum(1 for r in rounds if r.get("is_new_session"))
    quality_scores = [r.get("quality_score") for r in rounds
                      if r.get("quality_score") is not None]
    avg_quality = round(sum(quality_scores) / len(quality_scores), 1) if quality_scores else 0

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "theme": theme,
        "inject_session_id": inject_session_id or None,
        "inject_user_id": inject_user_id or None,
        "background_memories": [
            {"id": m.get("id", ""), "text": m.get("text", ""),
             "source_round": m.get("source_round", -1)}
            for m in background_memories
        ],
        "dataset_queries": dataset_queries,
        "samples": [
            {
                "sample_id": f"dynamic_eval_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "conversation": conversation,
                "metadata": {
                    "total_rounds": len(rounds),
                    "new_session_count": new_session_count,
                    "avg_quality_score": avg_quality,
                },
            }
        ],
    }


def _build_v2_quality_report(
    rounds: list[dict],
    summary: dict[str, Any],
    theme: str = "",
) -> dict[str, Any]:
    """构建 v2 格式质量报告, 每条 result 含 quality_score / dimension_scores / quality_reason 等。"""
    query_rounds = [r for r in rounds if not r.get("is_injection") and r.get("reply")]

    results: list[dict[str, Any]] = []
    for r in query_rounds:
        recalled = _safe_json_loads(r.get("relevant_memory", ""))
        results.append({
            "round_id": r.get("round_id", ""),
            "query": r.get("query", ""),
            "reply": r.get("reply", ""),
            "session_id": r.get("session_id", ""),
            "is_new_session": r.get("is_new_session", False),
            "quality_score": r.get("quality_score"),
            "dimension_scores": r.get("dimension_scores"),
            "dimension_info": r.get("dimension_info"),
            "quality_reason": r.get("quality_reason", ""),
            "strengths": r.get("strengths"),
            "weaknesses": r.get("weaknesses"),
            "hallucination_detected": r.get("hallucination_detected"),
            "task_completed": r.get("task_completed"),
            "ttft_ms": r.get("ttft_ms"),
            "cached_tokens": r.get("cached_tokens", 0),
            "prompt_tokens": r.get("prompt_tokens", 0),
            "recalled_memories_count": len(recalled),
            "ground_facts_count": len(r.get("ground_facts", [])),
            "relevant_memory": recalled,
        })

    quality_scores = [r.get("quality_score") for r in query_rounds
                      if r.get("quality_score") is not None]
    avg_quality = round(sum(quality_scores) / len(quality_scores), 1) if quality_scores else 0

    dim_scores_accum: dict[str, list[float]] = {}
    for r in query_rounds:
        ds = r.get("dimension_scores")
        if ds:
            for k, v in ds.items():
                dim_scores_accum.setdefault(k, []).append(v)
    avg_dim_scores = {k: round(sum(v) / len(v), 1) for k, v in dim_scores_accum.items()} or None

    total_recalled = sum(
        len(_safe_json_loads(r.get("relevant_memory", ""))) for r in query_rounds
    )
    new_session_count = sum(1 for r in query_rounds if r.get("is_new_session"))

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "theme": theme,
        "total_queries": len(query_rounds),
        "avg_ttft_ms": summary.get("avg_ttft_ms"),
        "avg_cached_tokens": summary.get("avg_cached_tokens"),
        "new_session_count": new_session_count,
        "summary": {
            "avg_quality_score": avg_quality,
            "avg_dimension_scores": avg_dim_scores,
            "total_recalled_memories": total_recalled,
        },
        "results": results,
    }


def _safe_json_loads(s: str) -> list:
    """安全解析 JSON 字符串, 失败返回空列表。"""
    if not s:
        return []
    try:
        val = json.loads(s)
        return val if isinstance(val, list) else []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------

def _save_results(run: EvalRun, all_rounds: list[dict], all_facts: dict[str, str],
                  llm: LLMClient, config: dict[str, Any],
                  evaluator_config: dict[str, Any],
                  theme: str = "",
                  background_memories: list[dict] | None = None,
                  dataset_queries: list[dict] | None = None,
                  inject_session_id: str = "",
                  inject_user_id: str = "") -> None:
    log = run.logger
    summary = compute_summary(all_rounds)

    # 保存 JSON 结果 (raw metrics)
    results = {
        "testId": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "config": config,
        "summary": summary,
        "facts": all_facts,
        "rounds": all_rounds,
    }
    results_path = run.result_dir / "dynamic_results.json"
    results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # 保存 CSV
    csv_path = run.result_dir / "dynamic_results.csv"
    fieldnames = [
        "round_id", "session_id", "question_id", "query", "reply", "gold_answer",
        "reply_length", "query_length", "ttft_ms", "cached_tokens", "prompt_tokens",
        "prefetch_committed", "is_new_session", "is_injection", "complexity",
        "error", "relevant_memory",
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rounds)

    # 质量评估: 逐条评估并合并到 round 对象
    query_rounds = [r for r in all_rounds if not r.get("is_injection") and r.get("reply")]
    if query_rounds and all_facts:
        log.info("使用配置驱动评测器评估 %d 条回复...", len(query_rounds))
        for r in tqdm(query_rounds, desc="质量评估", unit="q"):
            ground_ids = r.get("ground_facts") or []
            ground_texts = [all_facts.get(fid, fid) for fid in ground_ids]
            result = _config_driven_evaluate(
                llm, evaluator_config,
                r.get("query", ""), r.get("reply", ""),
                ground_texts,
                r.get("relevant_memory", ""),
            )
            r["quality_score"] = result.get("score")
            r["dimension_scores"] = result.get("dimension_scores")
            r["dimension_info"] = result.get("dimension_info")
            r["quality_reason"] = result.get("quality_reason", "")
            r["strengths"] = result.get("strengths")
            r["weaknesses"] = result.get("weaknesses")
            r["hallucination_detected"] = result.get("hallucination_detected")
            r["task_completed"] = result.get("task_completed")

        # 保存 v2 格式质量报告
        v2_report = _build_v2_quality_report(all_rounds, summary, theme)
        report_path = run.result_dir / "quality_report.json"
        report_path.write_text(json.dumps(v2_report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        log.info("质量评估: avg_quality_score=%s", v2_report["summary"]["avg_quality_score"])

    # 保存 v2 格式数据集
    v2_dataset = _build_v2_dataset(
        theme=theme,
        background_memories=background_memories or [],
        dataset_queries=dataset_queries or [],
        rounds=all_rounds,
        inject_session_id=inject_session_id,
        inject_user_id=inject_user_id,
    )
    dataset_path = run.result_dir / "dataset.json"
    dataset_path.write_text(json.dumps(v2_dataset, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log.info("数据集已保存: %s", dataset_path)

    # 收集 EchoMem 日志
    run.collect_echomem_logs()

    # 保存 summary
    quality_scores = [r.get("quality_score") for r in query_rounds
                      if r.get("quality_score") is not None]
    avg_quality = round(sum(quality_scores) / len(quality_scores), 1) if quality_scores else None
    full_summary = {
        "benchmark": "dynamic",
        "mode": config.get("mode", ""),
        **summary,
        "quality_overall_score": avg_quality,
    }
    run.save_summary(full_summary)

    log.info("=" * 60)
    log.info("评测完成! 结果目录: %s", run.result_dir)
    log.info("avg_ttft=%sms  avg_cached=%s  queries=%d  errors=%d",
             summary.get("avg_ttft_ms"), summary.get("avg_cached_tokens"),
             summary.get("total_queries"), summary.get("errors"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="动态评测: 仿真 EchoAgent+EchoMem 线上效果")
    # EchoAgent
    g = parser.add_argument_group("EchoAgent")
    g.add_argument("--echoagent-url", default=os.environ.get("ECHOAGENT_URL", "http://127.0.0.1:31020"))
    g.add_argument("--username", default=os.environ.get("ECHOAGENT_TEST_USERNAME", "test_user"))
    g.add_argument("--password", default=os.environ.get("ECHOAGENT_TEST_PASSWORD", ""))
    g.add_argument("--memory-engine-endpoint",
                   default=os.environ.get("GLOBAL_MEMORY_ENGINE_ENDPOINT", "http://127.0.0.1:31030"))

    # 模式选择
    g = parser.add_argument_group("模式")
    g.add_argument("--dataset", default="", help="数据集路径 (指定则进入 replay 模式; 不指定则 generate 模式)")
    g.add_argument("--dataset-sample", default="all")
    g.add_argument("--dataset-limit", type=int, default=0)

    # 评测器配置 (两种模式共用)
    g = parser.add_argument_group("评测器配置")
    g.add_argument("--evaluator-config",
                   default=str(_CONFIGS_DIR / "evaluator_template.yaml"),
                   help="评测器配置 YAML，路径相对于 run_eval.py (默认 configs/evaluator_template.yaml)")

    # Generate 模式参数
    g = parser.add_argument_group("Generate 模式")
    g.add_argument("--num-memories", type=int, default=5, help="生成的背景记忆数")
    g.add_argument("--num-queries", type=int, default=10, help="生成的提问数")
    g.add_argument("--new-session-ratio", type=float, default=0.3)
    g.add_argument("--typing-speed-ms", type=int, default=200)
    g.add_argument("--typing-jitter-ms", type=int, default=20)
    g.add_argument("--user-simulator-config",
                   default=str(_CONFIGS_DIR / "user_simulator_default.yaml"),
                   help="用户模拟器配置，路径相对于 run_eval.py (默认 configs/user_simulator_default.yaml)")

    # LLM (用于场景生成和质量评估)
    g = parser.add_argument_group("LLM")
    g.add_argument("--scenario-model", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_MODEL", "deepseek-v4-flash"))
    g.add_argument("--scenario-base-url", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_BASE_URL", ""))
    g.add_argument("--scenario-api-key", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_API_KEY", ""))
    g.add_argument("--llm-base-url", default=os.environ.get("LLM_BASE_URL", ""))
    g.add_argument("--llm-model", default=os.environ.get("LLM_MODEL", "doubao-seed-2.0-pro"))
    g.add_argument("--llm-api-key", default=os.environ.get("LLM_API_KEY", ""))

    # EchoMem 日志
    add_echomem_args(parser)

    # EchoMem 注入参数
    g = parser.add_argument_group("EchoMem 注入")
    g.add_argument("--commit-timeout-s", type=float, default=0.0, help="注入 commit 轮询超时 (秒)，0 表示无限等待")
    g.add_argument("--commit-poll-interval-s", type=float, default=2.0, help="注入 commit 轮询间隔 (秒)")

    # 输出
    g = parser.add_argument_group("输出")
    g.add_argument("--out-dir", default="", help="结果目录 (默认 dynamic/results/<timestamp>)")

    return parser


def main() -> None:
    args = build_parser().parse_args()

    # base_url 互补: 一个有另一个没有时, 没有的跟有的相同
    if args.scenario_base_url and not args.llm_base_url:
        args.llm_base_url = args.scenario_base_url
    elif args.llm_base_url and not args.scenario_base_url:
        args.scenario_base_url = args.llm_base_url

    # api_key 互补
    if args.scenario_api_key and not args.llm_api_key:
        args.llm_api_key = args.scenario_api_key
    elif args.llm_api_key and not args.scenario_api_key:
        args.scenario_api_key = args.llm_api_key

    if not args.password:
        # 尝试从 EchoAgent data/.env 获取
        env_path = _PROJECT_ROOT.parent / "EchoAgent" / "data" / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("JWT_SECRET="):
                    args.password = line.split("=", 1)[1].strip()
                    break
    if not args.password:
        print("错误: 需要 --password 或设置 ECHOAGENT_TEST_PASSWORD", file=sys.stderr)
        sys.exit(1)

    # 登录 EchoAgent (在创建 EvalRun 之前, 因为 auth_key 解析依赖登录)
    client = EchoAgentClient(args.echoagent_url, args.username, args.password)
    print(f"登录 EchoAgent ({args.echoagent_url})...")
    try:
        client.login()
    except Exception as e:
        print(f"登录失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 动态评测的 QA 经 EchoAgent -> echoagent 插件, 插件固定用 agent_id="echoagent"。
    # 注入也需用相同的 agent_id, 否则 EchoMem 按 agent_id 过滤时召回不到注入的记忆。
    if not args.agent_id or args.agent_id == "default":
        args.agent_id = "echoagent"

    # 注入直连 EchoMem, 必须用与召回相同的 auth_key。
    # 通过 echoagent 插件 credential 接口解析, 保证身份一致。
    if not args.echomem_auth_key:
        try:
            args.echomem_auth_key = client.get_memory_auth_key(args.memory_engine_endpoint)
        except Exception as e:
            print(f"警告: 解析 auth_key 失败: {e} - 注入将不携带身份, 召回可能无法匹配", file=sys.stderr)

    # 创建评测运行 (在 auth_key 解析后, 确保保存的配置包含正确的 auth_key)
    results_root = Path(args.out_dir) if args.out_dir else Path(__file__).parent / "results"
    config = EvalConfig(
        echomem_url=args.echomem_url,
        echomem_auth_key=args.echomem_auth_key,
        echomem_log_dir=args.echomem_log_dir,
        llm_base_url=args.llm_base_url,
        llm_model=args.llm_model,
        llm_api_key=args.llm_api_key,
    )
    run = EvalRun(
        benchmark_name="dynamic",
        results_root=results_root,
        config=config,
        echomem_log_dir=args.echomem_log_dir,
    )
    log = run.logger

    log.info("登录成功 (user=%s, uuid=%s)", args.username, client.user_uuid)
    log.info("agent_id=%s, auth_key=%s", args.agent_id, "已设置" if args.echomem_auth_key else "未设置")

    # 创建 LLM 客户端 (用于质量评估)
    llm = LLMClient(
        base_url=args.llm_base_url or args.scenario_base_url,
        api_key=args.llm_api_key or args.scenario_api_key,
        model=args.llm_model,
        temperature=0.3,
        max_tokens=4096,
        timeout_s=120.0,
    )

    # 选择模式
    if args.dataset:
        run_replay_mode(args, run, client, llm)
    else:
        run_generate_mode(args, run, client, llm)


if __name__ == "__main__":
    main()

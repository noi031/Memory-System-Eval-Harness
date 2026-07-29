#!/usr/bin/env python3
"""动态评测脚本: 通过 agent 插件评测不同 agent 的记忆系统效果。

两种模式:
  - generate: LLM 生成背景记忆 -> 注入 -> 逐轮 QA 测试端到端召回+TTFT
  - replay: 回放数据集对话, 注入背景记忆 -> 新会话 QA 测试跨 session 召回

记忆注入和 QA 的具体行为由 agent 插件决定 (--agent-plugin)。
默认 echo_agent 插件: 注入直连 EchoMem, QA 走 EchoAgent 完整管线 (含 prefill/TTFT)。
bare_llm 插件: 无记忆系统, 记忆拼入 system prompt, 仅作基线对比。

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tqdm import tqdm

from shared.eval_base import EvalConfig, EvalRun, add_agent_plugin_args
from shared.llm_client import LLMClient
from agents import AgentPlugin, load_agent_plugin

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
def run_generate_mode(args, run: EvalRun, plugin: AgentPlugin, llm: LLMClient) -> None:
    """Generate 模式: LLM 生成场景, 测试端到端召回+TTFT。"""
    log = run.logger
    log.info("模式: generate (LLM 生成场景)")

    # 加载评测器配置 (用于质量评估)
    evaluator_config_dict = _load_evaluator_config(args.evaluator_config)
    log.info("评测器配置: %s", args.evaluator_config)

    from dynamic import dynamic_evaluator

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
            log.info("  [%s] %s", fid, ftext[:120])

    # 注入背景记忆 (通过 agent 插件)
    inject_session_id = plugin.inject_memories(memories)

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
            session_id = plugin.create_session(
                title=f"test-{evaluator.theme}-s{session_count}",
            )
            log.info("  新 session: %s", session_id)

        # 打字模拟 (agent 插件内部处理 prefill)
        prefetch_committed = False
        memory_items: list[dict[str, Any]] = []
        if plugin.supports_typing_simulation and len(query) > 2:
            typing_result = plugin.simulate_typing(
                session_id, context_path, query,
                args.typing_speed_ms, args.typing_jitter_ms,
            )
            if typing_result is not None:
                prefetch_committed = typing_result.committed
                memory_items = typing_result.memory_items
                if prefetch_committed:
                    log.info("  prefetch committed")

        # 发送消息并获取回复
        response = plugin.send_message(session_id, query, context_path)

        metrics = {
            "round_id": round_data["id"],
            "query": query,
            "reply": response.text,
            "reply_length": len(response.text),
            "query_length": len(query),
            "ttft_ms": response.ttft_ms,
            "cached_tokens": response.cached_tokens,
            "prompt_tokens": response.prompt_tokens,
            "prefetch_committed": prefetch_committed,
            "is_new_session": need_new,
            "is_injection": False,
            "complexity": round_data.get("complexity", ""),
            "ground_facts": round_data.get("ground_facts", []),
            "error": response.error or "",
            "relevant_memory": json.dumps(memory_items, ensure_ascii=False),
        }
        metrics["session_id"] = session_id
        all_rounds.append(metrics)

        if response.error:
            log.error("  发送失败: %s", response.error)
            continue

        previous_queries.append(query)
        previous_replies.append(metrics.get("reply", ""))
        log.info("  Q[%d] %s", round_idx + 1, query[:80])
        log.info("    ttft=%sms cached=%d reply_len=%d",
                 metrics["ttft_ms"], metrics["cached_tokens"], metrics["reply_length"])
        log.info("    回复: %s", metrics["reply"][:200])

    _save_results(run, all_rounds, all_facts, llm, config={
        "mode": "generate",
        "num_memories": args.num_memories,
        "num_queries": args.num_queries,
        "evaluator_config": args.evaluator_config,
        "user_simulator_config": args.user_simulator_config,
    }, evaluator_config=evaluator_config_dict,
       theme=evaluator.theme,
       background_memories=memories,
       dataset_queries=dataset_queries,
       inject_session_id=inject_session_id,
       inject_user_id=getattr(args, "user_id", "default"))


# ---------------------------------------------------------------------------
# Replay mode
# ---------------------------------------------------------------------------


def run_replay_mode(args, run: EvalRun, plugin: AgentPlugin, llm: LLMClient) -> None:
    """Replay 模式: 回放 generate 模式导出的数据集, 先注入背景记忆再新会话 QA。"""
    log = run.logger
    log.info("模式: replay (回放数据集: %s)", args.dataset)

    # 加载评测器配置 (用于质量评估)
    evaluator_config_dict = _load_evaluator_config(args.evaluator_config)
    log.info("评测器配置: %s", args.evaluator_config)

    # 加载 generate 模式导出的数据集
    with open(args.dataset, encoding="utf-8") as f:
        dataset = json.load(f)

    background_memories = dataset.get("background_memories", [])
    dataset_queries = dataset.get("dataset_queries", [])
    log.info("共 %d 条背景记忆, %d 个 QA 问题", len(background_memories), len(dataset_queries))
    for m in background_memories:
        log.info("  [%s] %s", m.get("id", "?"), m.get("text", "")[:120])

    if args.dataset_limit > 0:
        dataset_queries = dataset_queries[:args.dataset_limit]
        log.info("限制 QA 数量为 %d", len(dataset_queries))

    # 构建 fact_id -> text 查找表
    fact_lookup = {m.get("id", ""): m.get("text", "") for m in background_memories}

    context_path = "/"
    all_rounds: list[dict[str, Any]] = []
    all_facts: dict[str, str] = {}

    # 注入背景记忆 (通过 agent 插件)
    inject_session_id = dataset.get("inject_session_id") or ""
    inject_session = ""
    if background_memories:
        try:
            inject_session = plugin.inject_memories(
                background_memories, session_id=inject_session_id,
            )
        except RuntimeError as exc:
            log.error("  记忆注入失败: %s", exc)

    # 在新 session 中进行 QA (测试跨 session 召回)
    qa_session = plugin.create_session(
        title=f"replay-qa-{inject_session or 'dynamic_eval'}",
    )

    for job_idx, q in enumerate(tqdm(dataset_queries, desc="提问", unit="q")):
        query = q.get("query", "")
        ground_facts = q.get("ground_facts", [])
        answer = "; ".join(fact_lookup.get(fid, fid) for fid in ground_facts)
        complexity = q.get("complexity", "simple")
        question_id = f"q{job_idx}"
        log.info("  [QA %d/%d] %s", job_idx + 1, len(dataset_queries), query[:60])

        # 打字模拟
        prefetch_committed = False
        memory_items: list[dict[str, Any]] = []
        if plugin.supports_typing_simulation and len(query) > 2:
            typing_result = plugin.simulate_typing(
                qa_session, context_path, query,
                args.typing_speed_ms, args.typing_jitter_ms,
            )
            if typing_result is not None:
                prefetch_committed = typing_result.committed
                memory_items = typing_result.memory_items

        # 发送消息并获取回复
        response = plugin.send_message(qa_session, query, context_path)

        metrics = {
            "round_id": question_id,
            "query": query,
            "reply": response.text,
            "reply_length": len(response.text),
            "query_length": len(query),
            "ttft_ms": response.ttft_ms,
            "cached_tokens": response.cached_tokens,
            "prompt_tokens": response.prompt_tokens,
            "prefetch_committed": prefetch_committed,
            "is_new_session": True,
            "is_injection": False,
            "complexity": complexity,
            "ground_facts": ground_facts,
            "error": response.error or "",
            "relevant_memory": json.dumps(memory_items, ensure_ascii=False),
        }
        metrics["session_id"] = qa_session
        metrics["question_id"] = question_id
        metrics["gold_answer"] = answer
        all_rounds.append(metrics)
        all_facts[question_id] = answer

        if response.error:
            log.error("  QA 发送失败: %s", response.error)
            continue

        log.info("    ttft=%sms reply_len=%d", metrics["ttft_ms"], metrics["reply_length"])
        log.info("    回复: %s", metrics["reply"][:200])

    _save_results(run, all_rounds, all_facts, llm, config={
        "mode": "replay",
        "dataset": args.dataset,
        "dataset_limit": args.dataset_limit,
        "evaluator_config": args.evaluator_config,
    }, evaluator_config=evaluator_config_dict,
       theme=dataset.get("theme", "replay"),
       background_memories=background_memories,
       dataset_queries=dataset_queries,
       inject_session_id=inject_session if background_memories else "",
       inject_user_id=getattr(args, "user_id", "default"))


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
    parser = argparse.ArgumentParser(description="动态评测: 支持 agent 插件, 默认 echo_agent")
    # Agent 插件
    g = parser.add_argument_group("Agent 插件")
    g.add_argument("--agent-plugin", default=os.environ.get("AGENT_PLUGIN", "echo_agent"),
                   help="agent 插件名称 (echo_agent / baseline_mem / bare_llm)")
    add_agent_plugin_args(parser, default_plugin="echo_agent")

    # 模式选择
    g = parser.add_argument_group("模式")
    g.add_argument("--dataset", default="", help="数据集路径 (指定则进入 replay 模式; 不指定则 generate 模式)")
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

    # LLM (用于场景生成, 仅 generate 模式)
    g = parser.add_argument_group("LLM (场景生成)")
    g.add_argument("--scenario-model", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_MODEL", "deepseek-v4-flash"))
    g.add_argument("--scenario-base-url", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_BASE_URL", ""))
    g.add_argument("--scenario-api-key", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_API_KEY", ""))

    # LLM (用于质量评估)
    g = parser.add_argument_group("LLM (质量评估)")
    g.add_argument("--evaluator-model", default=os.environ.get("ECHOAGENT_TEST_EVALUATOR_MODEL", "doubao-seed-2.0-pro"),
                   help="质量评估 LLM 模型名")
    g.add_argument("--evaluator-base-url", default=os.environ.get("ECHOAGENT_TEST_EVALUATOR_BASE_URL", ""),
                   help="质量评估 LLM base URL")
    g.add_argument("--evaluator-api-key", default=os.environ.get("ECHOAGENT_TEST_EVALUATOR_API_KEY", ""),
                   help="质量评估 LLM API key")

    # 输出
    g = parser.add_argument_group("输出")
    g.add_argument("--out-dir", default="", help="结果目录 (默认 dynamic/results/<timestamp>)")
    g.add_argument("--echomem-log-dir", default="", help="EchoMem log directory for log collection")

    return parser


def main() -> None:
    args = build_parser().parse_args()

    # evaluator / scenario 的 base_url / api_key 互补:
    # 一个有值另一个为空时, 空的自动复制有值的。两者都设置了则各自使用。
    if args.evaluator_base_url and not args.scenario_base_url:
        args.scenario_base_url = args.evaluator_base_url
    elif args.scenario_base_url and not args.evaluator_base_url:
        args.evaluator_base_url = args.scenario_base_url
    if args.evaluator_api_key and not args.scenario_api_key:
        args.scenario_api_key = args.evaluator_api_key
    elif args.scenario_api_key and not args.evaluator_api_key:
        args.evaluator_api_key = args.scenario_api_key

    # echo_agent 插件需要密码登录 EchoAgent
    if args.agent_plugin == "echo_agent" and not getattr(args, "password", ""):
        env_path = _PROJECT_ROOT.parent / "EchoAgent" / "data" / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("JWT_SECRET="):
                    args.password = line.split("=", 1)[1].strip()
                    break
    if args.agent_plugin == "echo_agent" and not getattr(args, "password", ""):
        print("错误: 需要 --password 或设置 ECHOAGENT_TEST_PASSWORD", file=sys.stderr)
        sys.exit(1)

    # 动态评测的 QA 经 EchoAgent -> echoagent 插件, 插件固定用 agent_id="echoagent"。
    # 注入也需用相同的 agent_id, 否则 EchoMem 按 agent_id 过滤时召回不到注入的记忆。
    if not getattr(args, "agent_id", "") or getattr(args, "agent_id", "") == "default":
        args.agent_id = "echoagent"

    # 加载 agent 插件 (load_agent_plugin 内部调 setup, 完成登录、auth_key 解析等)
    config_dict = {k: v for k, v in vars(args).items()}
    plugin = load_agent_plugin(args.agent_plugin, config_dict)
    # 插件 setup 可能更新 config_dict (如解析 auth_key), 同步回 args
    args.echomem_auth_key = config_dict.get("echomem_auth_key", "")

    # 创建评测运行
    results_root = Path(args.out_dir) if args.out_dir else Path(__file__).parent / "results"
    config = EvalConfig(
        echomem_log_dir=getattr(args, "echomem_log_dir", ""),
    )
    run = EvalRun(
        benchmark_name="dynamic",
        results_root=results_root,
        config=config,
        echomem_log_dir=getattr(args, "echomem_log_dir", ""),
        run_args={k: v for k, v in vars(args).items() if not k.startswith("_")},
    )
    log = run.logger
    log.info("agent_plugin=%s", args.agent_plugin)

    # 创建 LLM 客户端 (用于质量评估)
    llm = LLMClient(
        base_url=args.evaluator_base_url,
        api_key=args.evaluator_api_key,
        model=args.evaluator_model,
        temperature=0.3,
        max_tokens=4096,
        timeout_s=120.0,
    )

    # 选择模式
    if args.dataset:
        run_replay_mode(args, run, plugin, llm)
    else:
        run_generate_mode(args, run, plugin, llm)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)

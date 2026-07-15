"""Dynamic evaluator for memory recall testing.

This module provides the MemoryDynamicEvaluator class for generating
background memories and user queries in both static (dataset-based) and
dynamic (LLM-generated) modes.

Usage:
    from memory.dynamic_evaluator import MemoryDynamicEvaluator, get_evaluator, create_evaluator

    # Create a new evaluator
    evaluator = MemoryDynamicEvaluator(config)
    memories = evaluator.generate_background_memories()

    # Or use the global registry
    evaluator_id = create_evaluator(config)
    evaluator = get_evaluator(evaluator_id)
    query = evaluator.generate_next_query(context)
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any


# Import LLM client
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from memory import llm


# ---------------------------------------------------------------------------
# Global evaluator registry
# ---------------------------------------------------------------------------

_EVALUATOR_LOCK = threading.Lock()
_EVALUATORS: dict[str, "MemoryDynamicEvaluator"] = {}
_EVALUATOR_TTL_SECONDS = 3600  # 1 hour


def create_evaluator(config: dict[str, Any]) -> str:
    """Create a new evaluator and return its ID."""
    evaluator_id = f"eval-{uuid.uuid4().hex[:12]}"
    evaluator = MemoryDynamicEvaluator(config)
    with _EVALUATOR_LOCK:
        _EVALUATORS[evaluator_id] = evaluator
    return evaluator_id


def get_evaluator(evaluator_id: str) -> "MemoryDynamicEvaluator | None":
    """Get an evaluator by ID."""
    with _EVALUATOR_LOCK:
        return _EVALUATORS.get(evaluator_id)


def remove_evaluator(evaluator_id: str) -> bool:
    """Remove an evaluator by ID."""
    with _EVALUATOR_LOCK:
        if evaluator_id in _EVALUATORS:
            del _EVALUATORS[evaluator_id]
            return True
        return False


def list_evaluators() -> list[dict[str, Any]]:
    """List all active evaluators."""
    with _EVALUATOR_LOCK:
        return [
            {"id": eid, "created_at": ev.created_at, "mode": ev.mode, "theme": ev.theme}
            for eid, ev in _EVALUATORS.items()
        ]


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

BACKGROUND_MEMORIES_PROMPT = """You are a test scenario generator for a memory-augmented AI assistant.

Generate {num_memories} distinct factual statements that a user might naturally tell an AI assistant during conversations.

Requirements:
- Each fact should be a complete, standalone statement
- Facts should be realistic daily-life information (preferences, events, plans, contacts, etc.)
- Vary the length: some short (5-15 words), some medium (15-30 words), some long (30-50 words)
- Theme: {theme}
- Each fact should be memorable and testable

Output ONLY a JSON array of objects with this structure:
[
  {{"id": "f1", "text": "The fact statement here", "length_hint": "short"}},
  {{"id": "f2", "text": "Another fact...", "length_hint": "medium"}},
  ...
]

The "length_hint" should be one of: "short", "medium", "long".
The "id" should be "f1", "f2", "f3", etc.
"""

NEXT_QUERY_PROMPT = """You are a test query generator for evaluating a memory-augmented AI assistant.

Given:
1. Background facts that the user has shared (some in this session, some in previous sessions)
2. Conversation history
3. Current context

Generate the next user query that would naturally test the assistant's memory recall ability.

## Background Facts (for reference only - DO NOT include these texts directly in query)
{facts_text}

## Conversation History
{history_text}

## Current Context
- Round: {round_index}
- Is new session: {is_new_session}

## Requirements

Generate a query that:
1. Naturally continues the conversation OR starts a new topic (if new session)
2. Requires recalling one or more background facts to answer correctly
3. Is phrased as a NATURAL user question that IMPLIES or REFERENCES the fact, but does NOT quote it directly
4. Varies in complexity: simple recall, multi-fact synthesis, or temporal reasoning

## IMPORTANT: Query Formulation Rules

- DO NOT include the exact fact text in your query
- DO use vague references, pronouns, or hints that require the assistant to recall the specific details
- Examples of BAD queries (too explicit):
  - "我记得我喜欢早上7点去跑步，那时候一般有几个人？" (直接引用了事实文本)
  - "我家的猫小白是英短，它几岁了？" (直接说出了猫的名字和品种)
- Examples of GOOD queries (require memory recall):
  - "我平时早上锻炼一般几点出发来着？" (需要召回具体的锻炼时间)
  - "我家那只猫今年多大了？" (需要召回猫的具体年龄)
  - "下周那个重要的会是什么时候？" (需要召回会议时间)

Output ONLY a JSON object:
{{
  "query": "The user's question here",
  "ground_facts": ["f1", "f3"],
  "complexity": "simple" | "medium" | "complex",
  "reasoning": "Brief explanation of what this tests",
  "new_session_hint": true | false
}}

- ground_facts: IDs of facts needed to answer correctly
- complexity:
  - "simple": Direct recall of one fact
  - "medium": Recall of 2-3 facts or simple synthesis
  - "complex": Multi-fact synthesis, temporal reasoning, or cross-session recall
- new_session_hint: Whether this query suggests opening a new session next
"""


# ---------------------------------------------------------------------------
# Theme pools
# ---------------------------------------------------------------------------

THEME_POOL = [
    "职场与项目管理",
    "旅行规划与出行",
    "健康管理与就医",
    "学习与考试备考",
    "社交活动与聚会",
    "购物与消费决策",
    "烹饪与饮食计划",
    "运动与健身安排",
    "宠物养护与训练",
    "家庭财务与投资",
    "子女教育与成长",
    "房屋维修与改造",
    "汽车保养与驾驶",
    "园艺与种植",
    "摄影与创作",
    "志愿活动与社区服务",
]


# ---------------------------------------------------------------------------
# MemoryDynamicEvaluator class
# ---------------------------------------------------------------------------

class MemoryDynamicEvaluator:
    """Dynamic evaluator for generating test scenarios and queries."""

    def __init__(self, config: dict[str, Any]):
        """Initialize the evaluator.

        Args:
            config: Configuration dict with keys:
                - mode: "static" or "dynamic"
                - dataset_path: Path to dataset file (for static mode)
                - num_memories: Number of memories to generate (for dynamic mode)
                - theme: Theme for generated memories (for dynamic mode)
                - custom_scenario: Custom scenario text (skip LLM generation if provided)
                - llm_config: LLM configuration (model, base_url, api_key)
        """
        self.config = config
        self.mode = config.get("mode", "dynamic")
        self.theme = config.get("theme", "")
        self.custom_scenario = config.get("custom_scenario", "")
        self.created_at = time.time()

        self.background_memories: list[dict[str, Any]] = []
        self.conversation_history: list[dict[str, Any]] = []
        self.generated_queries: list[dict[str, Any]] = []
        self._memories_generated = False
        
        # For static mode: store dataset queries
        self.dataset_queries: list[dict[str, Any]] = []
        self._dataset_loaded = False

        # LLM config
        llm_config = config.get("llm_config", {})
        self.model = llm_config.get("model", "deepseek-v4-flash")
        self.base_url = llm_config.get("base_url") or None
        self.api_key = llm_config.get("api_key") or None
        
        # Debug log
        print(f"[DynamicEvaluator] Config received: mode={self.mode}, num_memories={config.get('num_memories')}, theme={self.theme}")
        print(f"[DynamicEvaluator] LLM config: model={self.model}, base_url={self.base_url}, api_key={'***' if self.api_key else 'None'}")

    def generate_background_memories(self) -> dict[str, Any]:
        """Generate or load background memories.

        Returns:
            {
                "memories": [...],
                "mode": "static" | "dynamic",
                "theme": str,
            }
        """
        if self._memories_generated:
            return {
                "memories": self.background_memories,
                "mode": self.mode,
                "theme": self.theme,
            }

        # Priority: custom_scenario > static dataset > dynamic generation
        if self.custom_scenario:
            # Custom scenario takes precedence regardless of mode
            self.background_memories = self._generate_memories_from_custom_scenario()
        elif self.mode == "static":
            self.background_memories = self._load_static_memories()
        else:
            self.background_memories = self._generate_dynamic_memories()

        self._memories_generated = True
        return {
            "memories": self.background_memories,
            "mode": self.mode,
            "theme": self.theme,
        }

    def _load_static_memories(self) -> list[dict[str, Any]]:
        """Load memories from a dataset file."""
        dataset_path = self.config.get("dataset_path", "")
        if not dataset_path:
            return self._generate_fallback_memories()

        path = Path(dataset_path).expanduser()
        if not path.exists():
            return self._generate_fallback_memories()

        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return self._generate_fallback_memories()

        memories = []
        queries = []

        # Handle our exported format with background_memories and samples
        if isinstance(data, dict) and "background_memories" in data:
            # Our exported format
            for mem in data.get("background_memories", []):
                text = mem.get("text", "")
                if text and len(text) > 2:
                    memories.append({
                        "id": mem.get("id", f"f{len(memories)+1}"),
                        "text": text,
                        "source": mem.get("source", "background_memories"),
                    })
            
            # Extract queries from samples
            samples = data.get("samples", [])
            for sample in samples[:1]:  # Use first sample
                conversation = sample.get("conversation", {})
                for session_id, session_data in conversation.items():
                    if isinstance(session_data, dict):
                        turns = session_data.get("turns", [])
                        for turn in turns:
                            if isinstance(turn, dict):
                                speaker = turn.get("speaker", "")
                                if speaker == "user":
                                    text = turn.get("text", "")
                                    if text:
                                        # Find matching ground facts from assistant turn
                                        ground_facts = []
                                        queries.append({
                                            "query": text,
                                            "ground_facts": ground_facts,  # Will be populated from dataset
                                            "complexity": "medium",
                                            "reasoning": "From dataset",
                                        })
            
            self.dataset_queries = queries
            self._dataset_loaded = True
            return memories

        # LoCoMo format: facts are embedded in conversations
        if isinstance(data, list):
            for sample_idx, sample in enumerate(data[:1]):  # Use first sample
                if not isinstance(sample, dict):
                    continue
                # Try to extract from conversation
                conv = sample.get("conversation", {})
                if isinstance(conv, dict):
                    for key in conv:
                        if key.startswith("session_") and not key.endswith("_date_time"):
                            turns = conv.get(key, [])
                            if isinstance(turns, list):
                                for turn_idx, turn in enumerate(turns):
                                    if isinstance(turn, dict) and turn.get("speaker") == "speaker_a":
                                        text = turn.get("text", "")
                                        if text and len(text) > 10:
                                            memories.append({
                                                "id": f"f{len(memories)+1}",
                                                "text": text,
                                                "length_hint": "short" if len(text) < 50 else "medium" if len(text) < 100 else "long",
                                                "source": f"locomo_session_{key}_turn_{turn_idx}",
                                            })
                                            # Also add as a query
                                            queries.append({
                                                "query": text,
                                                "ground_facts": [f"f{len(memories)}"],
                                                "complexity": "simple",
                                                "reasoning": "From LoCoMo dataset",
                                            })
                # Also check for explicit facts in sample
                events = sample.get("event_summary", {})
                if isinstance(events, dict):
                    for event_id, event_text in events.items():
                        if isinstance(event_text, str) and event_text:
                            memories.append({
                                "id": f"f{len(memories)+1}",
                                "text": event_text,
                                "length_hint": "short" if len(event_text) < 50 else "medium",
                                "source": f"locomo_event_{event_id}",
                            })

        # Limit to configured number
        num_memories = self.config.get("num_memories", 10)
        if len(memories) > num_memories:
            memories = memories[:num_memories]

        self.dataset_queries = queries[:num_memories]
        self._dataset_loaded = True

        if not memories:
            return self._generate_fallback_memories()

        return memories

    def _generate_memories_from_custom_scenario(self) -> list[dict[str, Any]]:
        """Generate memories from custom scenario text.
        
        This is used when custom_scenario is provided directly from the frontend,
        typically from parsed static dataset memories.
        """
        self.theme = "自定义场景"
        # Split custom scenario into memory-like chunks
        # Treat each sentence or paragraph as a memory
        sentences = re.split(r'[。\n]', self.custom_scenario)
        memories = []
        for i, sentence in enumerate(sentences):
            sentence = sentence.strip()
            if sentence and len(sentence) > 5:
                memories.append({
                    "id": f"f{i+1}",
                    "text": sentence,
                    "length_hint": "short" if len(sentence) < 50 else "medium" if len(sentence) < 100 else "long",
                })
        return memories if memories else self._generate_fallback_memories()

    def _generate_dynamic_memories(self) -> list[dict[str, Any]]:
        """Generate memories using LLM."""
        # Otherwise, generate memories using LLM
        num_memories = self.config.get("num_memories", 10)
        theme = self.theme or self._pick_random_theme()
        self.theme = theme

        prompt = BACKGROUND_MEMORIES_PROMPT.format(
            num_memories=num_memories,
            theme=theme,
        )

        try:
            result = llm.openai_chat(
                messages=[
                    {"role": "system", "content": "You are a test scenario generator. Output only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                model=self.model,
                temperature=0.9,
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=120,
            )

            if "error" in result:
                print(f"[DynamicEvaluator] LLM error: {result.get('error')}")
                return self._generate_fallback_memories()

            answer = result.get("answer", "")
            print(f"[DynamicEvaluator] LLM answer length: {len(answer)}, model: {self.model}, base_url: {self.base_url}")
            json_match = re.search(r"\[[\s\S]*\]", answer)
            if not json_match:
                print(f"[DynamicEvaluator] No JSON array found in answer")
                return self._generate_fallback_memories()

            memories = json.loads(json_match.group())
            print(f"[DynamicEvaluator] Parsed {len(memories)} memories from LLM")

            # Validate structure
            validated = []
            for i, m in enumerate(memories):
                if isinstance(m, dict) and m.get("text"):
                    validated.append({
                        "id": m.get("id") or f"f{i+1}",
                        "text": m.get("text", ""),
                        "length_hint": m.get("length_hint", "medium"),
                    })

            return validated if validated else self._generate_fallback_memories()

        except Exception as e:
            print(f"[DynamicEvaluator] Exception: {e}")
            return self._generate_fallback_memories()

    def _generate_fallback_memories(self) -> list[dict[str, Any]]:
        """Generate fallback memories when LLM or dataset is unavailable."""
        num_memories = self.config.get("num_memories", 10)
        theme = self.theme or "日常生活"
        
        # Base fallback memories
        base_memories = [
            {"id": "f1", "text": "我喜欢在周末去公园跑步，通常早上7点出发。", "length_hint": "medium"},
            {"id": "f2", "text": "我家的猫叫小白，是一只三岁的英短。", "length_hint": "short"},
            {"id": "f3", "text": "我下周三有一个重要的项目汇报，需要准备PPT。", "length_hint": "medium"},
            {"id": "f4", "text": "我最喜欢的餐厅是公司楼下那家川菜馆，水煮鱼很好吃。", "length_hint": "medium"},
            {"id": "f5", "text": "我女儿的生日是6月15日，她想要一个乐高玩具。", "length_hint": "medium"},
            {"id": "f6", "text": "我每天早上喝一杯咖啡，喜欢加牛奶不加糖。", "length_hint": "short"},
            {"id": "f7", "text": "我最近在学习Python编程，每天晚上花一小时练习。", "length_hint": "medium"},
            {"id": "f8", "text": "我家的车位在B2层03号，靠近电梯口。", "length_hint": "short"},
            {"id": "f9", "text": "我计划下个月去日本旅游，已经订好了机票和酒店。", "length_hint": "medium"},
            {"id": "f10", "text": "我儿子的班级在三年级二班，班主任是李老师。", "length_hint": "short"},
            {"id": "f11", "text": "我每周三晚上有瑜伽课，在小区对面的健身房。", "length_hint": "medium"},
            {"id": "f12", "text": "我最喜欢的电影是《肖申克的救赎》，看了至少五遍。", "length_hint": "medium"},
            {"id": "f13", "text": "我每天的通勤时间是40分钟，坐地铁3号线。", "length_hint": "short"},
            {"id": "f14", "text": "我最近在装修房子，预计下个月完工。", "length_hint": "short"},
            {"id": "f15", "text": "我习惯用印象笔记记录工作事项，已经用了三年了。", "length_hint": "medium"},
        ]
        
        # Return requested number of memories
        return base_memories[:num_memories]

    def _pick_random_theme(self) -> str:
        """Pick a random theme from the pool."""
        import random
        return random.choice(THEME_POOL)

    def generate_next_query(self, context: dict[str, Any]) -> dict[str, Any]:
        """Generate the next user query.

        Args:
            context: Context dict with keys:
                - round_index: Current round number
                - previous_queries: List of previous queries
                - previous_replies: List of previous replies
                - is_new_session: Whether this is a new session

        Returns:
            {
                "query": str,
                "ground_facts": list[str],
                "complexity": str,
                "reasoning": str,
                "new_session_hint": bool,
            }
        """
        if not self._memories_generated:
            self.generate_background_memories()

        if self.mode == "static":
            return self._generate_static_query(context)
        else:
            return self._generate_dynamic_query(context)

    def _generate_static_query(self, context: dict[str, Any]) -> dict[str, Any]:
        """Generate query from static dataset.
        
        If dataset_queries are loaded, use them directly.
        Otherwise, fallback to simple memory-based queries.
        """
        round_index = context.get("round_index", 0)
        
        # If we have loaded queries from dataset, use them directly
        if self.dataset_queries and round_index < len(self.dataset_queries):
            return self.dataset_queries[round_index]
        
        # Fallback: simple round-robin through memories (for backward compatibility)
        if round_index < len(self.background_memories):
            fact = self.background_memories[round_index]
            return {
                "query": f"我之前告诉你的关于{fact['text'][:30]}的事是什么？",
                "ground_facts": [fact["id"]],
                "complexity": "simple",
                "reasoning": "Simple recall test from loaded memories",
                "new_session_hint": False,
            }

        # Query about previously mentioned facts
        query_idx = round_index - len(self.background_memories)
        if query_idx >= 0 and query_idx < len(self.background_memories):
            fact = self.background_memories[query_idx]
            return {
                "query": f"我之前告诉你的那个关于{fact['text'][:20]}的事情，你还记得吗？",
                "ground_facts": [fact["id"]],
                "complexity": "simple",
                "reasoning": "Simple recall test",
                "new_session_hint": query_idx % 3 == 2,
            }

        # Final fallback to dynamic generation
        return self._generate_dynamic_query(context)

    def _generate_dynamic_query(self, context: dict[str, Any]) -> dict[str, Any]:
        """Generate query using LLM."""
        round_index = context.get("round_index", 0)
        previous_queries = context.get("previous_queries", [])
        previous_replies = context.get("previous_replies", [])
        is_new_session = context.get("is_new_session", False)

        # Build facts text
        facts_text = "\n".join(
            f"- [{m['id']}] {m['text']}"
            for m in self.background_memories[:10]  # Limit for prompt length
        )

        # Build history text
        history_parts = []
        for i, (q, r) in enumerate(zip(previous_queries[-5:], previous_replies[-5:])):
            history_parts.append(f"User: {q[:100]}")
            history_parts.append(f"Assistant: {r[:100]}")
        history_text = "\n".join(history_parts) if history_parts else "(No previous conversation)"

        prompt = NEXT_QUERY_PROMPT.format(
            facts_text=facts_text,
            history_text=history_text,
            round_index=round_index,
            is_new_session=is_new_session,
        )

        try:
            result = llm.openai_chat(
                messages=[
                    {"role": "system", "content": "You are a test query generator. Output only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                model=self.model,
                temperature=0.7,
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=60,
            )

            if "error" in result:
                return self._fallback_query(round_index)

            answer = result.get("answer", "")
            json_match = re.search(r"\{[\s\S]*\}", answer)
            if not json_match:
                return self._fallback_query(round_index)

            query_data = json.loads(json_match.group())

            # Validate structure
            return {
                "query": query_data.get("query", "你还记得什么？"),
                "ground_facts": query_data.get("ground_facts", []),
                "complexity": query_data.get("complexity", "simple"),
                "reasoning": query_data.get("reasoning", ""),
                "new_session_hint": query_data.get("new_session_hint", False),
            }

        except Exception:
            return self._fallback_query(round_index)

    def _fallback_query(self, round_index: int) -> dict[str, Any]:
        """Generate fallback query when LLM is unavailable."""
        if round_index < len(self.background_memories):
            fact = self.background_memories[round_index]
            return {
                "query": f"我刚才告诉你的那个关于{fact['text'][:15]}的事是什么？",
                "ground_facts": [fact["id"]],
                "complexity": "simple",
                "reasoning": "Fallback simple recall",
                "new_session_hint": False,
            }

        # Cross-session recall test
        import random
        fact = random.choice(self.background_memories) if self.background_memories else {"id": "f1", "text": "测试事实"}
        return {
            "query": "你能告诉我之前我说过的一些事情吗？",
            "ground_facts": [fact["id"]],
            "complexity": "medium",
            "reasoning": "Fallback cross-session recall",
            "new_session_hint": True,
        }

    def add_to_history(self, query: str, reply: str) -> None:
        """Add a query-reply pair to conversation history."""
        self.conversation_history.append({
            "query": query,
            "reply": reply,
            "timestamp": time.time(),
        })

    def get_state(self) -> dict[str, Any]:
        """Get the current state of the evaluator."""
        return {
            "mode": self.mode,
            "theme": self.theme,
            "num_background_memories": len(self.background_memories),
            "num_conversation_turns": len(self.conversation_history),
            "memories_generated": self._memories_generated,
            "created_at": self.created_at,
        }

    def evaluate_response(
        self,
        query: str,
        reply: str,
        ground_facts: list[dict[str, Any] | str],
        recalled_memories: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Evaluate the quality of an AI response.

        Args:
            query: The user query
            reply: The AI response to evaluate
            ground_facts: Expected facts (can be IDs like "f1" or objects with "text")
            recalled_memories: Memories that were recalled during inference

        Returns:
            {
                "score": int (0-100),
                "reason": str,
                "matched_facts": int,
                "total_facts": int,
                "recall_helped": bool,
                "details": list,
            }
        """
        if not self._memories_generated:
            self.generate_background_memories()

        # Build ground facts text for prompt
        ground_facts_text = ""
        fact_details = []
        
        if ground_facts:
            for fact_item in ground_facts:
                if isinstance(fact_item, str):
                    # It's a fact ID, look up the full fact
                    fact_id = fact_item
                    found = False
                    for m in self.background_memories:
                        if m.get("id") == fact_id:
                            fact_details.append(f"- [{fact_id}] {m.get('text', '')}")
                            found = True
                            break
                    if not found:
                        # Use the ID as-is if not found
                        fact_details.append(f"- [{fact_id}] (未找到)")
                elif isinstance(fact_item, dict):
                    # It's a fact object
                    fact_id = fact_item.get("id", "?")
                    text = fact_item.get("text", "") or fact_item.get("fact", "") or str(fact_item)
                    fact_details.append(f"- [{fact_id}] {text[:200]}")
            
            ground_facts_text = "\n".join(fact_details) if fact_details else "(无预设事实)"
        else:
            ground_facts_text = "(无预设事实)"

        # Build recalled memories text
        recalled_text = ""
        if recalled_memories:
            recalled_parts = []
            for i, mem in enumerate(recalled_memories[:5]):  # Limit to 5
                text = mem.get("text", "") or mem.get("query", "") or str(mem)
                if text:
                    recalled_parts.append(f"- {text[:200]}")
            recalled_text = "\n".join(recalled_parts) if recalled_parts else "(无召回记忆)"
        else:
            recalled_text = "(无召回记忆)"

        prompt = EVALUATE_RESPONSE_PROMPT.format(
            query=query,
            reply=reply,
            ground_facts=ground_facts_text,
            recalled_memories=recalled_text,
        )

        try:
            result = llm.openai_chat(
                messages=[
                    {"role": "system", "content": "You are a response quality evaluator. Output only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                model=self.model,
                temperature=0.3,  # Lower temperature for consistent evaluation
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=60,
            )

            if "error" in result:
                return self._fallback_evaluate(query, reply, ground_facts, recalled_memories)

            answer = result.get("answer", "")
            json_match = re.search(r"\{[\s\S]*\}", answer)
            if not json_match:
                return self._fallback_evaluate(query, reply, ground_facts, recalled_memories)

            eval_data = json.loads(json_match.group())

            # Validate and normalize
            score = eval_data.get("score", 50)
            if not isinstance(score, (int, float)):
                score = 50
            score = max(0, min(100, int(score)))

            # Extract individual scores
            fact_coverage = eval_data.get("fact_coverage_score", 0)
            if not isinstance(fact_coverage, (int, float)):
                fact_coverage = 0
            fact_coverage = max(0, min(40, int(fact_coverage)))

            accuracy = eval_data.get("accuracy_score", 0)
            if not isinstance(accuracy, (int, float)):
                accuracy = 0
            accuracy = max(0, min(30, int(accuracy)))

            relevance = eval_data.get("relevance_score", 0)
            if not isinstance(relevance, (int, float)):
                relevance = 0
            relevance = max(0, min(20, int(relevance)))

            recall_quality = eval_data.get("recall_quality_score", 0)
            if not isinstance(recall_quality, (int, float)):
                recall_quality = 0
            recall_quality = max(0, min(10, int(recall_quality)))

            return {
                "score": score,
                "fact_coverage_score": fact_coverage,
                "accuracy_score": accuracy,
                "relevance_score": relevance,
                "recall_quality_score": recall_quality,
                "reason": eval_data.get("reason", ""),
                "matched_facts": eval_data.get("matched_facts", 0),
                "total_facts": len(ground_facts) if ground_facts else 0,
                "recall_helped": eval_data.get("recall_helped", False),
                "details": eval_data.get("details", []),
            }

        except Exception:
            return self._fallback_evaluate(query, reply, ground_facts, recalled_memories)

    def _fallback_evaluate(
        self,
        query: str,
        reply: str,
        ground_facts: list[dict[str, Any] | str],
        recalled_memories: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Fallback evaluation when LLM is unavailable."""
        # Simple keyword matching fallback
        reply_lower = reply.lower()
        matched = 0
        total = len(ground_facts) if ground_facts else 0

        for fact_item in ground_facts:
            # Get fact text - handle both ID strings and objects
            if isinstance(fact_item, str):
                # Look up fact by ID
                fact_text = ""
                for m in self.background_memories:
                    if m.get("id") == fact_item:
                        fact_text = m.get("text", "")
                        break
                if not fact_text:
                    fact_text = fact_item
            elif isinstance(fact_item, dict):
                fact_text = fact_item.get("text", "") or fact_item.get("fact", "") or str(fact_item)
            else:
                fact_text = str(fact_item)
            
            if not fact_text:
                continue
            # Check if key terms from fact appear in reply
            keywords = [w for w in fact_text.lower().split() if len(w) >= 2][:5]
            if sum(1 for kw in keywords if kw in reply_lower) >= len(keywords) * 0.5:
                matched += 1

        score = int((matched / total) * 100) if total > 0 else 50
        recall_helped = len(recalled_memories) > 0

        # Calculate individual scores based on matching
        fact_coverage = int((matched / total) * 40) if total > 0 else 0
        accuracy = int((matched / total) * 30) if total > 0 else 15
        relevance = 15 if matched > 0 else 5  # Default relevance
        recall_quality = 5 if recall_helped else 0

        return {
            "score": score,
            "fact_coverage_score": fact_coverage,
            "accuracy_score": accuracy,
            "relevance_score": relevance,
            "recall_quality_score": recall_quality,
            "reason": f"匹配 {matched}/{total} 事实" if total > 0 else "无预设事实",
            "matched_facts": matched,
            "total_facts": total,
            "recall_helped": recall_helped,
            "details": [],
        }


# ---------------------------------------------------------------------------
# Evaluation prompt
# ---------------------------------------------------------------------------

EVALUATE_RESPONSE_PROMPT = """You are a quality evaluator for AI assistant responses.

Evaluate whether the AI's response correctly uses the expected background facts to answer the user's query.

## User Query
{query}

## AI Response
{reply}

## Expected Background Facts
{ground_facts}

## Recalled Memories (used by the AI)
{recalled_memories}

## Evaluation Criteria (Total: 100 points)

### 1. Fact Coverage (0-40 points)
How many of the expected facts were correctly mentioned or used in the response?
- 40: All expected facts correctly used
- 30: Most facts used, some missing or incorrect
- 20: About half of facts used
- 10: Few facts used
- 0: No expected facts mentioned

### 2. Accuracy (0-30 points)
Is the information in the response factually accurate and consistent with the provided facts?
- 30: All information accurate, no hallucination
- 20: Mostly accurate, minor inconsistencies
- 10: Some accurate parts, some wrong
- 0: Major inaccuracies or hallucination

### 3. Relevance (0-20 points)
Does the response directly address the user's query?
- 20: Fully addresses the query
- 15: Mostly addresses, some tangential content
- 10: Partially addresses
- 5: Barely addresses
- 0: Does not address the query

### 4. Recall Quality (0-10 points)
Did the recalled memories help produce a better response?
- 10: Recalled memories significantly improved the response
- 5: Recalled memories helped somewhat
- 0: No recall or recall didn't help

Output ONLY a JSON object:
{{
  "score": <0-100>,
  "fact_coverage_score": <0-40>,
  "accuracy_score": <0-30>,
  "relevance_score": <0-20>,
  "recall_quality_score": <0-10>,
  "matched_facts": <number of facts correctly used>,
  "total_facts": <total expected facts>,
  "recall_helped": true|false,
  "reason": "Brief explanation in Chinese",
  "details": [
    {{"fact_id": "...", "used": true|false, "correct": true|false, "note": "..."}}
  ]
}}
"""

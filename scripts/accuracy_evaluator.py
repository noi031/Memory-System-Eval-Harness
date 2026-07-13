#!/usr/bin/env python3
"""Accuracy evaluator for measuring recall quality and factual correctness.

This module provides evaluation of accuracy metrics that require external
ground truth validation, as opposed to runtime metrics collected from
EchoAgent/EchoMem Prometheus endpoints.

Usage:
    from accuracy_evaluator import AccuracyEvaluator

    evaluator = AccuracyEvaluator(model="deepseek-v4-flash", base_url="...")
    result = evaluator.evaluate_recall_quality(
        query="你知道我是谁吗？",
        reply="你是张三，华为的员工...",
        ground_facts=[{"id": "f1", "text": "张三是华为的员工"}]
    )
"""
from __future__ import annotations

import json
import os
import re
from typing import Any
from pathlib import Path

# Import LLM client from memory module
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory import llm


# ---------------------------------------------------------------------------
# Evaluation prompts
# ---------------------------------------------------------------------------

RECALL_QUALITY_PROMPT = """You are an expert evaluator for a memory-augmented AI assistant.

Given:
1. A user query
2. The assistant's reply
3. A set of ground-truth facts that the query depends on

Evaluate the assistant's performance on the following dimensions:

## Scoring Rubric

### recall_score (0-2)
- 2: The reply correctly uses ALL ground-truth facts (complete recall)
- 1: The reply uses SOME but not all ground-truth facts (partial recall)
- 0: The reply does not use any ground-truth facts (recall failure)

### factual_accuracy (0-2)
- 2: The reply contains only correct information (no hallucination)
- 1: The reply contains some inaccuracies or unverified claims
- 0: The reply contains significant hallucinations or false information

### relevance (0-2)
- 2: The reply is highly relevant and directly addresses the query
- 1: The reply is somewhat relevant but may include unnecessary information
- 0: The reply is not relevant to the query

## Input

**User Query:** {query}

**Assistant Reply:** {reply}

**Ground-Truth Facts:**
{facts_text}

## Output Format

Output a JSON object with the following structure:
```json
{
  "recall_score": 0-2,
  "factual_accuracy": 0-2,
  "relevance": 0-2,
  "ground_fact_coverage": 0.0-1.0,
  "reasoning": "Brief explanation of the scores"
}
```

For ground_fact_coverage, estimate what fraction of the ground-truth facts were correctly used in the reply (0.0 = none, 1.0 = all).

Only output the JSON object, no other text."""


# ---------------------------------------------------------------------------
# AccuracyEvaluator class
# ---------------------------------------------------------------------------

class AccuracyEvaluator:
    """Evaluator for accuracy metrics requiring external validation."""

    def __init__(
        self,
        model: str = "deepseek-v4-flash",
        base_url: str | None = None,
        api_key: str | None = None,
        method: str = "llm",
    ):
        """Initialize the evaluator.

        Args:
            model: LLM model for evaluation
            base_url: API base URL (optional, read from env if not provided)
            api_key: API key (optional, read from env if not provided)
            method: Evaluation method ("llm" or "heuristic")
        """
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.method = method

    def evaluate_recall_quality(
        self,
        query: str,
        reply: str,
        ground_facts: list[dict[str, Any]],
        recalled_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Evaluate recall quality for a single query-reply pair.

        Args:
            query: User query text
            reply: Assistant reply text
            ground_facts: List of ground-truth facts [{"id": ..., "text": ...}]
            recalled_items: Optional list of recalled memory items for coverage calc

        Returns:
            {
                "recall_score": 0-2,
                "factual_accuracy": 0-2,
                "relevance": 0-2,
                "ground_fact_coverage": 0.0-1.0,
                "reasoning": str,
            }
        """
        if self.method == "llm":
            return self._llm_evaluate(query, reply, ground_facts)
        else:
            return self._heuristic_evaluate(query, reply, ground_facts, recalled_items)

    def _llm_evaluate(
        self,
        query: str,
        reply: str,
        ground_facts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Use LLM to evaluate recall quality."""
        facts_text = "\n".join(
            f"- [{f.get('id', i)}] {f.get('text', '')}"
            for i, f in enumerate(ground_facts, 1)
        )

        prompt = RECALL_QUALITY_PROMPT.format(
            query=query,
            reply=reply,
            facts_text=facts_text if facts_text else "(No ground-truth facts provided)"
        )

        try:
            result = llm.openai_chat(
                messages=[
                    {"role": "system", "content": "You are an expert evaluator. Output only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                model=self.model,
                temperature=0.3,
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=60,
            )

            if "error" in result:
                return self._heuristic_evaluate(query, reply, ground_facts, None)

            answer = result.get("answer", "")
            json_match = re.search(r'\{[\s\S]*\}', answer)
            if not json_match:
                return self._heuristic_evaluate(query, reply, ground_facts, None)

            scores = json.loads(json_match.group())

            return {
                "recall_score": int(scores.get("recall_score", 0)),
                "factual_accuracy": int(scores.get("factual_accuracy", 0)),
                "relevance": int(scores.get("relevance", 0)),
                "ground_fact_coverage": float(scores.get("ground_fact_coverage", 0.0)),
                "reasoning": scores.get("reasoning", ""),
                "method": "llm",
            }

        except Exception as exc:
            # Fallback to heuristic on error
            return self._heuristic_evaluate(query, reply, ground_facts, None)

    def _heuristic_evaluate(
        self,
        query: str,
        reply: str,
        ground_facts: list[dict[str, Any]],
        recalled_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Heuristic evaluation when LLM is unavailable."""
        if not ground_facts:
            return {
                "recall_score": 2,  # Assume good if no ground truth
                "factual_accuracy": 2,
                "relevance": 2,
                "ground_fact_coverage": 1.0,
                "reasoning": "No ground-truth facts provided, assuming correct",
                "method": "heuristic",
            }

        reply_lower = reply.lower()
        matched_facts = 0
        total_facts = len(ground_facts)

        # Simple keyword matching
        for fact in ground_facts:
            fact_text = fact.get("text", "")
            # Extract key terms (words > 2 chars)
            key_terms = [w for w in re.findall(r'\w+', fact_text.lower()) if len(w) > 2]
            if any(term in reply_lower for term in key_terms[:3]):  # Match at least one key term
                matched_facts += 1

        coverage = matched_facts / total_facts if total_facts > 0 else 0.0

        # Derive scores from coverage
        recall_score = 2 if coverage >= 0.8 else 1 if coverage >= 0.3 else 0
        factual_accuracy = 2  # Assume no hallucination in heuristic mode
        relevance = 2 if coverage >= 0.5 else 1 if coverage >= 0.2 else 0

        return {
            "recall_score": recall_score,
            "factual_accuracy": factual_accuracy,
            "relevance": relevance,
            "ground_fact_coverage": round(coverage, 2),
            "reasoning": f"Heuristic: matched {matched_facts}/{total_facts} facts by keyword overlap",
            "method": "heuristic",
        }

    def evaluate_batch(
        self,
        rounds: list[dict[str, Any]],
        facts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Batch evaluate multiple query-reply pairs.

        Args:
            rounds: List of round data, each with "query", "reply", "ground_facts"
            facts: Full list of facts for reference

        Returns:
            {
                "per_query": [...],
                "overall_score": float,
                "cross_session_score": float,
                "same_session_score": float,
                "ground_fact_coverage_avg": float,
            }
        """
        per_query: list[dict[str, Any]] = []

        for r in rounds:
            # Skip injection rounds
            if r.get("is_injection"):
                continue

            ground_fact_ids = r.get("ground_facts", [])
            ground_facts = [f for f in facts if f.get("id") in ground_fact_ids]

            result = self.evaluate_recall_quality(
                query=r.get("query", ""),
                reply=r.get("reply", ""),
                ground_facts=ground_facts,
            )
            result["turn_id"] = r.get("id", "")
            result["is_new_session"] = r.get("is_new_session", False)
            per_query.append(result)

        if not per_query:
            return {
                "per_query": [],
                "overall_score": None,
                "cross_session_score": None,
                "same_session_score": None,
                "ground_fact_coverage_avg": None,
            }

        # Compute aggregate scores
        recall_scores = [q["recall_score"] for q in per_query]
        coverage_values = [q["ground_fact_coverage"] for q in per_query]

        cross_session_scores = [
            q["recall_score"] for q in per_query if q.get("is_new_session")
        ]
        same_session_scores = [
            q["recall_score"] for q in per_query if not q.get("is_new_session")
        ]

        return {
            "per_query": per_query,
            "overall_score": round(sum(recall_scores) / len(recall_scores), 2) if recall_scores else 0,
            "cross_session_score": round(sum(cross_session_scores) / len(cross_session_scores), 2) if cross_session_scores else None,
            "same_session_score": round(sum(same_session_scores) / len(same_session_scores), 2) if same_session_scores else None,
            "ground_fact_coverage_avg": round(sum(coverage_values) / len(coverage_values), 2) if coverage_values else 0,
        }

    def generate_report(
        self,
        rounds: list[dict[str, Any]],
        facts: list[dict[str, Any]],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate full accuracy report.

        Args:
            rounds: List of round data
            facts: Full fact list
            config: Optional config for metadata

        Returns:
            Full report dict
        """
        import time

        batch_result = self.evaluate_batch(rounds, facts)

        return {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "config": config or {},
            "summary": {
                "total_queries": len(batch_result["per_query"]),
                "overall_score": batch_result["overall_score"],
                "cross_session_score": batch_result["cross_session_score"],
                "same_session_score": batch_result["same_session_score"],
                "ground_fact_coverage_avg": batch_result["ground_fact_coverage_avg"],
            },
            "per_query": batch_result["per_query"],
        }


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def compute_accuracy_from_results(
    results_path: str,
    facts: list[dict[str, Any]],
    model: str = "deepseek-v4-flash",
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Compute accuracy metrics from a results JSON file.

    Args:
        results_path: Path to echoagent_live_test_results.json
        facts: List of ground-truth facts
        model: LLM model for evaluation
        base_url: API base URL
        api_key: API key

    Returns:
        Accuracy report dict
    """
    with open(results_path, "r", encoding="utf-8") as f:
        results = json.load(f)

    rounds = results.get("rounds", results.get("per_query", []))

    evaluator = AccuracyEvaluator(
        model=model,
        base_url=base_url,
        api_key=api_key,
    )

    return evaluator.generate_report(rounds, facts)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate accuracy metrics")
    parser.add_argument("--results", required=True, help="Path to results JSON file")
    parser.add_argument("--facts", required=True, help="Path to facts JSON file")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--method", default="llm", choices=["llm", "heuristic"])
    parser.add_argument("--out", default="accuracy_report.json")
    args = parser.parse_args()

    # Load facts
    with open(args.facts, "r", encoding="utf-8") as f:
        facts_data = json.load(f)
    facts = facts_data.get("facts", facts_data if isinstance(facts_data, list) else [])

    # Compute accuracy
    report = compute_accuracy_from_results(
        args.results,
        facts,
        model=args.model,
        base_url=args.base_url or None,
        api_key=args.api_key or None,
    )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"Accuracy report saved to {args.out}")
    print(f"Overall score: {report['summary']['overall_score']}")
    print(f"Ground fact coverage: {report['summary']['ground_fact_coverage_avg']}")
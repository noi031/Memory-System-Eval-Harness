#!/usr/bin/env python3
"""Test orchestrator for coordinating scenario execution and metrics collection.

This module orchestrates the end-to-end test flow:
1. Generate or load test scenarios
2. Execute conversations with EchoAgent
3. Collect runtime metrics from Prometheus endpoints
4. Evaluate accuracy metrics
5. Generate comprehensive reports

Usage:
    from test_orchestrator import TestOrchestrator

    orchestrator = TestOrchestrator(config)
    result = orchestrator.run_batch(scenario)
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# Import local modules
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.runtime_metrics_client import RuntimeMetricsClient, format_metrics_summary
from scripts.accuracy_evaluator import AccuracyEvaluator


# ---------------------------------------------------------------------------
# EchoAgent client (simplified version for orchestrator)
# ---------------------------------------------------------------------------

class EchoAgentClient:
    """Simplified EchoAgent HTTP client for test orchestration."""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token: str = ""

    def login(self) -> None:
        """Authenticate and get JWT token."""
        from urllib.request import Request, urlopen
        data = json.dumps({"username": self.username, "password": self.password}).encode("utf-8")
        req = Request(
            f"{self.base_url}/v1/auth/login",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        self.token = result.get("access_token", "")
        if not self.token:
            raise RuntimeError(f"Login failed: {result}")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def create_session(self, title: str = "", memory_engine_endpoint: str = "") -> str:
        """Create a new session and return session ID."""
        from urllib.request import Request, urlopen
        req = Request(
            f"{self.base_url}/v1/sessions",
            data=json.dumps({"title": title or f"test-{uuid.uuid4().hex[:8]}"}).encode("utf-8"),
            headers=self._headers(),
        )
        req.method = "POST"
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        session_id = result.get("data", result).get("id", "")
        
        # Enable memory engine if endpoint provided
        if session_id and memory_engine_endpoint:
            try:
                req = Request(
                    f"{self.base_url}/v1/sessions/{session_id}/memory-engine",
                    data=json.dumps({"enabled": True, "endpoint": memory_engine_endpoint}).encode("utf-8"),
                    headers=self._headers(),
                )
                req.method = "PUT"
                urlopen(req, timeout=10)
            except Exception:
                pass
        
        return session_id

    def send_message(self, session_id: str, content: str) -> dict[str, Any]:
        """Send a message and return streaming result."""
        from urllib.request import Request, urlopen
        from urllib.parse import quote
        
        context_path = quote("/", safe="")
        
        # Send message
        req = Request(
            f"{self.base_url}/v1/sessions/{session_id}/context-paths/{context_path}/messages",
            data=json.dumps({"content": content, "afterSeq": 0}).encode("utf-8"),
            headers=self._headers(),
        )
        req.method = "POST"
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        
        # Get streaming reply
        return self._read_stream(session_id, context_path)

    def _read_stream(self, session_id: str, context_path: str) -> dict[str, Any]:
        """Read SSE stream and extract reply and metrics."""
        from urllib.request import Request, urlopen
        
        url = f"{self.base_url}/v1/sessions/{session_id}/context-paths/{context_path}/streaming?seq=0"
        req = Request(url, headers={"Authorization": f"Bearer {self.token}", "Accept": "text/event-stream"})
        
        reply_parts: list[str] = []
        ttft_ms: float | None = None
        done_event: dict[str, Any] = {}
        send_time = time.monotonic()
        
        with urlopen(req, timeout=300) as resp:
            buffer = ""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", "replace")
                
                while "\n\n" in buffer:
                    event_block, buffer = buffer.split("\n\n", 1)
                    event_type = ""
                    data_lines: list[str] = []
                    
                    for line in event_block.splitlines():
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:])
                    
                    if not data_lines:
                        continue
                    
                    try:
                        data = json.loads("\n".join(data_lines))
                    except json.JSONDecodeError:
                        continue
                    
                    if event_type in ("create", "append"):
                        if ttft_ms is None:
                            ttft_ms = (time.monotonic() - send_time) * 1000
                        fragment = data.get("fragment", data.get("content", ""))
                        if isinstance(fragment, dict):
                            reply_parts.append(fragment.get("content", ""))
                        else:
                            reply_parts.append(str(fragment))
                    elif event_type == "done":
                        done_event = data if isinstance(data, dict) else {}
                        return {
                            "reply": "".join(reply_parts),
                            "ttft_ms": ttft_ms,
                            "done_event": done_event,
                        }
        
        return {"reply": "".join(reply_parts), "ttft_ms": ttft_ms, "done_event": done_event}


# ---------------------------------------------------------------------------
# TestOrchestrator
# ---------------------------------------------------------------------------

class TestOrchestrator:
    """Orchestrates test execution with metrics collection."""

    def __init__(
        self,
        echoagent_url: str,
        echomem_url: str,
        username: str,
        password: str,
        memory_engine_endpoint: str = "",
        metrics_client: RuntimeMetricsClient | None = None,
        accuracy_evaluator: AccuracyEvaluator | None = None,
        config: dict[str, Any] | None = None,
    ):
        """Initialize the orchestrator.

        Args:
            echoagent_url: EchoAgent backend URL
            echomem_url: EchoMem service URL
            username: EchoAgent login username
            password: EchoAgent login password
            memory_engine_endpoint: Memory engine endpoint for sessions
            metrics_client: Optional RuntimeMetricsClient instance
            accuracy_evaluator: Optional AccuracyEvaluator instance
            config: Optional configuration dict
        """
        self.echoagent_url = echoagent_url
        self.echomem_url = echomem_url
        self.username = username
        self.password = password
        self.memory_engine_endpoint = memory_engine_endpoint
        self.config = config or {}

        # Initialize components
        self.agent_client = EchoAgentClient(echoagent_url, username, password)
        self.metrics_client = metrics_client or RuntimeMetricsClient(
            echoagent_url=echoagent_url,
            echomem_url=echomem_url,
        )
        self.accuracy_evaluator = accuracy_evaluator or AccuracyEvaluator()

    def run_batch(
        self,
        scenario: dict[str, Any],
        out_dir: Path | None = None,
        collect_runtime_metrics: bool = True,
    ) -> dict[str, Any]:
        """Execute a single test batch.

        Args:
            scenario: Test scenario with "facts" and "rounds"
            out_dir: Optional output directory for per-turn logs
            collect_runtime_metrics: Whether to collect Prometheus metrics

        Returns:
            {
                "runtime": {...},
                "accuracy": {...},
                "turns": [...],
            }
        """
        # Login to EchoAgent
        self.agent_client.login()

        facts = scenario.get("facts", [])
        rounds = scenario.get("rounds", [])

        # Collect baseline metrics
        baseline_metrics = None
        if collect_runtime_metrics:
            baseline_metrics = self.metrics_client.fetch_metrics()

        turn_results: list[dict[str, Any]] = []
        runtime_snapshots: list[dict[str, Any]] = []
        current_session_id: str | None = None

        for i, round_data in enumerate(rounds):
            # Check if we need a new session
            if round_data.get("new_session") or current_session_id is None:
                current_session_id = self.agent_client.create_session(
                    title=f"test-{scenario.get('theme', 'scenario')[:20]}",
                    memory_engine_endpoint=self.memory_engine_endpoint,
                )
                is_new_session = True
            else:
                is_new_session = False

            # Execute the turn
            query = round_data.get("query", "")
            turn_start = time.monotonic()

            try:
                reply_result = self.agent_client.send_message(current_session_id, query)
                reply = reply_result.get("reply", "")
                ttft_ms = reply_result.get("ttft_ms")
                done_event = reply_result.get("done_event", {})
            except Exception as exc:
                reply = ""
                ttft_ms = None
                done_event = {}
                print(f"    [error] turn {i}: {exc}")

            turn_end = time.monotonic()

            # Collect runtime metrics after turn
            turn_metrics = None
            if collect_runtime_metrics:
                after_metrics = self.metrics_client.fetch_metrics()
                turn_metrics = self.metrics_client.extract_turn_metrics(after_metrics)
                if baseline_metrics:
                    delta = self.metrics_client.diff_metrics(baseline_metrics, after_metrics)
                    turn_metrics["delta"] = delta

            # Build turn result
            turn_result = {
                "turn_id": round_data.get("id", f"turn-{i}"),
                "session_id": current_session_id,
                "query": query,
                "reply": reply,
                "reply_length": len(reply),
                "query_length": len(query),
                "ttft_ms": round(ttft_ms, 1) if ttft_ms else None,
                "duration_ms": round((turn_end - turn_start) * 1000, 1),
                "is_new_session": is_new_session,
                "is_injection": round_data.get("is_injection", False),
                "ground_facts": round_data.get("ground_facts", []),
                "complexity": round_data.get("complexity", ""),
                "done_event": done_event,
            }

            # Extract cached/prompt tokens from done_event
            if done_event:
                turn_result["cached_tokens"] = done_event.get("cachedTokens", 0)
                turn_result["prompt_tokens"] = done_event.get("promptTokens", 0)

            turn_results.append(turn_result)

            if turn_metrics:
                runtime_snapshots.append({
                    "turn_id": turn_result["turn_id"],
                    "timestamp": time.time(),
                    "metrics": turn_metrics,
                })

            print(f"    turn {i}: ttft={ttft_ms}ms reply_len={len(reply)}")

        # Evaluate accuracy
        accuracy_report = self.accuracy_evaluator.generate_report(
            turn_results,
            facts,
            self.config,
        )

        # Build final result
        result = {
            "scenario": {
                "theme": scenario.get("theme", ""),
                "num_facts": len(facts),
                "num_rounds": len(rounds),
            },
            "turns": turn_results,
            "runtime": {
                "baseline": baseline_metrics,
                "snapshots": runtime_snapshots,
            },
            "accuracy": accuracy_report,
        }

        # Compute summary
        result["summary"] = self._compute_summary(turn_results, accuracy_report)

        return result

    def _compute_summary(
        self,
        turns: list[dict[str, Any]],
        accuracy_report: dict[str, Any],
    ) -> dict[str, Any]:
        """Compute aggregate summary from turn results."""
        non_injection_turns = [t for t in turns if not t.get("is_injection")]

        ttft_values = [t["ttft_ms"] for t in non_injection_turns if t.get("ttft_ms")]
        cached_values = [t.get("cached_tokens", 0) for t in non_injection_turns]
        prompt_values = [t.get("prompt_tokens", 0) for t in non_injection_turns]
        query_lengths = [t["query_length"] for t in non_injection_turns]
        reply_lengths = [t["reply_length"] for t in non_injection_turns]
        new_sessions = sum(1 for t in non_injection_turns if t.get("is_new_session"))

        return {
            "total_queries": len(non_injection_turns),
            "new_sessions": new_sessions,
            "avg_query_length": round(sum(query_lengths) / len(query_lengths), 1) if query_lengths else 0,
            "avg_reply_length": round(sum(reply_lengths) / len(reply_lengths), 1) if reply_lengths else 0,
            "avg_ttft_ms": round(sum(ttft_values) / len(ttft_values), 1) if ttft_values else None,
            "avg_cached_tokens": round(sum(cached_values) / len(cached_values), 1) if cached_values else 0,
            "avg_prompt_tokens": round(sum(prompt_values) / len(prompt_values), 1) if prompt_values else 0,
            "accuracy": accuracy_report.get("summary", {}),
        }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Test orchestrator for EchoAgent evaluation")
    parser.add_argument("--echoagent-url", default="http://127.0.0.1:31020")
    parser.add_argument("--echomem-url", default="http://127.0.0.1:8010")
    parser.add_argument("--username", default="test_user")
    parser.add_argument("--password", default="test_password")
    parser.add_argument("--memory-engine-endpoint", default="http://127.0.0.1:31030")
    parser.add_argument("--scenario", required=True, help="Path to scenario JSON file")
    parser.add_argument("--out-dir", default="runs/orchestrator_test")
    parser.add_argument("--no-runtime-metrics", action="store_true")
    args = parser.parse_args()

    # Load scenario
    with open(args.scenario, "r", encoding="utf-8") as f:
        scenario = json.load(f)

    # Create orchestrator
    orchestrator = TestOrchestrator(
        echoagent_url=args.echoagent_url,
        echomem_url=args.echomem_url,
        username=args.username,
        password=args.password,
        memory_engine_endpoint=args.memory_engine_endpoint,
    )

    # Run batch
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = orchestrator.run_batch(
        scenario,
        out_dir=out_dir,
        collect_runtime_metrics=not args.no_runtime_metrics,
    )

    # Save results
    (out_dir / "full_results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8"
    )

    # Save runtime metrics separately
    if result.get("runtime"):
        (out_dir / "runtime.json").write_text(
            json.dumps(result["runtime"], ensure_ascii=False, indent=2, default=str),
            encoding="utf-8"
        )

    # Save accuracy report
    if result.get("accuracy"):
        (out_dir / "accuracy.json").write_text(
            json.dumps(result["accuracy"], ensure_ascii=False, indent=2, default=str),
            encoding="utf-8"
        )

    # Save summary
    (out_dir / "summary.json").write_text(
        json.dumps(result["summary"], ensure_ascii=False, indent=2, default=str),
        encoding="utf-8"
    )

    print(f"\nResults saved to {out_dir}")
    print(f"Summary: {json.dumps(result['summary'], indent=2)}")


if __name__ == "__main__":
    main()
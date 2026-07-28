"""Evaluation infrastructure: result directory, logging, config, EchoMem log collection."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class EvalConfig:
    """Common configuration for all benchmark evaluations."""

    # EchoMem connection
    echomem_url: str = "http://127.0.0.1:8010"
    echomem_auth_key: str = ""
    account: str = "default"
    user_id: str = "default"
    agent_id: str = "default"
    workspace: str = ""

    # LLM for answering
    llm_base_url: str = ""
    llm_model: str = "doubao-seed-2.0-pro"
    llm_api_key: str = ""
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048

    # Retrieval
    top_k: int = 10
    memory_budget_chars: int = 8000

    # Concurrency
    concurrency: int = 4

    # Timeouts
    commit_timeout_s: float = 600.0
    commit_poll_interval_s: float = 2.0
    question_timeout_s: float = 120.0
    llm_timeout_s: float = 120.0
    llm_retries: int = 3

    # EchoMem log directory (passed in, not fetched via API)
    echomem_log_dir: str = ""

    # Dataset
    dataset_path: str = ""
    sample_filter: str = "all"
    question_limit: int = 0  # 0 = all

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # mask api keys
        for k in ("llm_api_key", "echomem_auth_key"):
            v = d.get(k, "")
            if v:
                d[k] = v[:4] + "***" + v[-4:] if len(v) > 8 else "***"
        return d


class EvalRun:
    """Manages a single evaluation run: result directory, logging, summary.

    Each run gets a timestamped subdirectory under ``results_root``::

        results_root / 20260728_153022 /
            config.json
            run.log
            results.csv
            summary.json
            echomem_logs/   (copied from EchoMem log dir if provided)
    """

    def __init__(
        self,
        benchmark_name: str,
        results_root: str | Path = "results",
        config: EvalConfig | None = None,
        echomem_log_dir: str = "",
    ):
        self.benchmark_name = benchmark_name
        self.config = config or EvalConfig()
        self.echomem_log_dir = echomem_log_dir or self.config.echomem_log_dir
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.result_dir = Path(results_root) / ts
        self.result_dir.mkdir(parents=True, exist_ok=True)

        self._setup_logging()
        self._save_config()

    def _setup_logging(self) -> None:
        """Configure file + console logging."""
        self.logger = logging.getLogger(f"eval.{self.benchmark_name}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()

        # File handler – full detail
        fh = logging.FileHandler(self.result_dir / "run.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        self.logger.addHandler(fh)

        # Console handler – INFO level, concise
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s"))
        self.logger.addHandler(ch)

        # Also configure echomem_client logger
        for name in ("echomem_client", "llm_client", "eval"):
            lg = logging.getLogger(name)
            lg.setLevel(logging.DEBUG)
            lg.handlers.clear()
            lg.addHandler(fh)
            lg.addHandler(ch)

    def _save_config(self) -> None:
        path = self.result_dir / "config.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "benchmark": self.benchmark_name,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "config": self.config.to_dict(),
                },
                f, indent=2, ensure_ascii=False,
            )

    def log(self, msg: str, level: int = logging.INFO) -> None:
        self.logger.log(level, msg)

    def save_summary(self, summary: dict[str, Any]) -> None:
        summary["benchmark"] = self.benchmark_name
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        path = self.result_dir / "summary.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        self.log(f"Summary saved to {path}")

    def collect_echomem_logs(self) -> None:
        """Copy EchoMem log files into the result directory.

        If ``echomem_log_dir`` is set and exists, copy commit/search related
        log files into ``result_dir / echomem_logs/``.
        """
        if not self.echomem_log_dir:
            return
        src = Path(self.echomem_log_dir)
        if not src.exists():
            self.log(f"EchoMem log dir not found: {src}", logging.WARNING)
            return

        dest = self.result_dir / "echomem_logs"
        dest.mkdir(exist_ok=True)
        copied = 0
        for f in src.iterdir():
            if f.is_file() and (f.suffix in (".log", ".jsonl", ".json") or "commit" in f.name or "search" in f.name):
                try:
                    shutil.copy2(f, dest / f.name)
                    copied += 1
                except Exception as e:
                    self.log(f"Failed to copy {f.name}: {e}", logging.WARNING)
        self.log(f"Copied {copied} EchoMem log files to {dest}")

    def elapsed_str(self, start: float) -> str:
        return f"{time.monotonic() - start:.1f}s"


def add_echomem_args(parser) -> None:
    """Add common EchoMem CLI args to an argparse parser."""
    g = parser.add_argument_group("EchoMem")
    g.add_argument("--echomem-url", default="http://127.0.0.1:8010", help="EchoMem HTTP base URL")
    g.add_argument("--echomem-auth-key", default=os.getenv("ECHOMEM_AUTH_KEY", ""), help="EchoMem X-Auth-Key")
    g.add_argument("--account", default="default")
    g.add_argument("--user-id", default="default")
    g.add_argument("--agent-id", default="default")
    g.add_argument("--workspace", default="", help="EchoMem workspace path")
    g.add_argument("--echomem-log-dir", default="", help="EchoMem log directory for log collection")


def add_llm_args(parser) -> None:
    """Add common LLM CLI args."""
    g = parser.add_argument_group("LLM")
    g.add_argument("--llm-base-url", default=os.getenv("LLM_BASE_URL", ""), help="LLM API base URL")
    g.add_argument("--llm-model", default=os.getenv("LLM_MODEL", "doubao-seed-2.0-pro"))
    g.add_argument("--llm-api-key", default=os.getenv("LLM_API_KEY", ""), help="LLM API key")
    g.add_argument("--llm-temperature", type=float, default=0.7)
    g.add_argument("--llm-max-tokens", type=int, default=2048)
    g.add_argument("--llm-timeout-s", type=float, default=120.0)
    g.add_argument("--llm-retries", type=int, default=3)


def add_eval_args(parser) -> None:
    """Add common evaluation args."""
    g = parser.add_argument_group("Evaluation")
    g.add_argument("--top-k", type=int, default=10, help="Number of memory items to retrieve (TOPK)")
    g.add_argument("--memory-budget-chars", type=int, default=8000, help="Max chars of memory to inject into prompt")
    g.add_argument("--concurrency", type=int, default=4, help="Number of concurrent QA tasks")
    g.add_argument("--commit-timeout-s", type=float, default=600.0, help="Commit poll timeout")
    g.add_argument("--commit-poll-interval-s", type=float, default=2.0)
    g.add_argument("--question-timeout-s", type=float, default=120.0, help="Per-question timeout")
    g.add_argument("--out-dir", default="results", help="Results root directory")


def build_config_from_args(args) -> EvalConfig:
    """Build an EvalConfig from parsed argparse args."""
    return EvalConfig(
        echomem_url=args.echomem_url,
        echomem_auth_key=args.echomem_auth_key,
        account=args.account,
        user_id=args.user_id,
        agent_id=args.agent_id,
        workspace=args.workspace,
        llm_base_url=args.llm_base_url,
        llm_model=args.llm_model,
        llm_api_key=args.llm_api_key,
        llm_temperature=args.llm_temperature,
        llm_max_tokens=args.llm_max_tokens,
        top_k=args.top_k,
        memory_budget_chars=args.memory_budget_chars,
        concurrency=args.concurrency,
        commit_timeout_s=args.commit_timeout_s,
        commit_poll_interval_s=args.commit_poll_interval_s,
        question_timeout_s=args.question_timeout_s,
        llm_timeout_s=args.llm_timeout_s,
        llm_retries=args.llm_retries,
        echomem_log_dir=args.echomem_log_dir,
    )

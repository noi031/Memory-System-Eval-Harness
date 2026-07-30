"""Evaluation infrastructure: result directory, logging, config, dual plugin CLI.

Design intent: this module is the shared backbone for every benchmark
run_eval. It provides:
  - EvalConfig: a dataclass holding only benchmark-generic fields (LLM
    credentials, concurrency, timeouts, dataset path). Memory-specific and
    agent-specific connection params are NOT here -- they live in their
    respective plugins.
  - add_memory_plugin_args / add_agent_plugin_args: pre-parse sys.argv to
    discover which plugin was requested, then delegate to that plugin's
    add_arguments so its CLI args appear in --help and are parsed.
  - EvalRun: manages the timestamped result directory, logging, and
    optional EchoMem log collection.
"""

from __future__ import annotations

import argparse
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

    # Plugins
    memory_plugin: str = "none"
    agent_plugin: str = "bare_llm"

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
    commit_timeout_s: float = 0.0
    commit_poll_interval_s: float = 2.0
    question_timeout_s: float = 120.0
    llm_timeout_s: float = 120.0
    llm_retries: int = 3

    # Dataset
    dataset_path: str = ""
    sample_filter: str = "all"
    question_limit: int = 0  # 0 = all

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        v = d.get("llm_api_key", "")
        if v:
            d["llm_api_key"] = v[:4] + "***" + v[-4:] if len(v) > 8 else "***"
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
        self.echomem_log_dir = echomem_log_dir
        self.started_at = datetime.now(timezone.utc)
        self.started_monotonic = time.monotonic()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.result_dir = Path(results_root) / ts
        self.result_dir.mkdir(parents=True, exist_ok=True)

        self._setup_logging()
        self._save_config()

    def _setup_logging(self) -> None:
        """Configure file + console logging."""
        self.logger = logging.getLogger(f"eval.{self.benchmark_name}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()
        self.logger.propagate = False

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
            lg.propagate = False

    def _save_config(self) -> None:
        path = self.result_dir / "config.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "benchmark": self.benchmark_name,
                    "started_at": self.started_at.isoformat(),
                    "config": self.config.to_dict(),
                },
                f, indent=2, ensure_ascii=False,
            )

    def save_config(self) -> None:
        """Persist the current, possibly runtime-resolved configuration."""
        self._save_config()

    def log(self, msg: str, level: int = logging.INFO) -> None:
        self.logger.log(level, msg)

    def save_summary(self, summary: dict[str, Any]) -> None:
        summary["benchmark"] = self.benchmark_name
        summary.setdefault("run_started_at", self.started_at.isoformat())
        summary.setdefault("run_finished_at", self.finished_at_iso())
        summary["finished_at"] = summary["run_finished_at"]
        path = self.result_dir / "summary.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        self.log(f"Summary saved to {path}")

    def finished_at_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

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


# ------------------------------------------------------------------ #
#  CLI arg helpers                                                    #
# ------------------------------------------------------------------ #

def _scan_argv_for_plugin(flag: str, default: str) -> str:
    """Pre-parse sys.argv to find --flag VALUE or --flag=VALUE."""
    for index, arg in enumerate(sys.argv):
        if arg == flag and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
        if arg.startswith(f"{flag}="):
            return arg.split("=", 1)[1]
    return default


def add_memory_plugin_args(
    parser: argparse.ArgumentParser,
    default: str = "none",
) -> None:
    """Declare --memory-plugin and the selected plugin's CLI arguments."""
    parser.add_argument("--memory-plugin", default=default, help="Memory plugin name")
    plugin_name = _scan_argv_for_plugin("--memory-plugin", default)
    from memories.registry import get_plugin_class
    get_plugin_class(plugin_name).add_arguments(parser)


def add_agent_plugin_args(
    parser: argparse.ArgumentParser,
    default_plugin: str = "bare_llm",
) -> None:
    """Declare --agent-plugin and the selected plugin's CLI arguments."""
    parser.add_argument("--agent-plugin", default=default_plugin, help="Agent plugin name")
    plugin_name = _scan_argv_for_plugin("--agent-plugin", default_plugin)
    from agents import get_plugin_class
    get_plugin_class(plugin_name).add_arguments(parser)


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
    g.add_argument("--commit-timeout-s", type=float, default=0.0, help="Commit poll timeout (0 = infinite)")
    g.add_argument("--commit-poll-interval-s", type=float, default=2.0)
    g.add_argument(
        "--question-timeout-s",
        type=float,
        default=120.0,
        help="End-to-end retrieval and answer timeout per question (0 = no extra limit)",
    )
    g.add_argument("--out-dir", default="results", help="Results root directory")
    g.add_argument(
        "--allow-incomplete-imports",
        action="store_true",
        help="Continue QA even when memory import did not complete (diagnostics only)",
    )


def build_config_from_args(args) -> EvalConfig:
    """Build an EvalConfig from parsed argparse args."""
    return EvalConfig(
        memory_plugin=getattr(args, "memory_plugin", "none"),
        agent_plugin=getattr(args, "agent_plugin", "bare_llm"),
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
    )


def results_root_for(benchmark_dir: str | Path, out_dir: str) -> Path:
    """Resolve the result root while preserving the historical default."""
    value = str(out_dir or "results").strip()
    if value == "results":
        return Path(benchmark_dir) / "results"
    return Path(value).expanduser()


def validate_eval_config(config: EvalConfig) -> None:
    errors: list[str] = []
    if not config.llm_base_url.strip():
        errors.append("missing LLM base URL")
    if not config.llm_model.strip():
        errors.append("missing LLM model")
    if not config.llm_api_key.strip():
        errors.append("missing LLM API key")
    if config.concurrency < 1:
        errors.append("concurrency must be >= 1")
    if config.question_timeout_s < 0:
        errors.append("question timeout must be >= 0")
    if config.commit_timeout_s < 0:
        errors.append("commit timeout must be >= 0")
    if config.commit_poll_interval_s <= 0:
        errors.append("commit poll interval must be > 0")
    if config.llm_timeout_s <= 0:
        errors.append("LLM timeout must be > 0")
    if config.llm_retries < 1:
        errors.append("LLM retries must be >= 1")
    if config.llm_max_tokens < 1:
        errors.append("LLM max tokens must be >= 1")
    if config.top_k < 1:
        errors.append("top-k must be >= 1")
    if config.memory_budget_chars < 1:
        errors.append("memory budget must be >= 1")
    if config.question_limit < 0:
        errors.append("questions must be >= 0")
    if errors:
        raise ValueError("; ".join(errors))

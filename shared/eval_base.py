"""Evaluation infrastructure: result directory, logging, config, EchoMem log collection."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class EvalConfig:
    """Eval-infra configuration shared by all runners."""

    concurrency: int = 4
    question_timeout_s: float = 120.0
    echomem_log_dir: str = ""
    dataset_path: str = ""
    sample_filter: str = "all"
    question_limit: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mask_secrets(d: dict) -> dict:
    """Return a copy of *d* with sensitive fields masked."""
    sensitive = ("api_key", "auth_key", "password", "secret", "token")
    masked: dict[str, Any] = {}
    for k, v in d.items():
        if any(s in k.lower() for s in sensitive):
            if v:
                s = str(v)
                masked[k] = s[:4] + "***" + s[-4:] if len(s) > 8 else "***"
            else:
                masked[k] = ""
        else:
            masked[k] = v
    return masked


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
        run_args: dict | None = None,
    ):
        self.benchmark_name = benchmark_name
        self.config = config or EvalConfig()
        self.echomem_log_dir = echomem_log_dir or self.config.echomem_log_dir
        self.run_args = _mask_secrets(run_args) if run_args else {}
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
        self.logger.propagate = False

        fh = logging.FileHandler(self.result_dir / "run.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        self.logger.addHandler(fh)

        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s"))
        self.logger.addHandler(ch)

        for name in ("echomem_client", "llm_client", "eval"):
            lg = logging.getLogger(name)
            lg.setLevel(logging.DEBUG)
            lg.handlers.clear()
            lg.propagate = False
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
                    "args": self.run_args,
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
        """Copy EchoMem log files into the result directory."""
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


# ---------------------------------------------------------------------------
# Reusable argparse helpers -- called by plugins that need them
# ---------------------------------------------------------------------------

def add_echomem_args(parser: argparse.ArgumentParser) -> None:
    """Add EchoMem connection + identity args."""
    g = parser.add_argument_group("EchoMem")
    g.add_argument("--echomem-url", default="http://127.0.0.1:8010", help="EchoMem HTTP base URL")
    g.add_argument("--echomem-auth-key", default=os.getenv("ECHOMEM_AUTH_KEY", ""), help="EchoMem X-Auth-Key")
    g.add_argument("--account", default="default")
    g.add_argument("--user-id", default="default")
    g.add_argument("--agent-id", default="default")
    g.add_argument("--workspace", default="", help="EchoMem workspace path")


def add_llm_args(parser: argparse.ArgumentParser) -> None:
    """Add LLM CLI args."""
    g = parser.add_argument_group("LLM")
    g.add_argument("--llm-base-url", default=os.getenv("LLM_BASE_URL", ""), help="LLM API base URL")
    g.add_argument("--llm-model", default=os.getenv("LLM_MODEL", "doubao-seed-2.0-pro"))
    g.add_argument("--llm-api-key", default=os.getenv("LLM_API_KEY", ""), help="LLM API key")
    g.add_argument("--llm-temperature", type=float, default=0.7)
    g.add_argument("--llm-max-tokens", type=int, default=2048)
    g.add_argument("--llm-timeout-s", type=float, default=120.0)
    g.add_argument("--llm-retries", type=int, default=3)


def add_eval_args(parser: argparse.ArgumentParser) -> None:
    """Add eval-infra args (concurrency, output, timeouts, log collection)."""
    g = parser.add_argument_group("Evaluation")
    g.add_argument("--concurrency", type=int, default=4, help="Number of concurrent QA tasks")
    g.add_argument("--question-timeout-s", type=float, default=120.0, help="Per-question timeout")
    g.add_argument("--out-dir", default="results", help="Results root directory")
    g.add_argument("--echomem-log-dir", default="", help="EchoMem log directory for log collection")


def build_config_from_args(args) -> EvalConfig:
    """Build an EvalConfig from parsed argparse args."""
    return EvalConfig(
        concurrency=args.concurrency,
        question_timeout_s=args.question_timeout_s,
        echomem_log_dir=getattr(args, "echomem_log_dir", ""),
    )


def add_agent_plugin_args(
    parser: argparse.ArgumentParser,
    default_plugin: str = "baseline_mem",
) -> None:
    """Pre-scan ``sys.argv`` for ``--agent-plugin`` and add the plugin's CLI args.

    Call this **after** adding ``--agent-plugin`` to *parser* but **before**
    ``parse_args()``.  This lets ``--help`` show plugin-specific arguments.
    """
    plugin_name = default_plugin
    for i, arg in enumerate(sys.argv):
        if arg == "--agent-plugin" and i + 1 < len(sys.argv):
            plugin_name = sys.argv[i + 1]
            break
        if arg.startswith("--agent-plugin="):
            plugin_name = arg.split("=", 1)[1]
            break
    from agents import get_plugin_class
    plugin_cls = get_plugin_class(plugin_name)
    plugin_cls.add_arguments(parser)

#!/usr/bin/env python3
"""Unified CLI entrypoint for all dataset evaluations."""

from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path

from shared.runtime_config import (
    apply_cli_runtime_overrides,
    dataset_argument,
    prepare_runtime_environment,
    runtime_check,
)
from shared.eval_base import cleanup_pending_evaluation_identities
from shared.service_manager import start_echomem_service, stop_echomem_service


ROOT = Path(__file__).resolve().parent
RUNNERS = {
    "locomo": ROOT / "benchmarks" / "locomo" / "run_eval.py",
    "hotpotqa": ROOT / "benchmarks" / "hotpotqa" / "run_eval.py",
    "longmemeval": ROOT / "benchmarks" / "longmemeval" / "run_eval.py",
    "dynamic": ROOT / "dynamic" / "run_eval.py",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-command memory-system dataset evaluation",
        add_help=False,
    )
    parser.add_argument("benchmark", nargs="?", choices=sorted(RUNNERS))
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--start-echomem", action="store_true")
    parser.add_argument("--keep-echomem", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    args, forwarded = parser.parse_known_args()

    if args.help and args.benchmark is None:
        parser.print_help()
        return
    if args.benchmark is None:
        parser.error("the following arguments are required: benchmark")

    prepare_runtime_environment(ROOT, args.env_file)
    apply_cli_runtime_overrides(forwarded)
    if args.help:
        sys.argv = [str(RUNNERS[args.benchmark]), "--help"]
        runpy.run_path(str(RUNNERS[args.benchmark]), run_name="__main__")
        return

    managed_service = None
    auto_start = args.start_echomem or os.environ.get("ECHOMEM_AUTO_START", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    try:
        if auto_start:
            managed_service = start_echomem_service(
                ROOT,
                timeout_s=float(os.environ.get("ECHOMEM_START_TIMEOUT_S", "180")),
            )
            # init/server may have created the workspace auth file.
            prepare_runtime_environment(ROOT, args.env_file)
        if args.benchmark != "dynamic":
            forwarded = dataset_argument(args.benchmark, forwarded)
            errors = runtime_check(args.benchmark, forwarded)
            if errors:
                for error in errors:
                    print(f"[check] ERROR: {error}", file=sys.stderr)
                raise SystemExit(2)
            print(
                "[check] OK "
                f"benchmark={args.benchmark} "
                f"echomem={os.environ.get('ECHOMEM_BASE_URL', 'http://127.0.0.1:8010')} "
                f"model={os.environ.get('LLM_MODEL', '')}"
            )
            if args.check:
                forwarded = [*forwarded, "--check"]
        elif args.check:
            forwarded = [*forwarded, "--check"]

        sys.argv = [str(RUNNERS[args.benchmark]), *forwarded]
        runpy.run_path(str(RUNNERS[args.benchmark]), run_name="__main__")
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[eval] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    finally:
        cleanup_pending_evaluation_identities()
        if managed_service is not None and not args.keep_echomem:
            stop_echomem_service(managed_service)


if __name__ == "__main__":
    main()

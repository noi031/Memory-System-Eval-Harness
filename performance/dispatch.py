"""Target dispatch: forward run.py / probe.py to ``targets/<system>/main.py``.

``python run.py --target echomem [args...]`` loads
``performance.targets.echomem.main`` and calls its ``main(rest_argv)``, so
each target system keeps its own orchestration entry point while the
top-level CLI stays a single thin dispatcher.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

_TARGET_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def find_target_argv(argv: list[str]) -> tuple[str, list[str]] | None:
    """Extract ``--target <name>`` / ``--target=<name>`` and the remaining argv.

    Returns ``(name, rest_argv)`` when ``--target`` is present, else ``None``.
    """
    for index, item in enumerate(argv):
        if item == "--target":
            if index + 1 >= len(argv):
                raise ValueError("--target requires a system name")
            return argv[index + 1], argv[:index] + argv[index + 2:]
        if item.startswith("--target="):
            return item.split("=", 1)[1], argv[:index] + argv[index + 1:]
    return None


def run_target(name: str, rest_argv: list[str]) -> int:
    """Load ``performance.targets.<name>.main`` and run it with ``rest_argv``."""
    targets_root = Path(__file__).resolve().parent / "targets"
    if not _TARGET_RE.match(name) or not (targets_root / name).is_dir():
        print(f"error: unknown target system: {name}", file=sys.stderr)
        return 2
    module = importlib.import_module(f"performance.targets.{name}.main")
    main = getattr(module, "main", None)
    if not callable(main):
        print(f"error: target {name} has no main() entry point", file=sys.stderr)
        return 2
    return int(main(rest_argv) or 0)


def dispatch(argv: list[str]) -> int | None:
    """Forward to a target when ``--target`` is present, else return ``None``."""
    try:
        found = find_target_argv(argv)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if found is None:
        return None
    name, rest_argv = found
    return run_target(name, rest_argv)

#!/usr/bin/env python3
from __future__ import annotations

import runpy
from pathlib import Path
import sys


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    scripts_dir = root / "scripts"
    for candidate in (root, scripts_dir):
        text = str(candidate)
        if text not in sys.path:
            sys.path.insert(0, text)
    target = scripts_dir / "local_judge.py"
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()

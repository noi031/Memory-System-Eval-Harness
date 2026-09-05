"""CLI entry：按 --target 转发到 targets/<system>/main.py。

``python run.py --target <system> [args...]`` 加载
``performance.targets.<system>.main`` 并调用其 ``main(rest_argv)``，每个被测
系统自带编排入口，顶层 CLI 只做薄分发。通用场景引擎（run/validate/probe/list）
由 ``targets/general`` 承载：``python run.py --target general run --scene ...``。

不带 ``--target`` 直接退出 2。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 支持从任意位置运行（`cd performance && python run.py` 或项目根 `python -m performance.run`）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from performance.dispatch import dispatch


def main(argv: list[str] | None = None) -> int:
    # 现代终端（Windows Terminal / VS Code / Git Bash）按 UTF-8 渲染控制台输出
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass
    argv = list(sys.argv[1:] if argv is None else argv)
    target_result = dispatch(argv)
    if target_result is not None:
        return target_result
    print(
        "error: run.py requires --target <system> "
        "(e.g. --target general for the generic scene engine)",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""通用场景引擎 CLI（general target）。

通用引擎承载不针对具体记忆系统的压测场景与探针：场景、探针、画像、结果
分别放在 ``targets/general/scenes`` / ``probes`` / ``profiles`` /
``results`` 下。入口统一走 ``python run.py --target general``（由 performance
顶层转发到本模块）：

- ``run --scene <scene.py> [--profile <profile.yaml>]`` — 按画像执行场景的
  任务至负载时长结束，写 summary.json / records.csv。
- ``validate --scene <scene.py>`` — 导入并检查场景的任务契约。
- ``probe --scene <probe.py> [--profile <profile.yaml>]`` — 单次执行探针并
  报告 PASS/FAIL/NOT_IMPLEMENTED/INCONCLUSIVE。
- ``list [--dir <scenes/>]`` — 列出场景文件（默认扫全部 ``targets/<system>/scenes``，
  显示 ``system/scene`` 前缀）。

默认输出目录按场景位置推导为 ``targets/<system>/results/<ts>``，
因此 ``targets/general/scenes`` 下的场景产出落在 ``targets/general/results``。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 支持从任意位置运行（项目根 `python -m performance --target general`）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from performance.engine import Engine, SceneError, load_scene
from performance.probe import (
    ProbeRunner,
    load_probe,
    print_probe_summary,
    summarize_probe,
    write_probe_output,
)
from performance.profile import (
    LoadSpec,
    Profile,
    ProfileError,
    TargetSpec,
    load_profile,
    with_name,
)
from performance.report import print_summary, summarize, write_outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="performance",
        description="Generic HTTP scenario load-testing engine "
                    "(scenario = Python, load profile = YAML).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="execute a scene for the load duration")
    run.add_argument("--scene", required=True, help="path to the Python scene file")
    run.add_argument("--profile", default=None, help="path to the YAML load profile")
    run.add_argument("--out", default=None, help="output directory (default targets/<system>/results/<ts>)")
    run.add_argument("--base-url", default=None, help="override target.base_url")
    run.add_argument("--workers", type=int, default=None, help="override load.workers")
    run.add_argument("--duration", type=float, default=None, help="override load.duration_s (0 = until Ctrl-C)")
    run.add_argument("--mix", default=None, metavar="TASK=W[,TASK=W...]", help="override load.mix, e.g. read=8,write=1")
    run.add_argument("--strict", action="store_true", help="exit 1 when any request failed")
    run.set_defaults(func=_cmd_run)

    validate = sub.add_parser("validate", help="import and check a scene file")
    validate.add_argument("--scene", required=True, help="path to the Python scene file")
    validate.set_defaults(func=_cmd_validate)

    probe = sub.add_parser("probe", help="run a probe once and report its outcome")
    probe.add_argument("--scene", required=True, help="path to the Python probe file")
    probe.add_argument("--profile", default=None, help="path to the YAML profile")
    probe.add_argument("--base-url", default=None, help="override target.base_url")
    probe.add_argument("--out", default=None,
                       help="output JSON file (default targets/<system>/results/probe/<ts>/<name>.json)")
    probe.set_defaults(func=_cmd_probe)

    listing = sub.add_parser("list", help="list scene files (default: all targets)")
    listing.add_argument("--dir", default=None, help="scan a specific directory instead of targets/*/scenes")
    listing.set_defaults(func=_cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    # 现代终端（Windows Terminal / VS Code / Git Bash）按 UTF-8 渲染控制台输出
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (SceneError, ProfileError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


def _cmd_run(args: argparse.Namespace) -> int:
    scene = load_scene(args.scene)
    profile = with_name(load_profile(args.profile), scene.name)
    profile = _apply_overrides(profile, args)
    engine = Engine(profile, scene)
    arrival_text = ", ".join(
        f"{name}={spec.model}@{spec.rps:g}rps"
        for name, spec in profile.load.arrival.items()
    ) or "none (continuous)"
    duration_text = f"{profile.load.duration_s:g}s" if profile.load.duration_s > 0 else "until Ctrl-C"
    tasks_text = ", ".join(profile.load.mix or {name: 1 for name in scene.tasks})
    print(
        f"scene '{scene.name}' -> {profile.target.base_url} "
        f"(workers={profile.load.workers}, duration={duration_text}, "
        f"tasks=[{tasks_text}], arrival={arrival_text})"
    )
    result = engine.run()
    summary = _build_summary(scene, result, profile)
    print_summary(summary)
    out_dir = _make_out_dir(args.out, args.scene)
    write_outputs(out_dir, result, summary)
    print(f"outputs written to {out_dir}")
    if args.strict and summary["total"]["fail"] > 0:
        return 1
    return 0


def _build_summary(scene, result, profile) -> dict:
    """Aggregate the run; merge the scene's optional custom report in."""
    summary = summarize(result, profile)
    if scene.report is not None:
        summary["custom"] = scene.report(result, profile)
    return summary


def _cmd_probe(args: argparse.Namespace) -> int:
    probe = load_probe(args.scene)
    profile = with_name(load_profile(args.profile), probe.name)
    if args.base_url is not None:
        profile.target = TargetSpec(
            base_url=args.base_url.rstrip("/"),
            headers=profile.target.headers,
            read_timeout_s=profile.target.read_timeout_s,
        )
    result = ProbeRunner(profile, probe).run()
    summary = summarize_probe(probe, result, profile)
    print_probe_summary(summary)
    out_path = Path(args.out) if args.out else _make_probe_out_path(probe, args.scene)
    write_probe_output(out_path, summary)
    print(f"probe report written to {out_path}")
    return 0 if summary["status"] not in probe.exit_on else 2


def _default_out_root(scene_path) -> Path:
    """Default results root for a scene: targets/<system>/results.

    The scene lives at ``targets/<system>/scenes/<name>.py``; ``--out``
    overrides this when given explicitly.
    """
    return Path(scene_path).resolve().parent.parent / "results"


def _make_probe_out_path(probe, scene_path: str) -> Path:
    return (
        _default_out_root(scene_path)
        / "probe"
        / time.strftime("%Y%m%d_%H%M%S")
        / f"{probe.name}.json"
    )


def _cmd_validate(args: argparse.Namespace) -> int:
    scene = load_scene(args.scene)
    tasks = ", ".join(scene.tasks)
    print(
        f"OK: {Path(args.scene)} -> '{scene.name}' tasks=[{tasks}]"
        + (" schedule=yes" if scene.schedule else "")
    )
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    if args.dir is not None:
        directory = Path(args.dir)
        if not directory.is_dir():
            print(f"error: directory not found: {directory}", file=sys.stderr)
            return 2
        for scene_path in _iter_scenes(directory):
            _print_scene(scene_path, prefix="")
        return 0
    # default: every target system's scenes directory
    targets_root = Path(__file__).resolve().parent.parent
    if not targets_root.is_dir():
        print(f"error: targets directory not found: {targets_root}", file=sys.stderr)
        return 2
    found = False
    for system_dir in sorted(path for path in targets_root.iterdir() if path.is_dir()):
        for scene_path in _iter_scenes(system_dir / "scenes"):
            _print_scene(scene_path, prefix=f"{system_dir.name}/")
            found = True
    if not found:
        print(f"no scene files (*.py) found under {targets_root}/<system>/scenes")
    return 0


def _iter_scenes(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    # underscore-prefixed modules are protocol/helpers, not scenes
    return sorted(path for path in directory.glob("*.py") if not path.name.startswith("_"))


def _print_scene(scene_path: Path, *, prefix: str) -> None:
    try:
        scene = load_scene(scene_path)
    except SceneError as exc:
        print(f"{prefix}{scene_path.name}: INVALID ({exc})")
        return
    print(f"{prefix}{scene_path.name}: tasks=[{', '.join(scene.tasks)}] — {scene.description}")


def _apply_overrides(profile: Profile, args: argparse.Namespace) -> Profile:
    target = profile.target
    load = profile.load
    if args.base_url is not None:
        target = TargetSpec(
            base_url=args.base_url.rstrip("/"),
            headers=target.headers,
            read_timeout_s=target.read_timeout_s,
        )
    if args.workers is not None or args.duration is not None or args.mix is not None:
        mix = load.mix
        if args.mix is not None:
            mix = _parse_mix_override(args.mix)
        load = LoadSpec(
            workers=args.workers if args.workers is not None else load.workers,
            duration_s=args.duration if args.duration is not None else load.duration_s,
            mix=mix,
            arrival=load.arrival,
        )
    profile.target = target
    profile.load = load
    return profile


def _parse_mix_override(text: str) -> dict[str, int]:
    mix: dict[str, int] = {}
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        task_name, _, weight = part.partition("=")
        task_name = task_name.strip()
        if not task_name or not weight.strip().lstrip("-").isdigit():
            raise ValueError(f"invalid --mix entry '{part}': expected TASK=WEIGHT")
        mix[task_name] = int(weight)
    if not mix or sum(mix.values()) == 0:
        raise ValueError("--mix requires at least one positive TASK=WEIGHT")
    return mix


def _make_out_dir(explicit: str | None, scene_path: str) -> Path:
    if explicit:
        return Path(explicit)
    return _default_out_root(scene_path) / time.strftime("%Y%m%d_%H%M%S")

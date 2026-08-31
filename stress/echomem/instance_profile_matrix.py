#!/usr/bin/env python3
"""Run the same real HTTP suite against externally managed instance profiles.

The harness does not assume that a "2U" or "8U" profile has a particular
container image or resource limit.  Each profile supplies a preparation
command that deploys or resets the target service, then the exact same formal
scenarios are executed and recorded for comparison.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    profiles = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("profile plan must contain a non-empty profiles list")
    for profile in profiles:
        if not isinstance(profile, dict) or not profile.get("name"):
            raise ValueError("every profile must contain a name")
        if not profile.get("base_url"):
            raise ValueError(f"profile {profile.get('name')} is missing base_url")
        if not profile.get("tenant_config"):
            raise ValueError(f"profile {profile.get('name')} is missing tenant_config")
    return payload


def run_command(command: str, *, cwd: str = "") -> dict[str, Any]:
    started = now()
    completed = subprocess.run(
        command,
        shell=True,
        cwd=cwd or None,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
        "started_at": started,
        "finished_at": now(),
    }


def formal_command(
    profile: dict[str, Any],
    args: argparse.Namespace,
    output: Path,
) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).with_name("formal_suite.py")),
        "--base-url",
        str(profile["base_url"]),
        "--tenant-config",
        str(Path(profile["tenant_config"]).expanduser()),
        "--out-dir",
        str(output),
        "--profile",
        args.profile,
        "--repeats",
        str(args.repeats),
        "--commit-workers",
        str(args.commit_workers),
        "--search-workers",
        str(args.search_workers),
    ]
    if profile.get("preflight_config"):
        command += ["--preflight-config", str(Path(profile["preflight_config"]).expanduser())]
    if args.scenarios:
        command += ["--scenarios", args.scenarios]
    if args.auth_header:
        command += ["--auth-header", args.auth_header]
    if args.case_timeout_s > 0:
        command += ["--case-timeout-s", str(args.case_timeout_s)]
    if args.duration_cap_s > 0:
        command += ["--duration-cap-s", str(args.duration_cap_s)]
    if args.allow_shared_identity:
        command.append("--allow-shared-identity")
    if args.no_server_metrics:
        command.append("--no-server-metrics")
    return command


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare the same real EchoMem suite across externally managed instance profiles"
    )
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--profile", default="pr421", choices=("pr421", "complete"))
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--commit-workers", type=int, default=8)
    parser.add_argument("--search-workers", type=int, default=32)
    parser.add_argument("--auth-header", default=os.getenv("ECHOMEM_AUTH_HEADER", "X-API-Key"))
    parser.add_argument("--case-timeout-s", type=float, default=0.0)
    parser.add_argument("--duration-cap-s", type=float, default=0.0)
    parser.add_argument("--allow-shared-identity", action="store_true")
    parser.add_argument("--no-server-metrics", action="store_true")
    args = parser.parse_args()

    if args.repeats < 1:
        parser.error("--repeats must be >= 1")
    plan = load_plan(args.plan.expanduser().resolve())
    root = args.out_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "created_at": now(),
        "plan": str(args.plan.expanduser().resolve()),
        "profile": args.profile,
        "scenarios": args.scenarios,
        "repeats": args.repeats,
        "profiles": [],
    }

    for index, profile in enumerate(plan["profiles"], start=1):
        name = str(profile["name"])
        profile_root = root / f"{index:02d}-{name}"
        profile_root.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "name": name,
            "base_url": profile["base_url"],
            "resource_profile": profile.get("resource_profile", {}),
            "prepare": None,
            "suite": None,
        }
        prepare = profile.get("prepare_command") or profile.get("reset_command")
        if prepare:
            entry["prepare"] = run_command(str(prepare), cwd=str(profile.get("cwd") or ""))
            (profile_root / "prepare.stdout.log").write_text(
                entry["prepare"]["stdout"], encoding="utf-8"
            )
            (profile_root / "prepare.stderr.log").write_text(
                entry["prepare"]["stderr"], encoding="utf-8"
            )
            if entry["prepare"]["returncode"] != 0:
                entry["status"] = "PREPARE_FAILED"
                manifest["profiles"].append(entry)
                (root / "matrix.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                continue

        command = formal_command(profile, args, profile_root / "suite")
        entry["command"] = shlex.join(command)
        completed = subprocess.run(
            command, text=True, capture_output=True, check=False
        )
        (profile_root / "suite.stdout.log").write_text(
            completed.stdout, encoding="utf-8"
        )
        (profile_root / "suite.stderr.log").write_text(
            completed.stderr, encoding="utf-8"
        )
        suite_path = profile_root / "suite" / "suite.json"
        entry["returncode"] = completed.returncode
        entry["status"] = "PASS" if completed.returncode == 0 else "FAILED"
        if suite_path.is_file():
            entry["suite"] = str(suite_path)
            try:
                suite = json.loads(suite_path.read_text(encoding="utf-8"))
                entry["run_count"] = len(suite.get("runs") or [])
                entry["run_statuses"] = {
                    str(run.get("scenario")): str(run.get("status"))
                    for run in suite.get("runs") or []
                }
            except json.JSONDecodeError as exc:
                entry["suite_error"] = str(exc)
        manifest["profiles"].append(entry)
        (root / "matrix.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    statuses = [item.get("status") for item in manifest["profiles"]]
    overall = "PASS" if statuses and all(item == "PASS" for item in statuses) else "FAILED"
    manifest["status"] = overall
    (root / "matrix.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": overall, "profiles": len(statuses)}, ensure_ascii=False))
    return 0 if overall == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

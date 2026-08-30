#!/usr/bin/env python3
"""Run a real, bounded load sweep and recovery probe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from limit_failure_probe import (
    create_sessions,
    discover_sessions,
    load_tenants,
    run_wave,
    write_report,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tenant-config", type=Path, required=True)
    parser.add_argument("--session-root", type=Path, required=True)
    parser.add_argument(
        "--create-sessions",
        action="store_true",
        help="Create sessions on the target instead of reusing another run's CSV",
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--levels", default="4,16,64,128,256")
    parser.add_argument("--timeout-s", type=float, default=8.0)
    args = parser.parse_args()

    tenants = load_tenants(args.tenant_config)
    sessions = (
        create_sessions(args.base_url, tenants, args.timeout_s)
        if args.create_sessions
        else discover_sessions(args.session_root, tenants)
    )
    levels = [int(item.strip()) for item in args.levels.split(",") if item.strip()]
    rows = []
    for workers in levels:
        # Keep each level bounded while making the arrival burst visible.
        count = min(512, max(32, workers * 2))
        for kind, amount in (("search", count), ("commit", count), ("open", count)):
            path = "/api/retrieval/search" if kind == "search" else "/api/sessions/open"
            if kind == "commit":
                path = "/commit"
            level_rows = run_wave(
                args.base_url,
                tenants,
                sessions,
                kind=kind,
                count=amount,
                workers=workers,
                timeout_s=args.timeout_s,
                path=path,
            )
            for row in level_rows:
                row["kind"] = f"{kind}-workers-{workers}"
            rows.extend(level_rows)

    # A small post-load wave demonstrates whether the service recovers.
    recovery = run_wave(
        args.base_url,
        tenants,
        sessions,
        kind="search",
        count=16,
        workers=4,
        timeout_s=args.timeout_s,
        path="/api/retrieval/search",
    )
    for row in recovery:
        row["kind"] = "recovery-search-workers-4"
    rows.extend(recovery)
    manifest = {
        "test_type": "real_limit_failure_sweep",
        "base_url": args.base_url,
        "tenants": [item["tenant_id"] for item in tenants],
        "workers_levels": levels,
        "timeout_s": args.timeout_s,
        "client_admission": False,
        "recovery_probe": "16 Search requests at 4 workers after the sweep",
        "session_source": "target_open" if args.create_sessions else "existing_result_csv",
    }
    write_report(args.out_dir, manifest, rows)
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

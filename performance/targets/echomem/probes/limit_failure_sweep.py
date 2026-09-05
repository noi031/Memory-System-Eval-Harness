"""真实、有界的负载扫描与恢复探针（薄包装，委托 limit_failure 的辅助函数）。

对 levels 每一档 worker 并发执行 search/commit/open wave，扫描结束后再跑
一小波恢复探针。数据采集器，没有失败语义：``exit_on = ()``，整体恒
PASS、退出码恒 0。

配置经 ``ctx.params`` 读取（键名与原 CLI 参数同名）：``tenant_config`` /
``session_root`` / ``create_sessions`` / ``out_dir`` / ``levels`` /
``timeout_s`` / ``search_count`` / ``commit_count`` / ``open_count`` /
``workers`` / ``kinds``；``base_url`` 来自 ``ctx.base_url``。``out_dir``
配置了才写盘（requests.json/csv、summary.json、report.html）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from performance.ctx import Ctx
from performance.targets.echomem.probes.limit_failure import (
    create_sessions,
    discover_sessions,
    load_tenants,
    run_wave,
    write_report,
)

exit_on = ()


def run(ctx: Ctx) -> None:
    params = ctx.params
    base_url = ctx.base_url
    tenant_config = Path(str(params.get("tenant_config") or ""))
    session_root = Path(str(params.get("session_root") or ""))
    create_sessions_enabled = bool(params.get("create_sessions", False))
    out_dir_raw = params.get("out_dir")
    out_dir = Path(str(out_dir_raw)) if out_dir_raw else None
    levels = [
        int(item.strip())
        for item in str(params.get("levels", "4,16,64,128,256")).split(",")
        if item.strip()
    ]
    timeout_s = float(params.get("timeout_s", 8.0))
    search_count = int(params.get("search_count", 0))
    commit_count = int(params.get("commit_count", 0))
    open_count = int(params.get("open_count", 0))
    workers = int(params.get("workers", 0))
    kinds = [
        item.strip().lower()
        for item in str(params.get("kinds", "search,commit,open")).split(",")
        if item.strip()
    ]
    unknown_kinds = sorted(set(kinds) - {"search", "commit", "open"})
    if not kinds or unknown_kinds:
        raise ValueError(
            "kinds must contain one or more of search,commit,open; "
            f"unknown: {', '.join(unknown_kinds)}"
        )

    tenants = load_tenants(tenant_config)
    sessions = (
        create_sessions(base_url, tenants, timeout_s)
        if create_sessions_enabled
        else discover_sessions(session_root, tenants)
    )
    rows: list[dict[str, Any]] = []
    for level in levels:
        # 配置的 workers 是上限而非替代值：否则固定值（如 256）会让每档
        # level 都以同一并发运行，扫描就测不出容量边界。
        effective_workers = (
            max(1, min(level, workers)) if workers > 0 else level
        )
        default_count = min(512, max(32, effective_workers * 2))
        counts = {
            "search": search_count if search_count > 0 else default_count,
            "commit": commit_count if commit_count > 0 else default_count,
            "open": open_count if open_count > 0 else default_count,
        }
        for kind in kinds:
            path = "/api/retrieval/search" if kind == "search" else "/api/sessions/open"
            if kind == "commit":
                path = "/commit"
            level_rows = run_wave(
                base_url,
                tenants,
                sessions,
                kind=kind,
                count=counts[kind],
                workers=effective_workers,
                timeout_s=timeout_s,
                path=path,
            )
            for row in level_rows:
                row["kind"] = f"{kind}-workers-{level}"
            rows.extend(level_rows)

    # A small post-load wave demonstrates whether the service recovers.
    recovery = run_wave(
        base_url,
        tenants,
        sessions,
        kind="search",
        count=16,
        workers=4,
        timeout_s=timeout_s,
        path="/api/retrieval/search",
    )
    for row in recovery:
        row["kind"] = "recovery-search-workers-4"
    rows.extend(recovery)
    manifest = {
        "test_type": "real_limit_failure_sweep",
        "base_url": base_url,
        "tenants": [item["tenant_id"] for item in tenants],
        "workers_levels": levels,
        "worker_override": workers or None,
        "counts": {
            "search": search_count or "auto",
            "commit": commit_count or "auto",
            "open": open_count or "auto",
        },
        "kinds": kinds,
        "timeout_s": timeout_s,
        "client_admission": False,
        "recovery_probe": "16 Search requests at 4 workers after the sweep",
        "session_source": "target_open" if create_sessions_enabled else "existing_result_csv",
    }
    if out_dir is not None:
        write_report(out_dir, manifest, rows)
    ctx.check(
        "limit_failure_sweep",
        status="PASS",
        reason=f"levels={levels} kinds={','.join(kinds)} total={len(rows)} requests",
        detail=json.dumps(manifest, ensure_ascii=False)[:500],
    )

"""单 instance profile 的正式套件执行（echomem 侧薄包装）。

通用编排 ``performance.suite.run_suite`` 负责 prepare → preflight → 灌种
→ 逐 case → acceptance → suite.json/acceptance.json 的整体流程；本模块挂
echomem 的钩子：case 选择（``select_cases``）、case → Profile（含 quick
收敛与 barrier/burst 参数，``build_case_profile``）、单 case 执行
（``run_case``，summarize 包装 + commit/search 证据 CSV）、preflight /
seed / acceptance 求值。

``run_case`` 与 ``summarize_case_records`` 仍在本模块导出，供测试直接使用。
"""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any

from performance.monitor import MetricsMonitor, write_metrics_csv
from performance.profile import Profile
from performance.records import RequestRecord
from performance.suite import (
    SeedContext,
    run_case as run_case_impl,
    run_suite as run_suite_impl,
    summarize_case_records as summarize_case_metrics,
)
from performance.targets.echomem.acceptance.evaluate import (
    evaluate_pr421_acceptance,
)
from performance.targets.echomem.acceptance.metrics import metric_coverage
from performance.targets.echomem.acceptance.preflight import run_preflight
from performance.targets.echomem.acceptance.seed import (
    TenantPreparer,
    load_tenant_specs,
)
from performance.targets.echomem.orchestrator.suites import (
    QuickSpec,
    build_case_profile,
    select_cases,
)
from performance.targets.echomem.protocol import is_anchor_query

SCENES_DIR = Path(__file__).resolve().parent.parent / "scenes"


def summarize_case_records(records: list[RequestRecord]) -> dict:
    """records → suite per-run summary（通用 metrics + echomem 扩展）。

    通用层 ``suite.summarize_case_records`` 只产出 metrics；这里补
    details/parameters（identity_mode/quality_seed/延迟阈值），并用
    ``is_anchor_query`` 计算 quality_asserted。
    """
    summary = summarize_case_metrics(records, is_anchor=is_anchor_query)
    summary["details"] = {"identity_mode": "independent_auth_keys", "quality_seed": []}
    summary["parameters"] = {
        "commit_delay_threshold_s": 10.0,
        "search_delay_threshold_s": 2.5,
    }
    return summary


# ---------------------------------------------------------------------- #
#  单 case 执行与产物                                                    #
# ---------------------------------------------------------------------- #

def _write_commit_results(case_dir: Path, records: list[RequestRecord]) -> None:
    """commit_results.csv：每条 commit_submit 一行，按 commit_done 对账状态。"""
    done_by_session = {
        r.session_id: r
        for r in records
        if r.op == "commit_done" and r.session_id
    }
    rows: list[dict[str, Any]] = []
    for record in records:
        if record.op != "commit_submit":
            continue
        done = done_by_session.get(record.session_id)
        if done is not None and done.status == "ok":
            status, done_ms = "completed", done.stage_ms
        elif done is not None:
            status, done_ms = "failed", done.stage_ms
        else:
            status, done_ms = "submitted", None
        rows.append(
            {
                "tenant_idx": record.tenant_idx,
                "session_id": record.session_id,
                "archive_id": record.archive_id,
                "status": status,
                "submit_ms": round(record.stage_ms, 3),
                "done_ms": round(done_ms, 3) if done_ms is not None else "",
                "end_to_end_s": round(done_ms / 1000.0, 3) if done_ms is not None else "",
            }
        )
    with (case_dir / "commit_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "tenant_idx", "session_id", "archive_id", "status",
                "submit_ms", "done_ms", "end_to_end_s",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_search_results(case_dir: Path, records: list[RequestRecord]) -> None:
    """search_results.csv：每条 read 一行，含质量字段与拒绝响应证据。"""
    rows: list[dict[str, Any]] = []
    for record in records:
        if record.op != "read":
            continue
        if record.status == "ok":
            status_code = "200"
        elif record.error_type == "http_4xx" and (
            record.retry_after_s is not None or record.reason_code
        ):
            status_code = "429"
        else:
            status_code = "500"
        rows.append(
            {
                "query": record.query,
                "hit_count": record.hit_count,
                "quality_ok": record.quality_ok,
                "degraded": record.degraded,
                "status_code": status_code,
                "end_to_end_s": round(record.stage_ms / 1000.0, 3),
                "tenant": record.tenant_idx,
                "session_id": record.session_id,
                "retry_after_s": record.retry_after_s or "",
                "reason_code": record.reason_code,
            }
        )
    with (case_dir / "search_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "query", "hit_count", "quality_ok", "degraded", "status_code",
                "end_to_end_s", "tenant", "session_id", "retry_after_s", "reason_code",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_case_evidence(case_dir: Path, records: list[RequestRecord]) -> None:
    """commit/search 证据 CSV（echomem 专属，写在通用 summary/records 之后）。"""
    _write_commit_results(case_dir, records)
    _write_search_results(case_dir, records)


def run_case(
    case: dict,
    profile: Profile,
    *,
    case_dir: Path,
    timeout_s: float | None = None,
    collect_metrics: bool = True,
) -> dict:
    """执行单个 case（经通用层 run_case）：load_scene + Engine.run 并写产物。

    通用层负责执行与 summary.json/records.csv；这里挂上 echomem 的
    summarize 包装（metrics + details/parameters）与 commit/search 证据 CSV。
    ``collect_metrics`` 时用 ``MetricsMonitor`` 后台采样服务端 /metrics，
    写 metrics_samples.csv 并把 PR421 B7 覆盖证据挂到 summary.details。
    """
    monitor = None
    started = time.time()
    if collect_metrics and profile.target.base_url:
        monitor = MetricsMonitor(profile.target.base_url, interval_s=2.0, timeout_s=5.0)
        monitor.start()
    try:
        result = run_case_impl(
            case,
            profile,
            scene_path=SCENES_DIR / f"{case['scene']}.py",
            case_dir=case_dir,
            timeout_s=timeout_s,
            summarize=summarize_case_records,
            write_evidence=_write_case_evidence,
        )
    finally:
        if monitor is not None:
            monitor.stop()
    if monitor is not None:
        write_metrics_csv(case_dir, monitor)
        summary = result["summary"]
        details = summary.setdefault("details", {})
        details["pr421_metric_coverage"] = metric_coverage(monitor, started, time.time())
    return result


# ---------------------------------------------------------------------- #
#  suite 编排（钩子 + 薄包装）                                            #
# ---------------------------------------------------------------------- #

def _preflight_stage(config: str) -> dict:
    """preflight 阶段条目：配置缺失时给 NOT_RUN，否则运行并附加 config。"""
    if not config:
        return {
            "status": "NOT_RUN", "config": "", "engines_checked": 0,
            "engines": [], "digest": "",
        }
    result = run_preflight(config, timeout_s=30.0)
    return {**result, "config": config}


def _prepare_seed(
    base_url: str,
    tenant_config: str,
    max_tenants: int,
    seed_sessions: int,
    seed_messages: int,
) -> tuple[list[SeedContext], dict]:
    """灌种：解析租户规格 → TenantPreparer 打开真实 session，返回上下文与摘要。"""
    specs = load_tenant_specs(tenant_config, tenant_count=max_tenants)
    preparer = TenantPreparer(base_url, tenant_specs=specs)
    contexts = preparer.prepare(
        seed_sessions, seed_messages, commit_poll_timeout_s=600.0
    )
    return [
        SeedContext(
            tenant_id=ctx.tenant_id,
            auth_key=ctx.auth_key,
            queries=list(ctx.queries),
        )
        for ctx in contexts
    ], {
        "status": "completed",
        "tenant_count": len(contexts),
        "identity_mode": preparer.identity_mode(),
        "seed_sessions_per_tenant": seed_sessions,
        "seed_messages_per_session": seed_messages,
    }


def run_suite(
    profile: dict,
    *,
    suite_dir: Path,
    quick: QuickSpec | None = None,
    profile_name: str = "4u8g",
    base_url: str = "",
    timeout_s: float = 120.0,
    scenarios: list[str] | None = None,
) -> dict:
    """执行单个 instance profile 的正式套件（通用编排 + echomem 钩子）。

    profile = instance-profiles JSON 里的单个 profile dict。通用流程见
    ``performance.suite.run_suite``；这里传入 echomem 的 case 选择、Profile
    构造（auth_headers 恒为空，租户凭据由灌种上下文按 case 覆盖）、单 case
    执行、preflight/seed/acceptance 钩子。``metrics_enabled`` 控制 case 级
    服务端 /metrics 采样。
    """
    metrics_enabled = bool(profile.get("metrics_enabled", True))

    def _run_case(case, case_profile, *, case_dir, timeout_s):
        return run_case(
            case, case_profile, case_dir=case_dir, timeout_s=timeout_s,
            collect_metrics=metrics_enabled,
        )

    return run_suite_impl(
        profile,
        suite_dir=suite_dir,
        profile_name=profile_name,
        base_url=base_url,
        timeout_s=timeout_s,
        scenarios=scenarios,
        quick=quick,
        select_cases=select_cases,
        build_profile=lambda case, url, tenant_count, q: build_case_profile(
            case, base_url=url, tenant_count=tenant_count, auth_headers={}, quick=q
        ),
        run_case=_run_case,
        preflight=_preflight_stage,
        seed=_prepare_seed,
        evaluate=evaluate_pr421_acceptance,
    )

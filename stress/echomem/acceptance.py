#!/usr/bin/env python3
"""Conservative PR421 acceptance checks for formal stress-suite results.

The checks consume only recorded run summaries and request CSVs.  Missing
server evidence is never inferred from client timing, and capabilities that
require an unavailable EchoMem control plane are reported explicitly.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"

# Review status is kept separate from measured PR421 gates. A harness item
# can be resolved while the corresponding EchoMem capability is unavailable.
PR28_REVIEW_RESOLUTION = [
    {
        "item": "Commit barrier and tenant distributions",
        "status": "RESOLVED",
        "evidence": "commit-barrier; uniform/zipf/explicit distributions",
    },
    {
        "item": "Retry-After retry and retry audit",
        "status": "RESOLVED",
        "evidence": "commit_with_retry; commit_results.csv; summary.json",
    },
    {
        "item": "Server-observe boundary and telemetry completeness",
        "status": "RESOLVED",
        "evidence": "server-observe; server_* fields; metric coverage",
    },
    {
        "item": "Real multi-tenant isolation evidence",
        "status": "RESOLVED",
        "evidence": "independent tenant credentials; directed marker probes",
    },
    {
        "item": "Commit final completion",
        "status": "PARTIAL",
        "evidence": "terminal-state polling is present; cursor/message-set reconciliation is absent",
    },
    {
        "item": "Saturation discipline",
        "status": "PARTIAL",
        "evidence": "saturation and rejection contract are present; queue-full precondition and recovery check are absent",
    },
    {
        "item": "Reproducible EchoMem environment",
        "status": "PARTIAL",
        "evidence": "runner environment is reproducible; target resource/profile/MySQL topology is not owned by the harness",
    },
    {
        "item": "k6 toolchain",
        "status": "NOT_IMPLEMENTED",
        "evidence": "formal runner remains Python standard-library based",
    },
    {
        "item": "Fault injection and restart recovery",
        "status": "NOT_IMPLEMENTED",
        "evidence": "no real LLM/vector fault injector or kill-9 deployment adapter",
    },
    {
        "item": "Incident regression and full capacity ladder",
        "status": "NOT_IMPLEMENTED",
        "evidence": "S7-S10 and complete 2/4/8/16/32 resource profiles are unavailable",
    },
    {
        "item": "Persistence reconciliation judge",
        "status": "NOT_IMPLEMENTED",
        "evidence": "cursor/message-set export adapter is unavailable",
    },
]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _result(
    name: str,
    status: str,
    *,
    target: Any = None,
    observed: Any = None,
    evidence: str = "",
    reason: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "target": target,
        "observed": observed,
        "evidence": evidence,
        "reason": reason,
    }


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    return run.get("summary") or {}


def _runs_for(manifest: dict[str, Any], scenario: str) -> list[dict[str, Any]]:
    return [
        run for run in manifest.get("runs") or []
        if str(run.get("scenario") or "") == scenario
    ]


def _metric_values(
    runs: list[dict[str, Any]],
    path: tuple[str, ...],
) -> list[float]:
    values: list[float] = []
    for run in runs:
        current: Any = _run_summary(run)
        for key in path:
            current = current.get(key) if isinstance(current, dict) else None
        number = _number(current)
        if number is not None:
            values.append(number)
    return values


def _target_coverage(manifest: dict[str, Any]) -> dict[str, Any]:
    runs = manifest.get("runs") or []
    coverages = [
        (_run_summary(run).get("details") or {}).get("pr421_metric_coverage")
        for run in runs
    ]
    coverages = [item for item in coverages if isinstance(item, dict)]
    if not coverages:
        return _result(
            "B7 lane/fan-out metrics",
            INCONCLUSIVE,
            evidence="details.pr421_metric_coverage",
            reason="没有采集到 PR421 B7 指标覆盖证据",
        )
    missing = sorted({
        str(key)
        for item in coverages
        for key in (item.get("missing") or [])
    })
    present = sorted({
        str(key)
        for item in coverages
        for key, value in (item.get("present") or {}).items()
        if value
    })
    status = PASS if not missing else INCONCLUSIVE
    return _result(
        "B7 lane/fan-out metrics",
        status,
        target="6 metric families with bounded labels",
        observed={"present": present, "missing": missing},
        evidence="details.pr421_metric_coverage",
        reason=(
            "全部指标族均有服务端证据"
            if status == PASS
            else "指标族未完整暴露，不能证明服务端 lane/fan-out 行为"
        ),
    )


def _search_success_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    runs = [
        run for run in manifest.get("runs") or []
        if str(run.get("scenario") or "") in {"mixed", "search-storm", "saturation"}
    ]
    rates = _metric_values(runs, ("metrics", "search", "success_rate"))
    if not rates:
        return _result(
            "Search success rate",
            INCONCLUSIVE,
            target=0.999,
            evidence="metrics.search.success_rate",
            reason="没有足够的 Search 成功率数据",
        )
    observed = min(rates)
    return _result(
        "Search success rate",
        PASS if observed >= 0.999 else FAIL,
        target=0.999,
        observed=observed,
        evidence="metrics.search.success_rate",
        reason="按所有选定压力场景的最差轮次判定",
    )


def _search_isolation_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    baseline = _metric_values(
        _runs_for(manifest, "baseline"),
        ("metrics", "search", "latency", "p95_s"),
    )
    stressed = _metric_values(
        _runs_for(manifest, "mixed") + _runs_for(manifest, "search-storm"),
        ("metrics", "search", "latency", "p95_s"),
    )
    if not baseline or not stressed or min(baseline) <= 0:
        return _result(
            "Search P95 isolation ratio",
            INCONCLUSIVE,
            target=1.20,
            evidence="baseline/mixed/search-storm metrics.search.latency.p95_s",
            reason="缺少有效基线或压力场景的成功请求 P95",
        )
    ratio = max(stressed) / min(baseline)
    return _result(
        "Search P95 isolation ratio",
        PASS if ratio <= 1.20 else FAIL,
        target=1.20,
        observed=ratio,
        evidence={"baseline_p95_s": baseline, "stressed_p95_s": stressed},
        reason=(
            "当前按已记录轮次的最差压力 P95/最优基线 P95计算；"
            "仅比较成功请求，超时率由 Search success rate 单独判定"
        ),
    )


def _fairness_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    runs = _runs_for(manifest, "tenant-skew") + _runs_for(manifest, "mixed")
    values: list[float] = []
    evidence: list[dict[str, Any]] = []
    for run in runs:
        fairness = (_run_summary(run).get("metrics") or {}).get("fairness") or {}
        completed = fairness.get("commit_completed_per_tenant") or {}
        rates = [_number(value) for value in completed.values()]
        rates = [value for value in rates if value is not None and value >= 0]
        if len(rates) < 2 or sum(value * value for value in rates) == 0:
            continue
        total = sum(rates)
        jain = total * total / (len(rates) * sum(value * value for value in rates))
        values.append(jain)
        evidence.append({"commit_completed_per_tenant": completed, "jain": jain})
    if not values:
        return _result(
            "Tenant fairness (Jain)",
            INCONCLUSIVE,
            target=0.90,
            evidence="metrics.fairness.commit_completed_per_tenant",
            reason="没有至少两个租户的有效 Commit 完成吞吐样本",
        )
    observed = min(values)
    return _result(
        "Tenant fairness (Jain)",
        PASS if observed >= 0.90 else FAIL,
        target=0.90,
        observed=observed,
        evidence=evidence,
        reason="按逐租户 Commit 完成吞吐计算并取最差轮次；延迟公平性另行展示",
    )


def _commit_completion_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    runs = _runs_for(manifest, "commit-barrier") + _runs_for(manifest, "tenant-skew")
    rates = _metric_values(runs, ("metrics", "commit", "success_rate"))
    if not rates:
        return _result(
            "Accepted Commit eventual completion",
            INCONCLUSIVE,
            target=1.0,
            evidence="metrics.commit.success_rate",
            reason="没有 Commit 最终状态数据",
        )
    observed = min(rates)
    return _result(
        "Accepted Commit eventual completion",
        PASS if observed >= 1.0 else FAIL,
        target=1.0,
        observed=observed,
        evidence="metrics.commit.success_rate",
        reason=(
            "当前按 runner 最终状态统计；尚未包含 cursor/消息集合对账"
        ),
    )


def _rejection_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    runs = _runs_for(manifest, "saturation")
    if not runs:
        return _result(
            "Saturation rejection rate",
            INCONCLUSIVE,
            target={"rate_max": 0.05, "latency_max_s": 1.0},
            reason="未执行 saturation 场景",
        )
    rejected = total = 0
    rejected_latencies: list[float] = []
    response_fields_complete = True
    for run in runs:
        output_dir = Path(run.get("output_dir") or "")
        rows = _read_csv(output_dir / "search_results.csv")
        for row in rows:
            code = _number(row.get("status_code"))
            if code is None:
                continue
            total += 1
            if int(code) in {429, 503}:
                rejected += 1
                latency = _number(row.get("end_to_end_s")) or _number(row.get("elapsed_s"))
                if latency is not None:
                    rejected_latencies.append(latency)
                # A rejection is only contract-complete when both the
                # retry hint and the server-provided reason are present.
                if not row.get("retry_after_s") or not row.get("reason_code"):
                    response_fields_complete = False
    if not total:
        return _result(
            "Saturation rejection rate",
            INCONCLUSIVE,
            target={"rate_max": 0.05, "latency_max_s": 1.0},
            evidence="search_results.csv",
            reason="saturation 没有有效 Search 响应",
        )
    if not rejected:
        return _result(
            "Saturation rejection rate",
            INCONCLUSIVE,
            target={"rate_max": 0.05, "latency_max_s": 1.0},
            observed={"rejection_rate": 0.0, "responses": total},
            evidence="search_results.csv",
            reason="没有实际 429/503 拒绝样本，无法验证 PR421 拒绝响应契约",
        )
    rate = rejected / total
    max_latency = max(rejected_latencies) if rejected_latencies else None
    observed = {"rejection_rate": rate, "max_rejection_latency_s": max_latency}
    if not response_fields_complete and rejected:
        status = INCONCLUSIVE
        reason = "存在拒绝响应，但 Retry-After/reason_code 完整性无法证明"
    elif rate > 0.05 or (max_latency is not None and max_latency > 1.0):
        status = FAIL
        reason = "拒绝率或拒绝响应耗时超过 PR421 门槛"
    else:
        status = PASS
        reason = "拒绝率和拒绝响应耗时均未超过门槛"
    return _result(
        "Saturation rejection rate",
        status,
        target={"rate_max": 0.05, "latency_max_s": 1.0},
        observed=observed,
        evidence="search_results.csv",
        reason=reason,
    )


def _hot_tenant_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    runs = _runs_for(manifest, "tenant-skew")
    ratios: list[float] = []
    evidence: list[dict[str, Any]] = []
    for run in runs:
        metrics = _run_summary(run).get("metrics") or {}
        per_tenant = metrics.get("per_tenant") or {}
        if len(per_tenant) < 4:
            continue
        hot = max(
            per_tenant,
            key=lambda tenant: int(
                ((per_tenant[tenant].get("commit") or {}).get("submitted")) or 0
            ),
        )
        bystander_p50 = []
        for tenant, data in per_tenant.items():
            if tenant == hot:
                continue
            p50 = _number(
                ((data.get("commit") or {}).get("completion") or {}).get("p50_s")
            )
            if p50 is not None and p50 > 0:
                bystander_p50.append(p50)
        if len(bystander_p50) >= 2:
            ratio = max(bystander_p50) / min(bystander_p50)
            ratios.append(ratio)
            evidence.append({"hot_tenant": hot, "bystander_p50_s": bystander_p50})
    if not ratios:
        return _result(
            "Hot-tenant bystander fairness",
            INCONCLUSIVE,
            target=1.50,
            evidence="metrics.per_tenant.*.commit.completion.p50_s",
            reason="没有足够的热租户和旁观租户完成样本",
        )
    observed = max(ratios)
    return _result(
        "Hot-tenant bystander fairness",
        PASS if observed <= 1.50 else FAIL,
        target=1.50,
        observed=observed,
        evidence=evidence,
        reason="比较旁观租户 Commit P50 的最大/最小比值",
    )


def evaluate_pr421_acceptance(manifest: dict[str, Any]) -> dict[str, Any]:
    """Evaluate measurable PR421 gates and list unavailable gates."""
    checks = [
        _target_coverage(manifest),
        _search_isolation_gate(manifest),
        _search_success_gate(manifest),
        _fairness_gate(manifest),
        _commit_completion_gate(manifest),
        _rejection_gate(manifest),
        _hot_tenant_gate(manifest),
    ]
    unavailable = [
        _result(
            name,
            NOT_IMPLEMENTED,
            reason=reason,
        )
        for name, reason in (
            ("Cursor/message-set reconciliation", "EchoMem 未提供统一 cursor/消息集合导出接口"),
            ("Kill-9 local/cluster recovery", "测试平台没有可控的被测进程/集群重启适配器"),
            ("LLM/vector failure injection", "当前正式套件禁止用 mock 代替真实依赖故障"),
            ("Capacity profile ladder", "被测服务资源 profile/限制尚未由平台统一装配"),
        )
    ]
    all_checks = checks + unavailable
    statuses = {item["status"] for item in all_checks}
    overall = (
        FAIL if FAIL in statuses
        else INCONCLUSIVE if statuses & {INCONCLUSIVE, NOT_IMPLEMENTED}
        else PASS
    )
    return {
        "version": "pr421-acceptance-v1",
        "overall": overall,
        "checks": all_checks,
        "pr28_review_resolution": PR28_REVIEW_RESOLUTION,
        "review": {
            "reasonable_targets": [
                "Search P95 隔离度应排除超时样本并单列错误率",
                "Jain 公平指数要求至少两个独立认证租户",
                "拒绝率必须和拒绝响应耗时、Retry-After、reason_code 一起验收",
                "Commit 恢复率必须基于最终状态，不把轮询窗口内未知状态算成功",
            ],
            "missing_or_weak_targets": [
                "PR421 的 deadline_exhausted=0 过于绝对，应同时看跨租户影响和错误率",
                "128 并发饱和需增加队列打满前置与降载后恢复检查",
                "Commit 100% 完成率还必须增加 cursor/消息集合对账",
                "热租户指标应固定旁观租户样本和持续未服务时间口径",
                "四档 profile 需要记录实际生效资源，而不是只记录配置值",
            ],
        },
    }


def build_model_analysis_input(
    manifest: dict[str, Any],
    acceptance: dict[str, Any],
) -> dict[str, Any]:
    """Create a bounded, secret-free context for an external LLM diagnosis."""
    return {
        "task": "Analyze EchoMem PR421 stress acceptance results",
        "rules": [
            "Use only supplied evidence; do not invent server behavior.",
            "Distinguish FAIL, INCONCLUSIVE, and NOT_IMPLEMENTED.",
            "Do not claim client-side queueing proves EchoMem server scheduling.",
            "Prioritize data-loss, cross-tenant leakage, recovery, and saturation failures.",
        ],
        "run_context": {
            "base_url": manifest.get("base_url"),
            "scenarios": manifest.get("scenarios") or [],
            "repeats": manifest.get("repeats"),
            "client_admission_enabled": manifest.get("client_admission_enabled"),
            "server_observation_mode": manifest.get("server_observation_mode"),
        },
        "acceptance": acceptance,
        "requested_output": [
            "one-paragraph executive conclusion",
            "failed or inconclusive gates with evidence",
            "most likely root cause and confidence",
            "next diagnostic action",
            "whether EchoMem code, deployment, or test-platform code needs change",
        ],
    }

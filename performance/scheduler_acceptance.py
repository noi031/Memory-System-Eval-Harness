#!/usr/bin/env python3
"""Evaluate the seven scheduler acceptance targets from real evidence.

This command intentionally separates the test-platform verdict from the
EchoMem capability verdict. Missing runtime controls are INCONCLUSIVE, never
silently treated as a pass.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path | None) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8").strip()
        # Some legacy server jobs wrote a literal "\\n" after the JSON object.
        # Accept that harmless artifact so old evidence remains auditable.
        if text.endswith("\\n"):
            text = text[:-2].rstrip()
        value = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _result(name: str, status: str, target: Any, observed: Any, reason: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "target": target,
        "observed": observed,
        "reason": reason,
    }


def _suite_runs(suite: dict[str, Any]) -> list[dict[str, Any]]:
    runs = suite.get("runs")
    return [item for item in runs if isinstance(item, dict)] if isinstance(runs, list) else []


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    value = run.get("summary")
    return value if isinstance(value, dict) else {}


def _scenario_runs(suite: dict[str, Any], names: set[str]) -> list[dict[str, Any]]:
    return [run for run in _suite_runs(suite) if str(run.get("scenario") or "") in names]


def _metric(summary: dict[str, Any], *path: str) -> Any:
    current: Any = summary
    for key in path:
        current = current.get(key) if isinstance(current, dict) else None
    return current


def _capacity(suite: dict[str, Any]) -> dict[str, Any]:
    names = {
        str(run.get("scenario") or "")
        for run in _suite_runs(suite)
        if str(run.get("scenario") or "").startswith("capacity-")
    }
    levels = sorted(
        (int(name.split("-", 1)[1]) for name in names if name.split("-", 1)[1].isdigit()),
    )
    target = "4U8G 实际生效配置 + 至少一档 DAU/热租户容量数据"
    if not levels:
        return _result(
            "DAU / 最大热用户容量",
            INCONCLUSIVE,
            target,
            {"capacity_levels": []},
            "未找到 capacity-* 场景，不能估计 DAU 或热租户容量",
        )
    profile = suite.get("instance_profile") or suite.get("config", {}).get("instance_profile")
    completed_levels = sorted(
        int(str(run.get("scenario")).split("-", 1)[1])
        for run in _suite_runs(suite)
        if str(run.get("scenario") or "").startswith("capacity-")
        and str(run.get("status") or "") == "completed"
        and str(run.get("scenario")).split("-", 1)[1].isdigit()
    )
    status = PASS if profile and completed_levels else INCONCLUSIVE
    return _result(
        "DAU / 最大热用户容量",
        status,
        target,
        {
            "capacity_levels": levels,
            "completed_capacity_levels": completed_levels,
            "max_completed_active_user_proxy": max(completed_levels, default=None),
            "instance_profile": profile,
        },
        (
            "已记录实际规格和完成的容量阶梯；该值是压测活跃用户代理上限，不等于业务 DAU"
            if status == PASS
            else "有容量场景，但没有实际完成的容量级别或实例规格证据"
        ),
    )


def _multi_spec(suite: dict[str, Any]) -> dict[str, Any]:
    profiles = suite.get("instance_profiles") or suite.get("profiles")
    if not isinstance(profiles, list):
        profiles = []
    target = "配置可切换并实际执行至少两种实例规格"
    if len(profiles) < 2:
        return _result(
            "多规格实例调度配置",
            INCONCLUSIVE,
            target,
            {"profiles": profiles},
            "本轮只运行 4U8G，无法证明 4U16G/16U32G/32U/64G 配置也能调度",
        )
    return _result(
        "多规格实例调度配置",
        PASS,
        target,
        {"profiles": profiles},
        "至少两种规格均有实际运行记录",
    )


def _fault_isolation(suite: dict[str, Any], fault: dict[str, Any]) -> dict[str, Any]:
    evidence = fault.get("tenant_fault_isolation") if isinstance(fault, dict) else None
    if not isinstance(evidence, dict):
        evidence = suite.get("tenant_fault_isolation")
    if not isinstance(evidence, dict):
        return _result(
            "单租户故障隔离",
            INCONCLUSIVE,
            "故障租户之外的租户 Search P95 劣化 <= 20%",
            {},
            "没有按租户注入故障并对旁观租户做前后 P95 配对的真实证据",
        )
    degradation = evidence.get("bystander_p95_degradation")
    try:
        value = float(degradation)
    except (TypeError, ValueError):
        value = None
    if value is None:
        return _result(
            "单租户故障隔离",
            INCONCLUSIVE,
            0.20,
            evidence,
            "故障隔离结果缺少旁观租户 Search P95 劣化值",
        )
    return _result(
        "单租户故障隔离",
        PASS if value <= 0.20 and evidence.get("fault_recovered") else FAIL,
        0.20,
        evidence,
        "旁观租户劣化不超过 20% 且故障已恢复"
        if value <= 0.20 and evidence.get("fault_recovered")
        else "旁观租户劣化超过 20% 或故障未恢复",
    )


def _fairness(suite: dict[str, Any]) -> dict[str, Any]:
    acceptance = suite.get("acceptance") if isinstance(suite.get("acceptance"), dict) else {}
    checks = acceptance.get("checks") if isinstance(acceptance.get("checks"), list) else []
    check = next((item for item in checks if item.get("name") == "Tenant fairness (Jain)"), None)
    if not isinstance(check, dict) or check.get("observed") in (None, ""):
        counts: dict[str, int] = {}
        run_count = 0
        for run in _suite_runs(suite):
            metrics = _run_summary(run).get("metrics")
            fairness = metrics.get("fairness") if isinstance(metrics, dict) else {}
            per_tenant = (
                fairness.get("commit_completed_per_tenant")
                if isinstance(fairness, dict)
                else None
            )
            if not isinstance(per_tenant, dict):
                continue
            run_count += 1
            for tenant, value in per_tenant.items():
                try:
                    count = int(value)
                except (TypeError, ValueError):
                    continue
                counts[str(tenant)] = counts.get(str(tenant), 0) + max(0, count)
        values = [float(value) for value in counts.values() if value > 0]
        if len(values) >= 2 and sum(values) > 0:
            jain = (sum(values) ** 2) / (len(values) * sum(value ** 2 for value in values))
            return _result(
                "Commit/Search 公平性 Jain",
                PASS if jain >= 0.90 else FAIL,
                0.90,
                {
                    "jain": round(jain, 4),
                    "completed_per_tenant": counts,
                    "runs_with_fairness": run_count,
                },
                "按独立租户最终完成 Commit 数计算 Jain 指数",
            )
        return _result(
            "Commit/Search 公平性 Jain",
            INCONCLUSIVE,
            0.90,
            {"completed_per_tenant": counts, "runs_with_fairness": run_count},
            "没有至少两个租户的有效 Commit 完成吞吐样本",
        )
    observed = check.get("observed")
    try:
        value = float(observed)
    except (TypeError, ValueError):
        value = None
    return _result(
        "Commit/Search 公平性 Jain",
        check.get("status", INCONCLUSIVE) if value is not None else INCONCLUSIVE,
        0.90,
        observed,
        check.get("reason", "按逐租户完成吞吐计算"),
    )


def _priority(suite: dict[str, Any]) -> dict[str, Any]:
    runs = _scenario_runs(suite, {"search-priority-blackbox"})
    if not runs:
        return _result(
            "Search 优先于 Commit",
            INCONCLUSIVE,
            "Search P95 <= 5s 且有同到达窗口证据",
            {},
            "未运行 search-priority-blackbox 场景",
        )
    p95_values = [
        _metric(_run_summary(run), "metrics", "search", "latency", "p95_s")
        for run in runs
    ]
    commit_counts = [
        _metric(_run_summary(run), "metrics", "commit", "submitted")
        for run in runs
    ]
    commit_counts = [
        int(value) for value in commit_counts
        if isinstance(value, (int, float)) and value > 0
    ]
    if not commit_counts:
        return _result(
            "Search 优先于 Commit",
            INCONCLUSIVE,
            5.0,
            {"runs": len(runs), "commit_submitted": 0},
            "场景存在但没有 Commit 洪泛样本，不能证明 Search 优先级",
        )
    values = [float(value) for value in p95_values if isinstance(value, (int, float))]
    if not values:
        return _result(
            "Search 优先于 Commit",
            INCONCLUSIVE,
            5.0,
            {"runs": len(runs)},
            "场景运行了，但没有 Search P95 数据",
        )
    worst = max(values)
    return _result(
        "Search 优先于 Commit",
        PASS if worst <= 5.0 else FAIL,
        5.0,
        {"worst_search_p95_s": worst, "commit_submitted": sum(commit_counts)},
        "同到达窗口 Search P95 在 5 秒内" if worst <= 5.0 else "Search P95 超过 5 秒",
    )


def _recovery(recovery: dict[str, Any]) -> dict[str, Any]:
    if not recovery:
        return _result(
            "Commit kill-9 恢复与重放",
            INCONCLUSIVE,
            "202 接受的 Commit 恢复后 100% 完成并通过消息对账",
            {},
            "未提供 kill-9/restart 真实控制结果",
        )
    status = str(recovery.get("status") or INCONCLUSIVE)
    replay_rate = recovery.get("replay_rate")
    if replay_rate is None:
        replay_rate = recovery.get("recovered_commit_rate")
    try:
        rate = float(replay_rate)
    except (TypeError, ValueError):
        rate = None
    cursor = recovery.get("cursor_reconciliation")
    cursor_proven = isinstance(cursor, dict) and str(cursor.get("status")) == PASS
    message_set_proven = bool(
        recovery.get("message_set_reconciled")
        or recovery.get("replay_verified")
        or recovery.get("replay_rate") is not None
    )
    passed = (
        status == PASS
        and (rate is None or rate >= 1.0)
        and recovery.get("recovered") is not False
        and (cursor_proven or message_set_proven)
    )
    return _result(
        "Commit kill-9 恢复与重放",
        PASS if passed else status if status in {FAIL, INCONCLUSIVE} else FAIL,
        1.0,
        recovery,
        "服务恢复、Commit 完成且消息集合对账通过" if passed else "恢复或重放证据不完整/失败",
    )


def _observability(capability: dict[str, Any], suite: dict[str, Any]) -> dict[str, Any]:
    checks = capability.get("checks") if isinstance(capability.get("checks"), list) else []
    metric = next((item for item in checks if item.get("name") == "Prometheus B7 metrics"), None)
    required = {"lane_queued", "lane_wait", "lane_exec", "lane_rejected"}
    present_map = (metric or {}).get("present") or {}
    present = {
        str(name) for name, available in present_map.items() if bool(available)
    }
    missing = sorted(required - present)
    coverage = [
        _metric(_run_summary(run), "details", "pr421_metric_coverage")
        for run in _suite_runs(suite)
    ]
    coverage = [item for item in coverage if isinstance(item, dict)]
    if missing and not coverage:
        return _result(
            "分层/分租户调度可观测性",
            INCONCLUSIVE,
            "每层每租户 queued/wait/exec/rejected 四类指标",
            {"missing": missing},
            "没有完整的 Prometheus B7 指标覆盖证据",
        )
    observed = {"missing": missing, "coverage_samples": len(coverage)}
    return _result(
        "分层/分租户调度可观测性",
        PASS if not missing else INCONCLUSIVE,
        "四类指标均存在且标签符合 bounded-label 契约",
        observed,
        "四类 lane 指标均有服务端证据" if not missing else "指标部分存在，不能完整验收",
    )


def evaluate(
    suite: dict[str, Any],
    *,
    capability: dict[str, Any] | None = None,
    recovery: dict[str, Any] | None = None,
    fault: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checks = [
        _capacity(suite),
        _multi_spec(suite),
        _fault_isolation(suite, fault or {}),
        _fairness(suite),
        _priority(suite),
        _recovery(recovery or {}),
        _observability(capability or {}, suite),
    ]
    statuses = [item["status"] for item in checks]
    overall = FAIL if FAIL in statuses else INCONCLUSIVE if INCONCLUSIVE in statuses else PASS
    return {
        "version": "scheduler-acceptance-v1",
        "created_at": _now(),
        "overall": overall,
        "checks": checks,
        "evidence": {
            "suite": suite.get("created_at") or suite.get("out_dir"),
            "capability": bool(capability),
            "recovery": bool(recovery),
            "fault": bool(fault),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the seven scheduler targets")
    parser.add_argument("--suite", required=True, type=Path)
    parser.add_argument("--capability", type=Path)
    parser.add_argument("--recovery", type=Path)
    parser.add_argument("--fault", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = evaluate(
        _load(args.suite),
        capability=_load(args.capability),
        recovery=_load(args.recovery),
        fault=_load(args.fault),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"overall": result["overall"], "checks": result["checks"]}, ensure_ascii=False))
    return 0 if result["overall"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())

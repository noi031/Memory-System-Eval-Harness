"""把运行套件数据映射为七项产品目标 O1-O7 的验收判定。

纯判定、无 I/O：O1/O3-O7 的结论来自 ``scheduler_acceptance.evaluate``
对套件证据的严格计算，O2 由多规格实例配置的完成情况直接给出。
"""

from __future__ import annotations

from typing import Any

from performance.targets.echomem.acceptance.scheduler import (
    evaluate as evaluate_scheduler_acceptance,
)

OBJECTIVES = [
    ("O1", "单实例最大用户量 / 热用户量"),
    ("O2", "多规格实例调度与 config"),
    ("O3", "单租户故障下 Search P95 劣化 <= 20%"),
    ("O4", "多租户公平性 Jain >= 0.9"),
    ("O5", "Commit 洪泛时 Search P95 <= 5s"),
    ("O6", "202 Commit 崩溃恢复后 100% 重放且不丢序"),
    ("O7", "每层每租户四元组可观测指标"),
]
INCONCLUSIVE = "INCONCLUSIVE"
PASS = "PASS"
FAIL = "FAIL"


def acceptance_by_name(suite: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把 ``suite["acceptance"]["checks"]`` 按 name 建索引。"""
    acceptance = suite.get("acceptance") or {}
    return {
        str(item.get("name")): item
        for item in acceptance.get("checks") or []
        if isinstance(item, dict) and item.get("name")
    }


def objective_statuses(suite: dict[str, Any]) -> list[dict[str, Any]]:
    recovery = suite.get("commit_recovery") or {}
    fault_suite = suite.get("fault_suite") or {}
    capability = suite.get("capability_probe") or {}

    recovery_for_scheduler = dict(recovery)
    message_reconciliation = recovery_for_scheduler.get("message_reconciliation")
    if (
        "message_set_reconciled" not in recovery_for_scheduler
        and isinstance(message_reconciliation, dict)
    ):
        recovery_for_scheduler["message_set_reconciled"] = (
            str(message_reconciliation.get("status") or "") == PASS
        )
    if (
        "replay_verified" not in recovery_for_scheduler
        and isinstance(recovery_for_scheduler.get("idempotency_reconciliation"), dict)
    ):
        recovery_for_scheduler["replay_verified"] = (
            str(
                recovery_for_scheduler["idempotency_reconciliation"].get("status") or ""
            )
            == PASS
        )
    strict_acceptance = evaluate_scheduler_acceptance(
        suite,
        capability=capability,
        recovery=recovery_for_scheduler,
        fault=fault_suite,
    )
    strict_by_name = {
        str(item.get("name")): item
        for item in strict_acceptance.get("checks") or []
        if isinstance(item, dict) and item.get("name")
    }

    def strict(name: str, fallback: str = INCONCLUSIVE) -> dict[str, Any]:
        item = strict_by_name.get(name)
        return item if isinstance(item, dict) else {"status": fallback}

    def strict_observed(name: str) -> Any:
        return strict(name).get("observed", {})

    instance_profiles = suite.get("instance_profiles")
    completed_profiles = (
        [
            item
            for item in instance_profiles
            if isinstance(item, dict)
            and str(item.get("status") or "").lower()
            in {"completed", "pass", "passed"}
            and int(item.get("completed_runs") or 0) > 0
        ]
        if isinstance(instance_profiles, list)
        else []
    )
    multi_spec_status = PASS if len(completed_profiles) >= 2 else INCONCLUSIVE

    return [
        {
            "id": "O1",
            "name": OBJECTIVES[0][1],
            "status": strict("DAU / 最大热用户容量")["status"],
            "reason": strict("DAU / 最大热用户容量").get("reason", ""),
            "observed": strict_observed("DAU / 最大热用户容量"),
            "owner": strict("DAU / 最大热用户容量").get("owner"),
            "evidence": "scheduler_acceptance: DAU / 最大热用户容量",
        },
        {
            "id": "O2",
            "name": OBJECTIVES[1][1],
            "status": multi_spec_status,
            "reason": (
                "至少两种规格均有真实完成场景，可比较调度与 config"
                if multi_spec_status == PASS
                else "当前只完成单一规格或没有真实场景结果；仅有 profile 配置不能证明多规格调度"
            ),
            "observed": {"completed_profiles": completed_profiles},
            "owner": "测试平台 + 部署资源",
            "evidence": completed_profiles,
        },
        {
            "id": "O3",
            "name": OBJECTIVES[2][1],
            "status": strict("单租户故障隔离")["status"],
            "reason": strict("单租户故障隔离").get("reason", ""),
            "observed": strict_observed("单租户故障隔离"),
            "owner": strict("单租户故障隔离").get("owner"),
            "evidence": "scheduler_acceptance: 单租户故障隔离",
        },
        {
            "id": "O4",
            "name": OBJECTIVES[3][1],
            "status": strict("Commit/Search 公平性 Jain")["status"],
            "reason": strict("Commit/Search 公平性 Jain").get("reason", ""),
            "observed": strict_observed("Commit/Search 公平性 Jain"),
            "owner": strict("Commit/Search 公平性 Jain").get("owner"),
            "evidence": "scheduler_acceptance: Commit/Search 公平性 Jain",
        },
        {
            "id": "O5",
            "name": OBJECTIVES[4][1],
            "status": strict("Search 优先于 Commit")["status"],
            "reason": strict("Search 优先于 Commit").get("reason", ""),
            "observed": strict_observed("Search 优先于 Commit"),
            "owner": strict("Search 优先于 Commit").get("owner"),
            "evidence": "scheduler_acceptance: Search 优先于 Commit",
        },
        {
            "id": "O6",
            "name": OBJECTIVES[5][1],
            "status": strict("Commit kill-9 恢复与重放")["status"],
            "reason": strict("Commit kill-9 恢复与重放").get("reason", ""),
            "observed": strict_observed("Commit kill-9 恢复与重放"),
            "owner": strict("Commit kill-9 恢复与重放").get("owner"),
            "evidence": "scheduler_acceptance: Commit kill-9 恢复与重放",
        },
        {
            "id": "O7",
            "name": OBJECTIVES[6][1],
            "status": strict("分层/分租户调度可观测性")["status"],
            "reason": strict("分层/分租户调度可观测性").get("reason", ""),
            "observed": strict_observed("分层/分租户调度可观测性"),
            "owner": strict("分层/分租户调度可观测性").get("owner"),
            "evidence": "scheduler_acceptance: 分层/分租户调度可观测性",
        },
    ]

"""观察负载场景矩阵（case 定义）与 echomem 侧 case → Profile 包装。

case 是观测负载的最小单元：label 唯一、携带场景文件名（scenes/ 下，不含
.py）与负载参数（tenants/duration_s/search_rps/commit_rpm/sessions/messages）。
观测模式只保留 ``six_metric_observation_cases``（M2/M3 的 6 例）：M1 由
T×U 容量探索器执行，M4/M5/M6 是探针，均不走 case 目录；``--scenarios``
的合法值即此目录的 label 集合。

quick 收敛（``QuickSpec``/``apply_quick``）与 case → Profile 的通用骨架
（``build_case_profile``）在通用套件层 ``performance.suite``；本模块按
echomem 约定填充场景目录、默认 query 列表与 barrier 参数回调。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from performance.profile import Profile
from performance.suite import (
    QuickSpec,
    apply_quick as apply_quick,
    build_case_profile as _build_case_profile_general,
)
from performance.targets.echomem.protocol import DEFAULT_QUERIES

SCENES_DIR = Path(__file__).resolve().parent.parent / "scenes"


def _case(**fields: Any) -> dict[str, Any]:
    case = {
        "commit_rpm": None,
        "sessions_per_tenant": 1,
        "messages_per_session": 3,
        "commit_barrier": False,
        "commit_barrier_count": 32,
        "commit_tenant_distribution": "uniform",
        "commit_zipf_exponent": 2.0,
        "commit_tenant_counts": None,
        "commit_barrier_waves": 1,
        "commit_barrier_cooldown_s": 0.0,
        "commit_burst_window_s": None,
        "quick_barrier_count_cap": 0,
        "quick_commit_rpm": None,
        "search_workers": None,
        "commit_workers": None,
        "per_tenant_concurrency": None,
        "read_only": False,
        "blackbox_search_priority": False,
        "fairness_bounded": False,
    }
    case.update(fields)
    return case


def six_metric_observation_cases(*, quick: bool = False) -> list[dict]:
    """Observation-only M2/M3 matrix, including heterogeneous tenant load.

    M1 is executed by the T x U capacity runner and M4/M5/M6 are probes. The
    cases here therefore contain only the paired Search/Commit windows needed
    for fairness and flood observations. No case encodes a performance gate.
    """
    duration = 15 if quick else 300
    barrier = 8 if quick else 64
    common = {
        "duration_s": duration,
        "search_rps": 8.0,
        "search_workers": 64,
        "commit_workers": 64,
        "sessions_per_tenant": 2,
        "messages_per_session": 4,
    }
    return [
        _case(
            label="m2-fairness-4t", scene="scene_c_mixed", tenants=4,
            commit_rpm=20.0 if quick else 2.0, commit_barrier=False,
            arrival_scope="per_tenant", commit_start_s=3 if quick else 30,
            arrival_end_s=duration, measurement_start_s=3 if quick else 30,
            measurement_end_s=duration, fairness_mode="independent-periodic-v1",
            **{**common, "search_rps": 1.0, "duration_s": duration + (30 if quick else 180)},
        ),
        _case(
            label="m2-fairness-8t", scene="scene_c_mixed", tenants=8,
            commit_rpm=20.0 if quick else 2.0, commit_barrier=False,
            arrival_scope="per_tenant", commit_start_s=3 if quick else 30,
            arrival_end_s=duration, measurement_start_s=3 if quick else 30,
            measurement_end_s=duration, fairness_mode="independent-periodic-v1",
            **{**common, "search_rps": 1.0, "duration_s": duration + (30 if quick else 180)},
        ),
        _case(
            label="m3-baseline", scene="scene_capacity", tenants=4,
            commit_rpm=0.0, read_only=True, **common,
        ),
        _case(
            label="m3-flood-uniform", scene="scene_barrier", tenants=4,
            barrier_prepare_before_commit=True,
            commit_rpm=0.0, commit_barrier=True,
            commit_barrier_count=barrier,
            commit_tenant_distribution="uniform", barrier_at_s=3 if quick else 30,
            blackbox_search_priority=True, **common,
        ),
        _case(
            label="m3-flood-single-tenant", scene="scene_barrier", tenants=4,
            barrier_prepare_before_commit=True,
            commit_rpm=0.0, commit_barrier=True,
            commit_barrier_count=barrier,
            commit_tenant_distribution="explicit",
            commit_tenant_counts=[barrier, 0, 0, 0],
            barrier_at_s=3 if quick else 30,
            blackbox_search_priority=True, **common,
        ),
        _case(
            label="m3-heterogeneous-tenants", scene="scene_c_mixed", tenants=4,
            commit_rpm=20.0 if quick else 2.0, commit_barrier=False,
            arrival_scope="per_tenant", commit_start_s=3 if quick else 30,
            arrival_end_s=duration, search_tenant_weights=[8, 4, 2, 1],
            commit_tenant_weights=[1, 2, 4, 8],
            heterogeneous_tenant_load=True,
            **{**common, "search_rps": 1.0,
               "duration_s": duration + (30 if quick else 180)},
        ),
    ]


def build_case_profile(
    case: dict,
    *,
    base_url: str,
    tenant_count: int,
    auth_headers: dict,
    quick: QuickSpec | None = None,
) -> Profile:
    """case → Profile：场景目录/默认 query 按 echomem 约定填充。

    quick 非 None 时先 ``apply_quick`` 收敛；barrier 参数经
    ``_apply_barrier_params`` 注入 params。通用骨架见
    ``performance.suite.build_case_profile``。
    """
    return _build_case_profile_general(
        case,
        scene_path=SCENES_DIR / f"{case['scene']}.py",
        base_url=base_url,
        tenant_count=tenant_count,
        auth_headers=auth_headers,
        queries=list(DEFAULT_QUERIES),
        quick=quick,
        extra_params=_apply_barrier_params,
    )


def _apply_barrier_params(params: dict[str, Any], case: dict) -> None:
    """按场景把 barrier 字段翻译进 params（与 case['scene'] 一致）。"""
    scene_name = case["scene"]
    params["query_mode"] = case.get("query_mode", "recall")
    params["commit_poll_timeout_s"] = case.get("commit_poll_timeout_s", 180)
    if scene_name == "scene_barrier":
        barrier_count = int(case.get("commit_barrier_count", 32))
        params.update(
            {
                "barrier_count": barrier_count,
                "barrier_prepare_before_commit": bool(case.get("barrier_prepare_before_commit", False)),
                "barrier_at_s": float(case.get("barrier_at_s", 0)),
                "barrier_distribution": str(
                    case.get("commit_tenant_distribution", "uniform")
                ),
                "barrier_zipf_exponent": float(case.get("commit_zipf_exponent", 2.0)),
                "barrier_waves": int(case.get("commit_barrier_waves", 1)),
                "barrier_cooldown_s": float(case.get("commit_barrier_cooldown_s", 0.0)),
                "barrier_max_workers": min(barrier_count, 32),
            }
        )
        if case.get("commit_tenant_counts"):
            params["commit_tenant_counts"] = [
                int(value) for value in case["commit_tenant_counts"]
            ]
        if (
            case.get("fairness_bounded")
            and case.get("commit_tenant_distribution") == "uniform"
        ):
            params["floor_to_tenants"] = True

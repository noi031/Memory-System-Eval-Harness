"""场景 A：纯读基线。

全部 worker 循环 POST /api/retrieval/search，查询词从 query 池轮询取，
top_k 由 profile params 控制（默认 5）；请求体 agent_id 为空。每请求
独立计时记录；HTTP 错误记 error，200 但空结果按锚词质量规则标记
quality_ok。

额外导出 ``report`` 钩子：EchoMem 特有质量分析（search 结果质量与命中
量），随 summary 的 ``custom`` 段输出。
"""

from __future__ import annotations

from performance.engine import RunResult
from performance.profile import Profile
from performance.targets.echomem.protocol import task_read

tasks = {"read": task_read}


def report(result: RunResult, profile: Profile) -> dict:
    """EchoMem search 质量聚合：quality_ok / degraded 占比与平均命中量。"""
    reads = [r for r in result.records if r.op == "read"]
    if not reads:
        return {"reads": 0}
    return {
        "reads": len(reads),
        "quality_ok_ratio": round(
            sum(1 for r in reads if r.quality_ok) / len(reads), 4
        ),
        "degraded_ratio": round(
            sum(1 for r in reads if r.degraded) / len(reads), 4
        ),
        "avg_hit_count": round(
            sum(r.hit_count for r in reads) / len(reads), 2
        ),
    }

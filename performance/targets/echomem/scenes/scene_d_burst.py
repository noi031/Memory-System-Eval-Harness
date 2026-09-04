"""场景 D：注入洪峰。

持续读负载跑满 duration；主线程在 delay = max(0, duration-burst_window)/2
处启动 burst：burst_commits 个写事务以 max_workers=8 并发执行（默认单
租户 tenants[0]，记录 extra="burst"），全部完成后读负载继续到 duration。
burst 事务复用写事务路径，seq 与主负载共享同一计数器。
"""

from __future__ import annotations

from performance.ctx import Ctx
from performance.targets.echomem.protocol import task_read, task_write

BURST_WORKERS = 8


def _burst(ctx: Ctx) -> None:
    task_write(ctx)


def schedule(ctx: Ctx) -> None:
    window = float(ctx.params.get("burst_window_s", 10.0))
    delay = max(0.0, ctx.duration_s - window) / 2.0
    ctx.at_time(
        delay,
        _burst,
        count=int(ctx.params.get("burst_commits", 32)),
        max_workers=BURST_WORKERS,
        name="burst",
        tenant_idx=0,
    )


tasks = {"read": task_read}

"""场景 D（多波）：注入洪峰多波变体。

读负载持续打满；在 delay = max(0, duration - burst_window_s) / 2 处开始注入
``burst_waves`` 波写事务（``protocol.task_write``，单租户 tenants[0]，与
scene_d_burst 一致），相邻两波间隔 ``burst_cooldown_s``，每波
``burst_commits`` 个事务、``burst_max_workers`` 并发，全部记录
extra="burst"。

参数（``ctx.params``）：``burst_commits``（默认 32）、``burst_window_s``
（默认 10.0）、``burst_waves``（默认 1）、``burst_cooldown_s``（默认 0.0）、
``burst_max_workers``（默认 8）。
"""

from __future__ import annotations

from performance.ctx import Ctx
from performance.targets.echomem.protocol import task_read, task_write


def _burst(ctx: Ctx) -> None:
    task_write(ctx)


def schedule(ctx: Ctx) -> None:
    window = float(ctx.params.get("burst_window_s", 10.0))
    delay = max(0.0, ctx.duration_s - window) / 2.0
    commits = int(ctx.params.get("burst_commits", 32))
    waves = int(ctx.params.get("burst_waves", 1))
    cooldown = float(ctx.params.get("burst_cooldown_s", 0.0))
    max_workers = int(ctx.params.get("burst_max_workers", 8))
    for wave in range(waves):
        ctx.at_time(
            delay + wave * cooldown,
            _burst,
            count=commits,
            max_workers=max_workers,
            name="burst",
            tenant_idx=0,
        )


tasks = {"read": task_read}

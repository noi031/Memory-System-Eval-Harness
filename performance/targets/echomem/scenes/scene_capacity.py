"""场景 K：固定速率容量负载。

读写混合持续打满 duration；每个任务的到达率由 ``profile.load.arrival``
按任务固定 rps 控制（如 read 16.0 rps、write 0.033 rps ≈ 2 rpm），worker
按 ``profile.load.mix`` 拆组。基础/均衡/commit-storm/search-storm/soak/
capacity-N 等正式场景共用本场景，差异全在画像。

参数（``ctx.params``）：与 protocol 标准任务一致（top_k / queries /
messages_per_session / commit_poll_timeout_s）。
"""

from __future__ import annotations

from performance.targets.echomem.protocol import task_read, task_write

tasks = {"read": task_read, "write": task_write}

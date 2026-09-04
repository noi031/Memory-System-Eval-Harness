"""场景 C：读写混合。

总 worker 数按 mix 权重拆分（read 优先 round）成读者组 + 写者组并发
运行，duration 内各自循环。profile.load.mix（如 read=8,write=1）与
tasks 的 key 对应；不配 mix 时按等权拆分。
"""

from __future__ import annotations

from performance.targets.echomem.protocol import task_read, task_write

tasks = {"read": task_read, "write": task_write}

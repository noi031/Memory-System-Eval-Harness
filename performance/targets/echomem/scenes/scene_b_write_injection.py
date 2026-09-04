"""场景 B：纯写注入。

一个写事务 = open -> add×N（末条携带 PERFTAIL anchor）-> commit submit
-> commit done（poll 到 completed，默认 600s 超时）。四个阶段独立计时
记录；add 失败即事务失败；commit 提交默认不重试，需要重试时在场景
代码里用 while/try 表达。
"""

from __future__ import annotations

from performance.targets.echomem.protocol import task_write

tasks = {"write": task_write}

"""EchoMem 探针：有向行为验证（契约/故障/恢复/限流/对账）。

探针模块导出 ``run(ctx)``，用 ``ctx.check`` 记录四态断言（PASS / FAIL /
NOT_IMPLEMENTED / INCONCLUSIVE）；配置经 ``ctx.params`` 读取（原 argparse
参数同名迁移），HTTP 细节保留在各探针或 ``_client`` 中。``_`` 开头模块为
共享辅助，不是探针。
"""

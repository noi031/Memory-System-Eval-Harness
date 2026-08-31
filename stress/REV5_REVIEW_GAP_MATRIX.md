# PR28 检视意见逐项验收矩阵

本文件用于对照 PR28 的 rev5 检视意见，不把“有文档描述”误认为“已经具备
可执行实现”。状态以当前 `feat/echomem-stress-suite` 分支为准。

## 已修复

| 检视项 | 当前实现 | 验收证据 |
| --- | --- | --- |
| Commit barrier | 支持固定数量、同一到达时刻的 Commit | `runner.py --commit-barrier-count` |
| 租户分布 | 支持 `uniform`、`zipf` 和显式计数分布 | `--commit-tenant-distribution explicit --commit-tenant-counts 200,20,20,20` |
| 429 重试 | 仅重试 429，优先使用 Retry-After，否则指数退避 | `commit_with_retry()` |
| 重试审计 | 保存每次尝试、状态码、请求 ID、退避时间 | `commit_results.csv` / `summary.json` |
| 客户端调度边界 | 正式 suite 默认关闭客户端准入调度 | `server-observe` / `--no-client-admission` |
| 服务端遥测边界 | 缺少服务端字段时不使用客户端时间冒充 | `server_*` 字段和覆盖率报告 |
| PR421 B7 标签边界 | 校验真实 lane 集合、禁止 tenant label、拒绝计数要求 reason_code | `pr421_metric_coverage()` |
| 多租户隔离 | 独立凭证、N×N marker 探针、共享凭证不判隔离通过 | isolation probe |
| 长稳态基础场景 | formal suite 已包含 soak 场景和重复轮次 | `formal_suite.py` |
| 可量化门禁判定 | `stress/echomem/acceptance.py` 按 PR421 指标逐项判定；缺证据输出 `INCONCLUSIVE`，不可执行项输出 `NOT_IMPLEMENTED` | `acceptance.json` / `suite.html` |

## 部分修复

| 检视项 | 已完成部分 | 尚未完成 |
| --- | --- | --- |
| Commit 最终完成率 | 有异步轮询、retry 和结果统计 | cursor/消息集合对账需 EchoMem 暴露真实接口 |
| 429 验收 | 有 Retry-After 重试和最终状态记录 | 没有“先证明队列饱和，再验证 429”的前置检查 |
| 环境可复现 | 有 runner Docker/compose 和 reset hook | 被测 EchoMem 的资源限制、固定镜像、配置哈希和 MySQL 拓扑没有统一落地 |
| 资源观测 | 有 RSS/CPU/线程/FD/Swap 和原始 metrics | 没有 GC、tracemalloc 和后台任务数量判据 |
| 长稳态 | 有 30 分钟 soak 默认场景和重复运行 | 尚未覆盖 2 小时验收、窗口化泄漏趋势和冷却后同状态比较 |

## 尚未实现

| 检视项 | 缺失内容 | 当前报告状态 |
| --- | --- | --- |
| k6 工具链 | 已提供真实 HTTP k6 脚本、summary 输出和 Python 对账入口；部署仍需安装 k6 | `PARTIAL` |
| LLM 故障注入 | 已提供真实 command/HTTP/Docker 控制器和故障计划编排；必须由部署提供真实故障开关 | `PARTIAL` |
| kill-9 重启恢复 | 已提供真实 PID/container SIGKILL、健康恢复时间线；cluster+MySQL 需部署提供重启拓扑 | `PARTIAL` |
| 冷缓存 TTL | TTL 到期后的命中率和延迟对比 | `NOT_IMPLEMENTED` |
| 原子写阻塞级联 | 原子写期间 Search/Commit 的级联影响 | `NOT_IMPLEMENTED` |
| 版本冲突毒循环 | 冲突重试次数、上限和最终状态 | `NOT_IMPLEMENTED` |
| 启动引擎隔离 | 单个引擎加载失败不拖垮其他引擎 | `NOT_IMPLEMENTED` |
| 容量阶梯 | 已提供可执行的 2/4/8/16/32 租户容量点；真实多规格资源 profile 由外部编排 | 容量场景已覆盖；独立凭证或资源限制缺失时仍为 `INCONCLUSIVE` |
| 租户自带 LLM Key | 不同租户 key、配额和泄漏检查 | `NOT_IMPLEMENTED` |
| 游标对账 | 已提供可配置 cursor URL、Commit message-set 对账和缺失项证据；EchoMem 必须暴露真实接口 | `PARTIAL` |
| Search 服务端优先级 | 已提供 `search-priority-blackbox`：Search 与 Commit barrier 同时施压，关闭客户端准入并采集服务端时序 | `PARTIAL`；缺少可配对基线或完整服务端队列时序时不能证明严格优先 |
| 提交阈值扫描 | 不同 auto-commit threshold 的系统性对比 | `NOT_IMPLEMENTED` |

## 验收结论

PR29 当前可以验收为：

> 真实 HTTP 多租户隔离和服务端观测型压测套件，已补齐 Commit barrier、
> Zipf/显式租户分布、429 Retry-After 重试，以及 PR421 可量化指标的保守判定层。

PR29 当前不能验收为：

> rev5 完整 SaaS 压测方案，或具备完整故障注入、重启恢复和持久化对账能力。

仍未完成的检视项不会被隐藏，而是在验收矩阵中明确标记为
`NOT_IMPLEMENTED`；因此 PR29 仍不能宣称为 rev5 全量事故回归方案。

下一阶段建议只实现最小闭环：

1. 为 EchoMem 暴露统一的 commit cursor/operation 查询和消息集合导出。
2. 在部署中接入真实 LLM/vector 故障控制接口，再执行 `fault_suite.py`。
3. 增加 cluster+MySQL 的真实重启控制脚本和恢复后的 cursor 对账。
4. 在 CI/服务器安装 k6，并将 k6 summary 与 runner 请求证据绑定。
5. 为 2U/4U/8U/16U/32U/64U 提供真实资源限制和 profile 生效证明，再执行
   `instance_profile_matrix.py`。

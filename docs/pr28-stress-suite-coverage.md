# PR #28（stress 压测套件）特性覆盖度评估

> 评估对象：`tech-innovation-group/Memory-System-Eval-Harness` PR #28
> `feat: add real EchoMem multi-tenant stress suite`（head `ccd099a2`，单 commit，21 个文件全部新增，
> 基于 `v3_mcpTool`）。本报告为**只读对比评估**，未合并代码。
> 评估日期：2026-08-27

## 1. 结论摘要

**PR #28 不单独满足「压测要测出的 EchoMem 全部特性」，与本地 `performance/` 模块互补。**

- PR #28 独有且本地没有：多租户**隔离**验证（N×N marker 探针）、**五种调度策略**对照、
  客户端/服务端排队证据分离、服务端逐请求 telemetry 字段探测、formal 上线套件（formal suite）；
- PR #28 缺少且本地 `performance/` 已有：四项特性保证的**自动 PASS/FAIL 判定**、
  **注入洪峰 D 场景**与劣化倍数量化、**写后读一致性窗口**、**Prometheus 直方图双视角**
  （bucket 插值）、EchoMem 验收数值目标断言、locomo 真实对话种子；
- 两者交集（延迟分布/公平性/资源斜率/soak）实现思路不同但目标一致；
- 对**当前本地 EchoMem**：PR #28 的服务端逐请求 telemetry 字段（`queue_entered_at`/
  `execution_started_at` 等）EchoMem 尚未暴露，该部分结论只能标记「缺失/客户端黑盒观测」；
  且 PR #28 默认鉴权头 `X-API-Key` 需显式改为 `--auth-header X-Auth-Key`。

## 2. 两套压测模块概览

| 维度 | PR #28 `stress/` | 本地 `performance/`（未提交） |
|---|---|---|
| 入口 | `runner.py`(2881 行) / `formal_suite.py` / `run_matrix.py` | `run_stress.py` / `scenarios.py` |
| 支持模块 | 6 个报告器 + compose/docker + tenants 配置 | loadgen / monitor / metrics_calc / report / prepare |
| 被测对象 | 外部 EchoMem HTTP 服务（`--base-url`） | 外部 EchoMem HTTP 服务（`--echomem-url`） |
| 场景 | formal_suite 五案例：baseline / mixed / commit-storm / search-storm / soak | 矩阵：A 纯读 / B 纯写 / C 读写混合 / D 注入洪峰 |
| 并发模型 | 目标速率（search-rps / commit-rpm）+ 客户端准入控制器（五策略） | 并发档（1,4,16,64）× read:write 比例 |
| 隔离验证 | N×N marker 探针（误命中=失败） | 无 |
| 判定结论 | 无自动 PASS/FAIL，输出数据 + 上线门槛目标（散布在 PLAN） | `feature_verdicts`：四项特性 PASS/FAIL/INCONCLUSIVE + 量化 measurements |
| 服务端观测 | /metrics 原始 JSONL + telemetry 逐请求字段探测 | /metrics 解析：histogram bucket 插值、gauge 序列、CPU 帧差 |
| 测试 | 19 例（`test_stress_runner.py` + `test_formal_data_report.py`） | 63 例（`tests/test_performance.py`，本机全绿） |

## 3. 需求清单（压测要测出的 EchoMem 全部特性）

来源三处，合并去重：

**A. 本地 `docs/performance-stress-test-design.md` §1.1「四项特性保证」（压测必须验明的验收判据）**
1. commit 异步、成功保证、不阻塞检索（202→completed 最终性；提交阶段拒绝分类；写洪峰窗口读 P95 劣化 < 2x）
2. 租户公平性（多租户 read P95 max/min < 3x）
3. 无内存泄漏（RSS 斜率 < 5 MB/min + 冷却回落）
4. 资源利用率随时间变化曲线（CPU/内存序列图）

**B. `EchoMem/docs/design/multi-tenant/recall-concurrency-backpressure-design.md`**
- §10.5 单节点验收压测目标：持续 300 commits/h + 并发 recall → recall P95 < 3s、无 5xx；
  突发 100 commits/min × 5min → 队列深度 ≤128、超出 429 且 retryable、recall 成功率 ≥99%；
  进程 RSS 稳态 < 6GB；provider 429 = 0
- §7 生产验收：突发 Commit 下 recall 无 345s/600s 固定耗时簇；排队超时任务不发起
  provider 调用（abandoned_workers 不因排队增长）；无超时型 BrokenPipe（每请求至多 1 条）
- §2 原则：前后台隔离（后台写入突发不拖死前台）；背压显式化（明确拒绝可重试，非无界排队）

**C. 多租户数据隔离**（跨租户绝不串读；同租户必命中）——EchoMem 多租户设计的硬性要求

## 4. 覆盖矩阵

| # | 需求特性 | PR #28 | 本地 performance/ | 当前 EchoMem 可行 |
|---|---|---|---|---|
| 1a | commit 异步成功保证（202→completed 最终性 + 失败分类） | 部分：状态轮询 + 提交/完成/失败/超时计数 + Retry-After；**无 guarantee_violations 判定** | ✅ `commit_durability()` + `guarantee_violations` | ✅（API 兼容，见 §5.3） |
| 1b | 写洪峰下 search 优先级（基线→洪峰劣化倍数） | 部分：commit-storm/search-storm 场景可对比；**无短窗口洪峰、无比值量化、无窗口切片** | ✅ D 场景 + `degradation_factor` + `burst_summary`（阈值 2x 默认） | ✅ |
| 2 | 租户公平性判定 | 部分：**采集** max/min 比、Jain 指数、吞吐差异、最大等待、连续未服务时间；**无阈值判定** | ✅ `tenant_fairness()` balanced（3x）+ measurements | ✅ 但需多租户独立凭据 |
| 3 | 无内存泄漏（RSS 斜率 + 冷却回落） | 部分：RSS/CPU 采样 + `linear_slope_per_minute` + soak 场景；**无判定、无冷却观测** | ✅ `rss_trend`（5 MB/min）+ 冷却回落 | ✅ |
| 4 | 资源利用率时间曲线 | ✅ 资源采样 + report.html 图（README 声明） | ✅ `cpu_utilization_series` + `gauge_series` | ✅ |
| 5 | 多租户隔离（跨租户不串读） | ✅ N×N marker 探针（默认 5 marker/租户，4×4×5=80 探针） | ❌ 无 | ✅（marker 写入-检索即可验证） |
| 6 | 调度策略对照（FIFO/优先/双通道/租户公平） | ✅ 五策略矩阵 + suite 跨策略比较 | ❌ 无 | ⚠️ 仅客户端准入形状，不能证明服务端调度 |
| 7 | 客户端 vs 服务端排队证据分离 | ✅ 设计完备（admission 记录 vs telemetry 字段探测） | ⚠️ 弱：仅 /metrics 全局指标 | ⚠️ 当前 EchoMem **不暴露逐请求 telemetry** → 服务端证据缺失，仅黑盒观测 |
| 8 | 延迟分布（commit/search P50/P90/P95/P99/max + 逐请求队列证据） | ✅ | ✅ | ✅ |
| 9 | 429 / Retry-After / 队列深度关联 | ✅ 采集 + 报告两列 | ⚠️ 分类错误计数，不做逐请求关联 | ✅（EchoMem 已有 429 `COMMIT_QUEUE_FULL`） |
| 10 | 写后读一致性窗口量化 | ⚠️ 仅隔离探针验证「必命中」，**无介入后多久可读的 P50/P95/超时计数** | ✅ `run_consistency_checks` | ✅ |
| 11 | Prometheus 服务端直方图双视角 | ⚠️ 原始 /metrics JSONL 保留，**无 bucket 插值分位** | ✅ `histogram_percentiles`（`echomem_recall_duration_seconds` 等） | ✅ |
| 12 | §10.5 验收数值目标断言（300/h、P95<3s、队列≤128、RSS<6GB、provider 429=0） | ❌ 只采集不判目标 | ⚠️ 特性阈值（2x/3x/5MB）≠ §10.5 数值；无 300 commits/h 目标档 | ✅（需构造对应负载） |
| 13 | 生产验收信号（345s/600s 耗时簇、abandoned_workers、BrokenPipe） | ❌ | ⚠️ 部分：错误分类明细可暴露；无专门信号断言 | ⚠️ 依赖日志侧 |
| 14 | 长稳态 soak（30~60min） | ✅ soak 案例 | ⚠️ 支持大 duration 但场景标称短 | ✅ |
| 15 | 真实对话种子（locomo） | ❌ 仅合成 marker | ✅ `--seed-source locomo` | ✅ |
| 16 | 资源：线程/FD/Swap（进程侧） | ✅（`--pid` 采样 RSS/CPU；README 声明含线程/FD/Swap 范围） | ⚠️ CPU/RSS/线程/句柄/commit 队列水位（无 FD/Swap） | ✅ / ⚠️ |

## 5. 兼容性与注意事项（若合并/使用）

### 5.1 合并兼容性
- PR #28 基于 `v3_mcpTool`；本地 `v3` 已包含 `v3_mcpTool` 全部提交（merge-base = `74f5d6a`），
  21 个文件全部新增 → 合入 `v3` 为干净 3-way merge，与本地未提交改动（`performance/`、
  `docs/performance-stress-test-design.md`、`tests/test_performance.py` 及 3 个已跟踪文件）无重叠。

### 5.2 鉴权头差异（必须处理）
- PR #28 默认鉴权头 `X-API-Key`（README: "matching the current EchoMem HTTP server"）；
- 当前本地 EchoMem 使用 `X-Auth-Key`（`entrypoints/api/base.py:59`，auth mode `x_auth_key`）。
- 使用须显式传 `--auth-header X-Auth-Key`（runner 已支持）。

### 5.3 API 面兼容（已核对，均存在）
| runner 调用 | EchoMem 路由 |
|---|---|
| `POST /api/sessions/open` | ✅ |
| `POST /api/sessions/{id}/messages` | ✅ |
| `POST /api/sessions/{id}/commit` | ✅ |
| `GET /api/sessions/{id}/commits/{archive_id}`（状态轮询） | ✅ `handlers.py:562/851` |
| `POST /api/retrieval/search` | ✅ |
| `GET /api-doc/openapi.json`（服务诊断） | ⚠️ 需确认；不可达时 `service_diagnosis` 返回提示，不影响主流程 |

### 5.4 服务端 telemetry 证据缺口（对当前 EchoMem 的关键限制）
PR #28 的报告把服务端证据建立在逐请求字段上（`request_id / received_at / queue_entered_at /
execution_started_at / finished_at / queue_depth / active_workers / status_code / retry_after /
terminal_status`）。**当前 EchoMem 的 HTTP 响应不含这些字段**（已 grep `entrypoints/api`，
无 `queue_entered_at` / `execution_started_at` / `telemetry`）。后果：
- SP 结论只能标记「客户端黑盒观测」，不能写「服务端限流已验证」；
- 缓解：EchoMem `/metrics` 已有全局 `echomem_session_commit_queue_depth`、
  `echomem_http_requests_inflight`、`echomem_recall_duration_seconds`（histogram）、
  `echomem_session_commit_duration_seconds`（histogram）、`echomem_process_cpu_seconds`、
  `echomem_process_resident_memory_bytes`（`EchoMem/src/echomem/metrics/registry.py` + `process.py`）——
  全局队列趋势可展示，逐请求关联需 EchoMem 侧补字段（PR #28 PLAN §服务端证据要求即此清单）。

### 5.5 文档-实现差异（PR #28 内部）
- `MULTI_TENANT_TEST_PLAN.md` 声明的「租户倾斜」场景（A 高流量 2.0 RPS/8 commit-rpm，
  B/C/D 低流量）**未在 `formal_suite.py` 的 5 个 SCENARIOS 中实现**（baseline/mixed/commit-storm/
  search-storm/soak）；runner 层按速率参数可手工拼出倾斜负载，但 suite 无一键案例；
- PLAN 声明的「预热 60s/正式 10min/冷却 60s/重复 3 次，CV>10% 增到 5 次」为流程约定，
  formal_suite 以 `--repeats 3` 实现，预热/冷却与 CV 升档逻辑未见实现。

## 6. 建议

1. **合并策略**：建议以 PR #28 `stress/` 为官方骨架合入 `v3`，本地 `performance/` 保留为
   「四项特性保证」判定层。二者目录、入口、报告均独立，可并行存在。
2. **补全优先级**（若要把「全部特性」收口到单套工具）：
   - P0：EchoMem 补逐请求 telemetry 字段（PR #28 PLAN 的服务端证据要求，属 EchoMem 侧改造）；
   - P1：从 `performance/` 移植——四项特性 PASS/FAIL 判定、D 注入洪峰窗口与劣化倍数、
     写后读一致性窗口、histogram bucket 插值、§10.5 验收目标断言表；
   - P2：formal_suite 补「租户倾斜」一键案例；`--auth-header X-Auth-Key` 设为本地默认提示；
   - P3：横评一次真压测（先 10.5 目标档负载）后再定最终形态。
3. **验证基线**：`performance/` 本地 63 例测试全绿（2026-08-27 实测）；PR #28 自述 19 例通过
   （合并后本地复跑）。

## 7. 参考

- 需求源 A：`docs/performance-stress-test-design.md`（本地）
- 需求源 B：`EchoMem/docs/design/multi-tenant/recall-concurrency-backpressure-design.md` §2/§7/§10.5
- PR #28：`stress/README.md`、`stress/MULTI_TENANT_TEST_PLAN.md`、`stress/echomem/runner.py`
  （head `ccd099a2`，API 快照 2026-08-27）
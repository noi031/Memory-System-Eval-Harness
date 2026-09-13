---
name: echomem-stress
description: >
  交互式准备、启动、监控、续跑并解读 EchoMem 真实模型 M1-M6 压测
  （Memory-System-Eval-Harness）。当用户要求对 EchoMem 做压测、测量容量、
  公平性、Search 优先级、租户故障隔离、Commit 恢复、可观测性，或生成
  HTML 报告时使用。
---

# EchoMem 压测

以引导式产品流程运行仓库的真实 HTTP 六项指标套件。开始运行前先读
`references/interactive-workflow.md`；以 `performance/targets/echomem/README.md`
作为安装与 CLI 的权威参考。

## 激活方式（前置条件与初始化）

在引导使用者之前，先确认以下依赖已就绪；缺哪一项就先把使用者引到
`performance/targets/echomem/README.md` 对应小节，不要跳过：

1. **套件仓库**：`Memory-System-Eval-Harness` 检出，`.venv` 已创建并
   `pip install -r requirements.txt`；确认含
   `performance/targets/echomem/observation_run.py`。
2. **EchoMem 部署**：真实服务可达（`/api/v1/system/ready` 200）；被测版本
   满足 M1-M3（当前 `origin/develop`）或完整 M1-M6（PR449 同步）的版本
   要求（见证据规则）。
3. **真实模型**：EchoMem 配置真实 LLM 与 Embedding；`test.env` 里有
   `ECHOMEM_LLM_API_KEY` / `ECHOMEM_EMBEDDING_API_KEY`；profile 钉住
   `required_embedding_model` 时 preflight 逐字匹配。
4. **租户与密钥**：`provision` 开通独立租户（容量档推荐 32），
   `tenants.json` 只存 `auth_key_env` 名，密钥在 Git 忽略的 `test.env`。
5. **profile**：按 README 创建唯一本机 profile（绝对路径、`require_4u8g`
   按目标设置；无 Docker 时留空 `resource_container` 并设
   `require_4u8g=false`）。
6. **破坏性授权**：M4 故障注入、M5 容器 kill/restart、root 登录与
   远程/共享资源，必须先获得使用者明确授权（安装本 skill 不等于授权）。

初始化以 quick 链路检查收尾（见「交互式流程」第 3 步），确认接线后再进入
正式范围。skill 本身无需安装动作：仓库内即用，助手引用相对路径即可。

## 便携图表报告工作流

供 Kimi、Codex 或其他本地编码助手使用：执行或重新生成报告前先读
`references/chart-report-workflow.md`。这些是仓库相对指令，不依赖任何
Codex 工具。助手需要本地文件与 shell 访问权限；助手所用模型与受测
EchoMem 服务使用的 LLM/Embedding 相互独立。纯报告请求不得启动新的付费
负载。按证据类型选择生成器：M1-M6 用规范套件；有界 Commit 诊断用
`scripts.build_commit_diagnostic_report`，绝不能替代六项指标。交付生成的
HTML 路径、证据范围与验证结果，而不是只给一段文字总结或原始 JSON。

## 非协商的证据规则

- 使用真实 EchoMem 部署、真实 LLM、真实 Embedding 与独立租户凭据。禁止
  使用 mock，禁止把一个 key 复用成多个租户。
- 禁止测试停留在仓库默认 `main` 分支的 EchoMem 检出。M1-M3 要求当前
  `origin/develop`；完整 M1-M6 要求 PR449 包含当前 `origin/develop`
  （直到 PR449 合并）。用 `git merge-base --is-ancestor origin/develop HEAD`
  验证，失败则以 `BLOCKED` 停止。
- 从所检出 EchoMem 版本的根 `configs/config.example.json` 构建
  `deploy/single-node/config.json`。当 `engine.enabled` 为空时以 `BLOCKED`
  停止：无内存引擎的单节点示例不是有效的真实记忆压测目标。
- HTTP 200 的 Search 若不含期望事实，不得计为成功召回；Commit 202 不得
  视为完成，必须轮询其终态。
- 失败、超时、被拒、provider 出错与挂起样本必须留在原分母中。禁止为
  美化结果而隐藏它们。
- 禁止打印、入库到 Git 或渲染 API key、租户 key、密码与测试控制 token。
  只引用环境变量名。
- 每次运行记录 EchoMem 与压测套件的精确 commit、配置指纹、模型名、容器
  身份与实际资源限制。
- 模型可用性预检与负载中的模型用量是两回事。`mock=false`、HTTP 200 或
  配置了模型名都不是调用证据；只有当有界服务阶段日志或 Provider 指标
  含真实样本时，报告才能声明负载中的模型用量，否则标注未验证。
- 遵守两级 readiness 门禁（`acceptance/readiness.py`）。**硬门**（
  `target-url`、`ready`）失败会中止运行；**可选证据门**（`/metrics`、
  `resource-container`、`fault_isolation`、`tenant_observability`）只喂养
  特定指标，观测入口（`observation_run` / `run_six_metrics.sh`）以降级
  模式运行它们：门失败只出现在 `readiness["degraded"]` 里并让受影响的
  指标降级，不会阻断整个运行。正式 `--six-metrics` 验收保持严格语义，
  仍要求 4U8G 与专用容器重启。
- **没有 Docker 或没有受保护观测接口的运行同样有效**，且仍必须产出
  `report.html`。缺失证据的诚实映射：
  - 无容器（`resource_container` 为空、`require_4u8g=false` →
    host-default）：M1 资源口径记为 0/None；结构化日志窗口跳过（阶段证据
    仅来自 `/metrics` histogram）；M5 不再要求 `allow_container_restart`。
  - fault 控制面 404 或缺少 token：跳过 24 例故障矩阵并记 INCONCLUSIVE
    审计命令；M4 置 BLOCKED。
  - tenant-observability 面 404 或缺少 token：不启动采样线程；M6 置
    BLOCKED。
  - `/metrics` 不可达：跳过 M1 资源时序与内存泄漏诊断。
  降级只把受影响指标推向 INCONCLUSIVE/PARTIAL/BLOCKED，**绝不伪造
  PASS/MEASURED**；每个被跳过的探针都在 `execution-manifest.json` 留下
  INCONCLUSIVE 命令供审计。
- 当前 M1-M6 流程只允许 `performance.targets.echomem.observation_run` 或
  `performance/targets/echomem/run_six_metrics.sh`。输出若无 `report.html`，
  停止并报 `WRONG_ENTRYPOINT`；禁止把其他 HTML 工件冒充当前六指标结果。
- **确认门不可跳过（最高优先级）**：任何会消耗模型额度、产生负载或破坏性
  效果的步骤（preflight 之后的正式压测、quick 链路检查、M4 故障注入、
  M5 kill/重启）启动之前，**必须**先向用户确认实际范围、参数与命令。
  用户要求压测但未指名范围时，把默认范围 M1、M2、M3 作为**提案**呈现并
  请求确认；未经确认不得以「默认」为由直接执行。用户点名范围时，点名本身
  是范围确认，但预览命令仍须展示并获得确认。`full` 包装始终是显式 M1-M6，
  同样须先确认。
- 把 `<OUTPUT>/report.html` 当作实时工件。在 preflight、记忆灌种、每个
  M1 档位、每个 M2/M3 场景、每个 M4 故障相位、每个 M5 恢复样本、最终 M6
  采集之后，都更新或重新生成同一个文件。未完成的指标要明确标注
  running / partial / blocked / not selected；绝不能等到整个运行结束才发布
  第一份报告。每次检查点后告诉用户报告路径、更新时间与最新测得分母，
  但不要用逐请求消息刷屏。
- 报告生成器是视觉布局的唯一实现。禁止在对话里或用手写脚本手工构建
  第二份 HTML 报告。展示结果前先读 `references/interactive-workflow.md`
  的「报告展示契约」。若已有持久化证据但缺少必需卡片、图表、分母、失败
  分类或原始证据链接，归类为 `REPORT_CONTRACT_GAP` 并修复生成器；禁止用
  推断值或单独的报告文件填补缺口。

## 交互式流程

1. 定位压测套件与 EchoMem 仓库，优先采用用户提供的路径。展示当前分支、
   commit、脏状态，以及套件是否含 `performance/targets/echomem/observation_run.py`。
   仅在获得授权后拉取分支引用，然后用 `origin/develop` 验证所选 EchoMem
   修订；禁止从目录名推断修订。
2. 检查前置条件并给出紧凑的 readiness 摘要：EchoMem 就绪、Docker/容器、
   profile、租户数、LLM preflight、Embedding preflight、受保护测试端点、
   输出目录、破坏性测试安全性。诚实区分两级门禁：`target-url` 与 `ready`
   是硬门（失败即停）；`/metrics`、`resource-container`、`fault_isolation`、
   `tenant_observability` 是可选证据门（失败只降级相关指标并记入
   `readiness["degraded"]`，绝不当作整场阻塞）。
3. 范围未明确时，把默认范围 M1-M3 作为**提案**请求确认，确认后再继续。
   新机器上的 quick M1-M3 链路检查同样必须先向用户确认再跑——「quick」是
   用户的选择，不是助手的默认动作。只有用户明确选择时才跑完整 M1-M6、
   单指标、续跑或纯报告。
4. 预览确切命令、所选指标、预估破坏性动作与输出目录（必须位于
   `performance/targets/echomem/results/` 之下，见「命令」节）。M4 故障注入、
   M5 kill/重启、root 登录或使用远端/共享资源前，必须获得用户明确授权。
5. 先跑 preflight，再执行仓库入口点。保持进程挂载并报告有意义的阶段变化；
   不得让必需进程无人跟踪地运行。
6. 完成后检查 `execution-manifest.json`、`summary.json`、`suite.json` 与
   `report.html`。打开 HTML 报告，总结每个所选指标的状态、分母、主数值与
   责任模块。解读结果前先确认 `<OUTPUT>/report.html` 存在；其他任何 HTML
   文件名都不是当前 M1-M6 报告的合法替身。

## 产品化对话

用以下状态引导工程师，而不是抛一张无差别的命令清单：

1. **发现**：检查本地仓库、EchoMem 就绪度、Docker 访问、profile、租户
   凭据、provider 配置与受保护端点。
2. **配置**：逐项向用户确认参数并主动给出默认值（见「参数配置」小节）：
   指标范围、档位 level、并发目标、每租户用户数、租户总数、每档时长、
   输出目录，以及本次运行是否允许故障注入或重启专用容器。默认值只是
   **待确认提案**：用户不指定时，把全部默认值展示出来请求一次整体确认，
   确认后才生效；用户指定则用指定值。未确认前不得启动任何会消耗模型
   额度或产生负载的步骤。不要一次抛全部问题——按「范围 → 档位/负载 →
   时长 → 破坏性」分两三轮问完，但每一轮问完都必须拿到明确答复才能继续
   到执行。
3. **预览**：展示精确目标 commit、所选指标、真实模型名、负载档位、破坏性
   动作与命令。发现模型名不匹配即停止。
4. **验证**：跑 provider 与记忆灌种/召回 preflight。HTTP 响应成功但缺期望
   事实，即为失败的召回 preflight。
5. **执行**：流式呈现阶段级进度并保持原分母。禁止用 EchoMem 内部队列或
   worker 配置缩短或自动封顶客户端负载——那些限制属于被测结果的一部分。
6. **讲解**：打开 HTML 报告，区分 measured、partial、blocked、inconclusive
   证据；说明责任模块与每个未完成指标的确切重跑条件。当指标 BLOCKED 或
   日志侧阶段证据不可用时，解释造成降级的门禁（缺容器、缺
   fault/tenant-observability 控制面、`/metrics` 不可达）以及补齐证据需要
   的配置，而不是把降级当成 EchoMem 缺陷。

容量或最大租户请求遵循 `performance/targets/echomem/README.md` 的双跑
流程：先保留目标版本默认调度配置，再跑单独指纹的调优配置（抬高本地
admission、模型、embedding、provider 预算与队列上限）。禁止合并两个结果
目录，也不得把调优数字描述成默认部署基线。若团队已对该确切账户、端点与
模型记录过 8/16/32/64 provider 并发证据，直接复用并只跑单调用
身份/维度 preflight，不要重复花费配额做 provider 扫描。

默认首轮容量上限为 32 租户、32 个观测到的在途请求。**以下只是默认值，
每次运行都必须在启动任何耗额度/负载步骤之前向用户确认，允许覆盖；
用户未确认前不得按默认值直接执行**：

| 参数 | 配置键 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 指标范围 | `--metrics` | `M1,M2,M3` | `full` 包装显式选 M1-M6 |
| Embedding 模型 | `required_embedding_model` | `qwen3.7-text-embedding-flash` | preflight 逐字匹配；可改，但改后须真实可用 |
| 租户档位 | `m1_tenant_levels` | `[1,2,4,8,16,32]`（quick `[1,2]`） | 每档租户数 |
| 用户档位 | `m1_user_levels` | `[1,2,4,8,16,32]`（quick `[1,2]`） | 每租户热用户数 |
| 并发目标 | `required_concurrency` | `32` | 校验档位覆盖；0 = 跳过校验 |
| 每档时长 | `m1_duration_s` | `300`（quick `15`） | 秒 |
| 每用户 Search RPS | `m1_search_rps_per_user` | `1` | M1 负载强度 |
| 租户总数 | `provision --count` | `32` | M2 至少 8 个独立凭据 |
| M4 采样 | `fault_isolation.samples` / `repeats` | `10`（quick）/ `100`（full）/ `3` | 故障矩阵轮次 |
| M5 样本 | `commit_recovery.samples` | `3`（m6_only `1`） | 每次 202→kill→恢复 |
| M5 恢复超时 | `commit_recovery.recovery_timeout_s` | `180` | 秒；探针默认 |
| M6 采样间隔 | `tenant_observability.max_sampling_gap_s` | `20` | 秒 |

覆盖示例（用户要求更高档位时）：

```json
{
  "m1_tenant_levels": [1, 2, 4, 8, 16, 32, 64, 128],
  "m1_user_levels": [1, 2, 4, 8, 16, 32, 64, 128],
  "required_concurrency": 128
}
```

在预览时向用户展示最终生效的参数值（默认或已覆盖），而不是只展示命令。
预览必须获得用户确认后才能进入执行（第 4 步）。
解释 32 个配置热用户与 32 个观测到的同时在途请求是两个不同事实，两个都
要报告。记录 EchoMem 并发与队列设置用于诊断，但禁止用它们降低发出的
客户端负载。

M3 必须同时含等负载公平/优先级证据与异构租户用例。默认异构用例对四个
独立租户施加 Search 权重 `[8,4,2,1]` 与 Commit 权重 `[1,2,4,8]`，证明一次
真实运行中不同租户可收到不同的 Search 与 Commit 强度。

最终报告还必须暴露三项跨指标审计：

- invalid-input 覆盖：每个用例与观测到的 HTTP 状态；
- 必需 API 台账：方法、路径、精确/最少调用次数，缺失端点保持可见；
- 端点与服务返回的模块时延分布。禁止用无关时钟相减推断内部 router、
  recall、scheduler 或 engine 时延。

profile 启用 `concurrency_topology` 时，对四种真实布局跑 16/32/64/128 矩阵：
每用户单会话串行访问、每用户多串行会话、单会话内并发请求、不均衡的
小 Search/大 Message+Commit 租户负载。把每个档位当作目标客户端并发而非
租户数，并保留「要求身份 / 实际身份」对照。启用 `payload_boundary` 时，
以 JSON 文本与原始二进制从 0 到 1 MiB 施压 Message、Commit、Search，把
超大 Commit 轮询到终态，并调用配置的 Streamable HTTP `add_memory` 工具。
缺少 MCP 配置置 BLOCKED；禁止用 HTTP 消息冒充 MCP。

M1-M3 的完整阶段可观测证据需要具体 `resource_container` 与 EchoMem DEBUG
JSON 日志：在有界运行窗口内采集白名单事件 `recall_stage_completed`、
`recall_engine_completed`、`dashscope_rerank_operation`、
`memory_extraction_completed`、`atomic_pipeline_completed`。用哈希后的
trace 引用关联响应轨迹，逐阶段报告观测数、P50/P95/P99 与排队等待分位数；
独立从窗口增量汇总七类受支持的 Prometheus histogram，并排展示日志/指标
覆盖。无容器（host-default）时跳过结构化日志窗口、阶段证据仅来自
`/metrics` histogram：把日志侧明确标注为不可用，禁止用端到端值相减推导
阶段时延。

## 命令

**结果目录固定位置（非协商）**：所有压测结果必须写入套件仓库的
`performance/targets/echomem/results/` 目录之下，一次运行一个子目录（如
`performance/targets/echomem/results/m1-m3-run1/`）。禁止把结果写到仓库根
`results/`、临时目录或其他任意位置；输出目录属于预览时必须展示给用户并
获确认的参数之一。

在套件仓库根目录、profile 与密钥 env 文件均在 Git 之外的前提下运行：

```bash
performance/targets/echomem/run_six_metrics.sh quick PROFILE OUTPUT ENV_FILE
performance/targets/echomem/run_six_metrics.sh full PROFILE OUTPUT ENV_FILE

.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE --env-file ENV_FILE --out-dir OUTPUT \
  # OUTPUT 必须形如 performance/targets/echomem/results/<run-name>/

.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE --env-file ENV_FILE --out-dir OUTPUT --resume
```

无 Docker 的本地部署：profile 里留空 `resource_container` 并设
`require_4u8g=false`；观测入口以降级模式运行并仍产出 `report.html`（见
证据规则）。运行 M4/M5 或 `full` 包装前等待用户明确授权——其破坏性动作
需要专用测试部署。

## 常见问题与排查

- **运行结束了但 M4/M5/M6 是 BLOCKED**：降级模式下的预期行为——fault
  控制面 / tenant-observability 面缺失（404 或缺少
  `ECHOMEM_TEST_CONTROL_TOKEN`），或未配置专用容器。这不是 EchoMem 性能
  缺陷；查 `suite.json` 的 `readiness["degraded"]`，并明确指出补齐该指标
  需要什么配置。
- **M1 资源数值全是 0/None**：host-default 模式——没有容器就没有
  CPU/内存样本。容量数字仍是实测的，只有 M1 资源侧降级。
- **运行后没有 `report.html`**：停止并报 `WRONG_ENTRYPOINT`。M1-M6 入口
  只有 `observation_run` / `run_six_metrics.sh`；用上方命令重跑，而不是
  改名其他工件。
- **readiness 显示某个可选门失败但运行继续**：这是设计行为——只有
  `target-url` / `ready` 是硬门，其余门只降级相关指标。不要因可选门
  降级而停止运行。
- **正式验收仍然严格**：`--six-metrics` 要求 4U8G 与
  `allow_container_restart`，不做降级。需要验收结论的用户应走该入口，
  而不是降级的观测运行。
- **Windows 下中文乱码**：跑测试与探针时设 `PYTHONUTF8=1` /
  `PYTHONIOENCODING=utf-8`；否则 GBK 解码 UTF-8 证据会看起来像套件故障。
- **M4 全矩阵 / M5 kill-重启需要授权**：安装本 skill 不等于授权。故障
  注入、root 登录或远端/共享资源使用前必须询问用户。

## 通用编号

- M1：单实例容量、热用户与 DAU 换算
- M2：等权重多租户公平性
- M3：均匀与单租户 Commit 洪泛下的 Search 优先级
- M4：一个租户延迟/拒绝故障下旁观租户的 Search 劣化
- M5：崩溃恢复后被接受的 Commit 重放、顺序与幂等性
- M6：按租户、按 lane 的队列/等待/执行/拒绝可观测性

禁止静默拉取、切换、重置或强制更新分支。若必需套件只在 PR 分支上，
说明原因并等待用户选择。
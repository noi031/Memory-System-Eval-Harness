# EchoMem 压测交互式工作流

本参考定义产品体验：如何在未知的本地机器上，引导另一位工程师产出可复现
的 M1-M6 HTML 报告。

## 1. 起始画面

从事实开始，而不是长篇问卷。检查机器并展示：

| 检查项 | 就绪条件 |
| --- | --- |
| 套件 | 找到仓库；记录分支、commit 与脏状态 |
| EchoMem | 找到仓库/部署；`/api/v1/system/ready` 成功 |
| 运行时 | Python 3.11+ 依赖已装；需要时 Docker 可达 |
| 真实模型 | LLM 与 Embedding 的 preflight 都成功 |
| 租户 | 存在独立凭据；容量档推荐 32 个 |
| 控制面 | fault 与 tenant-observability 端点可鉴权（仅完整覆盖需要；见下） |
| 恢复目标 | 为 M5 识别出专用本地容器（仅完整覆盖需要；见下） |
| 输出 | 新目录，必须位于 `performance/targets/echomem/results/` 之下；或用户明确选择续跑的既有目录 |

状态枚举：`READY`、`DEGRADED`、`NEEDS_SETUP`、`BLOCKED`、`DANGEROUS_TARGET`。
`DEGRADED` 表示硬门（`target-url`、`ready`）通过，但一个或多个可选证据门
失败（`/metrics`、`resource-container`、`fault_isolation`、
`tenant_observability`）：运行继续并照常产出 `report.html`，受影响指标
诚实降级——缺 fault 控制面时 M4 BLOCKED；缺 tenant-observability 面或其
token 时 M6 BLOCKED；host-default（无容器）时 M1 资源口径 0/None、无专用
容器时 M5 BLOCKED、结构化日志阶段证据不可用。`BLOCKED` 只保留给真正
中止运行的硬门失败（target-url/ready）。检查过程中绝不回显密钥值。

profile 钉住 `required_embedding_model` 时，逐字比较成功的 Embedding
preflight 模型名。当前正式 profile 的值为 `qwen3.7-text-embedding-flash`；
能工作的其他模型请求不可作为替代。

设置缺失时，把用户引向唯一本地指南 `performance/targets/echomem/README.md`。
不要发明第二条部署路径。

## 2. 范围选择器

**确认门不可跳过**：任何会消耗模型额度、产生负载或破坏性效果的步骤启动
之前，必须已获得用户确认。用户已点名指标或模式时，点名本身即范围确认，
但预览命令仍须展示并获得确认；用户未点名时，把默认范围 M1-M3 作为提案
请求确认，禁止以「默认」为由直接执行。可用的显式备选：

1. **快速链路检查（新机器推荐）**：真实 HTTP 与真实 provider、缩短采样。
   验证接线，不验证容量。同样必须先确认再跑——quick 是用户的选择，不是
   助手的默认动作。
2. **前三项指标（默认提案）**：M1 容量、M2 公平性、M3 Search 优先级。
3. **完整 M1-M6**：含租户故障注入与真实容器重启。
4. **单指标**：接受 M1 至 M6 中一个或多个。
5. **续跑**：同一 profile 与输出目录用 `--resume` 继续。
6. **纯报告**：不施加新负载，只重建或检查既有证据。

执行前展示简洁预览：

```text
目标:        EchoMem <branch>@<commit>
套件:        <branch>@<commit>
范围:        M1,M2,M3
模型:        <llm-name> / <embedding-name>（preflight 通过）
租户:        32 个独立凭据
资源:        观测到的 Docker 限制，或 host-default
破坏性:      无 | 租户故障 | 容器 kill/重启
输出:        <绝对路径>
```

预览必须展示**最终生效的参数值**（默认值或用户覆盖后的值），并标注每个
数值来源是「默认」还是「本次指定」：

```text
热用户档位:          1,2,4,8,16,32（默认；可覆盖）
要求并发:            32 个同时在途请求（默认；0 = 不校验）
每档时长:            300s（默认；quick 15s）
每用户 Search RPS:   1（默认）
租户总数:            32（默认；M2 至少 8 个独立凭据）
M4 采样:             10/100 样本 × 3 重复（默认，按 quick/full）
M5 样本:             3（默认；每次 202→kill→恢复）
M5 恢复超时:         180s（默认；探针默认值）
M6 采样间隔:         20s（默认）
EchoMem 限制:        观测并报告；不用于封顶客户端负载
异构租户:            Search 权重 8:4:2:1 / Commit 权重 1:2:4:8
```

每一项都可被用户覆盖：确认「档位/并发/用户数/时长加大或减小」的意图后，
把对应 profile 字段改掉再预览（配置键与默认值见 SKILL.md「参数配置」
表）。用户明确说「就用默认」后不再追问同一项；用户未提及时，展示
默认值提案请求一次整体确认，确认前不得按默认值执行。

M4 与 M5 必须针对专用测试部署。远程登录、共享计算、故障注入与容器
kill/重启需要当次运行获得明确授权；安装本 skill 不等于授权。

## 3. 本地准备

使用检出代码自带的配置：

1. 从其仓库部署 EchoMem，复制其当前 `configs/config.example.json`。凭据
   通过环境变量修改；不要用套件自持的配置模板替代。
2. 仅在专用测试部署上开启受保护测试控制：`ECHOMEM_TEST_CONTROL_ENABLED=true`
   且服务端与运行端持有相同的随机 `ECHOMEM_TEST_CONTROL_TOKEN`。
3. 完整覆盖需要确认这些端点存在：`GET/POST /api/inspect/test-control/fault`
   与 `GET /api/inspect/tenant-observability`。端点缺失（404）或 token 缺失
   只降级 M4/M6（BLOCKED），观测运行照常进行；正式 `--six-metrics` 验收
   仍要求它们。
4. 开通独立租户：

```bash
.venv/bin/python -m performance.targets.echomem.provision \
  --base-url http://127.0.0.1:8010 \
  --count 32 \
  --out .local-stress/tenants.json \
  --env-file .local-stress/test.env
chmod 600 .local-stress/tenants.json .local-stress/test.env
```

5. 按 `performance/targets/echomem/README.md` 创建本地 profile：用绝对
   路径，按实际测试目标设置 `require_4u8g`，识别专用恢复容器。无容器
   本地进程部署：留空 `resource_container` 并设 `require_4u8g=false`（
   M1 资源侧与 M5 将降级）。profile 与 env 文件都不得进入 Git。

正式运行前，先向用户确认是否要做快速链路检查；用户同意后再执行（quick
同样是消耗真实模型额度的步骤，不是免确认动作）：

```bash
performance/targets/echomem/run_six_metrics.sh quick \
  .local-stress/six-metrics.profile.json \
  performance/targets/echomem/results/local-six-metrics-quick \
  .local-stress/test.env
```

快速结果按设计是 `PARTIAL`，绝不能报成容量边界或正式验收结果。

命令退出后验证报告契约（`OUTPUT` 必须位于
`performance/targets/echomem/results/` 之下）：

```bash
test -f "OUTPUT/report.html"
```

若 `OUTPUT/report.html` 不存在，归类为 `WRONG_ENTRYPOINT` 并用第 5 节命令
重跑；不要重新解释或改名其他 HTML 工件为当前 M1-M6 报告。

## 4. 六项指标测试用例

### M1：容量、热用户与 DAU

为每个租户预灌唯一自然语言事实并验证 Search 可取回。按配置档位增加每
租户租户数与热用户数。每个档位跑真实 Search、Commit 与混合流量，记录
P50/P95/P99、吞吐、错误分类、召回分子/分母、CPU、内存、pending Commit
深度与负载停止后的恢复。

边界是最后一个可持续档位之后第一个出现持久阻塞、请求失败、崩溃、OOM
或无法排空的积压的档位。API/provider 错误是失败类型，不是 EchoMem 容量
的证据。把实测峰值吞吐换算为读重、均衡、写重 DAU 估计；标注为基于模型的
换算而非实测用户。

对默认 32 租户目标，区分这些测量：

- 32 档位上配置的活动租户或热用户数；
- 实际峰值同时在途 HTTP 请求数；
- 负载停止后积压能排空的最后一个档位；
- 第一个出现持久阻塞、超时、拒绝、崩溃、OOM 或积压不排空的档位。

禁止读取 EchoMem `max_concurrency`、队列容量或 worker 数来降低生成器
目标。把相关设置作为解释性证据写进报告；服务侧拒绝或队列上限是实测的
边界结果。用户可在首轮后追加 64/128 档而不改变测试逻辑。

### M2：等权重公平性

跑 4 与 8 个独立鉴权租户，施加相等的 Search 与 Commit 负载。每个 Commit
用自己的会话并轮询终态。逐租户报告 Commit 完成/秒与 Search P95；分别对
Commit 吞吐与 Search 时延倒数计算 Jain（较大者更好）。显式列出零完成
租户。等权重 Jain 分母不得包含冒充租户的重复凭据。

### M3：Commit 洪泛下的 Search 优先级

先测预灌热内存 Search 基线，再在真实 Commit 未完成时重复同样查询。两个
用例：

- `m3-flood-uniform`：Commit 负载铺满所有租户。
- `m3-flood-single-tenant`：单租户产生全部 Commit，其余租户持续 Search，
  暴露吵闹邻居耦合。

只统计时间戳与未完成 Commit 重叠的 Search 样本。报告基线与重叠窗口的
Search P95/P99、劣化比、错误、召回质量与 Commit planned/202/rejected/
completed/non-terminal 计数。该场景测跨操作优先级，不替代 M2 公平性。

另跑 `m3-heterogeneous-tenants` 用例，四个独立租户。施加 Search 权重
`8:4:2:1`、Commit 权重 `1:2:4:8`，逐租户报告配置权重、计划速率、实际
到达、Search P95/错误与召回质量、Commit 完成数。这验证一次运行能模拟读重
租户、两个中间租户与写重租户，而不是假设所有租户请求成本与速率相同。

### M4：单租户故障隔离

每次重复：先测故障前旁观租户基线，向一个已鉴权租户注入 `delay` 再注入
`reject`，故障期间旁观租户持续 Search，清除故障后测恢复。报告每个旁观者
的前/中/后 P95、错误率与劣化百分比。目标租户必须排除在旁观者分母之外。
控制端点响应本身不是证据；负载必须证明目标故障被施压。故障控制面缺失
（404/无 token）时矩阵被跳过并记 INCONCLUSIVE 审计命令，M4 置 BLOCKED。

### M5：被接受 Commit 的崩溃恢复

提交带幂等键的唯一标记 Commit。只有服务返回 202 且未到终态时，kill 专用
EchoMem 容器或进程。重启后不新建替代请求，轮询原操作；重复同一幂等键，
再对账 Commit 状态、历史、归档与 cursor。报告 accepted/recovered 样本与
精确的缺失、重复、乱序消息 ID。通过要求每个配置样本都覆盖，不只成功子集。
无容器且无 pid/restart_command 时不要求 `allow_container_restart`，恢复
探针早退 INCONCLUSIVE，M5 置 BLOCKED。

### M6：按租户 lane 可观测性

在整个 M1-M5 期间采样受保护可观测端点，刻意覆盖正常执行、排队、拒绝、
重置与重启世代。对每个观测到的 `tenant_id x lane`，要求队列深度、等待总
时长、执行总时长与拒绝计数。报告期望/观测单元格、缺失帧、负/非单调值、
重置事件，以及是否所有活动租户与 lane 都被覆盖。仅有 metrics 族存在不是
完整 M6 覆盖。tenant-observability 面缺失（404/无 token）时不启动采样
线程，M6 置 BLOCKED。

## 5. 命令

**执行前提**：以下任何命令只要会消耗模型额度、产生负载或破坏性效果，
启动前必须先向用户展示实际范围、参数与命令并**获得明确确认**（见第 2 节
确认门）。纯报告渲染不调用模型时仍需声明范围与输出路径。

正式完整运行：

```bash
# OUTPUT 必须形如 performance/targets/echomem/results/<run-name>/
performance/targets/echomem/run_six_metrics.sh full PROFILE OUTPUT ENV_FILE
```

前三项或所选指标：

```bash
# OUTPUT 必须形如 performance/targets/echomem/results/<run-name>/
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE \
  --env-file ENV_FILE \
  --out-dir OUTPUT
```

CLI 层 `--metrics` 缺省选择 `M1,M2,M3`、`full` 包装选择全部六项——这描述
的是工具事实，**不是免确认许可**：实际传给运行器的指标列表必须来自用户
已确认的范围，未确认前禁止直接调用。

同目录续跑：

```bash
# OUTPUT 必须形如 performance/targets/echomem/results/<run-name>/
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE \
  --env-file ENV_FILE \
  --out-dir OUTPUT \
  --resume
```

禁止用新运行覆盖旧结果。除非用户显式选择同一 profile 与指标集的
`--resume`，否则用 `performance/targets/echomem/results/` 下的新子目录。

## 6. 进度呈现

在 `OUTPUT/report.html` 维持一份实时报告。preflight 后创建，并在记忆灌种、
每个 M1 档位、每个 M2 租户层、每个 M3 洪泛模式、每个 M4 故障相位、每个
M5 恢复样本与最终 M6 采集后刷新。每次刷新必须用持久化证据、保留完整
分母，并把未完成指标标注为 running / partial / blocked / not selected。
验证文件修改时间在推进，并告诉用户路径、更新时间、完成/总工作量、当前
分母、最新 P95 与错误数。不要用逐请求消息刷屏，也不要等到全跑完才发布。

出错时，在安全前提下继续独立指标，并把错误归类为：

- 外部 provider：鉴权、余额、配额、限流、模型超时；
- 部署/控制：readiness、缺 token、受保护端点、容器；
- EchoMem：Search/Recall、路由/准入、Commit/恢复、引擎、租户隔离或
  可观测性；
- 套件执行或证据缺陷。

Provider 失败只阻塞依赖召回结论的指标，不抹掉有效的 M5 恢复或 M6 控制面
证据。测试控制端点缺失使 M4/M6 降级为 BLOCKED——查 `readiness["degraded"]`
区分部署/控制缺失与 EchoMem 缺陷，不要把缺失端点称为 EchoMem 性能失败。

## 7. 报告交付

检查并保留：

- `execution-manifest.json`：版本、配置指纹、provider preflight；
- `summary.json`：结构化 M1-M6 结果与分母；
- `suite.json`：case/探针证据；
- `records.csv` 与 `metrics_samples.csv`：原始请求与资源样本；
- `report.html`：可视报告，每项指标后附测试方法。

为用户打开 `report.html`。结论在前，然后 M1、M2、M3、M4、M5、M6。对每个
所选指标说明它反映什么、怎么测的、测得分母、图表/数据、状态、失败类型
与按 EchoMem 模块分组的具体改进：外部 provider、Search/Recall、
路由/准入、Commit/恢复、内存引擎、租户隔离/控制、可观测性、套件/部署。

六项指标之后追加报告级审计：

1. **invalid-input 矩阵**：施压缺失/非法鉴权、畸形 JSON、缺失/非法 Search
   字段、非法会话操作、非法 Commit 状态/记忆/历史/归档/cursor 目标、
   非法文件系统 URI、无 token 的受保护端点。展示每个用例与状态；不要只报
   成功子集。
2. **API 调用台账**：列出每个六指标运行时端点的方法、路径、精确或最少
   观测调用次数与覆盖状态。与 M1-M6 无关的产品 API 在台账之外并标注。
3. **模块时延证据**：画 HTTP 端点 P50/P95/P99 与 EchoMem 返回的任何显式
   时延字段。服务不暴露 router/recall/admission/Commit/atomic-engine 阶段
   时，标注为不可观测；禁止用减法制造阶段时延。

## 8. 报告展示契约

仓库报告生成器（而非 agent）拥有 HTML 结构、样式、图表、状态计算与转义。
agent 负责及时重新生成、验证、打开结果与简洁讲解。禁止创建并行的
`*-explained.html`、把其他工件改名成 `report.html`，或把密钥/原始 payload
贴进页面。

`report.html` 顶部必须让读者不看原始 JSON 就能理解运行。按序展示：

1. 总体结论：点名实测边界与最大阻塞项；
2. 生成/更新时间与运行状态（`RUNNING`、`PARTIAL`、`PASS`、`FAIL`、
   `BLOCKED`、`INCONCLUSIVE`、`NOT_SELECTED`），文字与颜色都要有；
3. EchoMem/套件 commit、profile/配置指纹、真实模型名、实际资源限制、
   所选指标、耗时与完成/总工作量；
4. 不含密钥值的 provider 与部署 preflight 状态。

每个指标小节保持相同阅读顺序：

1. **反映什么**与**怎么测的**，用平实语言。
2. 状态卡片：主值、分子/分母、最新完成档位或相位、错误与置信限制。
3. 对比/趋势图表，其后是绘图所用的精确值表格。提示或标签必须暴露精确
   值；图表绝不能替代分母。
4. 失败分类：EchoMem、外部 provider、部署/控制、套件/证据。未知失败
   保持可见。
5. 按责任 EchoMem 模块分组的具体改进建议，以及 partial/inconclusive 证据
   的确切重跑条件。
6. 用相对路径链接相关持久化 JSON/CSV/日志证据。

各指标专用可视化与数据：

| 指标 | 必需可视化 | 必需精确数据 |
| --- | --- | --- |
| M1 容量 | Search P95/P99、严格成功吞吐、错误率、CPU、RSS、Commit 积压的载入档位线/条 | 配置租户/热用户、观测峰值在途、Search 发送/严格成功/质量失败/非 200/传输错误、召回命中/尝试、Commit planned/202/completed/failed/pending、积排空结果、provider 错误、首个阻塞档位 |
| M2 公平性 | 逐租户 Commit 吞吐与 Search P95 条，加 Jain 汇总 | 凭据唯一租户数、每租户提供/实际 Search 与 Commit、完成、错误、倒时延输入、两个 Jain 分子/分母、零完成租户 |
| M3 优先级 | 基线与重叠洪泛 Search P95/P99（uniform、单租户、异构） | 确认未完成 Commit 重叠窗口、重叠 Search 数、召回命中/尝试、Commit planned/202/rejected/completed/non-terminal、逐租户配置权重与实际到达 |
| M4 隔离 | 每个旁观者的前/中/后 Search P95 与劣化百分比 | 注入租户与故障类型、控制响应、被施压故障证据、仅旁观者分母、每次重复的错误与恢复样本 |
| M5 恢复 | 接受到恢复漏斗与逐样本结果表 | planned、202 接受、非终态时 kill、终态恢复、重放幂等键、历史/归档/cursor 缺失/重复/乱序；失败样本留在分母 |
| M6 可观测性 | 租户×lane 覆盖矩阵与队列深度/等待/执行/拒绝图 | 期望与观测单元格、样本时间戳、队列深度、等待/执行总量或增量、拒绝数、缺失/非单调/重置帧、世代/重启边界 |

M1-M6 之后渲染 invalid-input 矩阵、API 调用台账、Search/Recall 与
Commit/Atomic 模块时延分布与原始工件索引。模块时延展示观测数与
P50/P95/P99，有排队等待时一并给出；标明每个值来自 trace 关联 JSON 日志
还是 Prometheus 窗口增量。禁止把端点时延、模型时延与内部阶段时间混入
一条未标注的序列。

每次实时刷新保留已完成小节与既往分母。agent 必须验证 `report.html`
存在、修改时间推进、展示的检查点与持久化证据一致，且没有任何所选指标
静默消失。生成器无法渲染可用字段时，报 `REPORT_CONTRACT_GAP`、修补规范
生成器、重新生成同一文件并在展示前重跑其聚焦测试。
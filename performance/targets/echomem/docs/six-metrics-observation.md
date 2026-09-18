# EchoMem 4U8G 六项黑盒观测运行手册

本入口只回答“本次真实环境观测到了什么”，不回答“是否达标”。它不设置 P95、
准确率、Jain、吞吐或劣化比例门槛。所有 HTTP 错误、超时、空召回、未发送请求、
Commit pending/failed 都保留在分母。最终数据状态只使用 `MEASURED`、`PARTIAL`、
`BLOCKED`、`EXECUTION_ERROR`。

## 安装

在仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

需要一个专用 EchoMem 测试容器。容器必须实际限制为 4 CPU、8 GiB，并允许测试
进程读取 Docker inspect；M5/M6 RESET 会对该容器执行 kill-9 和启动。不要填写
共享、生产或名称模糊的容器。

## 凭据与配置

复制 `performance/targets/echomem/docs/six-metrics.profile.example.json` 到 Git
忽略的本地目录。`tenant_config` 至少配置 8 个独立租户，每个凭据只引用环境变量：

```json
{
  "tenants": [
    {
      "tenant_id": "tenant-1",
      "user_id": "stress-user-1",
      "account_id": "stress-account-1",
      "agent_id": "stress-agent-1",
      "auth_key_env": "ECHOMEM_TENANT_1_KEY"
    }
  ]
}
```

禁止在 JSON、Git、日志或报告中写 `auth_key`。观测入口发现明文 key 会直接拒绝
运行。真实 LLM、embedding 和故障控制 token 同样只从环境变量读取：

```bash
export ECHOMEM_TENANT_1_KEY='...'
# 继续设置 ECHOMEM_TENANT_2_KEY ... ECHOMEM_TENANT_8_KEY
export ECHOMEM_TEST_CONTROL_TOKEN='...'
export YOUR_LLM_API_KEY='...'
export YOUR_EMBEDDING_API_KEY='...'
```

若专用服务允许 bootstrap 注册，可一次生成引用环境变量的租户文件和本地密钥文件：

```bash
.venv/bin/python -m performance.targets.echomem.provision \
  --base-url http://127.0.0.1:8010 --count 32 \
  --out .local-stress/tenants.json \
  --env-file .local-stress/test.env
```

不要省略 `--env-file` 后再把生成的明文 `auth_key` JSON 用于观测入口；该入口会拒绝运行。
模型密钥和测试控制 Token 继续追加到同一个 `test.env`，运行时通过 `--env-file` 加载。

`preflight_config` 必须指向 EchoMem 实际使用的 JSON 配置。运行器从该配置推导
expected lanes，不接受在测试配置里写死四条 lane。它会在发压前真实调用 LLM 和
embedding endpoint，mock/fake 或错误模型不会生成成功结果。

M1-M3 的模块耗时来自 EchoMem 自身证据，不从 HTTP 端到端耗时做减法。被测配置需要：

```json
{
  "runtime": {"log_level": "DEBUG"},
  "logging": {"level": "debug", "format": "json"}
}
```

profile 同时设置 `resource_container` 和 `require_stage_observability: true`。测试平台会在
本次运行时间窗内收集结构化 Search/Recall、Commit/Atomic Engine 日志，按脱敏后的
trace 引用关联请求，逐阶段输出 observations、P50、P95、P99 和 queue wait；七组
Prometheus Histogram 则按窗口累计值增量独立统计并与日志覆盖交叉校验。只有某阶段两路
均没有真实样本时才标记为不可观测，并说明是日志采集失败、指标缺失还是场景未触发。

## 完整运行

结果目录固定位于 `performance/targets/echomem/results/` 之下（禁止写到
仓库根 `results/` 或其他位置），一次运行一个子目录：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json \
  --profile 4U8G \
  --out-dir performance/targets/echomem/results/echomem-4u8g-$(date +%Y%m%d-%H%M%S)
```

完整运行包括 M1 的跨租户 T 阶梯、租户内 U 阶梯及 Search/Commit/mixed/hotspot
四类开放到达负载；M2 的 4/8 租户等需求窗口；M3 的
baseline/均匀洪泛/单租户洪泛；M4 的 24 个故障用例；M5 的三个独立 kill-9 样本；M6 的 2 秒时序采样。
默认关闭 soak。配置负载时长约为：M2 两档共 16 分钟、M3 三场景共 15 分钟、
M4 的 24 个前/中/后窗口共 72 分钟；不含准备、请求收尾和恢复等待。
M1 在出现持续拥塞后不再继续升档，实际耗时取决于命中哪一档；M5 取决于原任务恢复速度。
这些是场景时间预算，不保证真实模型端到端完成时间。阶段报告可先查看，不必等待全部结束。

M3 基线不要求 100% 准确率：必须每租户至少观察到一次真实事实/标记命中，且所有样本具有
完整延迟与质量记录；部分未命中、降级及错误继续进入分母。没有真实召回证据的租户单独标出，
不能用纯空召回或问候语充当记忆召回基线。发现基线包含写入时，必须修复负载后重测，不能直接比较洪泛劣化。

## 快速诊断版、正式完整版与单项复测

报告改进、独立 M3 与快速执行编排的逐项状态见
[改进跟踪清单](report-improvement-tracking.md)。清单中的待办不代表已通过实测。

用 `--metrics` 选择单项：

```bash
# 将 M1 替换为 M2、M3、M4、M5 或 M6
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json --profile 4U8G \
  --out-dir performance/targets/echomem/results/m1 --metrics M1
```

M6 单项会使用 4 个独立租户，以最长 45 秒负载与终态观察、8 个 Commit 屏障的真实基线/洪泛负载，
再执行一个真实 reject 故障用例和一次真实容器崩溃恢复，采集
NORMAL、QUEUE、REJECT、RESET 所需证据；不会把依赖数据冒充为 M3/M4/M5 的完整执行。
完整六项运行仍执行 M4 的 24 个故障用例和 M5 的 3 个恢复样本，测试目标没有缩减。
quick smoke 使用短窗口和小样本，报告固定标记
`quick-non-complete` 与 `PARTIAL`，不能与完整采样混用：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json --profile 4U8G \
  --out-dir performance/targets/echomem/results/smoke --quick
```

quick 一般需要 10 到 40 分钟，取决于真实模型和 Commit 恢复时间。

推荐直接使用 Python 模块入口（跨平台，仓库无 shell 包装）：

```bash
# 快速诊断版（采样缩短，结果标记 PARTIAL）
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE_JSON --env-file ENV_FILE --out-dir OUTPUT_DIR --quick

# 正式完整版（显式选择全部六项）
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE_JSON --env-file ENV_FILE --out-dir OUTPUT_DIR \
  --metrics M1,M2,M3,M4,M5,M6

# 只复测 M6
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE_JSON --env-file ENV_FILE --out-dir OUTPUT_DIR \
  --metrics M6
```

## 负载场景与探针选择（--scenarios / --probes）

三个参数回答三个问题：`--metrics` 选**结论**（M1-M6 哪些指标出报告），
`--scenarios` 选**负载**（6 个观测场景里跑哪几个），`--probes` 选**探针**
（14 个专项检查里做哪几个）。只给 `--metrics` 即可运行：负载场景按指标自动
推导（M2 → 公平性 2 例，M3 → 洪泛 4 例，M4/M6 的依赖窗口自动补上），探针
跑 profile 中配置了的全部（指标必需的探针不可排除）。

`--scenarios` 目录（`--help` 也会列出）：`m2-fairness-4t`、`m2-fairness-8t`、
`m3-baseline`、`m3-flood-uniform`、`m3-flood-single-tenant`、
`m3-heterogeneous-tenants`。显式给出时替换默认集，依赖窗口仍自动补齐。

`--probes` 目录（14 项）：`invalid-input`、`capability`、`blackbox-contract`、
`missing-cases`、`concurrent-commit`、`fault-isolation`、`limit-failure-sweep`、
`commit-recovery`、`tenant-observability`、`concurrency-topology`、
`payload-boundary`、`fault-plan`、`nxn-isolation`、`disconnect-recovery`。

例如只跑 M3 基线负载 + 隔离与断连恢复专项：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE_JSON --env-file ENV_FILE --out-dir OUTPUT_DIR \
  --metrics M3 --scenarios m3-baseline \
  --probes nxn-isolation,disconnect-recovery
```

## 续跑、停止与清理

每次新测试使用独立输出目录。目录已有 `summary.json` 时，未指定 `--resume`
的命令会直接退出，保留原报告；目录正在运行时，即使指定 `--resume` 也会拒绝启动。
输出锁覆盖预检和错误报告写入，因此第二个命令不会把原任务覆盖为 `BLOCKED`。

同一命令追加 `--resume`。已完成的 case 和 M1 子目录会跳过，未完成的通用 case
从第一个缺失 `summary.json` 的场景继续。中断时按 `Ctrl-C`；每个故障用例都在
`finally` 中关闭故障，M5 每次 kill 后都会启动目标容器。

中断后仍应人工确认专用容器已运行，并用控制接口执行一次 disable：

```bash
docker inspect echomem-stress-4u8g --format '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}'
curl -fsS -X POST http://127.0.0.1:8010/api/inspect/test-control/fault \
  -H "X-EchoMem-Test-Token: $ECHOMEM_TEST_CONTROL_TOKEN" \
  -H 'Content-Type: application/json' -d '{"action":"disable"}'
```

## 产物

### 容量停止口径

按“出现明显阻塞即停止加压”的口径，默认连续两个 10 秒窗口，每窗口至少 20 个
已发送 Search/Commit 请求，且 HTTP 429/503/504 或超时占比达到 10%，记为
`CONGESTION_OBSERVED`。这是测试平台明确采用的拥塞停止规则，不是 EchoMem 的
性能承诺。召回未命中、401 和客户端未发出的请求不据此判断容量耗尽。
每窗分子、分母、阈值与停压后恢复结果均保留。拥塞档不等于稳定承载量；测试停止
后仍观察原任务恢复，不能把“最终可恢复”抹掉为崩溃，也不能因可恢复就继续升档。

报告按 M1 容量、M2 公平性、M3 洪泛优先级、M4 故障隔离、M5 崩溃恢复、M6
可观测性排列。`schema_version=2` 明确记录本次编号规则；汇总器仍可读取旧场景标签。
正式入口也按此顺序执行。M6 从 M1 前就开始持续采样，最后汇总；M1 每档停压
后需确认恢复，若积压恢复证据不足或服务仍异常，则保留容量数据并停止后续负载，
避免污染公平性和洪泛结果。后续阶段失败不会覆盖已完成的容量数据。

M3 先串行 open/add 准备每个计划事务，再集中提交 Commit；Search 持续运行，
只有与已受理 Commit 的真实非终态证据重叠的样本才用于洪泛比较。准备阶段错误
单独保留；准备失败不会重发或从计划数中扣除。若准备时间耗尽场景预算，仍视为
证据不足，不将空洪泛判为完成。此模式当前仅支持单波洪泛。

结果根目录固定保留：

- `execution-manifest.json`：运行时间、Git commit、所选指标、环境摘要和探针执行记录
- `suite.json`：场景、探针及完整原始引用；`tenant_observability_monitor` 保留本机单调时钟窗口、采样间隔上限及采集异常类型
- `records.csv`：合并后的逐请求记录
- `metrics_samples.csv`：合并后的资源/Prometheus 采样
- `structured-stage-events.jsonl`：白名单结构化阶段事件；原始 trace id 已哈希，不含请求正文
- `summary.json`：M1-M6 四态观测汇总
- `tenant-observability-samples.json`：M6 两秒快照、队列和重启分段来源
- `tenant-observability-before.json`：M6 发压前同步基线，用于计算计数器增量
- `fault-isolation-*.json`、`commit-recovery-*.json`：逐探针证据
- `report.html`：结论先行的最终 HTML

汇总值均可追溯到 JSON/CSV。字段未采集时保持 `null`；运行器不会删除失败样本、
隐藏错误或用 0 填补缺失值。

### 正式 M3 的 CSV 证据与完整性

一键观测中的 `m3-baseline`、`m3-flood-uniform`、`m3-flood-single-tenant`
也需要真实的非终态证据，不能只因三个目录存在就标为 `MEASURED`。

- `commit_submit` 只有 HTTP 202 且包含 archive ID 才建立受理区间。
  `commit_done` 是一次轮询观察的汇总，不一定代表任务完成。
- `records.csv` 保存 `poll_evidence_version=echomem-poll-v1`、轮询次数、HTTP/传输异常数、
  `last_nonterminal_at_ms`、`observation_ended_at_ms` 与明确的 `commit_terminal_state`。
  只有成功状态查询明确返回 completed/failed/error 才写 `terminal_at_ms`；
  `completed_at_ms` 仅在 completed 时写入。timeout、404、停止观察均不证明任务执行失败。
- 按 tenant/session/archive 三元组对账，重复回执、重复观察和孤立终态单列，不能后写覆盖先写。
  `commit_results.csv` 的 `unresolved` 表示尚未确认终态，`ambiguous` 表示重复证据；
  `rejected` 是入口拒绝，`failed` 才是明确的任务失败。`observation_status` 另列轮询超时或停止。
- HTML 同时展示宽观察窗口与非终态确认窗口的 Search 样本、平均延迟、P95、错误和质量。
  在途峰值是客户端观察值，不等于服务端排队深度，更不能证明内部严格 Search 优先级。
- 实际生效的 tenant 数、query 模式和 barrier 参数写入 `summary.json.measurement_contract`，
  quick 缩减后的数量不会冒充原计划。缺清单、缺租户召回基线、计划 Commit 未全部受理、
  终态不明或确认重叠证据不全时保留数据并标为 `PARTIAL`。不以 P95 或质量数值作为性能准入门槛。
- 历史 CSV 不补造新字段，旧报告重新汇总可能降为 `PARTIAL`；这是证据不足，不是服务性能退化。

### M2 独立周期公平性

六项观测入口可在 profile 中显式配置 `semantic_seed_cache` 为已有容量运行目录的绝对路径。
目录必须包含 `identities.private.json`（权限 0600）及 `seed-evidence.json`；它们不是公开报告，禁止提交仓库。
配置的每个租户必须与缓存中的 tenant/user/account/agent/key 完全匹配且只匹配一次。
缓存必须同样来自当前配置的种子语料；旧缓存会被明确拒绝。
平台不会再次 Commit，而是对每个租户现场抽取验证问题发起真实 Search；全部校验通过后才发压。
旧的 PASS 不作本次证据，当前校验失败仍保留全部租户分母并阻止开始测量。
报告标注 `validated-cache`，语料数量按实际缓存统计；这代表复用记忆，不代表本版本重新生成过记忆。
未配置该选项仍走下述全新注入流程；M2/M3 组合运行复用同一次准备结果，M4 使用同一事实样本。

观测入口的 M2/M3 及 M4 依赖基线，默认种子为固定事实语料
（`semantic_seed_kind=synthetic`：确定性 synthetic 语料，`seed=42`，`build_corpus`
生成锚词 marker 查询）；`semantic_seed_kind=locomo` 时改用 `conv-30/session_1`
的 LoCoMo 单会话语料。每租户先抽取验证用例，灌种后**真实执行检索**确认事实可命中
（healthy 数不足 → 种子失败，保留通过数/总数并阻止开始测量）。问题中不包含答案或
证据标识；断言只检查返回 items 是否包含该题对应证据消息的租户专属标识，忽略 query
回显、ID 和 debug 元数据。使用专用、未混入其他相冲突事实的测试租户。
负载期间任何错误、空召回或降级仍算质量失败。
M3 基线可用实际事实命中证明真实召回，不再要求编号命中；M4 断言答案别名而不是查询句本身。
M1 容量阶梯仍有独立的身份与记忆准备流程，不能把组合入口的种子通过当成 M1 全部租户验证通过。

与洪泛补测不同，正式 M2 分别使用 4 和 8 个独立租户，每个租户
拥有独立的 Search/Write 限速器。同档位、同语料、同速率，不能让快租户拿走慢租户的发压配额。

| 阶段 | 每租户负载 | 统计口径 |
| --- | --- | --- |
| 0–30 秒预热 | Search 1 次/秒，不启动 Write | 不参与 Jain 测量窗口 |
| 30–300 秒测量 | Search 1 次/秒；每 30 秒启动一个独立写事务 | 每租户计划 270 次 Search、9 次 Write |
| 300–480 秒观察排空 | 不再启动新事务，继续观察已有事务 | 完成数另列，不能回填窗口内吞吐 |

Write 是 `open → add×4 → commit → poll`，不是每次直接重复提交同一个会话。
计划时刻对应事务开始，真正 Commit HTTP 发送会晚于 open/add；报告分别列启动数、
提交数、HTTP 202 受理数，不能把三者混为一谈。
每个租户的实际启动时刻、计划时刻、序号和发压延迟写入 `records.csv` 的 `arrival` 行。
worker 不足或请求慢导致实际启动不足，保留未启动数量，不能只展示成功请求。

Commit 吞吐按 `[30,300)` 内确认完成数除以 270 秒；Search 按请求开始时间选入该窗口，
即使响应晚于 300 秒也保留其完整延迟。对所有 4/8 租户分别计算吞吐 Jain 与 `1/P95` Jain，
零完成租户仍在分母中，全部为零时 Jain 为 undefined。排空阶段失败及最终未完成任务另外保留。
Jain 接近 1 只说明租户之间均匀，并不说明性能好；短窗口不能证明长期稳态。

快速模式是 3 秒预热、12 秒测量、每租户每 3 秒一次 Write、最多 30 秒排空，
始终为 PARTIAL；额外 quick 时长上限可能进一步截短，应以报告实际窗口和缺口为准。
没有执行时钟或仍使用旧 barrier 合约的历史运行保留数据，但不标为新版 M3 完整实测。

### M6 过程完整性

M6 不只比较首尾。每一帧均使用运行前锁定的 `tenant × lane` 分母，空帧、
缺失字段、重复行、布尔值、负数及 NaN 不会被跳过。报告同时展示有效快照数、
每帧有效单元数，以及在全部采样帧中均完整的单元数；后者不是服务可用租户数量。

采样器在一次请求结束后等待 2 秒；HTTP 超时为 15 秒。默认最大采样空档为 20 秒，
可通过 `tenant_observability.max_sampling_gap_s` 调整。该参数约束证据完整性，
不是 Search 性能合格线。比较包括负载窗口首尾；旧产物没有本机时钟边界时，
只计算实际相邻帧间隔，不补造窗口覆盖证据。

计数器只在已确认的同一进程内做差。`queued` 下降是正常现象，累计计数下降
需要解释：同进程下降记录为异常；已确认重启则分段，不跨进程相减；身份未知
保持证据不足。M6 的 RESET 必须来自进程身份变化，不能仅凭计数下降或 PID
缺失推断重启。故障期间无法获取的帧仍保留，不能借“计划重启”隐藏采集失败。

只有每帧覆盖、采样间隔和进程分段核验完整，且实际观察到四种行为，M6 才为
`MEASURED`；缺数据为 `PARTIAL`，没有有效分母为 `BLOCKED`，采集失败或同进程
计数异常为 `EXECUTION_ERROR`。这些状态不代表服务性能是否达标。

## EchoMem 接口契约

当前完整执行需要：公开 Search、session open/message/Commit、commit status、history、
archive、cursor，以及受保护的 `/api/inspect/test-control/fault` 和
`/api/inspect/tenant-observability`。后者需返回 tenant、lane、queued、
wait/exec seconds total、rejected/accepted/completed/failed total。可靠切分 RESET
还需 `process_started_at`（可附 `process_id`）或每进程唯一的 `boot_id`；单独 PID
可能复用，不足以证明一直是同一进程。缺接口或身份字段时报告会
标记 `BLOCKED/PARTIAL`，不会降低测试要求。

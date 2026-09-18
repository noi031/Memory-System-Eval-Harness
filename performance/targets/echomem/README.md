# EchoMem 六项本机压测

这一个文件包含完整的本机部署、配置、执行和结果解释。测试直接访问本机 EchoMem，
不需要服务器，也不要求把容器限制为 4U8G。报告会记录容器实际 CPU、内存和镜像，
因此不同电脑的容量数据应分别比较。

## 交给任意 AI 助手的完整任务

压测执行规范一律以本仓库 `performance/skills/echomem-stress/SKILL.md` 及其
`references/`（interactive-workflow.md、chart-report-workflow.md）为准。让 AI 在测试
平台仓库中工作，先完整阅读该 skill 的全部文件，再按其中的
Discover → Configure → Preview → Validate → Execute → Explain 流程执行。

本 README 只描述 EchoMem 本机部署与环境准备，**不预设任何压测范围、指标、档位、
并发、租户数、模型或时长**——这些全部由使用者在 skill 引导下逐项指定并确认。
执行任何会消耗模型额度、产生负载或破坏性效果的命令前，AI 必须先向使用者展示
实际范围、参数与命令并取得明确确认；使用者未指名时逐项询问，默认值只是待确认
提案，不得免确认执行。先核对两个仓库的 branch、commit 和 dirty state，不要静默
fetch、switch、reset。

测试平台不得根据 EchoMem 的 worker、queue、provider budget 或 max concurrency
自动降载。保留超时、拒绝、Provider 异常、pending、空召回和质量失败的原始分母。
报告顶部必须列出实际预检的 LLM、Embedding、Endpoint、真实请求状态和配置指纹。
预检成功只证明模型可用；只有同时采到压测期间的模型阶段日志或 Provider 指标，才可
说明负载调用了模型。`mock=false`、HTTP 200 或配置中写了模型名都不能作为调用证据。
AI 不能读取文件、执行 Shell、访问 Docker 或持续跟踪长任务时，就不能声称已经完成
压测。quick 链路检查的结果只能标记为 `PARTIAL`，且同样必须经使用者确认后才能运行。

> 完整测试会对专用 EchoMem 容器注入租户故障，并在 M5 中执行真实 `kill -9` 和重启。
> 请勿指向日常开发、共享或生产容器。

## 可选：加载交互式 Skill

仓库自带的 `performance/skills/echomem-stress/` 是可移植的 `SKILL.md` 目录。支持项目
Skill、自定义 Agent 指令或上下文文件的 AI，可以按自身产品的方式加载整个目录；不支持
Skill 的 AI 直接阅读上一节列出的三个文件即可，测试命令和结果完全相同。

Codex 用户可选择安装到个人 Skill 目录：

```bash
mkdir -p ~/.codex/skills/echomem-stress
cp -R performance/skills/echomem-stress/. ~/.codex/skills/echomem-stress/
```

其他 AI 不需要执行这段安装命令。无论使用哪种 AI，执行规范都要求先展示 readiness，且
**任何会消耗模型额度、产生负载或破坏性效果的步骤（含 quick 链路检查）启动前，必须先
向使用者展示范围、参数与实际命令并取得确认**；在
新机器上是否先跑 quick，也要先征询使用者、由其决定后再执行。链路通过后再选择
M1-M3、完整 M1-M6、单项、续跑或仅重建报告。
M4 故障注入、M5 容器重启以及远程/共享资源操作仍需获得操作者明确授权。

## 测试内容

| 指标 | 测试动作 | 主要输出 |
| --- | --- | --- |
| M1 单实例容量与 DAU | 预注入真实记忆，按租户数和租户内热用户数逐档增加 Search、Commit 与混合负载，直到出现持续阻塞、失败、崩溃或积压不能恢复 | 每档 P95、吞吐、错误类型、质量分母、CPU、内存、最后正常档、首个拥塞档、三种业务画像 DAU 换算 |
| M2 多租户公平性 | 4 租户、8 租户使用独立凭证，以同档位请求并发 Search 和独立 Session Commit | 每租户 Commit 吞吐、Search P95、Commit Jain 指数、Search Jain 指数和零完成租户 |
| M3 Commit 洪泛优先级 | 先测热记忆 Search 基线，再运行均匀洪泛和单租户洪泛；仅统计与未完成 Commit 确认重叠的 Search | 基线/洪泛 Search P95、错误、召回质量、Commit 计划/202/拒绝/完成/未终态数量 |
| M4 单租户故障隔离 | 依次给一个租户注入 `delay` 和 `reject`，其余租户持续执行真实记忆 Search | 旁观租户故障前/中/后的 Search P95、错误率和劣化百分比 |
| M5 202 Commit 崩溃恢复 | Commit 返回 202 且尚未完成时 kill 容器，重启后只轮询原任务并重复提交幂等键 | 恢复终态，以及 history、archive、cursor 的消息集合、顺序、丢失和重复对账 |
| M6 分层分租户可观测性 | 全程采集受保护观测接口，并覆盖 NORMAL、QUEUE、REJECT、RESET | 每个 `tenant × lane` 的 queued、wait、exec、rejected 四元组，缺失帧、非法值和重启代际 |

测试是**观测型**的，不内置“P95 必须小于多少”之类性能门槛。`MEASURED` 表示规定的
数据分母已采集完整，不等于性能优秀；失败、超时、HTTP 200 但召回质量失败、Provider
异常和长期 pending Commit 都会原样进入报告。

每次运行还会生成两组横向证据：关键接口调用账本按 Search、Open、Add、Commit、
Commit 状态、History、Archive、Cursor、Metrics、故障控制和租户观测分别统计实际
调用次数与错误；负向契约探针独立检查缺认证、畸形 JSON、缺必填字段、错误字段类型、
不存在资源及非法故障类型，不把这些请求混入性能分母。接口和模块耗时分开显示：
客户端记录 HTTP 端到端耗时；测试平台按 `trace_id` 关联 EchoMem 的
`recall_stage_completed`、`recall_engine_completed`、`memory_extraction_completed` 和
`atomic_pipeline_completed` 结构化日志，分别统计 observations、P50、P95、P99 与
queue wait。七组 Prometheus Histogram 使用测试窗口内累计值增量独立汇总，并与日志
覆盖交叉校验。不得通过端到端耗时相减推算模块耗时；只有日志和指标均无真实样本时，
才将对应阶段标记为“不可观测”并列明原因。

M3 同时包含均匀 Commit 洪泛和单租户洪泛。后者让一个租户承担全部 Commit，四个租户
继续独立 Search，用于观察不同租户负载与耗时是否串扰；它是异构/吵闹邻居场景，不能
拿来计算 M2 的等权 Jain 公平性。当前 EchoMem 故障控制作用于目标租户全部认证请求，
若要分别制造“仅 Search 慢”或“仅 Commit 慢”，服务端还需提供按 operation 选择的故障范围。

## 1. 准备环境

本机需要 macOS 或 Linux、Docker Compose、Git、Python 3.11+，以及可用的真实 LLM 和
Embedding 凭证。禁止使用 mock。EchoMem 被测版本还需包含故障控制和租户观测接口。

获取两个仓库。`ECHOMEM_DIR` 可以换成自己的绝对路径。EchoMem 必须显式检出
`develop`；不要依赖 `git clone` 当时的默认分支，因为默认分支可能是 `main`：

```bash
git clone --branch develop https://github.com/tech-innovation-group/EchoMem.git
cd EchoMem
git fetch origin develop
git switch develop
git pull --ff-only origin develop
export ECHOMEM_DIR="$PWD"
printf 'EchoMem branch=%s commit=%s\n' \
  "$(git branch --show-current)" "$(git rev-parse HEAD)"
test "$(git branch --show-current)" = develop
cd ..

git clone https://github.com/tech-innovation-group/Memory-System-Eval-Harness.git
cd Memory-System-Eval-Harness
git fetch origin performance_refactor
git switch performance_refactor
git pull --ff-only origin performance_refactor
git rev-parse HEAD
```

六项观测入口已通过
[PR32](https://github.com/tech-innovation-group/Memory-System-Eval-Harness/pull/32)
合入 `performance_refactor`；PR32 仅保留为历史评审记录。测试归档时保留最后一条命令
输出的完整 commit。

### EchoMem 代码要求与 PR449

M1、M2、M3 和 M5 可使用提供标准 Session、Commit、Search、History、Archive 与 Cursor
接口的 EchoMem 版本。完整 M1-M6 还要求 EchoMem 包含 PR449 的黑盒测试接口：

```text
GET/POST /api/inspect/test-control/fault
GET      /api/inspect/tenant-observability
```

只运行 M1-M3 时使用上一步锁定的最新 `develop`。运行完整 M1-M6 时，在 PR449 合入
`develop` 前必须显式检出 PR449，并验证它已同步当前 `origin/develop`：

```bash
cd "$ECHOMEM_DIR"
git fetch origin develop pull/449/head:pr449-blackbox
git switch pr449-blackbox
git merge-base --is-ancestor origin/develop HEAD || {
  echo 'BLOCKED: PR449 尚未同步当前 origin/develop，请使用已同步分支后再测完整 M1-M6。'
  exit 1
}
printf 'EchoMem branch=%s commit=%s develop=%s\n' \
  "$(git branch --show-current)" "$(git rev-parse HEAD)" "$(git rev-parse origin/develop)"
git rev-parse HEAD
```

若上面的祖先校验失败，停止测试；不要让 AI 静默把旧 PR449 历史强行 rebase 或
cherry-pick。PR449 合入后，完整 M1-M6 也直接使用最新 `develop`。无论选择哪个版本，
都必须把最终 EchoMem branch、commit 和 `origin/develop` commit 写入报告。

## 2. 本机部署 EchoMem

使用 EchoMem 仓库自带的单节点 Compose，不设置 CPU 或内存上限：

```bash
cd "$ECHOMEM_DIR/deploy/single-node"
./manage.sh init
cp ../../configs/config.example.json ./config.json
```

这里必须复制仓库根目录的完整 `configs/config.example.json`，不能使用
`deploy/single-node/config.json.example`；后者允许 `engine.enabled=[]`，只能启动空引擎服务，
不能完成真实记忆 Commit/Search 压测。启动前执行硬校验：

```bash
test "$(jq '.engine.enabled | length' config.json)" -gt 0 || {
  echo 'BLOCKED: engine.enabled 为空，未启用任何真实记忆引擎。'
  exit 1
}
git -C "$ECHOMEM_DIR" branch --show-current
git -C "$ECHOMEM_DIR" rev-parse HEAD
```

编辑当前目录的 `.env` 和 `config.json`：

1. 以被测 EchoMem 代码中的 `configs/config.example.json` 为准，不使用测试平台模板；
2. 保留配置中的 `api_key_env`，把真实密钥只填入 `.env`；
3. 确认 `engine.enabled` 包含本次要测的真实记忆引擎；
4. LLM 与 Embedding 都必须可用，Search 返回 HTTP 200 不能替代模型预检；
5. 为压测专用控制面设置随机 token，并启用测试控制。

Embedding 至少确认以下字段；维度必须与被测版本的索引配置一致：

```json
{
  "model": {
    "embedding": {
      "provider": "openai_compatible",
      "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key_env": "ECHOMEM_EMBEDDING_API_KEY",
      "model": "<使用者确认的实际 Embedding 模型名>",
      "dimensions": 1024
    }
  }
}
```

这里是字段核对示例，不要用这段不完整 JSON 覆盖整个 `config.json`。必须从当前 EchoMem
代码自己的 `config.example.json` 开始，只修改对应值。真实 key 只写 `.env`。

EchoMem Core 进程需要接收下面两个环境变量：

```dotenv
ECHOMEM_TEST_CONTROL_ENABLED=true
ECHOMEM_TEST_CONTROL_TOKEN=<本机随机长字符串>
```

同时确认 `deploy/single-node/compose.yaml` 的 `core.environment` 已透传这两个变量。被测版本
还必须提供：

```text
GET/POST /api/inspect/test-control/fault
GET      /api/inspect/tenant-observability
```

缺少这些接口时，M4 和 M6 会明确报告 `BLOCKED`，不能用客户端模拟结果替代。

启动并检查：

```bash
cd "$ECHOMEM_DIR/deploy/single-node"
./manage.sh up
./manage.sh status
./manage.sh smoke
curl -fsS http://127.0.0.1:8010/api/v1/system/ready
curl -fsS http://127.0.0.1:8010/metrics >/dev/null
```

取得 Core 容器名，后面填入 profile：

```bash
docker inspect --format '{{.Name}}' "$(docker compose ps -q core)"
```

输出通常类似 `/echomem-core-1`；profile 使用去掉开头 `/` 后的 `echomem-core-1`。

### 2.1 并发调优（可选）与配置指纹

容量测试**每次独立运行**：当前生效配置即被测基线，一次运行一个结果目录与配置
指纹，禁止合并不同配置的结果。是否需要并发调优由使用者按需决定；调优实验必须
使用单独指纹与独立结果目录，禁止把调优数字描述成默认部署基线。

当前 4U8G `small` 默认通常包含 `model.max_concurrent=4`、Retrieval admission=8、
`llm_max_concurrent=4`、`embed_max_concurrent=4`、Recall LLM/Embedding=1/2，以及较小的
Recall 队列。这些值可能在默认 32 客户端并发前先形成排队，不能把该现象描述成硬件极限。

若团队已对该确切账户、端点与模型记录过 Provider 并发证据，可在运行
备注中引用该证据并跳过重复阶梯；仍需做一次真实鉴权、模型名、返回维度和单条向量检查。
没有既有证据的机器不得假定 Provider 支持 64 并发。

下面是 4U8G 的**起始调优方向**，不是所有机器通用的最终值：

```json
{
  "instance": {"profile": "small"},
  "scheduling": {
    "http": {"max_workers": 128},
    "retrieval": {"admission_permits": 32},
    "commit": {
      "executor_workers": 5,
      "gate_workers": 3,
      "queue_max": 320,
      "tenant_quota": 80
    },
    "llm_gateway": {
      "llm_max_concurrent": 6,
      "recall_llm_max_concurrent": 1,
      "episode_llm_max_concurrent": 3,
      "workers_llm_share": 2,
      "provider_budget_llm": 12,
      "embed_max_concurrent": 8,
      "recall_embed_max_concurrent": 16,
      "episode_embed_max_concurrent": 3,
      "workers_embed_share": 2,
      "provider_budget_embed": 29
    },
    "tenant": {"qps": 128, "concurrency": 32}
  },
  "recall": {
    "concurrency": {
      "engine": {"max_concurrent": 32, "queue_capacity": 256, "max_queued_per_tenant": 32},
      "intent_llm": {"max_concurrent": 8, "queue_capacity": 256, "max_queued_per_tenant": 32},
      "query_embedding": {"max_concurrent": 16, "queue_capacity": 256, "max_queued_per_tenant": 32},
      "rerank": {"max_concurrent": 8, "queue_capacity": 256, "max_queued_per_tenant": 32}
    }
  }
}
```

将这些字段合并进完整 `config.json`，不要覆盖其他引擎配置。`provider_budget_llm` 和
`provider_budget_embed` 必须分别不小于所有 LLM/Embedding 消费方份额之和。4U8G 下
`http.max_workers=128` 与 `retrieval.admission_permits=32` 满足 EchoMem 的 4:1 约束；
Commit executor+gate 为 `5+3=8`，不超过 4 核的 2 倍约束。

不要为了展示“32 热租户”把租户常驻缓存硬改成 32。4U8G 的租户缓存有真实内存预算，
启动校验拒绝超出预算的配置也属于有效容量证据。32 个独立凭据表示测试平台会产生
最多 32 租户流量，不代表 32 个租户必须同时常驻；报告要分别展示活动租户、峰值在途请求、
常驻缓存上限、淘汰以及首个持续积压档。

每次改配置后重启专用 Core，并在日志中保存 `instance_profile_resolved` 和
`provider_budget_configured`，确认实际生效值。默认测试平台仍然发送配置的 32 客户端并发，
不会读取这些服务端值后自动减压。

### 后续扩展到 128 并发时核对什么

128 个租户、128 个热用户和 128 个同时在途 HTTP 请求不是同一个指标。下面是扩展
并发实验时的核对表，不是已完成的 128 并发测试结论；实际档位以使用者确认的值为准。

| 层级 | 128 并发实验需要核对的内容 |
| --- | --- |
| 发压端 | 增加独立凭据和档位，设置 `required_concurrency=128`；核实实际在途峰值、计划/实际到达差和发压机资源，不能只看配置数字 |
| HTTP / Retrieval | 若目标是让 128 个 Search 同时进入 Retrieval，核对 `admission_permits=128`；当前版本 4:1 约束要求 `http.max_workers` 至少 512。若保留更小值，则必须将拒绝/排队作为实测结果，而不是降低客户端发压 |
| Recall 各阶段 | 分别核对 `engine`、`intent_llm`、`query_embedding`、`rerank` 的 `max_concurrent`、`queue_capacity`、`max_queued_per_tenant`；阶段有分叉，128 请求不一定只产生 128 次模型调用 |
| LLM / Embedding 网关 | 核对总池、Recall/episode/worker 份额和 Provider budget。当前 V4 要求 `recall_llm_max_concurrent * 4 <= llm_max_concurrent`；例如 Recall LLM 配 128 时总池至少 512，不能只改单一字段。预算还需覆盖其他消费者之和 |
| Commit | 分别记录受理队列、每租户配额、executor/gate 实际工作数。128 个未完成 Commit 不等于 128 个执行线程；不能为了数字违反实例 CPU/内存校验，也不能将 202 当成吞吐完成数 |
| 租户 / 连接 | 核对租户 QPS、单租户 concurrency、客户端连接池、服务进程文件描述符和容器资源；128 租户不要求硬改成 128 个常驻缓存 |
| 外部模型 | 单独核实真实账号/模型的并发、RPM/TPM、429、超时和重试。扩大 EchoMem 本地队列不会提高供应商限额；不要把 Provider 限流报告为 EchoMem 最大容量 |

配置应通过被测版本启动校验，并保留 `instance_profile_resolved` 和实际资源限制。
“不让内部配置影响发压”指测试平台不读取这些值来偷偷降低客户端负载，并不代表
服务端配置对性能没有影响。不同配置的运行必须分开报告，不能混用容量边界。

## 3. 安装测试平台

回到测试平台仓库根目录：

```bash
cd /absolute/path/to/Memory-System-Eval-Harness
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p .local-stress
```

## 4. 创建独立测试租户

M2 必须使用不同租户凭证；重复使用同一个 key 只能测到并发，不能证明租户公平。

```bash
.venv/bin/python -m performance.targets.echomem.provision \
  --base-url http://127.0.0.1:8010 \
  --count 32 \
  --out .local-stress/tenants.json \
  --env-file .local-stress/test.env
chmod 600 .local-stress/tenants.json .local-stress/test.env
```

`tenants.json` 只保存 `auth_key_env` 名称，真实租户 key 位于 Git 忽略的 `test.env`。
继续编辑 `test.env`，加入：

```dotenv
ECHOMEM_TEST_CONTROL_TOKEN=<与 EchoMem Core 完全相同的 token>
ECHOMEM_LLM_API_KEY=<真实 LLM key>
ECHOMEM_EMBEDDING_API_KEY=<真实 Embedding key>
```

若 `config.json` 使用其他 `*_api_key_env` 名称，也要把对应变量加入 `test.env`。测试平台
会在发压前分别验证 LLM 和 Embedding；任何一个失败都会阻止依赖真实记忆的场景。

采集 M1-M3 的完整内部阶段耗时时，EchoMem 的被测配置应启用 DEBUG JSON 日志：

```json
{
  "runtime": {"log_level": "DEBUG"},
  "logging": {"level": "debug", "format": "json"}
}
```

profile 中的 `resource_container` 是该 EchoMem 容器的准确名称，并设置
`require_stage_observability: true`。运行器只保存白名单阶段、耗时、队列等待和脱敏后的
trace 引用，不保存请求正文或原始 trace id。无容器本地进程部署
（`resource_container` 为空、`require_4u8g=false`）时此要求自动放宽：结构化
日志窗口跳过、阶段证据仅来自 `/metrics` histogram，M1 资源口径与 M5 相应降级，
观测运行仍正常产出报告。

## 5. 创建唯一的本机 profile

新建 `.local-stress/six-metrics.profile.json`，只放当前这一个 profile。profile
与密钥 env 文件都不得进入 Git。**所有数值（档位、并发目标、时长、租户数等）由
使用者按 `performance/skills/echomem-stress/SKILL.md` 引导逐项确认后填写**，
本 README 不预设任何数值。字段结构与语义如下：

| 字段 | 语义与约束 |
| --- | --- |
| `name` | 唯一本机 profile 名；文件只有一个 profile 时运行器自动选择，无需 `--profile` |
| `base_url` | 被测 EchoMem 的 HTTP 地址（如 `http://127.0.0.1:8010`） |
| `resource_container` | 容器名（如 `echomem-core-1`）。无容器本地进程部署留空 → host-default 降级：M1 资源口径 0/None、结构化日志窗口跳过、M5 不要求 `allow_container_restart` |
| `require_4u8g` | 按实际测试目标设置；Docker 未设上限时 CPU/内存字段可能显示 `0`（宿主机默认资源）。报告保存容器 ID、镜像 ID 与 Docker 资源配置 |
| `tenant_config` / `preflight_config` | tenants.json 与 EchoMem `deploy/single-node/config.json` 的绝对路径 |
| `m1_tenant_levels` / `m1_user_levels` | M1 容量档位，由使用者指定；只加档位不补足独立租户凭据时正式运行直接失败 |
| `required_concurrency` | 需要观测到的同时在途请求数目标，**不等同于配置的用户数**；测试平台不会读取 EchoMem 的 `max_concurrency`、队列容量或 worker 数后主动降载 |
| `required_embedding_model` | 硬性预检条件：只接受真实成功调用该模型名；服务实际使用其他 Embedding 时正式发压前直接停止 |
| `require_stage_observability` | 为 true 时要求 DEBUG JSON 日志与容器以采集白名单阶段事件；host-default 下自动放宽 |
| `m1_duration_s` / `m1_search_rps_per_user` | 由使用者指定 |
| `dau_scenarios` | 三种业务画像（读重/均衡/写重）的 DAU 换算输入 |
| `fault_isolation` / `tenant_observability` / `commit_recovery` | M4/M6/M5 专用段；控制面端点缺失或 token 缺失时对应指标如实降级（BLOCKED），不会阻断运行 |

`seed_search_timeout_s` 控制 M2/M3 等 observation 场景在记忆准备后的单次召回验证
等待时间，默认 60 秒。例如配置 `"seed_search_timeout_s": 120` 可保留超过 60 秒的
慢响应。这不修改正式场景的请求超时、发压强度、服务配置或质量断言，也不覆盖 M1
独立容量扫描。必须使用新的结果目录区分不同配置；旧超时仍保留为失败。每条验证
记录包含实际延迟、等待上限、传输错误类型、合成问题和预期事实。HTTP 200 或更长
等待不代表召回质量通过。

M1 独立命令 `python -m performance.targets.echomem.acceptance.capacity_experiment`
可用 `--search-workers <N>` 显式配置客户端 Search 工作线程数。不要把线程数当作
实际并发；实际在途峰值与每档 `planned/sent/not_sent` 必须一起检查。若出现
`generator_saturated`，说明客户端没有发出该请求，不能归因为 EchoMem 拒绝。提高
客户端工作线程数不改变服务端 worker、模型配额或队列配置，实际数量记录在每档
measurement 的 `pools`。

还必须核对最外层 `recall.max_inflight`，不能只调 `recall.concurrency.*`。
当前已验证的 EchoMem 版本在未配置该字段时默认 16；`ECHOMEM_RECALL_MAX_INFLIGHT`
环境变量优先于 JSON。以目标版本源码和实际日志为准，不能假设所有版本默认值相同。
出现 `RETRIEVAL_BUSY` 时核查 `retrieval_admission_rejected` 中的 `in_flight/max_inflight`。
例如内部阶段均为 128、外层仍为 16 时，实际同时执行 Recall 仍会被 16 限制。
调优实验可显式设置 `recall.max_inflight`，并同时核对 HTTP、租户、模型预算及
供应商限额；不要无条件设置为 0 来关闭保护。此配置通常需要重启生效，须按部署
流程授权执行。配置拒绝边界不能描述为硬件极限。

租户开通（独立凭据，不耗模型额度）：

```bash
.venv/bin/python -m performance.targets.echomem.provision \
  --base-url http://127.0.0.1:8010 \
  --count <使用者确认的租户数> \
  --out .local-stress/tenants.json \
  --env-file .local-stress/test.env
```

`tenants.json` 只保存 `auth_key_env` 名称，真实租户 key 位于 Git 忽略的 `test.env`。
只改档位而没有补足独立租户凭据时，正式运行应直接失败；这能避免把重复 key 误报成
多租户容量。

M3 除等负载场景外还会运行异构租户场景：四个独立租户的 Search 权重为 `8:4:2:1`，
Commit 权重为 `1:2:4:8`。报告逐租户展示计划速率、实际请求数、Search P95/错误/召回
质量与 Commit 完成量，用于验证读多写少、读写均衡、写多读少租户能在同一轮被真实
压测。

## 6. 运行观测套件

**结果目录固定位置（非协商）**：所有压测结果必须写入
`performance/targets/echomem/results/` 之下，一次运行一个子目录（如
`performance/targets/echomem/results/<run-name>/`）。禁止把结果写到仓库根
`results/`、临时目录或其他任意位置。

所有命令均在测试平台仓库根目录执行，统一使用 Python 模块入口（仓库不提供
shell 包装）。实际传给运行器的指标、profile、env 文件与输出目录必须来自
使用者按 skill 流程确认的范围与参数：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE --env-file ENV_FILE \
  --out-dir performance/targets/echomem/results/<run-name> \
  --metrics <使用者确认的指标>
```

profile 文件只有一个 profile 时，脚本会自动选择该 profile，不需要再写 `--profile`。
quick 链路检查（加 `--quick`）使用真实 HTTP、模型、租户，但缩短采样时间，结果
固定视为 `PARTIAL`，只用于确认整条链路能跑通；quick 是使用者的选择，须先确认
再运行。

### 6.1 三个参数的分工（大白话）

`--metrics` / `--scenarios` / `--probes` 回答三个不同的问题，不要混用：

| 参数 | 问的是 | 类比 | 缺省时 |
|---|---|---|---|
| `--metrics` | **要什么结论**（M1 容量 … M6 可观测，选哪些指标出报告） | 体检项目：查哪些科目 | 默认 `M1,M2,M3` |
| `--scenarios` | **打什么负载**（跑哪几种流量场景，为结论产生证据） | 怎么练：跑圈、举铁还是冲刺 | 按所选指标自动推导（自动挡）；M4/M6 的依赖窗口自动补上 |
| `--probes` | **验什么功能**（一次性行为检查：隔离、恢复、边界） | 专项质检：卡尺量、拉力测 | 跑 profile 里配置了的全部探针；指标必需的探针不可被排除 |

一句话：`--metrics` 是"体检报告要哪些科目"，`--scenarios` 是"用哪几种训练量来
制造数据"，`--probes` 是"另外做哪些专项检测"。只给 `--metrics` 就能跑（自动挡）；
给了 `--scenarios`/`--probes` 就是手动挡，按你点的执行（指标必需的探针仍然会跑，
保证结论有证据）。

## 7. 指标组合与续跑

`--metrics` 接受 `M1` 到 `M6` 的任意组合（如 `M1,M2,M3`），CLI 缺省选择
`M1,M2,M3`；实际传给运行器的指标列表必须来自使用者已确认的范围。执行顺序为
`M1 → M2 → M3 → M4 → M5`，M6 从开始到结束持续采样。默认不运行 soak。机器速度、
模型限流和容量边界不同会影响总时长，M1 的逐档容量测试通常最耗时。

`--scenarios` 从 6 个负载场景里选（`--help` 会列出全部）：M2 公平性 2 例
（`m2-fairness-4t` / `m2-fairness-8t`）+ M3 洪泛 4 例（`m3-baseline` /
`m3-flood-uniform` / `m3-flood-single-tenant` / `m3-heterogeneous-tenants`）。
不传时按指标推导（M2 → 公平性 2 例，M3 → 洪泛 4 例；M4 自动补 `m3-baseline`，
M6 自动补 `m3-baseline` + `m3-flood-uniform`）；显式传了 `--scenarios` 就以它为准，
依赖窗口仍自动补齐，例如只跑基线加探针：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE --env-file ENV_FILE \
  --out-dir performance/targets/echomem/results/<run-name> \
  --metrics M3,M4 --scenarios m3-baseline
```

`--probes` 从 14 个探针里选（`--help` 会列出全部），例如只做隔离与断连恢复专项：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE --env-file ENV_FILE \
  --out-dir performance/targets/echomem/results/<run-name> \
  --metrics M3 --probes nxn-isolation,disconnect-recovery
```

单指标或组合示例（参数以使用者确认为准）：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE --env-file ENV_FILE \
  --out-dir performance/targets/echomem/results/<run-name> --metrics M1
```

中断后使用原 profile、原输出目录和 `--resume` 续跑；禁止用新运行覆盖旧结果，
除非使用者显式选择同一 profile 与指标集的 `--resume`，否则使用
`performance/targets/echomem/results/` 下的新子目录：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE --env-file ENV_FILE \
  --out-dir performance/targets/echomem/results/<run-name> --resume
```

## 8. 查看报告

最终给人阅读的主结果始终是本次 `OUTPUT_DIR/report.html`，其中 `OUTPUT_DIR`
位于 `performance/targets/echomem/results/` 之下，例如
`performance/targets/echomem/results/<run-name>/report.html`。

不能只交付 HTML；同目录的结构化分母和逐请求证据必须一起保留：

| 文件 | 内容 |
| --- | --- |
| `report.html` | 六项总体结论、图表、测试方式、失败类型与 EchoMem 模块建议 |
| `summary.json` | 报告使用的结构化汇总和每项状态 |
| `suite.json` | 场景、探针与分母明细 |
| `records.csv` | 每个请求的延迟、状态和结果 |
| `metrics_samples.csv` | 测试期间 CPU、内存及 Prometheus 采样 |
| `execution-manifest.json` | 测试平台 commit、profile、模型预检和执行状态 |

| 状态 | 含义 |
| --- | --- |
| `MEASURED` | 该项要求的场景和分母完整，数据可用于分析 |
| `PARTIAL` | 有真实数据，但场景、重复次数或分母不完整 |
| `BLOCKED` | 配置、模型、租户、受保护接口或容器条件未满足 |
| `EXECUTION_ERROR` | 测试平台或执行过程发生异常 |

不要删除失败样本后重算，也不要只保留 HTML。容量结论必须同时给出最后正常档和首个持续
拥塞档；Provider 失败、EchoMem admission 拒绝、原子引擎质量失败和客户端传输错误会按
不同责任域拆开展示。

## 9. 本机清理与恢复

```bash
cd "$ECHOMEM_DIR/deploy/single-node"
./manage.sh status
./manage.sh smoke
./manage.sh down
```

`down` 不会删除 `deploy/single-node/data/workspace`。复测应使用新的结果目录，不要覆盖旧
目录，否则原始分母和版本证据会丢失。

## 常见阻塞

| 现象 | 处理 |
| --- | --- |
| `resource-container` 阻塞 | 核对 profile 容器名、Docker 是否运行以及当前用户是否可访问 Docker |
| `fault_isolation` 或 `tenant_observability` 阻塞 | 核对 EchoMem 接口、启用开关及 Core/runner 两侧 token 是否一致 |
| 模型预检失败 | 同时检查 LLM 与 Embedding endpoint、模型名、余额、限流和 `api_key_env` |
| Search 200 但无召回 | 检查种子 Commit、自然语言查询命中和返回正文中的预期事实 |
| M2 不能证明公平性 | 确认租户凭证各不相同，不能让多个 tenant 复用同一个 key |
| M5 未执行重启 | 只有 Commit 已返回 202 且仍未完成时才会 kill；确认目标是专用本机容器 |
| 容器 CPU/内存显示 0 | 本机模式下表示 Docker 未设 cgroup 上限，测试使用宿主机默认资源 |

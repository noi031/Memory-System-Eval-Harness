# EchoMem stress testing

`echomem/runner.py` is a real HTTP runner for an EchoMem service. It uses
Python's standard library and does not replace the real model service with a
mock during an official run.

Official runs must configure EchoMem with a real LLM and embedding provider:
DashScope OpenAI-compatible API, `deepseek-v4-flash-0731` for text generation,
and `text-embedding-v3` for embeddings. The API key is supplied through
`DASHSCOPE_API_KEY`; it must not be written into JSON, HTML, CSV, or logs.
For a formal suite, pass the actual EchoMem config explicitly:

```bash
python3 -m stress.echomem.formal_suite \
  --preflight-config "$ECHOMEM_CONFIG" \
  --tenant-config stress/echomem/tenants.server.json \
  --profile report4
```

The default authentication header is `X-API-Key`, matching the current
EchoMem HTTP server. Use `--auth-header Authorization` when the deployment
expects a bearer token, or set `ECHOMEM_AUTH_HEADER`.

```bash
python3 stress/echomem/runner.py \
  --base-url http://127.0.0.1:8010 \
  --auth-key "$ECHOMEM_AUTH_KEY" \
  --scenario all \
  --tenants 1 \
  --sessions-per-tenant 2 \
  --duration-s 300 \
  --search-rps 2 \
  --pid "$(pgrep -f echomem | head -1)" \
  --out-dir results/stress/echomem_$(date +%Y%m%d_%H%M%S)
```

Use repeated `--tenant` only when each label maps to an independently
authenticated EchoMem tenant. With one credential, the fairness result is
`INCONCLUSIVE`, not a pass.

For a formal multi-tenant run, `--tenant-config` is required. The runner
refuses `--tenants > 1` with a shared credential so a labeled single-tenant
run cannot be mistaken for an isolation result. Use
`--allow-shared-identity` only for explicitly exploratory, non-isolation
measurements.

The output directory contains `summary.json`, `report.html`, and CSV files for
commit, search, resource, and `/metrics` samples. Raw Prometheus responses are
also retained in `server_metrics.jsonl`. Transport/startup/authentication
failures are reported as `ENVIRONMENT_ERROR`.

### PR397/PR421 缺口补测

在多个 EchoMem 实例之间切换测试时，限流阶梯必须在目标实例上新建
session，不能复用另一实例的 CSV session ID：

```bash
python3 stress/echomem/limit_failure_sweep.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.json \
  --session-root results/stress \
  --create-sessions \
  --levels 4,16,64,128,256 \
  --out-dir results/stress/limit-sweep-$(date +%Y%m%d_%H%M%S)
```

该探针会在每个 Commit 前写入真实消息，并分别记录 HTTP 状态、transport
超时和降载后的恢复波次；因此空 Session 的 `400` 不会被当作限流证据。
同一 Session 的并发 Commit 可用以下命令检查合并、重复 archive 和终态：

```bash
python3 stress/echomem/concurrent_commit_cases.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.json \
  --concurrency 8 \
  --out results/stress/concurrent-commit.json
```

该测试不会把相同 `archive_id` 自动解释为幂等通过；EchoMem 仍需公开
`operation_id` 或幂等键和消息集合接口，才能完成严格对账。

For a standard stress run, the runner writes `summary.json`, `report.html`,
request CSVs, raw `/metrics`, and the server-observation timeline. It explicitly
distinguishes client-side worker wait from server-side queueing; without server
telemetry it must not be used to claim server-side rate limiting. The standard
run does not add a client-side scheduling policy.

### report(4) A/B/C/D 矩阵

使用 `report4` profile 可以复现 `report(4).html` 的核心设计；使用
`report6` profile 可以执行 report(6) 的 8 租户、12 组 A/B/C/D 方案：

```bash
python3 stress/echomem/formal_suite.py \
  --profile report4 \
  --tenant-config stress/echomem/tenants.server.json \
  --out-dir results/stress/report4_$(date +%Y%m%d_%H%M%S)
```

```bash
python3 -m stress.echomem.formal_suite \
  --profile report6 \
  --tenant-config stress/echomem/tenants.server.example.json \
  --preflight-config "$ECHOMEM_CONFIG" \
  --out-dir results/stress/report6_$(date +%Y%m%d_%H%M%S)
```

### PR397 + PR421 完整测试

需要同时覆盖 PR397/report(6) 的 A/B/C/D 故障发现型矩阵和 PR421 的
饱和、热租户、公平性及验收门禁时，使用 `complete` profile。它执行两套
场景的并集，共 26 个场景；默认重复 3 次，即 78 个独立运行目录。
新增 2/4/8/32 租户容量阶梯，以及 Search/Commit 同时到达的服务端优先级黑盒场景。

```bash
python3 -m stress.echomem.formal_suite \
  --profile complete \
  --preflight-config "$ECHOMEM_CONFIG" \
  --tenant-config stress/echomem/tenants.server.json \
  --fault-plan stress/echomem/fault-plan.example.json \
  --out-dir results/stress/complete_$(date +%Y%m%d_%H%M%S)
```

`complete` 会在 `suite.json` 中记录 PR397 和 PR421 的来源、场景清单和
实际运行数，并生成统一的 `suite.html`、`acceptance.json` 和
`model_analysis_input.json`。`--fault-plan` 只编排部署提供的真实故障
控制命令；没有真实控制接口时，LLM/vector 故障、kill-9/重启恢复和 k6
对账会标记为 `INCONCLUSIVE`，不会伪造为通过；只有真实 HTTP 接口明确
返回 `404` 才标记为 `NOT_IMPLEMENTED`。

report6 固定每租户并发 1/2、每场景 60 秒、每租户 2 个会话且每会话
10 条消息。C 场景按总请求量精确保持 8:1、4:1、1:1；D 场景在
10 秒窗口内提交 32 个 Commit。计时前每个 session 还会写入并完成
确定性质量种子；Search 使用对应 marker 查询，空召回会计为质量失败，
不会再把 HTTP 200 当作检索成功。正式运行必须提供 8 个独立认证 Key。

report6 的结果会同时保留：

- `seed_results.csv`：计时前种子写入、Commit 最终状态和 marker；
- `search_results.csv`：query、expected marker、marker 是否命中和质量状态；
- `commit_results.csv`：所有提交、429 重试、最终状态和请求级时间线；
- `acceptance.json`：质量断言、租户隔离、服务端遥测以及未执行能力的分层判定。

Search 延迟统计只使用质量断言通过的请求；种子失败或 marker 未命中会在
场景和正式套件验收中明确显示为 `FAIL`，不隐藏分母。

该 profile 使用 8 个独立认证租户和每租户并发 1/4/16。A 是纯 Search
基线，B 是纯 Commit，C 覆盖 8:1/4:1/1:1 读写比例，D 在固定冷却窗口后
重复 3 轮 Commit 洪峰。成功请求延迟和错误率分开统计；如果 A 基线错误率
过高，不能继续用它计算压力劣化倍数。

Example formal multi-tenant command:

```bash
python3 stress/echomem/run_matrix.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.json \
  --tenants 4 \
  --duration-s 600 \
  --search-rps 2 \
  --commit-rpm 2 \
  --sessions-per-tenant 4 \
  --messages-per-session 3 \
  --out-dir results/stress/matrix_$(date +%Y%m%d_%H%M%S)
```

Do not use `--allow-shared-identity` for a release decision. It is only
allowed for exploratory performance measurements and makes isolation and
fairness conclusions `INCONCLUSIVE`.

## Real tenant isolation

Changing only `account_id` or a tenant label does not create a real tenant.
For an isolation or fairness conclusion, provide one independent EchoMem
credential per tenant:

```json
{
  "tenants": [
    {"tenant_id": "tenant-a", "user_id": "user-a", "auth_key_env": "STRESS_TENANT_A_KEY"},
    {"tenant_id": "tenant-b", "user_id": "user-b", "auth_key_env": "STRESS_TENANT_B_KEY"}
  ]
}
```

Run with `--tenant-config tenants.json`. The runner executes a marker probe:
the writer tenant must retrieve its own marker and the reader tenant must not
retrieve it. Without this file, multiple labels share one credential and the
tenant-isolation result is `INCONCLUSIVE`.

Start from `echomem/tenants.example.json`, then provide the four keys through
the environment:

```bash
export STRESS_TENANT_A_KEY='...'
export STRESS_TENANT_B_KEY='...'
export STRESS_TENANT_C_KEY='...'
export STRESS_TENANT_D_KEY='...'
python3 stress/echomem/runner.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.json \
  --duration-s 120 \
  --search-rps 2 \
  --sessions-per-tenant 2 \
  --messages-per-session 3 \
  --out-dir results/stress/server-observe-$(date +%Y%m%d_%H%M%S)
```

The report never writes the key itself. It records only the key source name,
tenant id, request order, and timing evidence.

### Commit barrier and Retry-After

For a rate-limit or Commit-storm regression test, use a fixed barrier rather
than relying only on a long fixed-rate stream:

```bash
python3 stress/echomem/runner.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.json \
  --tenants 4 \
  --commit-barrier \
  --commit-barrier-count 160 \
  --commit-tenant-distribution zipf \
  --commit-zipf-exponent 2 \
  --commit-max-attempts 3 \
  --out-dir results/stress/commit-barrier-$(date +%Y%m%d_%H%M%S)
```

The barrier schedules all 160 initial Commit requests at one arrival instant
and distributes them across tenants using the selected distribution. A
`explicit` distribution requires `--commit-tenant-counts` and is useful for
reproducing a fixed hot-tenant mix, such as `200,20,20,20`.
`429` is retried only for the same session, after the server-provided
`Retry-After`; if that header is absent, bounded exponential backoff is used.
Every attempt, retry count, retry wait, final status and request ID is retained
in `commit_results.csv` and `summary.json`. A non-429 failure is not replayed,
so data or service errors cannot be hidden by the retry mechanism.

## Client scheduling

The standard platform does not simulate FIFO, Search priority, dual lanes, or
tenant-fair scheduling. Online users may connect directly to EchoMem without
this test platform, so adding one of those policies would make the load
generator an artificial intermediary and could hide or create contention.

Search and Commit are submitted through separate client worker pools only to
allow concurrent HTTP requests; this is an execution-capacity setting, not a
business scheduling policy. The report records the client worker wait
separately from EchoMem server queue telemetry.

The low-level `--scheduler-policy` options remain only for backward
compatibility with old exploratory runs and unit tests. They are not exposed
by `formal_suite.py`, `run_matrix.py`, the Web entry, or the Feishu entry.
The high-level platform commands intentionally have no client-scheduling
selector or admission-capacity setting.
`--search-rps` is the total Search arrival rate for the whole run, not a
per-tenant rate. For example, with four tenants and a desired 0.5 RPS per
tenant, set `--search-rps 2`; the runner distributes arrivals round-robin and
the report shows the actual per-tenant counts. Set `--commit-rpm` to a positive
value for fixed-rate Commit arrivals per tenant. The runner prepares dedicated sessions before the timed interval and
then submits Commit requests at the configured rate; this is the recommended
mode for sustained load. With `--commit-rpm 0`, it retains the legacy
one-Commit-per-prepared-session mode.
The formal isolation probe writes five distinct random markers per tenant by
default and tests every directed writer/reader pair for every marker. With
four tenants this produces `4 x 4 x 5 = 80` probes. Change the count with
`--isolation-markers-per-tenant`; a release run should not reduce it to one
without documenting why.

For a direct server-observation run:

```bash
python3 stress/echomem/runner.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.json \
  --tenants 4 \
  --no-client-admission \
  --commit-workers 64 \
  --search-workers 64 \
  --duration-s 600 \
  --search-rps 2 \
  --commit-rpm 2 \
  --out-dir results/stress/server-observe-$(date +%Y%m%d_%H%M%S)
```

`--no-client-admission` removes all client-side admission gating. The executor
worker pool can still become a client-side bottleneck, so its queue wait remains
recorded and its worker count must be sized above the expected in-flight load.
Use this mode for service-side scheduling observations.

## Formal release suite

For an online-readiness decision, use `formal_suite.py` instead of a single
short matrix run. It executes these cases:

- `baseline`: one independently authenticated tenant for the reference latency
- `mixed`: four tenants with balanced Search and Commit traffic
- `commit-storm`: four tenants with elevated Commit traffic
- `search-storm`: four tenants with elevated Search traffic
- `soak`: four tenants under a longer steady-state load
- `commit-barrier`: 160 simultaneous Commit arrivals with Zipf-distributed
  tenant load, used for the rev5 S2-style rate-limit regression
- `saturation`: 128 concurrent arrivals for the PR421 entrance saturation gate
- `tenant-skew`: explicit 200/20/20/20 Commit distribution for hot-tenant fairness
- `capacity-2`, `capacity-4`, `capacity-8`, `capacity-16`, `capacity-32`:
  capacity ladder points; each requires at least that many independently
  authenticated tenants
- `search-priority-blackbox`: simultaneous Search traffic and a Commit barrier;
  this observes EchoMem's real HTTP behavior and server telemetry. It does not
  claim strict priority unless the service exposes enough queue/start-time
  evidence to compare Search against a clean baseline.

Each case runs once per repetition with client-side admission disabled. The
default is three repetitions per case. Every run retains `summary.json`,
request CSVs, raw `/metrics`, and its own `report.html`; the suite-level
`suite.html` contains the numeric comparison table.

### 多规格实例对比

测试平台不假定规格对应哪一种容器或资源限制。本轮使用服务器上可用的
`4U8G`、`8U16G` 两档。把每个规格的真实
部署、重启或恢复动作写成 `prepare_command`，平台会在执行同一套真实 HTTP
场景前调用它，并把规格、配置路径、准备日志和完整结果写入 `matrix.json`。
示例计划见 `stress/echomem/instance-profiles.example.json`：

```bash
python3 stress/echomem/instance_profile_matrix.py \
  --plan stress/echomem/instance-profiles.example.json \
  --profile pr421 \
  --scenarios baseline,mixed,commit-storm,search-priority-blackbox,soak \
  --repeats 1 \
  --out-dir results/stress/instance-matrix-$(date +%Y%m%d_%H%M%S)
```

如果 `prepare_command` 没有真正切换实例规格，平台只会如实记录准备命令
及其结果，不能把同一实例的重复运行报告成多规格对比。

容量/饱和测试可以给底层 runner 增加 `--skip-isolation`。这会明确把
`isolation` 写成 `INCONCLUSIVE`，仅用于避免 N×N marker 探针干扰吞吐、
队列和拒绝行为测量；它不能产生多租户隔离通过证据。隔离必须使用独立
认证凭证单独执行。

```bash
python3 stress/echomem/formal_suite.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.server.json \
  --out-dir results/stress/formal_$(date +%Y%m%d_%H%M%S) \
  --repeats 3 \
  --pid "$(pgrep -f echomem | head -1)" \
  --reset-command '/path/to/reset-echomem-test-data.sh'
```

The reset command is optional but strongly recommended. It must restore the
same EchoMem version, configuration, index/data snapshot, and resource
limits before every case; otherwise growing indexes or stale memory can be
confused with a scheduling effect. The suite does not claim that a client-side
policy is EchoMem's internal scheduler. Service-side rate limiting requires
server telemetry such as queue depth, execution start time, HTTP 429, and
`Retry-After`; those values are retained when the service exposes them.

The generated `suite.html` is data-first. It shows numeric distributions for
every scenario in the server-observation mode, including Commit and Search mean/P50/P95/P99/max,
client admission wait, server queue wait, server execution time, per-tenant
counts and quantiles, delayed requests, HTTP 429, and telemetry coverage.
Expand a row to inspect every delayed request and the raw CSV files. Missing
server timestamps are rendered as `-`; they are never replaced with
client-side timing.

The formal suite also writes three machine-readable review artifacts:

- `acceptance.json`: PR421 gate-by-gate status, target, observed value, evidence,
  and reason, plus the structured PR28 review-resolution matrix.
- `model_analysis_input.json`: bounded, secret-free context for an external LLM
  to analyze failures, review gaps, and propose the next diagnostic action.
- `suite.json`: the original run manifest plus the acceptance result.

The acceptance layer distinguishes `PASS`, `FAIL`, `INCONCLUSIVE`, and
`NOT_IMPLEMENTED`. A configured target is not treated as verified merely
because a scenario ran; missing server evidence and unavailable control-plane
operations remain visible. The harness reports `NOT_IMPLEMENTED` only after
the target explicitly returns HTTP 404; its own missing configuration is
`INCONCLUSIVE`.

### PR397 + PR421 缺口总报告

可以将正式验收、PR397 可观测缺口、饱和阶梯和故障套件的结果汇总为一份
HTML。先把各结果 JSON 放入同一目录并使用固定文件名：

```text
pr421_acceptance.json
pr397_observable.json
saturation.json
fault_suite.json
```

然后执行：

```bash
python3 stress/echomem/completeness_report.py \
  --root results/stress/complete-$(date +%Y%m%d_%H%M%S) \
  --out results/stress/complete-report.html
```

报告会逐项保留 `PASS`、`FAIL`、`INCONCLUSIVE` 和
`NOT_IMPLEMENTED`。测试平台没有配置探针不等于 EchoMem 没有实现：
只有真实 HTTP 接口明确返回 404 才使用 `NOT_IMPLEMENTED`。写后读测试会同时检查 `history`、`archive`、
`commit memories` 和 Search：如果数据已经持久化但 Search 未召回，会标记
为检索可见性问题，而不会误报成数据丢失。

### PR397 observable missing cases

The following command runs the PR397 cases that can be observed through the
real EchoMem HTTP API:

```bash
python3 stress/echomem/missing_cases.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.server.json \
  --out-dir results/stress/pr397-missing-$(date +%Y%m%d_%H%M%S)
```

It records write-after-read visibility, the Commit state sequence, cold versus
warm Search latency, and the explicit status of idempotency. Idempotency is
`INCONCLUSIVE` unless EchoMem exposes a documented idempotency key or
operation replay contract; two successful HTTP requests alone are not proof.
Capacity runs may use `runner.py --skip-isolation`, but a tenant config that
reuses one key is recorded as `shared_auth_key` and cannot produce isolation
evidence.

For a service-side scheduling observation, the formal suite already disables
the runner's admission controller:

```bash
python3 stress/echomem/formal_suite.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.server.json \
  --out-dir results/stress/formal_server_observe_$(date +%Y%m%d_%H%M%S) \
  --repeats 3 \
  --commit-workers 64 \
  --search-workers 64
```

The report labels this mode explicitly. It is only a server-side queueing
conclusion when EchoMem provides per-request `received_at`,
`queue_entered_at`, `execution_started_at`, `finished_at`, `queue_depth`, and
`active_workers`. Without those fields, the report still shows client
observations but marks server scheduling evidence as missing.

## PR28 检视意见跟踪

PR28 的逐条验收状态见
[`REV5_REVIEW_GAP_MATRIX.md`](REV5_REVIEW_GAP_MATRIX.md)。当前已落地
Commit barrier、Zipf 分布、429 Retry-After 重试、请求级重试审计和服务端
观测边界；cursor 对账、真实重启恢复、故障注入和 k6 工具链已提供可执行
入口，但必须配置真实的 EchoMem 控制接口、重启拓扑和 cursor API，缺少依赖
时仍会保守标记为 `PARTIAL` 或 `INCONCLUSIVE`；只有接口明确返回 404
才使用 `NOT_IMPLEMENTED`。
`acceptance.json` 和 `suite.html` 还会逐条列出
`RESOLVED/PARTIAL/NOT_IMPLEMENTED`，作为检视意见是否闭环的机器可读证据。

PR421 的验收目标会写入 formal suite 的 `suite.json`，并额外生成
`acceptance.json` 和 `model_analysis_input.json`，包括 Search P95
隔离度 `≤1.20x`、Jain 公平指数 `≥0.90`、已接受 Commit 恢复率 `100%`、
拒绝响应的 `Retry-After/reason_code`、B7 车道四元组和 fan-out 指标覆盖，
以及 128 并发饱和场景的拒绝率和返回时延门槛。报告会区分“指标未暴露”
与“指标暴露但未达标”，前者只能是 `INCONCLUSIVE`。

这些阈值的样本口径和合理性复核见
[`PR421_ACCEPTANCE_REVIEW.md`](PR421_ACCEPTANCE_REVIEW.md)。尤其要把成功请求
延迟与超时率分开，把 202 后的 Commit 最终 drain 与轮询窗口分开，并将
“目标已配置”和“目标已验证”分开显示。

## 故障发现型压测补充

PR28 的基础性能矩阵之外，正式故障发现建议按 S1 至 S4 分层执行。S1
优先验证写后读一致性、Commit 幂等性、状态机和队列背压；S2 验证连续
Commit 洪峰、Search 优先级和租户抗饥饿；S3 验证断连回收、重启恢复和长稳态；
S4 才进行向量库、LLM、worker 或网络故障注入。

详细的流程、输出字段和判定门槛见
[`FAULT_DISCOVERY_PLAN.md`](FAULT_DISCOVERY_PLAN.md)。其中：

- commit 完成后仍不可检索到 marker，直接判定 FAIL；
- 轮询超时不能当作成功，必须经过 drain 或标记为 INCONCLUSIVE；
- 幂等、状态机和恢复测试没有服务端接口支持时，标记
  `INCONCLUSIVE`，不使用 mock 结果代替；
- 高并发场景同时报告成功请求延迟和超时率，避免超时样本污染 P95。

### k6、故障注入与恢复

PR28 现在提供 `stress/k6/echomem_stress.js`，它只发送真实 EchoMem
HTTP 请求，并通过 `handleSummary` 输出 k6 原始结果。使用
`stress/echomem/k6_reconcile.py` 可以把 k6 计数和 Python runner 的
逐请求 CSV 对账；缺少任一证据或发现 `mock_model=true` 时不会判通过。

真实故障由 `stress/echomem/fault_suite.py` 编排，计划文件见
`stress/echomem/fault-plan.example.json`。每个故障必须配置一个真实的
HTTP 控制接口、shell 控制命令或 Docker 容器；没有控制点时结果为
`INCONCLUSIVE`，不会伪造 500、超时或熔断；只有控制 HTTP 接口明确返回
404 才标记为 `NOT_IMPLEMENTED`。

kill-9 恢复使用 `recovery.py`，支持真实 PID 或容器，记录杀进程前健康、
恢复轮询时间线和恢复耗时。cursor 对账使用
`cursor_reconcile.py --cursor-url-template`，按已完成 Commit 的
`session_id`、`archive_id` 和 `message_ids` 对照服务端真实 message-set。
默认通过 EchoMem 已有的
`/fs/read?uri=echo://sessions/{session_id}/current/commit_cursor.json`
读取游标；无法访问或无法解析时保持 `INCONCLUSIVE`。

将故障和恢复纳入正式验收时显式传入计划，默认不会主动执行破坏性操作：

```bash
python3 stress/echomem/formal_suite.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.server.json \
  --fault-plan stress/echomem/fault-plan.example.json \
  --out-dir results/stress/formal_fault_$(date +%Y%m%d_%H%M%S)
```

已有 k6 结果可通过 `--k6-summary` 和 `--k6-runner-dir` 纳入同一份
`suite.json` / `acceptance.json`。

Commit 进行中 kill-9 恢复可用以下探针执行。它会先写入真实消息，再提交
真实 Commit，在 `pending` 窗口杀掉指定容器并启动同一容器，随后轮询
Commit 终态并保存 history、Commit memories 和恢复时间线：

```bash
python3 stress/echomem/commit_recovery_probe.py \
  --base-url http://127.0.0.1:8010 \
  --container echomem \
  --tenant-config stress/echomem/tenants.server.json \
  --tenant stress-a \
  --messages 12 \
  --content-chars 2500 \
  --kill-delay-s 0.2 \
  --out results/stress/commit-recovery-$(date +%Y%m%d_%H%M%S).json
```

该探针不会把“服务恢复健康”直接当作“Commit 正确恢复”：如果没有
cursor/message-set 导出，报告仍会保留为部分证据，幂等和精确 replay
需要 EchoMem 提供对应接口后才能闭环。

示例：

```bash
python3 stress/echomem/fault_suite.py \
  --plan stress/echomem/fault-plan.example.json \
  --out-dir results/stress/fault-suite-$(date +%Y%m%d_%H%M%S) \
  --commit-csv results/stress/run-01/commit_results.csv \
  --auth-key "$ECHOMEM_AUTH_KEY"
```

## Docker isolation

The runner can be executed as a disposable container so each test gets a
clean process, filesystem, and output directory. `network_mode: host` is used
on Linux because EchoMem is commonly bound to `127.0.0.1:8010` on the server;
the runner container still does not share EchoMem's filesystem or Python
environment. The compose service uses the host PID namespace only so the
runner can sample the target process's RSS/CPU when `--pid` is supplied.

```bash
cd /path/to/Memory-System-Eval-Harness
export ECHOMEM_BASE_URL=http://127.0.0.1:8010
export ECHOMEM_AUTH_KEY='set-on-server'
export STRESS_DURATION_S=300
export STRESS_OUTPUT_DIR=/var/lib/echomem-stress/results
RUN_ID=$(date +%Y%m%d_%H%M%S) ./stress/deploy_stress.sh
```

Each invocation builds the runner image, starts one disposable container,
writes results to `STRESS_OUTPUT_DIR/echomem_<RUN_ID>`, and removes the
container after completion. Do not put API keys in compose files or commit
them to git.

### Unified capability probe and request-level k6 reconciliation

Run the capability probe against the target EchoMem instance before gap tests.
All requests use real HTTP. An unconfigured capability is `INCONCLUSIVE`;
an explicit HTTP 404 is `NOT_IMPLEMENTED`; an unreachable target is
`INCONCLUSIVE`.

### 黑盒接入边界与归属

测试平台不修改 EchoMem 代码，只通过公开 HTTP 接口取证。当前
`EchoMem develop` 可直接使用的只读证据链为：

- `GET /api/sessions/{session_id}/history`：核对会话累计消息是否包含已提交消息；
- `GET /api/sessions/{session_id}/archives/{archive_id}`：核对单个 Commit
  归档及其中的消息；
- `GET /api/sessions/{session_id}/commits/{archive_id}`：核对 Commit 状态、
  阶段、错误和终态；
- `GET /api/sessions/{session_id}/commits/{archive_id}/memories`：核对该
  Commit 产出的记忆摘要；
- `GET /fs/read?uri=echo://sessions/{session_id}/current/commit_cursor.json`：
  读取持久化 Commit cursor。

因此，消息丢失、Commit 状态和 kill-9 后的最终收敛属于测试平台可以黑盒
验证的内容；普通 Session Commit 的幂等 replay 次数，以及 Memory Garden
的幂等/版本冲突，必须分别使用其公开契约验证。没有拿到外部证据时标记为
`INCONCLUSIVE`，不会写成 EchoMem 的 `NOT_IMPLEMENTED`。只有真实请求得到
明确 `404`，才可以下“该接口未实现”的结论。

```bash
python3 stress/echomem/capability_probe.py \
  --base-url http://127.0.0.1:8010 \
  --auth-key "$ECHOMEM_AUTH_KEY" \
  --cursor-path "/api/sessions/{session}/message-set" \
  --operation-path "/api/operations/status" \
  --conflict-path "/api/sessions/{session}/conflicts" \
  --ttl-path "/api/cache/status" \
  --engine-path "/api/engines/status" \
  --fault-path "/admin/fault/status" \
  --out results/stress/capability-probe.json
```

The paths above are deployment examples and must match real EchoMem contracts.
Do not add fake test-platform endpoints just to make the probe pass.
`completeness_report.py` consumes `capability-probe.json` and attaches the
observed status to the corresponding PR397/PR421 gaps.

For request-level k6 reconciliation, emit k6's JSON point stream in addition
to the summary:

```bash
k6 run --out json=results/k6-request-stream.json \
  stress/k6/echomem_stress.js
python3 stress/echomem/k6_reconcile.py \
  --k6-summary results/k6-summary.json \
  --runner-dir results/stress/run-01 \
  --k6-request-stream results/k6-request-stream.json \
  --out results/stress/run-01/k6-reconciliation.json
```

Search and Commit requests carry stable `X-Request-ID` values and k6 tags.
Without the request stream, the reconciler only performs aggregate-count
comparison and keeps the result `INCONCLUSIVE`.

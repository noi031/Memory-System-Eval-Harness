# EchoMem stress testing

`echomem/runner.py` is a real HTTP runner for an EchoMem service. It uses
Python's standard library and does not replace the real model service with a
mock during an official run.

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

For a standard stress run, the runner writes `summary.json`, `report.html`,
request CSVs, raw `/metrics`, and the server-observation timeline. It explicitly
distinguishes client-side worker wait from server-side queueing; without server
telemetry it must not be used to claim server-side rate limiting. The standard
run does not add a client-side scheduling policy.

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
- `capacity-16`: optional 16-tenant capacity point; select it explicitly and
  provide at least 16 independently authenticated tenants

Each case runs once per repetition with client-side admission disabled. The
default is three repetitions per case. Every run retains `summary.json`,
request CSVs, raw `/metrics`, and its own `report.html`; the suite-level
`suite.html` contains the numeric comparison table.

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
  and reason.
- `model_analysis_input.json`: bounded, secret-free context for an external LLM
  to analyze failures and propose the next diagnostic action.
- `suite.json`: the original run manifest plus the acceptance result.

The acceptance layer distinguishes `PASS`, `FAIL`, `INCONCLUSIVE`, and
`NOT_IMPLEMENTED`. A configured target is not treated as verified merely
because a scenario ran; missing server evidence and unavailable control-plane
operations remain visible.

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
观测边界；cursor 对账、真实重启恢复、故障注入、容量阶梯和 k6 工具链仍需
后续实现，不应在报告中标记为已通过。

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
  `NOT_IMPLEMENTED`，不使用 mock 结果代替；
- 高并发场景同时报告成功请求延迟和超时率，避免超时样本污染 P95。

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

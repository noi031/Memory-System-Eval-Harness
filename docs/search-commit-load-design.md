# Search 与 Commit 快速压测方案

## 1. 目标

本方案用于快速验证 EchoMem 的真实写入和检索路径，不依赖完整 LoCoMo 数据集。默认使用少量结构化短文本生成记忆，再用固定语义问题检索这些记忆。

它回答两个问题：

1. `Commit` 返回 `completed` 后，写入内容是否真的能被 `Search` 找到；
2. 在单租户、多租户、读写混合和 Commit 洪峰下，Search 是否稳定、延迟是否可接受。

## 2. 两类 Search 流量

### 2.1 recall-only：纯记忆召回

每个租户先写入一组短事实。例如：

```text
事实编号 PERFANCHOR-0-0-0：林晓负责北极星知识库迁移，计划在周三下午于杭州研发中心完成方案评审。
```

写入流程必须完整执行：

```text
open session -> add messages -> commit -> poll commit_status
```

收到 HTTP `202` 只表示异步任务已接受；轮询得到 `completed` 后，压测平台还会在正式窗口前做一次真实 Search 验证。只有预验证命中的 query 才进入 recall 流量池。

正式 Search 使用语义问题，而不是只搜索唯一编号，例如：

```text
林晓在哪里负责什么项目？
```

每个问题绑定预期事实编号。正式请求返回后，平台检查返回记忆内容中是否包含该编号：

- 命中预期事实：该题召回正确；
- 返回空结果：召回失败；
- 返回其他记忆但不含预期事实：召回错误；
- 服务端明确标记 `degraded`：单独记录为降级/容量证据，不伪装成召回正确。

输出至少包括：召回准确率、逐题结果、命中条数、P50/P95/P99、错误数、超时数和降级数。

### 2.2 mixed：线上混合流量

混合流量包含：

- `recall`：已经预注入并验证过的语义召回问题；
- `no_recall`：预期不命中既有记忆的日常问题，例如“帮我查一下工单状态”；
- 后续可扩展其他 query 类型。

默认比例为 recall 70%、no-recall 30%。两类流量分别统计：

- recall：召回准确率、空结果、错误、P50/P95/P99；
- no-recall：HTTP 成功率、空结果率、错误、P50/P95/P99；空结果本身不算错误；
- overall：总吞吐、总延迟、429、降级和资源竞争。

## 3. Commit 测试方案

### 3.1 单次写入

每次 Commit 都记录四个阶段：

1. `open`：创建 session；
2. `add`：按顺序写入 user/assistant 消息；
3. `commit_submit`：提交归档，`202` 只记为 accepted；
4. `commit_done`：轮询到 `completed` 才记为完成。

每条消息记录 server message id、内容 hash、顺序号和事务 id。写入完成后，可用 history、archive、cursor 和 Search probe 做对账。

### 3.2 并发写入

多租户测试时，每个租户使用独立凭证和独立 session。可使用固定速率，例如每租户每分钟 2 次 Commit；公平性测试应使用固定速率窗口，不使用一次性 barrier 结果代替稳态结果。

Commit 吞吐按“正式窗口内最终 completed 的数量 / 窗口分钟数”计算，不把 202 数量当作完成吞吐。

## 4. 推荐命令

纯召回质量和延迟：

```bash
python3 performance/run_stress.py \
  --echomem-url http://127.0.0.1:8010 \
  --auth-mode provision --tenants 1 \
  --seed-source synthetic \
  --search-query-profile recall-only \
  --seed-recall-probe-limit 20 \
  --scenarios A --concurrency-steps 1,4,16 \
  --duration-s 60
```

模拟线上混合流量：

```bash
python3 performance/run_stress.py \
  --echomem-url http://127.0.0.1:8010 \
  --auth-mode provision --tenants 4 \
  --seed-source synthetic \
  --search-query-profile mixed --search-recall-ratio 0.7 \
  --mode fixed-rps --per-tenant-rps 2 \
  --scenarios A,C,K --duration-s 60
```

如果服务使用预置身份，使用 `--tenant-config` 提供每个租户的独立 key；不要用同一个 key 假装多个租户。

## 5. 报告口径

报告会同时保留 `summary.json`、`requests.csv`、`metrics_samples.csv` 和 `report.html`。`requests.csv` 中每条 Search 记录包含：

- `query_kind`：`recall`、`no_recall` 或 `fallback`；
- `expected_terms`：该 recall query 期望命中的事实标记；
- `recall_matched`：是否命中预期事实；
- `hit_count`、`quality_ok`、`degraded` 和客户端延迟。

因此可以区分：接口很快但没有召回、召回了错误记忆、正常 no-recall 空结果，以及服务端过载降级。

## 6. 与 LoCoMo 的关系

合成短文本方案用于快速回归、并发和故障定位，优点是耗时短、事实和预期答案可控。LoCoMo/`conv-30` 仍适合作为完整质量评测，两者不能混为同一个指标：

- synthetic：快速验证服务路径和可控召回正确性；
- LoCoMo：验证真实多轮对话上的数据集准确率。

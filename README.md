# Memory-System-Eval-Harness

记忆系统评测框架。全 CLI，无网页 UI。直接通过 Python 脚本完成数据集加载、
记忆注入、Agent 问答、Judge 评分和结果报告。

## 设计目标

### 1. 支撑业界所有 agent 的评测

框架支撑业界所有 agent 的评测：被测 agent 通过统一插件协议接入，同一套评测流程
可在不同 agent 与记忆后端上跑出可比结果，确保结果可复现、可审计。

- **AgentPlugin 协议**：所有被测 agent 实现统一接口（`setup -> inject_memories ->
  create_session -> send_message -> getlog`），评测流程只调用接口，不接触 agent
  特定的 HTTP API。新增 agent 只需创建插件目录，无需改动框架。
- **双记忆后端**：`echomem` 和 `openviking` 两个后端实现同一 `MemoryClient`
  协议，通过 `--memory-backend` 切换，保证同一套评测可以在不同后端上跑出可比
  结果。
- **LLM Judge + provenance**：LoCoMo / LongMemEval 使用 LLM Judge 评分，
  HotpotQA 使用官方 F1/EM 指标。每次运行产出 `summary.json`、`config.json`、
  `memory_provenance.json` 和逐题 `agent_traces/*.json`，记录数据集 SHA-256、
  身份、prompt 来源和工具调用链，确保结果可复现、可审计。

### 2. 支撑内部需求

支撑压测、精度测试、定位算法改进点等内部场景。

- **动态评测**：`generate` 模式由 LLM 生成场景和提问，端到端走 EchoAgent 完整
  管线（含 prefill / TTFT）；`replay` 模式回放数据集对话，测试跨 session 召回。
- **多维质量评分**：动态评测通过 YAML 配置定义 10 个评分维度（任务完成度、
  事实覆盖、信息准确性等，满分 100），由 LLM 逐轮打分并输出诊断。
- **诊断与定位**：LoCoMo 产出 `diagnosis.json`、`retrieval_traces.jsonl` 和
  `retrieval_coverage`，标注失败题、可重试题和检索覆盖缺口。`blackbox.py` 和
  `compare.py` 支持黑盒指标导出和两次运行对比。
- **断点续跑**：QA 和 Judge 均支持 `--resume-qa` / `--resume-judge`，健康行不
  重复调用模型；`--checkpoint-interval` 定期落盘部分结果。

### 3. 简单易用 / AI 入口

直接 Python 调用，CLI 参数即配置，AI 友好。

- **直接启动**：`python benchmarks/<name>/run_eval.py` 或
  `python dynamic/run_eval.py`，一条命令完成全流程，无需额外包装层。
- **CLI 参数驱动**：所有连接地址、模型配置、记忆后端、插件选择通过 CLI 参数
 传入，可写在 `.bat` / `.sh` 脚本中固化。环境变量作为默认值，CLI 参数覆盖。
- **预检**：评测启动时自动验证数据集、记忆后端连通性和模型配置，通过后才进入
  正式评测流程。

正式 EchoMem 压测还支持 `--preflight-config`，会在第一条压测请求前拒绝
`fake-llm` / `fake-embedding`，并检查真实模型的 Endpoint、模型名和 API Key
环境变量。默认真实模型为 DashScope 的 `deepseek-v4-flash-0731`；
Embedding 使用 `text-embedding-v3`。

### 4. 生产一致

确保评测结果与生产环境完全一致。

- **真实记忆注入**：评测通过 `inject_memories()` 将数据集对话写入真实 EchoMem
  或 OpenViking 后端（`open_session -> add_message -> commit -> poll`），不使用
  mock 或旁路。
- **身份隔离**：每次评测新开独立 tenant / user / agent 身份，`--resume-qa` 时
  复用原有身份。身份信息（account / user_id / auth_key）记录在 resume manifest
  中，auth key 仅掩码保存。
- **数据完整性校验**：LoCoMo 在 QA 前校验数据集 SHA-256 和实际 session
  manifest，session 数量不匹配时拒绝运行，防止复用 tenant 被污染。
- **生产管线**：动态评测的 QA 阶段走 EchoAgent 完整 HTTP 管线，含 prefill /
  typing simulation / TTFT 采集，与线上行为一致。

## 目录结构

```
plugins/                     # Agent 插件 (AgentPlugin 协议)
  base.py                    #   AgentPlugin ABC + AgentResponse / TypingResult
  registry.py                #   按名动态加载，无需手动注册
  bare_llm/                  #   纯 LLM 基线 (无记忆检索)
  echo_agent/                #   EchoAgent + EchoMem 完整管线 (动态评测默认)
  vikingbot/                 #   VikingBot 工具调用 agent (LoCoMo 默认)
  echomem_mcp/               #   LLM 通过 EchoMem MCP 工具检索记忆
  openviking_mcp/            #   LLM 通过 MemoryClient 工具检索记忆
backends/                    # 记忆后端客户端
  memory_types.py            #   MemoryClient 协议 + BaseHTTPMemoryClient + NullMemoryClient
  memory_args.py             #   add_memory_backend_args() -- 后端连接 CLI 参数
  echomem/                   #   EchoMemClient (端口 8010)
  openviking/                #   OpenVikingClient (端口 19080)
benchmarks/                  # 静态数据集评测
  locomo/                    #   LoCoMo: LLM Judge (CORRECT/WRONG)
    run_eval.py              #     入口脚本
    dataset.py               #     数据集加载与解析
    import_memory.py         #     记忆导入
    qa.py                    #     QA 任务构建与执行
    judge.py                 #     LLM Judge
    reporting.py             #     结果汇总
    data/                    #     内置 locomo10.json
    results/                 #     运行结果
  hotpotqa/                  #   HotpotQA: F1/EM 官方指标
  longmemeval/               #   LongMemEval: LLM yes/no accuracy
  doc/                       #   benchmark 通用文档
dynamic/                     # 动态评测 (generate / replay)
  run_eval.py                #   入口脚本
  workflows.py               #   generate / replay 工作流
  simulator.py               #   场景与查询生成
  metrics.py                 #   动态指标和多维质量评估
  artifacts.py               #   JSON/CSV/报告输出
  model_client.py            #   动态 LLM 客户端
  prompt_config.py           #   prompt 配置加载
  configs/                   #   evaluator / user_simulator YAML 配置
  results/                   #   运行结果
performance/                 # 性能压测（多租户并发读写、注入/检索延迟、CPU/RSS）
  run_stress.py              #   入口脚本
  prepare.py                 #   租户准备 + 种子注入 + query 池
  loadgen.py                 #   读写负载注入器 + 逐请求埋点
  monitor.py                 #   /metrics 周期采样 + Prometheus 文本解析
  scenarios.py               #   场景矩阵（A 纯读 / B 纯写 / C 混合 / D 洪峰）
  metrics_calc.py            #   统计纯函数
  report.py                  #   产物与自包含 HTML 报告
  acceptance.py              #   PR421 验收门禁求值器（纯函数，消费已落盘制品）
  formal_suite.py            #   正式多租户验收套件编排（子进程跑 run_stress）
  formal_data_report.py      #   套件数据报告（suite.json → suite.html）
  probes/                    #   故障/恢复/限流/对账探针（独立 CLI，真实 HTTP）
  tenants.example.json       #   租户凭据示例
  instance-profiles.example.json  # 机器规格 profile 示例
  results/                   #   运行结果

正式套件的 barrier 场景会在正式提交屏障前只执行少量 seed warm-up；
屏障本身会按场景配置单独准备精确数量的未提交 session。不要把
`sessions_per_tenant` 配成 barrier 提交总数，否则真实模型 seed 会占满
case timeout，导致正式 barrier 尚未开始就生成 `NO_SUMMARY`。
shared/                      # 共享基础设施
  eval_base.py               #   EvalConfig / EvalRun / CLI arg helpers
  llm_client.py              #   LLM 客户端 (OpenAI 兼容, urllib)
  dataset_io.py              #   通用数据集路径解析与下载
  runtime_config.py          #   环境变量映射 + 预检
  recovery.py                #   QA CSV 健康判定与恢复
  qa.py                      #   通用 QA 数据结构
  csv_io.py                  #   CSV 读写工具
  import_guard.py            #   导入完整性校验
  benchmark_qa.py            #   benchmark QA 共享逻辑
scripts/                     # 辅助工具
  backend_doctor.py          #   记忆客户端健康检查
  validate_evidence.py       #   QA 检索证据格式检查
```

正式数据集的加载、Judge、指标、重试和报告归属 `benchmarks/<dataset>/`。评测
针对 agent 插件而非记忆后端；记忆注入通过 `AgentPlugin.inject_memories()` 统一
完成，评测平台不直接感知记忆后端。

## 核心架构

### 插件生命周期

```
setup(config)
  -> inject_memories(memories, backend=...)
  -> (create_session -> [simulate_typing] -> send_message)*
  -> getlog
  -> teardown
```

评测流程只调用 `AgentPlugin` 接口方法。`setup` 初始化客户端和记忆后端；
`inject_memories` 将数据集对话写入后端；QA 阶段逐题 `create_session` ->
`send_message`（可选 `simulate_typing` 触发 prefill）；`getlog` 收集后端日志。

### Benchmark 三阶段流程

```
导入记忆 (inject_memories) -> 逐题 QA (仅检索不写入) -> Judge / Evaluate
```

- **导入**：将数据集 conversation 按 session 分批写入记忆后端，commit + poll
  直到抽取完成。LoCoMo 校验数据集 SHA-256 和 session manifest。
- **QA**：并发（`--concurrency`）逐题检索记忆 -> 构建 prompt -> LLM 回答。
  检索阶段不写入记忆。支持 `--resume-qa` 断点续跑。
- **评测**：LoCoMo / LongMemEval 使用 LLM Judge；HotpotQA 使用官方 F1/EM。
  产出 `summary.json`、`qa_results.csv`、`judge_results.csv`、`agent_traces/`。

### 动态评测双模式

```
generate: LLM 生成场景 -> 注入 EchoMem -> 逐轮 QA (端到端 EchoAgent 管线)
replay:   回放数据集对话 -> 注入 EchoMem -> 新会话 QA (跨 session 召回)
```

两种模式的注入阶段直连 EchoMem，不经 EchoAgent；QA 阶段走 EchoAgent 完整
管线（含 prefill / TTFT）。质量评分由 YAML 配置驱动，10 个维度满分 100。

## 快速开始

### 前置条件

- Python 3.10+
- 依赖安装：`pip install -r requirements.txt`（仅需 `tqdm` 和 `PyYAML`）
- 对应的后端服务已启动（见下表）

### 服务启动

评测前需启动对应的后端服务。以下为各服务端口说明：

| 服务 | 端口 | 用途 | 启动方式 |
|---|---|---|---|
| EchoMem | 8010 (HTTP) / 8011 (WS) | 记忆后端 (echomem) | `echomem server --host 127.0.0.1 --port 8010 --workspace <workspace>` |
| OpenViking | 19080 | 记忆后端 (openviking) | `openviking-server --config <config>` |
| EchoAgent Backend | 31020 | 动态评测 agent 后端 | `node dist/src/main.js`（EchoAgent 仓库） |
| EchoAgent Memory Engine | 31030 | EchoAgent 记忆引擎插件 | 随 EchoAgent Backend 启动 |

Benchmark 评测只需启动记忆后端（EchoMem 或 OpenViking）。动态评测还需额外
启动 EchoAgent Backend（含 Memory Engine）。

### 环境变量（可选）

CLI 参数可直接传入，也可通过环境变量设默认值：

| 变量 | 说明 |
|---|---|
| `ECHOMEM_BASE_URL` | EchoMem HTTP 地址，默认 `http://127.0.0.1:8010` |
| `ECHOMEM_ACCOUNT` / `ECHOMEM_USER_ID` / `ECHOMEM_AGENT_ID` | 记忆后端身份 |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | 回答模型配置 |
| `JUDGE_MODEL` / `JUDGE_TOKEN` / `JUDGE_BASE_URL` | Judge 模型（默认同回答模型） |
| `HOTPOTQA_DATASET` / `LONGMEMEVAL_DATASET` | 数据集路径（LoCoMo 已内置） |

### 预检

评测启动时自动执行预检：加载数据集验证非空、调用 `memory_client.health()`
检查记忆后端连通性。通过后进入正式评测流程。

## 运行评测

### Benchmark 评测

直接调用 `benchmarks/<name>/run_eval.py`，通过 `--agent-plugin` 选择被测 agent，
通过 `--memory-backend` 选择记忆后端。

#### LoCoMo + echomem_mcp（EchoMem 后端）

<div style="color: red;">

无工具调用时，测试平台仍通过 EchoMem MCP 执行每题的初始 `memory_query`：

<pre style="color: red;"><code class="language-bash">./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8010 \
  --mcp-url http://127.0.0.1:8001 \
  --sample conv-30 \
  --no-tool-calling \
  --mcp-read-mode disabled \
  --concurrency 4 \
  --judge-concurrency 4 \
  --top-k 25 \
  --memory-budget-chars 8000 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --llm-temperature 0.7 \
  --question-timeout-s 600 \
  --llm-timeout-s 600 \
  --llm-retries 3</code></pre>

允许模型通过 MCP 调用工具，但禁止读取 `messages.jsonl`：

<pre style="color: red;"><code class="language-bash">./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8010 \
  --mcp-url http://127.0.0.1:8001 \
  --sample conv-30 \
  --tool-calling \
  --mcp-read-mode disabled \
  --concurrency 4 \
  --judge-concurrency 4 \
  --top-k 25 \
  --memory-budget-chars 8000 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --llm-temperature 0.7 \
  --question-timeout-s 600 \
  --llm-timeout-s 600 \
  --llm-retries 3</code></pre>

允许模型通过 MCP 调用工具，并允许读取 `messages.jsonl`：

<pre style="color: red;"><code class="language-bash">./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8010 \
  --mcp-url http://127.0.0.1:8001 \
  --sample conv-30 \
  --tool-calling \
  --mcp-read-mode allow \
  --concurrency 4 \
  --judge-concurrency 4 \
  --top-k 25 \
  --memory-budget-chars 8000 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --llm-temperature 0.7 \
  --question-timeout-s 600 \
  --llm-timeout-s 600 \
  --llm-retries 3</code></pre>

</div>

#### LoCoMo + vikingbot（OpenViking 后端）

```bash
python benchmarks/locomo/run_eval.py \
  --agent-plugin vikingbot \
  --memory-backend openviking \
  --echomem-url http://127.0.0.1:19080 \
  --workspace D:/.openviking/data \
  --sample conv-30 \
  --questions 0 \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash-0731 \
  --llm-api-key YOUR_KEY \
  --commit-timeout-s 0 \
  --question-timeout-s 0 \
  --llm-timeout-s 600
```

#### 断点续跑

```bash
./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8010 \
  --mcp-url http://127.0.0.1:8001 \
  --sample conv-30 \
  --no-tool-calling \
  --resume-qa benchmarks/locomo/results/20260803_143943_618591 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --question-timeout-s 600 \
  --llm-timeout-s 600 \
  --llm-retries 3
```

#### 其他 benchmark

```bash
# HotpotQA
python benchmarks/hotpotqa/run_eval.py \
  --agent-plugin bare_llm \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash-0731 \
  --llm-api-key YOUR_KEY \
  --questions 10

# LongMemEval
python benchmarks/longmemeval/run_eval.py \
  --agent-plugin bare_llm \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash-0731 \
  --llm-api-key YOUR_KEY \
  --questions 10
```

| Benchmark | 默认插件 | 评测方式 | 数据集 |
|---|---|---|---|
| `locomo` | `vikingbot` | LLM Judge (CORRECT/WRONG) | 内置 `locomo10.json` |
| `hotpotqa` | `bare_llm` | F1/EM 官方指标 | 需设置 `HOTPOTQA_DATASET` |
| `longmemeval` | `bare_llm` | LLM yes/no accuracy | 需设置 `LONGMEMEVAL_DATASET` |

结果写入 `benchmarks/<name>/results/<timestamp>/`，主要文件：`qa_results.csv`、
`judge_results.csv`、`summary.json`、`config.json`、`agent_traces/`、
`backend_logs.json`。

### 动态评测

直接调用 `dynamic/run_eval.py`。需先启动 EchoAgent Backend（端口 31020）和
EchoMem（端口 8010）。

#### Generate 模式

LLM 生成场景和提问，端到端走 EchoAgent 完整管线：

```bash
python dynamic/run_eval.py \
  --echoagent-url http://127.0.0.1:31020 \
  --memory-engine-endpoint http://127.0.0.1:31030 \
  --echomem-url http://127.0.0.1:8010 \
  --username test_user \
  --password YOUR_PASSWORD \
  --num-memories 5 \
  --num-queries 5 \
  --new-session-ratio 0.3 \
  --typing-speed-ms 2 \
  --scenario-model deepseek-v4-flash-0731 \
  --scenario-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --scenario-api-key YOUR_KEY \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash-0731 \
  --llm-api-key YOUR_KEY
```

#### Replay 模式

回放已有数据集对话，测试跨 session 召回：

```bash
python dynamic/run_eval.py \
  --echoagent-url http://127.0.0.1:31020 \
  --memory-engine-endpoint http://127.0.0.1:31030 \
  --echomem-url http://127.0.0.1:8010 \
  --username test_user \
  --password YOUR_PASSWORD \
  --dataset dynamic/results/20260728_175544/dataset.json \
  --new-session-ratio 0.3 \
  --typing-speed-ms 2 \
  --scenario-model deepseek-v4-flash-0731 \
  --scenario-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --scenario-api-key YOUR_KEY \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash-0731 \
  --llm-api-key YOUR_KEY
```

结果写入 `dynamic/results/<timestamp>/`，主要文件：`dataset.json`、
`dynamic_results.csv`、`summary.json`、`quality_report.json`。

#### 双后端对比（EchoMem vs OpenViking）

同 agent 隔离口径下对比两个记忆后端：先 `generate` 一次产出场景
`dataset.json`，再用 `replay` 把**同一份**数据集对两个后端各回放一遍
（vikingbot 插件通过 `--memory-backend` 切换后端），最后生成自包含 HTML
图表报告：

```bash
# 1) generate 一次：LLM 模拟用户生成背景记忆 + 查询
python dynamic/run_eval.py --agent-plugin vikingbot --memory-backend echomem \
  --echomem-url http://127.0.0.1:8010 --num-memories 20 --num-queries 50 \
  --scenario-base-url ... --scenario-model ... --scenario-api-key ... \
  --llm-base-url ... --llm-model ... --llm-api-key ... \
  --out-dir dynamic/results/formal_gen

# 2) replay 同一份 dataset.json 到 EchoMem
python dynamic/run_eval.py --agent-plugin vikingbot --memory-backend echomem \
  --echomem-url http://127.0.0.1:8010 --dataset <dataset.json> \
  --llm-base-url ... --llm-model ... --llm-api-key ... --out-dir dynamic/results/formal_em

# 3) replay 同一份 dataset.json 到 OpenViking
python dynamic/run_eval.py --agent-plugin vikingbot --memory-backend openviking \
  --echomem-url http://127.0.0.1:19080 --workspace D:/.openviking/data \
  --dataset <dataset.json> --llm-base-url ... --llm-model ... --llm-api-key ... \
  --out-dir dynamic/results/formal_ov

# 4) 生成对比报告（token / 注入耗时 / 检索延迟 / 召回精度 / 答案质量）
python scripts/compare_memory_backends.py \
  --echomem-run dynamic/results/formal_em/<run> \
  --openviking-run dynamic/results/formal_ov/<run> \
  --dataset <dataset.json> --output reports/echomem_vs_openviking/index.html
```

`replay` 会自动识别 `generate` 产出的动态 v2 `dataset.json`
（含 `background_memories` + `dataset_queries`），保留每轮 `ground_facts`
的记忆 id，供召回精度计算；注入耗时记录在结果的 `config.inject_elapsed_s`。
一键流程见 `START_BAT/compare_echomem_vs_openviking.bat`。

## 性能压测

对运行中的 EchoMem 服务做多租户高并发**读写性能**压测（不需要 LLM）：检索
吞吐/延迟（客户端 + 服务端 `/metrics` 双视角）、注入四段延迟（open / add /
commit 提交 / commit 完成）、读写混合与「注入洪峰」下的劣化（**检出"注入阻塞
检索"**）、进程 CPU/RSS/线程/commit 队列水位。设计见
`docs/performance-stress-test-design.md`。

压测同时验明 EchoMem 的**四项特性保证**（见 `summary.json` 的
`signals` / `commit_durability` / `tenant_fairness` / `resources.rss_trend`）：

1. **commit 异步与成功保证**：202 接受后最终必须 completed；提交失败不重试
   （客户端 `max_retries=0`，失败分类输出）；commit 不阻塞检索——D 场景洪峰窗口
   读 P95 劣化超过阈值（默认 2x）即报信号。
2. **租户公平性**：按场景×租户分组读延迟，租户间 P95 max/min ≥ 3x 报不均衡信号。
3. **无内存泄漏**：RSS 时间序列最小二乘斜率 ≥ 5 MB/min 或冷却后未回落显著，报
   疑似泄漏信号。
4. **资源利用率随时间变化**：`report.html` 以独立子图展示 CPU%/RSS/线程/commit
   队列/inflight 的全过程曲线；`metrics_samples.csv` 含原始时序。

运行结束后 `report.html` 顶部与终端摘要会给出**逐特性结论**（通过 / 不通过 /
数据不足）与总体结论，判定依据含数据引用（见 `summary.json["feature_verdicts"]`）。

除结论外，报告还给出**特性量化分析**（`report.html`「特性量化分析」小节、
`summary.json["feature_verdicts"].features[*].measurements`、终端结论行内），
把「是否满足」扩展为「满足到什么程度」：写洪峰时 search 的 P95 比基线高多少
（绝对毫秒差 + 倍率）、最慢租户比最快租户多等的时长、RSS 增长率与每小时外推、
CPU/内存的均值与峰值。

```bash
# 快速冒烟（并发档 1,16，时长 15s）
python performance/run_stress.py --quick --tenants 4 --duration-s 60

# 全矩阵（并发档 1,4,16,64 x A/B/C/D；C 场景读:写比 8:1,4:1,1:1）
python performance/run_stress.py

# 长期满负荷（检验泄漏/公平性，建议 --duration-s 300+）
python performance/run_stress.py --duration-s 300 --scenarios A,B,D

# 外网部署：任意 IP:端口 + 静态预置身份（不创建租户）+ metrics 不可达降级
python performance/run_stress.py \
  --echomem-url http://203.0.113.10:8010 \
  --auth-mode static --auth-key XXX --tenant-id T1 --user-id U1 \
  --tenants 1 --scenarios A,D --concurrency-steps 1,8 --duration-s 30

# 只跑纯读基线 + 注入洪峰
python performance/run_stress.py --scenarios A,D --concurrency-steps 1,16,64

# 真实对话种子：locomo conv-30 复制灌入 8 个租户，压测结束后自动清理租户
python performance/run_stress.py --tenants 8 --seed-source locomo \
  --sample-filter conv-30 --cleanup-identities
```

场景说明：`A` 纯读基线（劣化对照）· `B` 纯写注入（四段延迟 + 写后读一致性 +
commit 成功保证）· `C` 读写混合（多档 read:write）· `D` 注入洪峰（读持续 +
突发 K 个 commit，检出 search-commit 干扰与读写数据倾斜）。

`performance/` 还提供两条互补路径（设计见
`docs/performance-stress-test-design.md` §3.8–3.12）：

### 调度专项七项验收

截图中的调度要求使用 `scheduler_acceptance.py` 单独验收，不把普通
A/B/C/D 压测结果当作专项结论。它按以下七项分别输出 `PASS`、`FAIL` 或
`INCONCLUSIVE`：DAU/热租户容量、多规格配置、单租户故障隔离、Jain 公平性、
Search 优先级、Commit kill-9 恢复重放、分层调度可观测性。

```bash
python -m performance.scheduler_acceptance \
  --suite results/performance/formal_<ts>/suite.json \
  --capability results/performance/probes/capability-probe.json \
  --recovery results/performance/probes/recovery.json \
  --fault results/performance/probes/fault-suite.json \
  --out results/performance/probes/scheduler-acceptance.json
```

缺少故障控制、重启控制或多规格实测时，报告保留 `INCONCLUSIVE`，不会
根据客户端延迟或 HTTP 200 推断 EchoMem 已实现对应保证。

- **正式验收套件**（`formal_suite.py`）：以子进程方式逐 case 重跑
  `run_stress.py`（`report6` / `pr421` / `complete` 三档场景目录），把原生产物
  推导成 `acceptance.py` 验收门禁（8 个 gate：search 成功率 / report6 质量 /
  隔离 / 公平 / commit 完成 / 拒绝 / hot tenant / 容量阶梯）消费的契约摘要，
  产出 `suite.json` / `acceptance.json` / `model_analysis_input.json` /
  `suite.html`。只有每次运行都用独立租户凭据才允许做出上线结论。
- **故障 / 恢复 / 限流 / 对账探针**（`probes/`）：独立 CLI，直接以真实 HTTP
  访问 EchoMem；只在部署方显式提供故障/恢复控制时才执行真实操作，否则如实上报
  `INCONCLUSIVE`，显式 404 是「未实现」的唯一证据。

```bash
# 正式验收套件（默认 pr421 场景目录，3 轮）
python -m performance.formal_suite \
  --base-url http://127.0.0.1:8010 \
  --tenant-config performance/tenants.example.json --repeats 3

种子数据准备默认按最多 4 个租户并行执行，以缩短真实模型 commit 的准备时间；
种子阶段不计入压测窗口。可通过 `--seed-concurrency N` 调整，正式负载阶段仍
按场景配置独立控制并发。

# 探针：真实限流阶梯扫描
python performance/probes/limit_failure_sweep.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config performance/tenants.example.json \
  --session-root <session_root> --out-dir results/performance/probes
```

单实例 4U8G 的完整验收使用 `performance/run_4u8g_complete.sh`；默认单轮执行
PR397/report(6) 与 PR421 的完整场景并集，不执行 `soak`，也不启动 4U16G：

```bash
export ECHOMEM_BASE_URL=http://127.0.0.1:8010
export ECHOMEM_CONFIG=/etc/echomem/4u8g/config.json
export STRESS_TENANT_CONFIG=/opt/echomem-stress/tenants-32.generated.json
export STRESS_OUTPUT_DIR=/opt/echomem-stress/results/4u8g-complete-$(date +%Y%m%d_%H%M%S)
./performance/run_4u8g_complete.sh
```

结果写入 `performance/results/<ts>/`：`summary.json`（按场景×并发档分节的延迟/
吞吐/错误/资源/劣化倍数）、`requests.csv`（逐请求）、`metrics_samples.csv`
（服务端采样时序）、`report.html`（自包含报告）。正式套件结果写入
`results/performance/formal_<ts>/`：`suite.json` / `acceptance.json` /
`model_analysis_input.json` / `summary.json` / `suite.html`，每个 case 的
`run/` 保留 run_stress 原生产物。

| 参数 | 说明 | 默认 |
|---|---|---|
| `--echomem-url` | 目标服务地址（IP:端口可配，含外网） | `http://127.0.0.1:8010` |
| `--auth-mode` | `provision` 自助创建租户 / `static` 预置身份 | `provision` |
| `--tenants` × `--concurrency-steps` | 租户数 × 每租户并发阶梯 | 8 × `1,4,16,64` |
| `--scenarios` | 场景过滤 | `A,B,C,D` |
| `--mix-ratios` | C 场景读:写比档位 | `8:1,4:1,1:1` |
| `--burst-commits` / `--burst-window-s` | D 场景洪峰事务数 / 窗口 | 32 / 10 |
| `--duration-s` | 每场景每并发档时长 | 60 |
| `--seed-source` | 种子数据源：`synthetic` 合成锚词消息 / `locomo` 真实对话 | `synthetic` |
| `--dataset-path` | locomo 数据集路径（仅 `--seed-source locomo`） | `benchmarks/locomo/data/locomo10.json` |
| `--sample-filter` | locomo 样本过滤器（单个 / 逗号分隔多个 / `all`） | `conv-30` |
| `--no-metrics` / `--skip-health` | 外网降级：不抓 /metrics、跳过预检 | 关 |
| `--cleanup-identities` | 压测结束后删除 provision 租户（身份 + 会话/记忆数据全清；static 模式拒绝） | 关 |

## 服务器测试指南

本节用于团队成员在 Linux 服务器上测试已经启动的 EchoMem。推荐把 EchoMem
和 Harness 放在同一台机器上：EchoMem 只监听 `127.0.0.1:8010`，Harness
通过本机或 Docker host network 访问，不需要把 EchoMem API 暴露到公网。

### 1. 拉取测试平台

使用测试平台 PR29 的 `v3` 分支：

```bash
git clone -b v3 git@github.com:noi031/Memory-System-Eval-Harness.git
cd Memory-System-Eval-Harness
git rev-parse --short HEAD
```

已有目录执行：

```bash
git fetch origin
git checkout v3
git pull --ff-only origin v3
```

测试代码不要在服务器上临时修改；需要修改时先提交到测试平台 PR。

### 2. 启动并检查 EchoMem

EchoMem 必须先启动，Harness 不负责拉起被测服务：

```bash
cd /opt/echomem
export ECHOMEM_AUTO_COMMIT_THRESHOLD=20000
echomem server \
  --workspace /opt/echomem-stress/workspace \
  --host 127.0.0.1 \
  --port 8010
```

另开终端检查：

```bash
curl -fsS http://127.0.0.1:8010/health
curl -fsS http://127.0.0.1:8010/metrics >/dev/null
```

如果这里失败，先查看 EchoMem 日志，不要直接启动压测。

### 3. 配置真实模型

正式压测禁止使用 `fake-llm` 或 `fake-embedding`。模型 endpoint、模型名和
API Key 环境变量必须与 EchoMem 的 `config.json` 一致：

```text
LLM endpoint:       https://dashscope.aliyuncs.com/compatible-mode/v1
LLM model:          deepseek-v4-flash-0731
Embedding endpoint: https://dashscope.aliyuncs.com/compatible-mode/v1
Embedding model:    text-embedding-v3
MCP:                关闭
Rerank:             关闭，除非本轮测试明确要求开启
```

示例环境变量如下，实际变量名以 `config.json` 中的 `api_key_env` 为准：

```bash
export ECHOMEM_LLM_API_KEY='你的模型 key'
export ECHOMEM_EMBEDDING_API_KEY='你的模型 key'
export ECHOMEM_ATOMIC_ENGINE_LLM_API_KEY="$ECHOMEM_LLM_API_KEY"
export ECHOMEM_EPISODE_ENGINE_LLM_API_KEY="$ECHOMEM_LLM_API_KEY"
export ECHOMEM_BASE_ENGINE_LLM_API_KEY="$ECHOMEM_LLM_API_KEY"
export ECHOMEM_MEMORY_UNIT_ENGINE_LLM_API_KEY="$ECHOMEM_LLM_API_KEY"
export ECHOMEM_INTENT_LLM_API_KEY="$ECHOMEM_LLM_API_KEY"
export ECHOMEM_MEMROUTER_LLM_API_KEY="$ECHOMEM_LLM_API_KEY"
```

不要把真实 API Key 写入 Git、README、日志或结果报告。

### 4. 准备独立租户凭据

公平性和隔离测试必须使用不同租户凭据，不能让所有租户共用一个 Key：

```bash
cp performance/tenants.example.json /opt/echomem-stress/tenants.json
export ECHOMEM_TENANT_A_KEY='tenant-a 的 key'
export ECHOMEM_TENANT_B_KEY='tenant-b 的 key'
export ECHOMEM_TENANT_C_KEY='tenant-c 的 key'
export ECHOMEM_TENANT_D_KEY='tenant-d 的 key'
```

`tenants.json` 中的 `auth_key_env` 必须和当前 shell 中的变量对应。缺少独立
凭据时可以做单租户诊断，但不能据此下多租户公平性或隔离结论。

### 5. 先跑短检查

在完整测试前先跑一个 10 秒基线，确认地址、凭据、模型和工作目录都正确：

```bash
export ECHOMEM_BASE_URL=http://127.0.0.1:8010
export ECHOMEM_CONFIG=/opt/echomem-stress/workspace/config.json
export STRESS_TENANT_CONFIG=/opt/echomem-stress/tenants.json
export STRESS_OUTPUT_DIR=/opt/echomem-stress/results/smoke-$(date +%Y%m%d_%H%M%S)

python3 -m performance.formal_suite \
  --base-url "$ECHOMEM_BASE_URL" \
  --tenant-config "$STRESS_TENANT_CONFIG" \
  --preflight-config "$ECHOMEM_CONFIG" \
  --profile pr421 \
  --scenarios baseline \
  --repeats 1 \
  --duration-cap-s 10 \
  --case-timeout-s 120 \
  --commit-timeout-s 60 \
  --out-dir "$STRESS_OUTPUT_DIR"
```

必须看到：

```text
FORMAL_PROGRESS 1/1 scenario=baseline repeat=1 policy=server-observe status=completed
```

### 6. 执行 4U8G 完整测试

默认执行 PR397/report(6) 与 PR421 的 25 个 bounded 场景，单轮、不执行
30 分钟 `soak`，只测试 4U8G：

```bash
cd /opt/Memory-System-Eval-Harness
export ECHOMEM_BASE_URL=http://127.0.0.1:8010
export ECHOMEM_CONFIG=/opt/echomem-stress/workspace/config.json
export STRESS_TENANT_CONFIG=/opt/echomem-stress/tenants-32.json
export STRESS_REPEATS=1
export STRESS_CASE_TIMEOUT_S=180
export STRESS_COMMIT_TIMEOUT_S=600
export STRESS_OUTPUT_DIR=/opt/echomem-stress/results/4u8g-$(date +%Y%m%d_%H%M%S)
mkdir -p "$STRESS_OUTPUT_DIR"

nohup ./performance/run_4u8g_complete.sh \
  >"$STRESS_OUTPUT_DIR/launcher.log" 2>&1 &
echo $! >"$STRESS_OUTPUT_DIR/launcher.pid"
```

`tenant-skew` 会一次提交 260 个 Commit，单场景可能明显慢于普通场景；
平台默认限制 barrier 同时在途数为 32（可用 `STRESS_BARRIER_WAVE_SIZE` 调整），
因此总样本仍是 260 个，但不会把 260 个真实任务一次性压入 EchoMem。
`STRESS_CASE_TIMEOUT_S=0` 表示按场景时长 + Commit 轮询预算自动计算；只有诊断时
才建议手动设置较小的超时。超时会记录为 `TIMEOUT`，不会伪装成 EchoMem 的业务失败。

服务器没有 Python 依赖时，使用 runner 镜像，并确保工作目录为 `/harness`：

```bash
docker run --rm --network host \
  --env-file /opt/echomem-stress/formal-run.env \
  -v /opt/Memory-System-Eval-Harness:/harness \
  -v /opt/echomem-stress:/opt/echomem-stress \
  -w /harness \
  -e ECHOMEM_BASE_URL=http://127.0.0.1:8010 \
  -e ECHOMEM_CONFIG=/opt/echomem-stress/workspace/config.json \
  -e STRESS_TENANT_CONFIG=/opt/echomem-stress/tenants-32.json \
  -e STRESS_OUTPUT_DIR=/opt/echomem-stress/results/4u8g-docker-$(date +%Y%m%d_%H%M%S) \
  echomem-stress-runner:latest \
  bash -lc 'export STRESS_CASE_TIMEOUT_S=180; export STRESS_COMMIT_TIMEOUT_S=600; ./performance/run_4u8g_complete.sh'
```

### 7. 查看进度和结果

```bash
tail -f "$STRESS_OUTPUT_DIR/launcher.log"
cat "$STRESS_OUTPUT_DIR/suite.json"
cat "$STRESS_OUTPUT_DIR/acceptance.json"
find "$STRESS_OUTPUT_DIR" -name summary.json -type f | sort
```

最终应确认 `suite.json` 中 25 个场景均有结果；`acceptance.json` 中的
`PASS`、`FAIL`、`INCONCLUSIVE` 要逐项查看，不能只看总准确率或退出码。

### 七项目标统一自动化入口

使用 `performance/objective_suite.py` 可以按实例规格逐个执行容量、稳定性、
公平性、Search 优先级、Commit 恢复和 `/metrics` 可观测性检查。真实服务器上先
把 `performance/instance-profiles.example.json` 复制为实际 profile 配置，并填写
真实的 `tenant_config`、`preflight_config` 和可选 `prepare_command`：

```bash
python3 -m performance.objective_suite \
  --profiles performance/instance-profiles.example.json \
  --profile 4U8G \
  --out-dir results/objective-suite-$(date +%Y%m%d_%H%M%S) \
  --quick
```

`--quick` 只做 bounded smoke；正式数据去掉 `--quick`。O1 的“最大用户量”是压测
窗口内完成的容量阶梯上限，不直接等同于业务 DAU；O6 必须额外提供真实 container
重启和 cursor/message-set 对账配置；O7 必须实际抓到服务端 `/metrics` 四元组。
报告输出 `objective-suite.json` 和 `objective-suite.html`，不会把缺失证据算成通过。
报告文件为 `suite.html`，逐请求和资源时序通常位于各场景的 `run/` 目录。

### 8. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `Connection refused :8010` | EchoMem 已退出或端口未监听 | 检查 `docker ps` 和 `/health` |
| `ModuleNotFoundError: performance` | runner 当前目录不是 Harness 根目录 | 使用 `-w /harness` |
| 长时间停在 `tenant-skew` | 260 个 Commit 屏障等待或服务异常 | 停止本轮，查看场景 `summary.json`，缩短 case timeout 后重跑 |
| 容器 `exit 137` | 容器被终止，常见于内存压力 | 检查 `docker inspect`、宿主机内存和 RSS 曲线 |
| `fake-llm` / `fake-embedding` | EchoMem 配置仍是 fake 模型 | 修正 `config.json` 和 `*_API_KEY` |
| 只有 `suite.json` 没有场景结果 | 首个场景前退出或目标服务不可达 | 查看 `launcher.log` 和 `run/*/summary.json` |

结果建议只保留 3 天：

```bash
find /opt/echomem-stress/results -mindepth 1 -maxdepth 1 \
  -type d -mtime +3 -exec rm -rf -- {} +
```

## 扩展指南

### 新增 Agent 插件

1. 创建 `plugins/<name>/` 目录
2. 创建 `__init__.py`（空即可）
3. 创建 `plugin.py`，实现 `AgentPlugin` 子类：

```python
from plugins.base import AgentPlugin, AgentResponse

class MyAgentPlugin(AgentPlugin):
    def setup(self, config: dict) -> None:
        # 初始化客户端、创建 memory_client
        ...

    def inject_memories(self, memories, *, backend="echomem", session_id=""):
        # 写入记忆后端 (不支持的插件不覆盖，默认 no-op)
        ...

    def create_session(self, title=""):
        # 创建 QA 会话，返回 session_id
        ...

    def send_message(self, session_id, message, context_path="/", *, extra=None):
        # 发送消息，返回 AgentResponse
        return AgentResponse(text="...")

    def getlog(self) -> str:
        # 返回日志 JSON 字符串
        return "{}"
```

4. 实现 `add_arguments` classmethod 声明 CLI 参数，可复用
   `backends/memory_args.py` 中的 `add_memory_backend_args()`。

`registry.py` 自动扫描 `plugins.<name>.plugin` 模块中 `AgentPlugin` 的子类，
无需手动注册。运行：`python benchmarks/locomo/run_eval.py --agent-plugin <name> ...`

### 新增记忆后端

1. 创建 `backends/<name>/` 目录
2. 创建 `client.py`，实现 `BaseHTTPMemoryClient` 子类，覆盖 `_headers()` 和
   `_fetch_commit_status()` 等抽象方法，并实现 `search` / `fs_read` /
   `fs_list` / `fs_glob` 等检索方法
3. 在 `backends/memory_args.py` 的 `add_memory_backend_args()` 中添加连接参数
4. 在使用该后端的插件 `setup()` 中实例化客户端

```python
from backends.memory_types import BaseHTTPMemoryClient

class MyBackendClient(BaseHTTPMemoryClient):
    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}
    # 实现 search / commit / fs 等方法...
```

### 新增 Benchmark 数据集

1. 创建 `benchmarks/<name>/` 目录
2. 实现核心模块：
   - `dataset.py` - 数据集加载与解析
   - `import_memory.py` - 记忆导入逻辑
   - `qa.py` - QA 任务构建与执行
   - `judge.py` 或 `evaluate.py` - 评测逻辑
   - `reporting.py` - 结果汇总
   - `run_eval.py` - 入口脚本
3. 复用 `shared/` 基础设施：`EvalConfig` / `EvalRun` /
   `add_agent_plugin_args` / `add_eval_args` / `add_judge_args` / `LLMClient`

## 评测流程概览

| Benchmark | 导入方式 | QA 方式 | 评测方式 |
|---|---|---|---|
| LoCoMo | 集中导入所有 session | 仅检索不写入 | LLM judge (CORRECT/WRONG) |
| HotpotQA | per_question 或 global | 仅检索不写入 | answer/supporting-fact/joint F1/EM |
| LongMemEval | 逐题隔离导入 haystack | 仅检索不写入 | 官方 accuracy (LLM yes/no) |
| 动态 (generate) | LLM 生成场景 | 端到端 EchoAgent | 配置驱动质量评估 (0-100) |
| 动态 (replay) | 先注入对话再 QA | 跨 session 检索 | 配置驱动质量评估 (0-100) |

> **指标变更同步约定**：如果任何一个 benchmark（locomo / hotpotqa / longmemeval）
> 或 dynamic 的评估指标、产物字段（`summary.json` / `quality_report.json` /
> `eval_results.csv` / `dynamic_results.json` 等），或 performance 压测的产物字段
> （`summary.json` / `requests.csv` / `metrics_samples.csv` 等）发生增删或含义改变，
> 必须同步更新 `scripts/memory-eval-improve` skill 中对应的 benchmark/dynamic/
> performance **特有字段描述**
> （`references/benchmark-specific-fields.md` 与 `references/analysis-dimensions.md`），
> 避免分析报告基于过时的字段定义得出结论。

## 辅助工具

```bash
# 记忆客户端健康检查
python scripts/backend_doctor.py --format json

# QA 检索证据格式检查
python scripts/validate_evidence.py --input /path/to/qa_results.csv --strict

# LoCoMo 黑盒指标导出
python benchmarks/locomo/blackbox.py \
  --qa /path/to/run/qa_results.csv \
  --judge /path/to/run/judge_results.csv \
  --import-results /path/to/run/import_results.csv \
  --summary /path/to/run/summary.json \
  --out-dir /path/to/report

# 两次运行结果对比
python benchmarks/locomo/compare.py \
  --left /path/to/run-a \
  --right /path/to/run-b \
  --out-dir /path/to/comparison
```

各 benchmark 详细参数见对应 `docs/usage.md`。插件设计细节见
`plugins/README.md`，记忆后端设计细节见 `backends/README.md`。

# Memory-System-Eval-Harness v3

记忆系统评测框架。全 CLI, 无网页 UI。

## 目录结构

```
plugins/              # 评测插件 (AgentPlugin 协议)
  base.py             # AgentPlugin 抽象基类 (setup/inject_memories/create_session/...)
  registry.py         # 插件发现与加载
  bare_llm/           # 纯 LLM 基线 (system prompt + 上下文 + 用户查询)
  echo_agent/         # EchoAgent 外部 agent 插件
  vikingbot/          # VikingBot 历史 prompt、工具协议和 agent loop
  echomem_mcp/        # EchoMem MCP agent + 记忆客户端
  openviking_mcp/     # OpenViking MCP agent + 记忆客户端
benchmarks/
  locomo/           # LoCoMo 数据集评测
    dataset.py      # LoCoMo 数据解析
    import_memory.py qa.py judge.py diagnosis.py retry.py
    blackbox.py compare.py reporting.py
    data/ docs/ results/ run_eval.py
  hotpotqa/         # HotpotQA 数据集评测
    dataset.py import_memory.py qa.py evaluate.py recovery.py reporting.py
    data/ docs/ results/ run_eval.py
  longmemeval/      # LongMemEval 数据集评测
    dataset.py import_memory.py qa.py judge.py evaluate.py
    recovery.py parallel.py reporting.py
    data/ docs/ results/ run_eval.py
  generic/          # 非正式/自定义数据集 dry-run 解析
dynamic/
  simulator.py      # 场景与查询生成
  workflows.py      # generate/replay 工作流
  metrics.py        # 动态指标和质量评估
  artifacts.py      # JSON/CSV/报告输出
  model_client.py   # 动态 LLM 客户端
  prompt_config.py  # prompt 配置加载
  configs/ docs/ results/ run_eval.py
shared/             # 共享库
  dataset_io.py     # 通用 JSON/JSONL 读取、下载和路径解析
  llm_client.py     # LLM 客户端 (OpenAI 兼容)
  text.py           # 通用文本规范化
  qa.py             # 通用 QA 数据结构和执行辅助
  recovery.py       # QA CSV 健康判定、恢复选择和成功行合并
  eval_base.py      # 评测基础设施 (配置, 日志, 结果目录, EchoMem 日志收集)
  memory_types.py   # 记忆类型 (CommitResult, SearchResult, MemoryClient Protocol)
  memory_args.py    # 记忆后端 CLI 参数 (--memory-backend 等)
scripts/
  backend_doctor.py    # 记忆客户端健康检查
  validate_evidence.py # QA 检索证据格式检查
```

正式数据集的加载、Judge、指标、重试和报告都归属
`benchmarks/<dataset>/`。评测针对 agent 插件而非记忆后端；记忆注入通过
`AgentPlugin.inject_memories()` 统一完成，评测平台不直接感知记忆后端。
当前支持 echomem 和 openviking 两个记忆后端，由 `--memory-backend` 参数选择。

## LoCoMo 测试

LoCoMo 数据集已经包含在
`benchmarks/locomo/data/locomo10.json`，测试者不需要另外下载或指定
`--dataset`。

### 1. 配置

`conv-30` 默认注入记忆到新创建的独立身份。先复制配置模板：

```bash
cp .env.example .env
```

然后至少设置以下内容：

```dotenv
ECHOMEM_BASE_URL=http://127.0.0.1:8010
ECHOMEM_ROOT=/absolute/path/to/EchoMem-repository
ECHOMEM_WORKSPACE=/absolute/path/to/existing-conv30-memory-workspace
ECHOMEM_AUTO_START=1

ECHOMEM_ACCOUNT=default
ECHOMEM_USER_ID=default
ECHOMEM_AGENT_ID=default

ANSWER_BASE_URL=https://provider.example.com/compatible-mode/v1
ANSWER_MODEL=deepseek-v4-flash
ANSWER_TOKEN=YOUR_API_KEY
```

`ECHOMEM_WORKSPACE` 指向 EchoMem 的 workspace 目录。保持 `ECHOMEM_AUTO_START=1`
时，`eval.sh` 会使用该目录启动 EchoMem，并在评测结束后关闭服务。若
`ECHOMEM_BASE_URL` 上已有服务，必须确认它也是由同一 workspace 启动的。

### 2. 运行检查

```bash
./eval.sh locomo --check --sample conv-30
```

正常输出应包含：

```text
samples=1
questions=81
session_mode=locomo
```

### 3. 运行全量 81 题

#### EchoMem MCP 三种对照模式

下面三条命令都使用 `echomem_mcp`、同一个 `conv-30` 数据集和真实
EchoMem 记忆注入。先设置 DashScope 兼容 API 环境变量；API key 只放在
本地环境中，不要写入仓库：

```bash
export LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export LLM_MODEL=deepseek-v4-flash
export LLM_API_KEY="$DASHSCOPE_API_KEY"
```

1. **不带 MCP 工具调用**：仍执行 EchoMem 记忆注入，并由平台通过 MCP
   `memory_query` 做初始召回；回答阶段不向模型暴露工具，只进行一次模型调用。

   ```bash
   # EchoMem HTTP=8110, MCP=8111；不使用 --reuse-memory-from，
   # 因此会新建身份并重新注入 conv-30 的 19 个 session。
   ./.venv/bin/python benchmarks/locomo/run_eval.py \
     --agent-plugin echomem_mcp \
     --echomem-url http://127.0.0.1:8110 \
     --mcp-url http://127.0.0.1:8111 \
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
     --llm-retries 3
   ```

   如果 EchoMem MCP 开启了鉴权，再额外增加：

   ```bash
   --echomem-auth-key "$ECHOMEM_AUTH_KEY" \
   --mcp-auth-key "$ECHOMEM_AUTH_KEY"
   ```

2. **带 MCP 工具调用，但不读取 `messages.jsonl`**：平台仍会先通过 MCP
   `memory_query` 做初始召回；模型侧保留 `memory_query`、`list`、`glob`，
   从工具 schema 中移除 `read`。即使模型返回未暴露的 `read`，harness 也会拒绝
   转发该调用。

   ```bash
   ./.venv/bin/python benchmarks/locomo/run_eval.py \
     --agent-plugin echomem_mcp \
     --echomem-url http://127.0.0.1:8110 \
     --mcp-url http://127.0.0.1:8111 \
     --echomem-auth-key "$ECHOMEM_AUTH_KEY" \
     --mcp-auth-key "$ECHOMEM_AUTH_KEY" \
     --sample conv-30 \
     --llm-base-url "$LLM_BASE_URL" \
     --llm-model "$LLM_MODEL" \
     --llm-api-key "$LLM_API_KEY" \
     --tool-calling \
     --mcp-read-mode disabled
   ```

3. **带 MCP 工具调用并允许读取 `messages.jsonl`**：平台仍会先通过 MCP
   `memory_query` 做初始召回，并保留全部 MCP 工具。模型是否调用 `read` 由
   模型根据 MCP 工具描述和当前上下文自行决定。

   ```bash
   ./.venv/bin/python benchmarks/locomo/run_eval.py \
     --agent-plugin echomem_mcp \
     --echomem-url http://127.0.0.1:8110 \
     --mcp-url http://127.0.0.1:8111 \
     --echomem-auth-key "$ECHOMEM_AUTH_KEY" \
     --mcp-auth-key "$ECHOMEM_AUTH_KEY" \
     --sample conv-30 \
     --llm-base-url "$LLM_BASE_URL" \
     --llm-model "$LLM_MODEL" \
     --llm-api-key "$LLM_API_KEY" \
     --tool-calling \
     --mcp-read-mode allow
   ```

结果目录中的 `summary.json` 会额外记录：

- `messages_jsonl_read_questions`：至少成功发起一次 transcript 读取的题数
- `messages_jsonl_read_calls`：识别到的 `messages.jsonl` 读取调用数
- `messages_jsonl_read_rate`：读取题数 / QA 题数
- `tool_call_total`、`avg_iterations`、`accuracy`：工具使用和准确率对照指标

`require` 仍可作为兼容参数使用，但不再追加 transcript 强制 Prompt；是否读取
`messages.jsonl` 由模型自行决定。

结果写入 `results/<run-name>/<timestamp>/`，主要文件包括
`qa_results.csv`、`judge_results.csv`、`summary.json` 和
`agent_traces/`。

## 动态评测

```bash
# 先检查本地配置、EchoAgent 登录、credential 映射和 EchoMem 健康状态
./eval.sh dynamic --check \
  --username test_user --password YOUR_PASSWORD

# Generate 模式
./eval.sh dynamic \
  --username test_user --password YOUR_PASSWORD \
  --scenario-api-key YOUR_KEY \
  --llm-api-key YOUR_KEY

# Replay 模式
./eval.sh dynamic \
  --username test_user --password YOUR_PASSWORD \
  --dataset /path/to/locomo.json \
  --llm-api-key YOUR_KEY
```

## 评测流程概述

| Benchmark | 导入方式 | QA 方式 | 评测方式 |
|---|---|---|---|
| LoCoMo | 集中导入所有 session | 仅检索不写入 | LLM judge (CORRECT/WRONG) |
| HotpotQA | per_question 或 global | 仅检索不写入 | answer/supporting-fact/joint F1/EM |
| LongMemEval | 逐题隔离导入 haystack | 仅检索不写入 | 官方 accuracy (LLM yes/no) |
| 动态 (generate) | LLM 生成场景 | 端到端 EchoAgent | 配置驱动质量评估 (0-100) |
| 动态 (replay) | 先注入对话再 QA | 跨 session 检索 | 配置驱动质量评估 (0-100) |

# Memory-System-Eval-Harness v3

记忆系统评测框架。全 CLI, 无网页 UI。

## 目录结构

```
agents/
  vikingbot/        # 历史 VikingBot prompt、工具协议和 agent loop
backends/
  echomemory/       # 当前唯一记忆后端
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
  client.py         # EchoAgent HTTP/SSE/prefetch
  simulator.py      # 场景与查询生成
  workflows.py      # generate/replay 工作流
  metrics.py        # 动态指标和质量评估
  artifacts.py      # JSON/CSV/报告输出
  configs/ docs/ results/ run_eval.py
shared/             # 共享库
  dataset_io.py     # 通用 JSON/JSONL 读取、下载和路径解析
  llm_client.py     # LLM 客户端 (OpenAI 兼容)
  text.py           # 通用文本规范化
  qa.py             # 通用 QA 数据结构和执行辅助
  recovery.py       # QA CSV 健康判定、恢复选择和成功行合并
  eval_base.py      # 评测基础设施 (配置, 日志, 结果目录, EchoMem 日志收集)
scripts/
  benchmark_adapter.py # benchmarks/generic/adapter.py 的兼容 CLI 入口
  backend_doctor.py    # EchoMemory backend 注册和契约检查
  validate_evidence.py # QA 检索证据格式检查
```

正式数据集的加载、Judge、指标、重试和报告都归属
`benchmarks/<dataset>/`，不会通过 `scripts/benchmark_adapter.py` 间接执行。
运行时只支持 EchoMemory；不包含 OpenViking backend、SDK、配置或数据集工作流。

## LoCoMo 测试

LoCoMo 数据集已经包含在
`benchmarks/locomo/data/locomo10.json`，测试者不需要另外下载或指定
`--dataset`。

### 1. 配置已有记忆

`conv-30` 默认直接复用已经注入好的 EchoMem workspace，不会重新执行
open/add/commit。先复制配置模板：

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

`ECHOMEM_WORKSPACE` 必须指向包含 `conv-30` 已有记忆的目录，不能是新建空目录。
保持 `ECHOMEM_AUTO_START=1` 时，`eval.sh` 会使用该目录启动 EchoMem，并在评测
结束后关闭服务。若 `ECHOMEM_BASE_URL` 上已有服务，必须确认它也是由同一
workspace 启动的。

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

带 VikingBoat 0.4.11 对齐工具调用：

```bash
./eval.sh locomo --sample conv-30 --tools
```

不暴露工具，仅使用初始注入的完整记忆正文：

```bash
./eval.sh locomo --sample conv-30 --no-tools
```

保留文件工具但关闭语义 `memory_search`，同时跳过初始向量检索：

```bash
./eval.sh locomo --sample conv-30 --tools --no-search
```

本地验证额外 prompt 时，通过文件参数追加到所选 profile 的 system prompt：

```bash
./eval.sh locomo \
  --sample conv-30 \
  --tools \
  --qa-prompt-file /path/to/local-prompt.txt
```

prompt 文件不会进入代码仓；`summary.json` 和 resume manifest 仅记录文件名和
SHA-256。

两种模式使用相同的数据集和已有记忆：

| 命令 | 默认 QA profile | 记忆行为 |
|---|---|---|
| `--tools` | `vikingboat0411` | 初始检索，并允许只读 `memory_*` 工具循环 |
| `--no-tools` | `vikingboat0411-natural-no-tools` | 只使用完整初始记忆正文，不暴露工具 schema |
| `--tools --no-search` | `vikingboat0411` | 跳过初始语义检索且不暴露 `memory_search`；保留 `memory_read_many`、`memory_list`、`memory_grep`、`memory_glob` |

要用同一份新注入的记忆对比两种模式，先保留隔离身份，再复用它：

```bash
./eval.sh locomo \
  --sample conv-30 \
  --inject-memory \
  --keep-memory-account \
  --memory-identity-file .runtime/locomo-conv30.json \
  --tools

./eval.sh locomo \
  --sample conv-30 \
  --memory-identity-file .runtime/locomo-conv30.json \
  --no-tools
```

身份文件包含 EchoMem 临时 auth key，已由 `.gitignore` 排除，并以 `0600` 权限
写入；不要提交或分享该文件。

结果写入 `results/<run-name>/<timestamp>/`，主要文件包括
`qa_results.csv`、`judge_results.csv`、`summary.json` 和
`agent_traces/`。

### 4. 可选操作

只运行一题做 smoke test：

```bash
./eval.sh locomo --sample conv-30 --questions 1 --tools
```

确实需要重新注入记忆时：

```bash
./eval.sh locomo --sample conv-30 --inject-memory --tools
```

`--inject-memory` 会使用隔离身份执行 open/add/commit，不属于默认的已有记忆
复用测试。

## 其他用法

使用根目录统一入口；首次运行会自动创建 `.venv` 并安装依赖。

```bash
# EchoMem 未启动时自动启动；结束后自动关闭
./eval.sh locomo --start-echomem --sample conv-30 --questions 1

# 保留自动启动的 EchoMem 服务
./eval.sh locomo --start-echomem --keep-echomem --sample conv-30

# 从中断运行继续，健康 QA/Judge 行不会再次调用模型
./eval.sh locomo \
  --sample conv-30 \
  --reuse-memory-account \
  --resume-qa /path/to/interrupted-run \
  --resume-judge /path/to/interrupted-run

# 其他静态数据集
./eval.sh hotpotqa --questions 10
./eval.sh longmemeval --questions 10
```

默认情况下，只要任何记忆导入没有完成，评测会在 QA 前退出并生成失败
`summary.json`。`--allow-incomplete-imports` 仅用于排障，不能用于正式分数。

LoCoMo 默认直接使用已配置身份中的现有记忆并跳过 open/add/commit。
这里的“现有记忆”来自 `.env` 中 `ECHOMEM_WORKSPACE` 指向的目录。若
`ECHOMEM_BASE_URL` 上已经运行了 EchoMem，它必须也是用同一目录启动的；
`--workspace` 不会在运行中的服务内动态切换存储目录。最稳妥的交付方式是保持
`ECHOMEM_AUTO_START=1`，并确保该端口未被另一份 EchoMem workspace 占用。
需要重新注入时增加 `--inject-memory`，评测会创建独立 tenant/user 并在结束时
自动删除；需要保留该隔离记忆供 workspace 诊断时再增加
`--keep-memory-account`。其他静态数据集仍保持原有隔离注入行为。运行结果会记录
`memory_source=existing|injected` 和实际身份，但 auth key 只会以掩码形式保存。
LoCoMo 在进入 QA 前还会校验数据集 SHA-256 和实际 session manifest；session
数量与当前 `session-mode` 不一致时默认拒绝运行，防止复用 tenant 被额外注入
污染。`--allow-memory-provenance-mismatch` 仅用于诊断。

LoCoMo 未显式指定 profile 时，`--tools` 自动选择 `vikingboat0411`，
`--no-tools` 自动选择 `vikingboat0411-natural-no-tools`。高级复现仍可通过
`--qa-profile legacy-77` 显式选择 77% 历史复现口径。
prompt/loop 来源与 EchoMemory 适配边界见
`docs/v2-source-provenance.md`，并会写入 `summary.json` 和逐题
`agent_traces/*.json`。

LoCoMo 默认按数据集原始 session 分批导入，避免把整段长对话压成一个
超大 commit。快速检查可增加 `--max-sessions 1`；兼容旧单 session 行为时
可显式使用 `--session-mode single`。

`--question-timeout-s` 是单题检索和回答共享的总 deadline；设为 `0` 表示
不增加单题总限制，但底层 HTTP 请求仍使用各自的连接超时。

### 直接运行 Python

```bash
# LoCoMo
python benchmarks/locomo/run_eval.py \
  --dataset /path/to/locomo.json \
  --llm-api-key YOUR_KEY

# HotpotQA
python benchmarks/hotpotqa/run_eval.py \
  --dataset /path/to/hotpotqa.json \
  --llm-api-key YOUR_KEY

# LongMemEval
python benchmarks/longmemeval/run_eval.py \
  --dataset /path/to/longmemeval.json \
  --llm-api-key YOUR_KEY
```

### 动态评测

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

通常优先使用 `./eval.sh`; 各 benchmark 详细参数见对应 `docs/usage.md`。

辅助检查均可独立运行：

```bash
python scripts/backend_doctor.py --format json
python scripts/validate_evidence.py --input /path/to/qa_results.csv --strict
python benchmarks/locomo/blackbox.py \
  --qa /path/to/run/qa_results.csv \
  --judge /path/to/run/judge_results.csv \
  --import-results /path/to/run/import_results.csv \
  --summary /path/to/run/summary.json \
  --out-dir /path/to/report
python benchmarks/locomo/compare.py \
  --left /path/to/run-a \
  --right /path/to/run-b \
  --out-dir /path/to/comparison
python benchmarks/hotpotqa/recovery.py --help
python benchmarks/longmemeval/recovery.py --help
```

## 评测流程概述

| Benchmark | 导入方式 | QA 方式 | 评测方式 |
|---|---|---|---|
| LoCoMo | 集中导入所有 session | 仅检索不写入 | LLM judge (CORRECT/WRONG) |
| HotpotQA | per_question 或 global | 仅检索不写入 | answer/supporting-fact/joint F1/EM |
| LongMemEval | 逐题隔离导入 haystack | 仅检索不写入 | 官方 accuracy (LLM yes/no) |
| 动态 (generate) | LLM 生成场景 | 端到端 EchoAgent | 配置驱动质量评估 (0-100) |
| 动态 (replay) | 先注入对话再 QA | 跨 session 检索 | 配置驱动质量评估 (0-100) |

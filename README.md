# Memory-System-Eval-Harness v3

记忆系统评测框架。全 CLI, 无网页 UI。

## 目录结构

```
agents/             # Agent 插件 (每个插件封装一种 agent 的交互方式)
  base.py             # AgentPlugin ABC + AgentResponse / TypingResult
  registry.py         # 按名动态加载插件
  baseline_mem/       # 基线 agent+记忆 (EchoMem 检索 + LLM 生成, benchmark 默认)
    plugin.py docs/design.md
  echo_agent/         # EchoAgent + EchoMem 完整管线 (动态评测默认)
    plugin.py client.py docs/design.md
  bare_llm/           # 无记忆系统基线插件
    plugin.py docs/design.md
benchmarks/
  locomo/           # LoCoMo 数据集评测
    data/           # 放数据集文件
    docs/usage.md   # 使用文档
    results/        # 评测结果 (每次一个时间戳子目录)
    run_eval.py     # 评测入口
  hotpotqa/         # HotpotQA 数据集评测
    data/ docs/ results/ run_eval.py
  longmemeval/      # LongMemEval 数据集评测
    data/ docs/ results/ run_eval.py
dynamic/
  docs/usage.md       # 动态评测文档
  configs/            # 用户模拟器/评测器 YAML 配置
  results/            # 评测结果
  run_eval.py         # 动态评测入口
  dynamic_evaluator.py  # 记忆生成 + 查询生成 (静态/动态模式)
  llm.py              # LLM 客户端 (动态评测专用)
  prompt_config_loader.py  # 模拟器/评测器 prompt 配置加载
shared/             # 共享库 (被 benchmarks + dynamic + agents 共用)
  echomem_client.py   # EchoMem HTTP 客户端
  llm_client.py       # LLM 客户端 (OpenAI 兼容)
  dataset.py          # 数据集加载
  benchmark_adapter.py  # 数据集解析/归一化 (locomo/hotpotqa/longmemeval/generic)
  judge.py            # 评测工具 (LLM judge, F1/EM, LongMemEval accuracy)
  benchmark_runner.py # 通用 QA 阶段执行 (通过 agent 插件并发跑题)
  eval_base.py        # 评测基础设施 (配置, 日志, 结果目录, agent 插件参数加载)
```

评测流程通过 `AgentPlugin` 接口与被测 agent 交互, 不直接调用 agent 特定的 HTTP API。每个 agent 对应一个插件, 通过 `--agent-plugin` 选择。每个插件通过 `add_arguments` classmethod 声明自己的 CLI 参数, `--help` 只显示当前插件相关参数。详见 `agents/README.md`。

## 快速开始

1. 启动 EchoMem 服务 (`http://127.0.0.1:8010`)
2. (动态评测) 启动 EchoAgent 后端 (`http://127.0.0.1:31020`)

### 静态评测 (LoCoMo / HotpotQA / LongMemEval)

默认使用 `baseline_mem` 插件 (EchoMem 检索 + LLM 生成)。使用 `--agent-plugin` 切换插件, 可用插件及参数见 `agents/README.md`。

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

默认使用 `echo_agent` 插件 (EchoAgent + EchoMem 完整管线)。

```bash
# Generate 模式
python dynamic/run_eval.py \
  --username test_user --password YOUR_PASSWORD \
  --scenario-api-key YOUR_KEY \
  --llm-api-key YOUR_KEY

# Replay 模式
python dynamic/run_eval.py \
  --username test_user --password YOUR_PASSWORD \
  --dataset /path/to/locomo.json \
  --llm-api-key YOUR_KEY
```

> 切换插件后 CLI 参数会变化, 使用 `--help` 查看当前插件支持的参数。

各 benchmark 详细参数见对应 `docs/usage.md`。

## 评测流程概述

| Benchmark | 导入方式 | QA 方式 | 评测方式 |
|---|---|---|---|
| LoCoMo | 集中导入所有 session | 仅检索不写入 | LLM judge (CORRECT/WRONG) |
| HotpotQA | per_question 或 global | 仅检索不写入 | 官方 F1/EM |
| LongMemEval | 逐题隔离导入 haystack | 仅检索不写入 | 官方 accuracy (LLM yes/no) |
| 动态 (generate) | LLM 生成场景 | 端到端 EchoAgent | LLM 质量评估 (0-2 分) |
| 动态 (replay) | 先注入对话再 QA | 跨 session 检索 | LLM 质量评估 (0-2 分) |

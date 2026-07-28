# Memory-System-Eval-Harness v3

记忆系统评测框架。全 CLI, 无网页 UI。

## 目录结构

```
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
  docs/usage.md     # 动态评测文档
  results/          # 评测结果
  run_eval.py       # 动态评测入口
shared/             # 共享库
  echomem_client.py # EchoMem HTTP 客户端
  llm_client.py     # LLM 客户端 (OpenAI 兼容)
  dataset.py        # 数据集加载 (基于 v2 benchmark_adapter.py)
  judge.py           # 评测工具 (LLM judge, F1/EM, LongMemEval accuracy)
  qa.py              # 通用 QA 流程 (search -> prompt -> LLM answer)
  eval_base.py      # 评测基础设施 (配置, 日志, 结果目录, EchoMem 日志收集)
scripts/             # v2 实现层 (保留, benchmark_adapter.py 等被 shared/ 引用)
```

## 快速开始

1. 启动 EchoMem 服务 (`http://127.0.0.1:8010`)
2. (动态评测) 启动 EchoAgent 后端 (`http://127.0.0.1:31020`)

### 静态评测 (LoCoMo / HotpotQA / LongMemEval)

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

各 benchmark 详细参数见对应 `docs/usage.md`。

## 评测流程概述

| Benchmark | 导入方式 | QA 方式 | 评测方式 |
|---|---|---|---|
| LoCoMo | 集中导入所有 session | 仅检索不写入 | LLM judge (CORRECT/WRONG) |
| HotpotQA | per_question 或 global | 仅检索不写入 | 官方 F1/EM |
| LongMemEval | 逐题隔离导入 haystack | 仅检索不写入 | 官方 accuracy (LLM yes/no) |
| 动态 (generate) | LLM 生成场景 | 端到端 EchoAgent | LLM 质量评估 (0-2 分) |
| 动态 (replay) | 先注入对话再 QA | 跨 session 检索 | LLM 质量评估 (0-2 分) |

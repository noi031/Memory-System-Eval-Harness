# LongMemEval 评测

## 评测流程

1. **逐题隔离导入**: 每题各自导入自己的 haystack_sessions (每题一个 EchoMem session, 包含所有 haystack 消息)
2. **逐题 QA**: search EchoMem 检索记忆 -> 组装 prompt -> LLM 生成回答 (仅检索不写入)
3. **官方 accuracy 评测**: 按题型用 LLM judge yes/no 判定回答正确性

题型: `single-session-user`, `single-session-assistant`, `multi-session`,
`temporal-reasoning`, `knowledge-update`, `single-session-preference`,
`single-session-abstention`

## 使用方法

```bash
# 不指定 --dataset 则自动查找/下载
python benchmarks/longmemeval/run_eval.py \
  --llm-api-key YOUR_API_KEY

# 指定数据集路径
python benchmarks/longmemeval/run_eval.py \
  --dataset /path/to/longmemeval.json \
  --llm-api-key YOUR_API_KEY

# 限制数量
python benchmarks/longmemeval/run_eval.py \
  --dataset /path/to/longmemeval.json \
  --questions 20 \
  --concurrency 8 \
  --llm-api-key YOUR_API_KEY

# 使用独立 judge 模型
python benchmarks/longmemeval/run_eval.py \
  --dataset /path/to/longmemeval.json \
  --judge-model gpt-4o \
  --judge-api-key YOUR_JUDGE_KEY \
  --judge-base-url https://api.openai.com/v1 \
  --llm-api-key YOUR_API_KEY
```

## 参数说明

### 必填参数
| 参数 | 说明 |
|---|---|
| `--llm-api-key` | LLM API Key |

### 数据集参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--dataset` | (自动) | LongMemEval JSON 数据集路径。不指定时自动在 `benchmarks/longmemeval/data/` 查找 `longmemeval_s_cleaned.json`, 找不到则从 HuggingFace 下载 |
| `--sample` | `all` | 筛选 sample |
| `--questions` | `0` | 限制 QA 数量 (0=全部) |
| `--agent-plugin` | `baseline_mem` | QA 阶段使用的 agent 插件名，见 `agents/` 目录 |

### Judge 参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--judge-model` | (同 `--llm-model`) | Judge LLM 模型名 |
| `--judge-api-key` | (同 `--llm-api-key`) | Judge API Key |
| `--judge-base-url` | (同 `--llm-base-url`) | Judge base URL |

### EchoMem / LLM / 评测参数
默认使用 `baseline_mem` 插件, 参数与 LoCoMo 相同。切换 `--agent-plugin` 后可用参数会变化, 使用 `--help` 查看。

## 输出文件

`benchmarks/longmemeval/results/<timestamp>/` 下:
- `config.json`, `run.log` - 配置和日志
- `import_results.csv` - 导入结果 (含 sessions 数量)
- `qa_results.csv` - QA 结果
- `eval_results.csv` - Judge 结果 (question_id, question_type, correct)
- `summary.json` - 汇总 (accuracy, per_type accuracy, token usage)
- `echomem_logs/` - EchoMem 日志

# HotpotQA 评测

## 评测流程

1. **导入记忆** (两种模式):
   - `per_question` (默认): 每题各自导入自己的 context passages 到独立 EchoMem session
   - `global`: 所有题的 passages 合并导入到一个共享 EchoMem session
2. **逐题 QA**: search EchoMem 检索记忆 -> 组装 prompt -> LLM 生成回答
3. **F1/EM 评测**: 官方 normalize_answer + precision/recall/F1 + exact_match (无需 LLM judge)

## 使用方法

```bash
# 不指定 --dataset 则自动查找/下载
python benchmarks/hotpotqa/run_eval.py \
  --import-mode per_question \
  --llm-api-key YOUR_API_KEY

# 指定数据集路径
python benchmarks/hotpotqa/run_eval.py \
  --dataset /path/to/hotpotqa.json \
  --import-mode per_question \
  --llm-api-key YOUR_API_KEY

# global 模式 (共享 session)
python benchmarks/hotpotqa/run_eval.py \
  --dataset /path/to/hotpotqa.json \
  --import-mode global \
  --concurrency 8 \
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
| `--dataset` | (自动) | HotpotQA JSON 数据集路径。不指定时自动在 `benchmarks/hotpotqa/data/` 查找 `hotpot_dev_distractor_v1.json`, 找不到则从远程下载 |
| `--sample` | `all` | 筛选 sample |
| `--questions` | `0` | 限制 QA 数量 (0=全部) |
| `--import-mode` | `per_question` | 导入模式: `per_question` 或 `global` |
| `--agent-plugin` | `baseline_mem` | QA 阶段使用的 agent 插件名，见 `agents/` 目录 |

### EchoMem / LLM / 评测参数
默认使用 `baseline_mem` 插件, 参数与 LoCoMo 相同, 详见 `benchmarks/locomo/docs/usage.md` 中的参数表。切换 `--agent-plugin` 后可用参数会变化, 使用 `--help` 查看。

HotpotQA 不需要 Judge 参数 (使用官方 F1/EM, 无需 LLM judge)。

## 输出文件

`benchmarks/hotpotqa/results/<timestamp>/` 下:
- `config.json`, `run.log` - 配置和日志
- `import_results.csv` - 导入结果
- `qa_results.csv` - QA 结果
- `eval_results.csv` - F1/EM 评测结果 (question_id, f1, em)
- `summary.json` - 汇总 (avg_f1, avg_em, token usage)
- `echomem_logs/` - EchoMem 日志

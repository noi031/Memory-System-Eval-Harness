# LoCoMo 评测

## 评测流程

1. **集中导入**: 遍历数据集所有 sample, 每个 sample 的 conversation sessions 逐个 open -> add_messages -> commit -> poll_commit
2. **逐题 QA**: 对每个 QA 问题, search EchoMem 检索记忆 -> 组装 prompt -> LLM 生成回答 (仅检索不写入)
3. **LLM Judge**: 用 LLM 判定回答 CORRECT / WRONG

## 使用方法

```bash
# 基本用法 (不指定 --dataset 则自动查找/下载)
python benchmarks/locomo/run_eval.py \
  --echomem-url http://127.0.0.1:8010 \
  --llm-base-url https://ark.cn-beijing.volces.com/api/coding/v3 \
  --llm-model doubao-seed-2.0-pro \
  --llm-api-key YOUR_API_KEY

# 指定数据集路径
python benchmarks/locomo/run_eval.py \
  --dataset /path/to/locomo.json \
  --echomem-url http://127.0.0.1:8010 \
  --llm-base-url https://ark.cn-beijing.volces.com/api/coding/v3 \
  --llm-model doubao-seed-2.0-pro \
  --llm-api-key YOUR_API_KEY

# 指定 sample 和问题数量
python benchmarks/locomo/run_eval.py \
  --dataset /path/to/locomo.json \
  --sample sample_0 \
  --questions 10 \
  --llm-api-key YOUR_API_KEY

# 自定义检索参数
python benchmarks/locomo/run_eval.py \
  --dataset /path/to/locomo.json \
  --top-k 20 \
  --memory-budget-chars 12000 \
  --concurrency 8 \
  --llm-api-key YOUR_API_KEY
```

## 参数说明

### 必填参数
| 参数 | 说明 |
|---|---|
| `--llm-api-key` | LLM API Key (也可通过环境变量 `LLM_API_KEY` 设置) |

### 数据集参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--dataset` | (自动) | LoCoMo JSON 数据集路径。不指定时自动在 `benchmarks/locomo/data/` 查找 `locomo10.json`, 找不到则从 GitHub 下载 |
| `--sample` | `all` | 筛选 sample: `all` 或 sample_id |
| `--questions` | `0` | 限制 QA 数量 (0=全部) |

### EchoMem 参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--echomem-url` | `http://127.0.0.1:8010` | EchoMem HTTP 地址 |
| `--echomem-auth-key` | (空) | EchoMem X-Auth-Key (也可通过 `ECHOMEM_AUTH_KEY` 设置) |
| `--account` | `default` | EchoMem account |
| `--user-id` | `default` | EchoMem user_id |
| `--agent-id` | `default` | EchoMem agent_id |
| `--workspace` | (空) | EchoMem workspace 路径 |
| `--echomem-log-dir` | (空) | EchoMem 日志目录 (用于收集日志到评测结果) |

### LLM 参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--llm-base-url` | (空) | LLM API base URL (也可通过 `LLM_BASE_URL` 设置) |
| `--llm-model` | `doubao-seed-2.0-pro` | LLM 模型名 |
| `--llm-temperature` | `0.7` | 生成温度 |
| `--llm-max-tokens` | `2048` | 最大生成 token 数 |
| `--llm-timeout-s` | `120.0` | LLM 请求超时 (秒) |
| `--llm-retries` | `3` | LLM 请求重试次数 |

### 评测参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--top-k` | `10` | 检索记忆条数 (TOPK) |
| `--memory-budget-chars` | `8000` | 注入 prompt 的记忆最大字符数 |
| `--concurrency` | `4` | QA 并发数 |
| `--commit-timeout-s` | `600.0` | Commit 轮询超时 (秒) |
| `--commit-poll-interval-s` | `2.0` | Commit 轮询间隔 (秒) |
| `--question-timeout-s` | `120.0` | 单题超时 (秒) |
| `--out-dir` | `results` | 结果目录 |

### Judge 参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--judge-model` | (同 `--llm-model`) | Judge LLM 模型名 |
| `--judge-api-key` | (同 `--llm-api-key`) | Judge API Key |
| `--judge-base-url` | (同 `--llm-base-url`) | Judge base URL |

## 输出文件

每次评测在 `benchmarks/locomo/results/<timestamp>/` 下生成:
- `config.json` - 评测配置
- `run.log` - 完整日志
- `import_results.csv` - 导入结果 (sample_id, session_id, status, messages, elapsed)
- `qa_results.csv` - QA 结果 (question_id, question, answer, response, retrieval_error, elapsed)
- `judge_results.csv` - Judge 结果 (question_id, verdict, reasoning)
- `summary.json` - 汇总指标 (accuracy, avg_elapsed, token usage)
- `echomem_logs/` - EchoMem 日志 (如配置了 `--echomem-log-dir`)

# LoCoMo 评测

## 评测流程

1. **记忆准备**: 默认复用已有记忆；使用 `--inject-memory` 时才执行 open -> add_messages -> commit -> poll_commit
2. **逐题 QA**: 默认使用历史 VikingBot prompt 和 `memory_search` / `memory_read_many` 多轮工具循环，仅检索不写入
3. **LLM Judge**: 用 LLM 判定回答 CORRECT / WRONG

## 使用方法

先在 `.env` 中将 `ECHOMEM_WORKSPACE` 指向已注入 `conv-30` 的 EchoMem
workspace，并保留 `ECHOMEM_AUTO_START=1`。下面两条命令会直接对该目录中的
已有记忆做只读 QA，不执行 open/add/commit。若目标端口已有 EchoMem 服务，
该服务必须由同一个 workspace 启动。

```bash
# 默认复用已有 conv-30 记忆，自动选择 VikingBoat 0.4.11 工具口径
./eval.sh locomo --sample conv-30 --tools

# 自然无工具对照
./eval.sh locomo --sample conv-30 --no-tools

# 关闭语义 memory_search 和初始向量检索，保留文件工具
./eval.sh locomo --sample conv-30 --tools --no-search

# 追加仅保存在本地的实验 prompt
./eval.sh locomo \
  --sample conv-30 \
  --tools \
  --qa-prompt-file /path/to/local-prompt.txt

# 重新注入到隔离身份
./eval.sh locomo --sample conv-30 --inject-memory --tools

# 同一份新注入记忆的工具 / 无工具对照
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

# 从中断运行继续；只复用健康行，失败和缺失题会重新 QA
./eval.sh locomo \
  --sample conv-30 \
  --reuse-memory-account \
  --resume-qa /path/to/interrupted-run

# QA 已完成但 Judge 中断时，只复用问题、gold 和回答完全一致的判分
./eval.sh locomo \
  --sample conv-30 \
  --reuse-memory-account \
  --resume-qa /path/to/interrupted-run \
  --resume-judge /path/to/interrupted-run

# 使用 VikingBot v0.4.11 prompt、工具语义和循环口径
# 后端和模型可见工具均使用只读 EchoMemory memory_* 接口
./eval.sh locomo \
  --sample conv-30 \
  --reuse-memory-account \
  --qa-profile vikingboat0411

# 同一 VikingBoat 0.4.11 prompt 和初始记忆注入，但不暴露工具
./eval.sh locomo \
  --sample conv-30 \
  --reuse-memory-account \
  --qa-profile vikingboat0411 \
  --no-tools

# 自然无工具对照：只保留完整初始记忆正文，不保留工具指令或 URI-only 条目
./eval.sh locomo \
  --sample conv-30 \
  --reuse-memory-account \
  --qa-profile vikingboat0411-natural-no-tools \
  --no-tools

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
| `--dataset` | 内置 | 默认使用仓库中的 `benchmarks/locomo/data/locomo10.json` |
| `--sample` | `all` | 筛选 sample: `all` 或 sample_id |
| `--questions` | `0` | 限制 QA 数量 (0=全部) |
| `--session-mode` | `auto` | 单 sample 按原始 session; 多 sample 各自合并; 也可显式选 `locomo`/`single` |
| `--max-sessions` | `0` | 每个 sample 最多导入多少个原始 session (0=全部) |

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
| `--llm-temperature` | `0.7` | 回答模型生成温度；profile 可选择不显式发送 temperature |
| `--llm-max-tokens` | `1024` | LoCoMo 历史 profile 的最大生成 token 数 |
| `--llm-timeout-s` | `120.0` | LLM 请求超时 (秒) |
| `--llm-retries` | `3` | LLM 请求重试次数 |

### 评测参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--qa-profile` | 自动 | `--tools` 默认选择 `vikingboat0411`；`--no-tools` 默认选择 `vikingboat0411-natural-no-tools`。显式指定时可覆盖 |
| `--top-k` | profile 决定 | 三个保留 profile 均为 `25` |
| `--memory-budget-chars` | `6000` | 总记忆字符预算 |
| `--tool-search-limit` | profile 决定 | 三个保留 profile 均为 `25` |
| `--initial-min-score` | profile 决定 | `legacy-77=0`；VikingBoat 0.4.11 profiles=`0.1` |
| `--tool-min-score` | profile 决定 | `legacy-77=0`；VikingBoat 0.4.11 profiles=`0.35` |
| `--tool-search-pool-multiplier` | profile 决定 | 三个保留 profile 均为 `1` |
| `--tool-set` | profile 决定 | `legacy-77=vikingbot_native_safe`；VikingBoat 0.4.11 profiles=`vikingbot_echo_native` |
| `--tools` / `--no-tools` | `--tools` | 是否向回答模型暴露 profile 的记忆工具；关闭后保留相同 prompt 和初始检索注入，只执行一次模型调用 |
| `--search` / `--no-search` | `--search` | 是否启用语义 `memory_search` 与初始向量检索；`--no-search` 保留 `memory_read_many`、`memory_list`、`memory_grep`、`memory_glob` |
| `--user-memory-budget-chars` | `4000` | user memory prompt 预算 |
| `--agent-memory-budget-chars` | `2000` | agent memory prompt 预算 |
| `--max-iterations` | `50` | 单题最大模型/tool-loop 迭代数 |
| `--vikingbot-workspace` | 仓库内置历史 bootstrap | 默认使用 `agents/vikingbot/bootstrap/` 中固定的原始 `SOUL.md` 和 `TOOLS.md` 快照 |
| `--qa-prompt-file` | (空) | 将本地 UTF-8 文件追加到所选 profile 的 system prompt；`summary.json` 和 resume manifest 仅记录文件名和 SHA-256 |
| `--checkpoint-interval` | `10` | 每完成 N 题写一次 `qa_results.checkpoint.csv`；0 表示关闭 |
| `--resume-qa` | (空) | 从先前运行目录或 QA CSV 恢复健康答案；严格校验数据集、身份、模型和 QA 参数，且必须使用 `--reuse-memory-account` |
| `--concurrency` | `4` | QA 并发数 |
| `--commit-timeout-s` | `0` | Commit 轮询超时 (秒)，0 表示无限等待 |
| `--commit-poll-interval-s` | `2.0` | Commit 轮询间隔 (秒) |
| `--question-timeout-s` | profile 决定 | 三个保留 profile 均为 `600` 秒；0 表示不增加总限制 |
| `--out-dir` | `results` | 结果目录 |
| `--allow-incomplete-imports` | false | 导入未完成仍继续，仅限诊断 |
| `--allow-memory-provenance-mismatch` | false | session manifest 与数据集/session-mode 不一致时仍继续；仅限诊断 |
| `--memory-session-prefix` | 按 sample 推导 | `conv-30` 自动推导为 `echomem-locomo-conv-30-`；显式指定时覆盖 |
| `--memory-identity-file` | (空) | 本地保存或复用隔离 EchoMem tenant 的身份文件；包含 auth key，文件以 `0600` 写入且不得提交 |
| `--reuse-memory-account` | true | LoCoMo 默认复用已配置身份中的现有记忆，跳过 open/add/commit |
| `--inject-memory` | false | 显式切换为重新注入：创建隔离身份并执行 open/add/commit |
| `--keep-memory-account` | false | 评测结束后保留临时隔离身份，供 workspace 诊断 |

### Judge 参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--judge-model` | (同 `--llm-model`) | Judge LLM 模型名 |
| `--judge-api-key` | (同 `--llm-api-key`) | Judge API Key |
| `--judge-base-url` | (同 `--llm-base-url`) | Judge base URL |
| `--judge-concurrency` | `4` | Judge 并发数；结果仍按原始题目顺序写入 |
| `--judge-checkpoint-interval` | `10` | 每完成 N 题写一次 `judge_results.checkpoint.csv`；0 表示关闭 |
| `--resume-judge` | (空) | 从先前运行目录或 Judge CSV 恢复判分；严格校验 Judge 模型和 prompt，并逐行校验 question/gold/response |

## 输出文件

每次评测在 `benchmarks/locomo/results/<timestamp>/` 下生成:
- `config.json` - 评测配置
- `run.log` - 完整日志
- `import_results.csv` - 导入结果 (sample_id, session_id, status, messages, elapsed)
- `memory_provenance.json` - 数据集 SHA-256、预期/实际 session 数和实际 session URI manifest
- `qa_results.csv` - QA 结果，包含 tool_call_count、iterations、qa_profile
- `qa_results.checkpoint.csv` - 运行中定期更新的可恢复 QA 快照
- `qa_resume_manifest.json` - 恢复兼容性所需的数据集、身份、模型、QA 参数和本地 prompt/tool/runtime contract hash
- `judge_results.csv` - Judge 结果 (question_id, verdict, reasoning)
- `judge_results.checkpoint.csv` - 运行中定期更新的 Judge 快照
- `judge_resume_manifest.json` - Judge 模型和 prompt 指纹，用于防止混合判分口径
- `diagnosis.json` - 失败分类、检索覆盖率、可重试/缺失/重复题目 ID
- `retrieval_traces.jsonl` - 每题检索内容和失败归因 trace
- `agent_traces/*.json` - VikingBot 初始 prompt、逐轮模型消息、请求/响应模型身份、工具协议 hash、工具参数/结果、原始与清洗后答案
- `summary.json` - 汇总指标，包含 memory_source、qa_profile、served model ids、tool protocol hash、tool_call_total、avg_iterations 和 diagnosis 摘要
- `strict_blackbox_metrics.json` - 仅使用外部可观测状态、延迟、重试和 token usage 的指标
- `strict_blackbox_report.md` - strict black-box Markdown 报告
- `echomem_logs/` - EchoMem 日志 (如配置了 `--echomem-log-dir`)

## 独立分析与恢复

```bash
# 从已有结果重建 strict black-box 报告
python benchmarks/locomo/blackbox.py \
  --qa /path/to/run/qa_results.csv \
  --judge /path/to/run/judge_results.csv \
  --import-results /path/to/run/import_results.csv \
  --summary /path/to/run/summary.json \
  --out-dir /path/to/report

# 对比两个运行，输出 JSON/CSV/Markdown
python benchmarks/locomo/compare.py \
  --left /path/to/baseline \
  --right /path/to/candidate \
  --out-dir /path/to/comparison

# 查看失败或缺失题并生成重跑参数
python benchmarks/locomo/retry.py --help

# 检查检索证据 JSON
python scripts/validate_evidence.py \
  --input /path/to/run/qa_results.csv \
  --strict
```

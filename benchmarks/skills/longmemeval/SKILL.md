---
name: longmemeval
description: >
  启动 LongMemEval 评测（长上下文记忆 benchmark，7 类题型）。当用户要求
  「跑 longmemeval 评测 / 启动 longmemeval benchmark / 评测长上下文记忆 /
  生成 longmemeval 评测命令」时使用。本 skill 负责：列出 longmemeval 的全部
  可配置参数，向用户逐项确认（先解释参数含义、给出推荐/默认值），再生成并
  执行评测命令、交付结果。
---

# LongMemEval 评测（run_eval.py --dataset longmemeval）

被测系统：EchoMem / OpenViking 记忆后端 + LLM。入口 `python run_eval.py
--dataset longmemeval`（评测代码在 `benchmarks/longmemeval/`）。完整事实核对
见 `benchmarks/longmemeval/docs/usage.md`，本节是执行用的操作手册。

**工作流（必做）**：先向用户**追问全部可配置参数**（第 4 节轮次清单），拿到
答案后再生成命令并执行；不要替用户拍板题集、并行度、LLM 与 Judge 配置。

---

## 1. 评测流程

1. **逐题隔离导入**：每题各自导入自己的 haystack_sessions（每题一个 EchoMem
   session，包含所有 haystack 消息）。
2. **逐题 QA**：search EchoMem 检索记忆 → 组装 prompt → LLM 生成回答
   （仅检索不写入）。
3. **官方 accuracy 评测**：按题型用 LLM judge yes/no 判定回答正确性。

题型：`single-session-user`、`single-session-assistant`、`multi-session`、
`temporal-reasoning`、`knowledge-update`、`single-session-preference`、
`single-session-abstention`。

## 2. 全部可配置参数

### 2.1 必填参数

| 参数 | 含义 |
|---|---|
| `--llm-api-key` | LLM API Key（也可通过环境变量 `LLM_API_KEY` 设置） |

### 2.2 数据集参数

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--dataset-path` | 自动 | LongMemEval JSON 数据集路径。不指定时自动在 `benchmarks/longmemeval/data/` 查找 `longmemeval_s_cleaned.json`，找不到则从 HuggingFace 下载 |
| `--sample` | `all` | 筛选 sample |
| `--questions` | `0` | 限制 QA 数量（0=全部） |
| `--question-ids` | 空 | 逗号分隔的 question/native/sample ID |
| `--random-count` | `0` | 从已筛选题目中稳定随机抽取数量（0=不随机抽取） |
| `--random-seed` | `30` | 随机抽样 seed |
| `--agent-plugin` | `vikingbot` | QA 阶段使用的 agent 插件名，见 `plugins/` 目录。切换后可用参数会变化 |

### 2.3 评测基础设施参数（benchmark 自身）

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--concurrency` | `4` | QA 并发数 |
| `--checkpoint-interval` | `10` | 每完成 N 题写一次 `qa_results.checkpoint.csv`；0 表示关闭 |
| `--resume` | 空 | **统一续跑**：复用先前运行身份，跳过已完成 import batch，恢复健康 QA 答案，复用匹配的 Judge 判定；只跑缺失/失败部分。summary 指标对合并后的整轮累计。**不支持与 `--parallel-shards` 同用** |
| `--reuse-memory-from` | 空 | 复用先前运行的身份 + 已导入的 haystack 记忆，但**全新重跑全部 QA/Judge**（指标只算本轮） |
| `--out-dir` | `results` | 结果目录 |

> 导入不完整（任一 import 记录失败/超时）时评测**立即终止**，QA/Judge 不启动；失败信息写入 `run.log` 与 `summary.json`（status=failed, phase=import）。

### 2.4 并行参数

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--parallel-shards` | `1` | 按稳定 round-robin 切分为多少个隔离 CLI 进程（分片评测；完成后自动合并） |
| `--parallel-workers` | `2` | 同时运行的最大 shard 进程数 |
| `--parallel-dry-run` | false | 只写 `parallel_manifest.json`，不执行 shard |

### 2.5 Judge 参数

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--judge-model` | 同 `--llm-model` | Judge LLM 模型名 |
| `--judge-api-key` | 同 `--llm-api-key` | Judge API Key |
| `--judge-base-url` | 同 `--llm-base-url` | Judge base URL |

### 2.6 插件参数（默认 `vikingbot`）

LLM 凭据、QA 检索行为、记忆后端连接和 VikingBot 特有参数均由插件声明，与
LoCoMo 默认插件一致，明细见 `benchmarks/locomo/docs/usage.md`。

**LLM 参数：**

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--llm-base-url` | 空 | LLM API base URL（也可通过 `LLM_BASE_URL` 设置） |
| `--llm-model` | `doubao-seed-2.0-pro` | LLM 模型名 |
| `--llm-api-key` | 空 | LLM API Key（也可通过 `LLM_API_KEY` 设置） |
| `--llm-temperature` | `0.7` | 回答模型生成温度；profile 可选择不显式发送 temperature |
| `--llm-max-tokens` | profile 决定 | 最大生成 token 数 |
| `--llm-timeout-s` | `120.0` | LLM 请求超时（秒） |
| `--llm-retries` | `3` | LLM 请求重试次数 |

**记忆后端参数：**

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--memory-backend` | `echomem` | 记忆后端选择：`echomem` 或 `openviking` |
| `--echomem-url` | `http://127.0.0.1:8010` | 后端 HTTP 地址（openviking 时指向 19080） |
| `--echomem-auth-key` | 空 | 后端 X-Auth-Key（也可通过 `ECHOMEM_AUTH_KEY` 设置） |
| `--echomem-log-access-key` | 空 | 特权日志查询 key（`GET /api/logs` 的 X-Log-Access-Key；也可通过 `ECHOMEM_LOG_ACCESS_KEY` 设置）。不提供则 `backend_logs.json` 仅含 403 错误 |
| `--account` | `default` | 后端 account |
| `--user-id` | `default` | 后端 user_id |
| `--agent-id` | `default` | 后端 agent_id |
| `--workspace` | 空 | 后端 workspace 路径 |
| `--commit-timeout-s` | `0` | Commit 轮询超时（秒），0 表示无限等待 |
| `--commit-poll-interval-s` | `2.0` | Commit 轮询间隔（秒） |
| `--timeout-s` | `60.0` | 后端 HTTP 请求超时（秒） |
| `--max-retries` | `3` | 后端 HTTP 请求最大重试次数 |

**QA 检索参数：**

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--top-k` | `25` | 检索返回条数 |
| `--memory-budget-chars` | `6000` | 总记忆字符预算 |
| `--question-timeout-s` | `600` | 单题总超时（秒）；0 表示不增加总限制 |

**VikingBot 插件参数：**

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--tool-search-limit` | `25` | 每轮工具检索条数上限 |
| `--initial-min-score` | `0.1` | 初始检索最低分阈值 |
| `--tool-min-score` | `0.35` | 工具检索最低分阈值 |
| `--tool-search-pool-multiplier` | `1` | 工具检索候选池倍数 |
| `--tool-set` | `vikingbot_echo_native` | 工具集名称 |
| `--tool-calling` | `False` | 是否向回答模型暴露记忆工具；关闭后保留相同 prompt 和初始检索注入，只执行一次模型调用 |
| `--user-memory-budget-chars` | `4000` | user memory prompt 预算 |
| `--agent-memory-budget-chars` | `2000` | agent memory prompt 预算 |
| `--max-iterations` | `50` | 单题最大模型/tool-loop 迭代数 |
| `--vikingbot-workspace` | 内置 bootstrap | 默认使用 `plugins/vikingbot/bootstrap/` 中固定的原始 `SOUL.md` 和 `TOOLS.md` 快照 |

> 切换 `--agent-plugin` 后可用参数会变化（如 `echomem_mcp` 暴露 `--mcp-url`/
> `--mcp-read-mode` 等，`echo_agent` 暴露 `--echoagent-url` 等），以
> `python run_eval.py --dataset longmemeval --help` 为准。

## 3. 输出文件

`benchmarks/longmemeval/results/<timestamp>/` 下：
- `config.json` / `run.log` - 配置和日志
- `import_results.csv` - 导入结果（含 sessions 数量）
- `backend_logs.json` - 后端日志（需提供 `--echomem-log-access-key` / `ECHOMEM_LOG_ACCESS_KEY`，否则仅含 403 错误）
- `qa_results.csv` - QA 结果，`retrieval_items_json` 列含原始检索证据及后端 metadata
- `eval_results.csv` - Judge 结果（question_id, question_type, correct）
- `summary.json` - 汇总（accuracy、per_type accuracy、token usage）

并行运行额外生成 `parallel_manifest.json`、每个 shard 的 `runner.log`、
`parallel_summary.json`，以及 `merged/` 下去重合并后的 CSV 和 `summary.json`。

## 4. 追问工作流（执行前必问）

用 `AskUserQuestion` 分轮追问，每轮 ≤4 题。**先解释参数含义再给选项**（含义见
第 2 节表格），推荐值标注在选项里；自由文本参数（API key、URL、路径）用 Other
输入。用户已明确给过某项时不要重复问；用户选择「全部默认」时跳过对应轮次。

### R1 插件与运行范围（4 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q1 用哪个 agent 插件？ | `--agent-plugin` | vikingbot（推荐，默认）/ echomem_mcp / echo_agent / bare_llm（无记忆基线） |
| Q2 筛选哪个 sample？ | `--sample` | all（推荐）/ 具体 sample id |
| Q3 跑多少题？ | `--questions` | 0=全部（推荐）/ N 题快速验证 |
| Q4 稳定随机抽取？ | `--random-count` / `--random-seed` | 不抽取（推荐，0）/ 抽取 N 题（seed 默认 30） |

### R2 题集与数据源（4 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q5 指定题目 ID？ | `--question-ids` | 空（推荐）/ 逗号分隔 ID 列表 |
| Q6 数据集路径？ | `--dataset-path` | 自动查找/下载（推荐）/ 显式 JSON 路径 |
| Q7 并行分片？ | `--parallel-shards` | 1=不分片（推荐）/ 8（分片并行）/ 其他 |
| Q8 分片进程数上限？ | `--parallel-workers`（Q7>1 时） | 2（推荐）/ 4 / 其他 |

### R3 运行方式（3 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q9 运行方式？（三选一，互斥） | `--resume` / `--reuse-memory-from` | 全新运行（推荐）/ 续跑 `--resume <run-dir>`（复用身份，跳过已导入批次，恢复健康 QA 答案与匹配 Judge 判定，只补缺失/失败部分；**与分片互斥**）/ 复用旧记忆重跑 `--reuse-memory-from <run-dir>`（复用身份+已导入 haystack 记忆，QA/Judge 全量重跑，指标只算本轮）。**续跑与复用旧记忆重跑只能选一个** |
| Q10 只生成分片清单？ | `--parallel-dry-run` | false（推荐）/ true（只写 parallel_manifest.json 不执行） |
| Q11 结果目录？ | `--out-dir` | results（推荐）/ 其他目录 |

### R4 LLM 配置（4 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q13 LLM base URL？ | `--llm-base-url` | 留空用环境变量 `LLM_BASE_URL`（推荐）/ 显式 URL |
| Q14 LLM 模型？ | `--llm-model` | doubao-seed-2.0-pro（推荐）/ 其他模型 |
| Q15 LLM API Key？ | `--llm-api-key` | 从环境变量 `LLM_API_KEY` 取（推荐）/ Other 输入 |
| Q16 生成细节用默认？ | `--llm-temperature` / `--llm-max-tokens` / `--llm-timeout-s` / `--llm-retries` | 用推荐默认 0.7 / profile 决定 / 120s / 3（推荐）/ Other 覆盖 |

### R5 记忆后端（4 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q17 记忆后端？ | `--memory-backend` | echomem（推荐）/ openviking |
| Q18 后端地址？ | `--echomem-url` | http://127.0.0.1:8010（推荐）/ 其他地址 |
| Q19 后端鉴权与日志 key？ | `--echomem-auth-key` / `--echomem-log-access-key` | 分别从环境变量 `ECHOMEM_AUTH_KEY` / `ECHOMEM_LOG_ACCESS_KEY` 取（推荐；日志 key 缺失则 `backend_logs.json` 仅含 403 错误）/ Other 输入 |
| Q20 身份与 workspace？ | `--account` / `--user-id` / `--agent-id` / `--workspace` | 全用 default（推荐）/ Other 覆盖 |

### R6 后端行为细节（1 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q21 Commit/HTTP 行为用默认？ | `--commit-timeout-s` / `--commit-poll-interval-s` / `--timeout-s` / `--max-retries` | 用推荐默认 0 / 2.0s / 60s / 3（推荐）/ Other 覆盖 |

### R7 检索与工具（4 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q22 检索条数？ | `--top-k` | 25（推荐）/ 其他 |
| Q23 记忆字符预算？ | `--memory-budget-chars` | 6000（推荐）/ 其他 |
| Q24 开启工具调用？ | `--tool-calling` | 关（推荐，单次 RAG）/ 开（多轮工具检索，含 `--tool-set` 默认 vikingbot_echo_native） |
| Q25 检索分数/预算细节用默认？ | `--tool-search-limit` / `--initial-min-score` / `--tool-min-score` / `--tool-search-pool-multiplier` / `--user-memory-budget-chars` / `--agent-memory-budget-chars` / `--max-iterations` | 用推荐默认 25 / 0.1 / 0.35 / 1 / 4000 / 2000 / 50（推荐）/ Other 覆盖 |

### R8 Judge（3 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q26 Judge 模型？ | `--judge-model` | 同 `--llm-model`（推荐）/ 独立模型（如 gpt-4o） |
| Q27 Judge API Key？ | `--judge-api-key` | 同 `--llm-api-key`（推荐）/ Other 输入 |
| Q28 Judge base URL？ | `--judge-base-url` | 同 `--llm-base-url`（推荐）/ 显式 URL |

### R9 评测基础设施（3 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q29 QA 并发数？ | `--concurrency` | 4（推荐）/ 8 / 1 |
| Q30 checkpoint 间隔？ | `--checkpoint-interval` | 10（推荐）/ 0=关闭 / 其他 |
| Q31 运行标识确认？ | （汇总） | 展示最终命令，请用户确认后执行 / 修改 |

## 5. 前置检查

1. 记忆后端存活：echomem `http://127.0.0.1:8010`（或用户指定的后端）可访问，
   `X-Auth-Key` 可用（如有）。
2. LLM 凭据齐备：`--llm-api-key` 或 `LLM_API_KEY` 已提供。
3. 后端日志 key：`--echomem-log-access-key` 或 `ECHOMEM_LOG_ACCESS_KEY` 已提供
   （否则结果目录的 `backend_logs.json` 只有 403 错误）。
4. 数据集：不指定 `--dataset-path` 时确认 `benchmarks/longmemeval/data/` 有
   `longmemeval_s_cleaned.json`（没有会触发 HuggingFace 下载）。
5. 续跑场景：确认目标结果目录存在，且原运行的参数（`--questions` 等）与本次
   一致；续跑不能与 `--parallel-shards` 同用。

## 6. 生成并执行

确认配置（第 4 节）后，在 `Memory-System-Eval-Harness/` 下组装命令并执行：

```bash
python run_eval.py --dataset longmemeval \
  [--dataset-path <path>] \
  [--sample <sample>] [--questions <N>] [--question-ids <ids>] \
  [--random-count 0] [--random-seed 30] \
  [--agent-plugin vikingbot] \
  [--concurrency 4] [--checkpoint-interval 10] \
  [--resume <run-dir>] [--reuse-memory-from <run-dir>] \
  [--parallel-shards 1] [--parallel-workers 2] [--parallel-dry-run] \
  [--out-dir results] \
  --llm-api-key <KEY> \
  [--llm-base-url <URL>] [--llm-model <model>] [--llm-temperature 0.7] \
  [--memory-backend echomem] [--echomem-url http://127.0.0.1:8010] \
  [--echomem-auth-key <KEY>] [--echomem-log-access-key <KEY>] [--top-k 25] [--memory-budget-chars 6000] \
  [--tool-calling] \
  [--judge-model <model>] [--judge-api-key <KEY>] [--judge-base-url <URL>]
```

执行期间不要中断（除非故意留 `--resume` 续跑点）；并行分片完成后自动合并。

## 7. 交付

- 结果目录：`benchmarks/longmemeval/results/<timestamp>/`（第 3 节文件清单；
  并行运行多出 `parallel_manifest.json`、`parallel_summary.json`、`merged/`）。
- 向用户汇报：跑了哪些参数（题数、题型分布、并行度、插件、模型、并发、
  Judge）、总体 accuracy 与 per_type accuracy、失败/缺失题数及原因、token
  usage。
- 失败/缺失题恢复可用 `benchmarks/longmemeval/recovery.py` 生成重跑命令。

---
name: locomo
description: >
  启动 LoCoMo 评测（长对话记忆 benchmark）。当用户要求「跑 locomo 评测 /
  启动 locomo benchmark / 评测长对话记忆 / 生成 locomo 评测命令」时使用。
  本 skill 负责：列出 locomo 的全部可配置参数（含各 agent 插件变体与 Judge
  参数），向用户逐项确认（先解释参数含义、给出推荐/默认值），再生成并执行
  评测命令、交付结果。
---

# LoCoMo 评测（run_eval.py --dataset locomo）

被测系统：EchoMem / OpenViking 记忆后端 + LLM。入口 `python run_eval.py
--dataset locomo`（评测代码在 `benchmarks/locomo/`）。完整事实核对见
`benchmarks/locomo/docs/usage.md`，本节是执行用的操作手册。

**工作流（必做）**：先向用户**追问全部可配置参数**（第 4 节轮次清单），拿到
答案后再生成命令并执行；不要替用户拍板插件、题集、LLM 与 Judge 配置。

---

## 1. 评测流程

1. **记忆注入**：总是新开用户身份并执行 open → add_messages → commit →
   poll_commit；指定 `--resume` 时复用已有身份，跳过已完成的 session 仅注入
   缺失部分（逐 batch 增量落盘，中断可续）。
2. **逐题 QA**：默认使用历史 VikingBot prompt 和 `memory_search` /
   `memory_read_many` 多轮工具循环（`--tool-calling` / `--qa-profile`），仅检索
   不写入；也可用 `echomem_mcp` 插件走 MCP 工具调用（`--mcp-read-mode` 控制是否
   允许读取 `messages.jsonl`）。
3. **LLM Judge**：用 LLM 判定回答 CORRECT / WRONG。

## 2. 全部可配置参数

### 2.1 必填参数

| 参数 | 含义 |
|---|---|
| `--llm-api-key` | LLM API Key（也可通过环境变量 `LLM_API_KEY` 设置） |

### 2.2 数据集参数

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--dataset-path` | 内置 | 默认使用仓库中的 `benchmarks/locomo/data/locomo10.json` |
| `--sample` | `all` | 筛选 sample：`all` 或 sample_id |
| `--questions` | `0` | 限制 QA 数量（0=全部） |
| `--question-ids` | 空 | 逗号分隔的 question/native/sample ID，在 `--questions` 前应用 |
| `--session-mode` | `auto` | 会话组织方式：单 sample 按原始 session；多 sample 各自合并；也可显式选 `locomo`/`single` |
| `--max-sessions` | `0` | 每个 sample 最多导入多少个原始 session（0=全部） |

### 2.3 评测基础设施参数（benchmark 自身）

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--agent-plugin` | `vikingbot` | QA 阶段使用的 agent 插件名，见 `plugins/` 目录。切换后可用参数会变化 |
| `--qa-profile` | 自动 | 开启 `--tool-calling` 默认选 `vikingboat0411`；关闭默认选 `vikingboat0411-natural-no-tools`。显式指定时可覆盖 |
| `--checkpoint-interval` | `10` | 每完成 N 题写一次 `qa_results.checkpoint.csv`；0 表示关闭 |
| `--resume` | 空 | **统一续跑**：从先前运行目录或 CSV 恢复——复用身份，跳过已完成 import batch，恢复健康 QA 答案，复用一致 Judge 判定；只跑缺失/失败部分。summary/blackbox 指标对合并后的整轮累计 |
| `--reuse-memory-from` | 空 | 复用身份+已注入记忆、QA/Judge 全量重跑（指标只算本轮） |
| `--concurrency` | `4` | QA 并发数 |
| `--out-dir` | `results` | 结果目录 |

> 导入不完整（任一 import 记录失败/超时）时评测**立即终止**，QA/Judge 不启动；失败信息写入 `run.log` 与 `summary.json`（status=failed, phase=import）。

### 2.4 记忆后端参数（通过插件声明）

不同插件暴露的参数不同：支持多后端的插件（`vikingbot`、`echo_agent`）额外暴露
`--memory-backend`；不支持记忆注入的插件（`bare_llm`）不声明任何后端参数。

#### vikingbot 插件（默认）

支持 `--memory-backend` 选择 echomem 或 openviking（openviking 时
`--echomem-url` 指向 `http://127.0.0.1:19080`）。

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--memory-backend` | `echomem` | 记忆后端选择：`echomem` 或 `openviking` |
| `--echomem-url` | `http://127.0.0.1:8010` | 后端 HTTP 地址 |
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

#### echomem_mcp 插件

固定使用 EchoMem 后端，不暴露 `--memory-backend`。声明 QA 检索参数
（`--top-k` 默认覆盖为 25）。

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--echomem-url` | `http://127.0.0.1:8010` | EchoMem HTTP 地址 |
| `--echomem-auth-key` | 空 | X-Auth-Key（也可通过 `ECHOMEM_AUTH_KEY` 设置） |
| `--echomem-log-access-key` | 空 | 特权日志查询 key（`GET /api/logs` 的 X-Log-Access-Key；也可通过 `ECHOMEM_LOG_ACCESS_KEY` 设置）。不提供则 `backend_logs.json` 仅含 403 错误 |
| `--account` | `default` | 后端 account |
| `--user-id` | `default` | 后端 user_id |
| `--agent-id` | `default` | 后端 agent_id |
| `--workspace` | 空 | 后端 workspace 路径 |
| `--commit-timeout-s` | `0` | Commit 轮询超时（秒），0 表示无限等待 |
| `--commit-poll-interval-s` | `2.0` | Commit 轮询间隔（秒） |
| `--timeout-s` | `60.0` | 后端 HTTP 请求超时（秒） |
| `--max-retries` | `3` | 后端 HTTP 请求最大重试次数 |
| `--mcp-url` | `http://127.0.0.1:8001` | EchoMem MCP server URL |
| `--mcp-auth-key` | 空 | MCP server X-Auth-Key（留空时回退到 `--echomem-auth-key`） |
| `--mcp-max-iterations` | `50` | 每个问题的最大工具调用迭代次数 |
| `--tool-calling` | `False` | 是否启用 LLM 工具调用 |
| `--mcp-read-mode` | `allow` | 对话读取策略：`disabled`（从工具 schema 移除 `read`，不会把 transcript 请求转发给 MCP）/ `allow`（保留 `read`，是否读取由模型自行决定） |

#### echo_agent 插件

支持 `--memory-backend`。`--echomem-auth-key` 留空时自动从 echoagent 插件解析；
`--agent-id` 为 `default` 时自动设为 `echoagent`。不声明 QA 检索参数（QA 走
EchoAgent 管线）。

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--memory-backend` | `echomem` | 记忆后端选择：`echomem` 或 `openviking` |
| `--echomem-url` | `http://127.0.0.1:8010` | 后端 HTTP 地址 |
| `--echomem-auth-key` | 空 | 后端 X-Auth-Key（留空时自动解析） |
| `--echomem-log-access-key` | 空 | 特权日志查询 key（`GET /api/logs` 的 X-Log-Access-Key；也可通过 `ECHOMEM_LOG_ACCESS_KEY` 设置）。不提供则 `backend_logs.json` 仅含 403 错误 |
| `--account` | `default` | 后端 account |
| `--user-id` | `default` | 后端 user_id |
| `--agent-id` | `default` | 后端 agent_id（默认自动设为 `echoagent`） |
| `--workspace` | 空 | 后端 workspace 路径 |
| `--commit-timeout-s` | `0` | Commit 轮询超时（秒），0 表示无限等待 |
| `--commit-poll-interval-s` | `2.0` | Commit 轮询间隔（秒） |
| `--timeout-s` | `60.0` | 后端 HTTP 请求超时（秒） |
| `--max-retries` | `3` | 后端 HTTP 请求最大重试次数 |
| `--echoagent-url` | `http://127.0.0.1:31020` | EchoAgent 后端地址（环境变量 `ECHOAGENT_URL`） |
| `--username` | `test_user` | EchoAgent 登录用户名（环境变量 `ECHOAGENT_TEST_USERNAME`） |
| `--password` | 空 | EchoAgent 登录密码（环境变量 `ECHOAGENT_TEST_PASSWORD`） |
| `--memory-engine-endpoint` | `http://127.0.0.1:31030` | echoagent 插件地址（环境变量 `GLOBAL_MEMORY_ENGINE_ENDPOINT`） |

#### echoagent_live 插件

与 `echo_agent` 共享 `EchoAgentClient`，但不模拟打字、不触发 prefill 管线。
默认地址指向外网部署。后端/身份/Commit 参数同上（`--memory-backend`、
`--echomem-url`、`--echomem-auth-key`、`--account`、`--user-id`、`--agent-id`、
`--workspace`、`--commit-timeout-s`、`--commit-poll-interval-s`、`--timeout-s`、
`--max-retries`），另有：

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--echoagent-url` | `https://echo-agent.online` | EchoAgent 后端地址（环境变量 `ECHOAGENT_URL`） |
| `--echoagent-api-prefix` | `/api` | API 路径前缀（外网反代 `/api` → `/v1`；本地直连 `/v1`）（环境变量 `ECHOAGENT_API_PREFIX`） |
| `--username` | `test_user` | EchoAgent 登录用户名（环境变量 `ECHOAGENT_TEST_USERNAME`） |
| `--password` | 空 | EchoAgent 登录密码（环境变量 `ECHOAGENT_TEST_PASSWORD`） |
| `--memory-engine-endpoint` | `http://8.134.127.8:31030` | echoagent 插件地址（环境变量 `GLOBAL_MEMORY_ENGINE_ENDPOINT`） |

#### bare_llm 插件

不声明任何记忆后端参数，适用于无记忆系统基线测试。声明 QA 检索参数。

### 2.5 LLM 参数（通过插件声明）

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--llm-base-url` | 空 | LLM API base URL（也可通过 `LLM_BASE_URL` 设置） |
| `--llm-model` | `doubao-seed-2.0-pro` | LLM 模型名 |
| `--llm-api-key` | 空 | LLM API Key（也可通过 `LLM_API_KEY` 设置） |
| `--llm-temperature` | `0.7` | 回答模型生成温度；profile 可选择不显式发送 temperature |
| `--llm-max-tokens` | profile 决定 | 最大生成 token 数 |
| `--llm-timeout-s` | `120.0` | LLM 请求超时（秒） |
| `--llm-retries` | `3` | LLM 请求重试次数 |

### 2.6 QA 检索参数（通过插件声明）

由 `bare_llm`、`echomem_mcp`、`vikingbot` 声明；`echo_agent` 和
`echoagent_live` 不声明（QA 走 EchoAgent 管线）。

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--top-k` | profile 决定 | 检索返回条数（两个保留 profile 均为 25） |
| `--memory-budget-chars` | `6000` | 总记忆字符预算 |
| `--question-timeout-s` | profile 决定 | 单题总超时（两个保留 profile 均为 600 秒）；0 表示不增加总限制 |

### 2.7 VikingBot 插件参数

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--tool-search-limit` | profile 决定 | 每轮工具检索条数上限（两个保留 profile 均为 25） |
| `--initial-min-score` | profile 决定 | 初始检索最低分阈值（VikingBoat 0.4.11 profiles=0.1） |
| `--tool-min-score` | profile 决定 | 工具检索最低分阈值（VikingBoat 0.4.11 profiles=0.35） |
| `--tool-search-pool-multiplier` | profile 决定 | 工具检索候选池倍数（两个保留 profile 均为 1） |
| `--tool-set` | profile 决定 | 工具集名称（VikingBoat 0.4.11 profiles=`vikingbot_echo_native`） |
| `--tool-calling` | `False` | 是否向回答模型暴露 profile 的记忆工具；关闭后保留相同 prompt 和初始检索注入，只执行一次模型调用 |
| `--user-memory-budget-chars` | `4000` | user memory prompt 预算 |
| `--agent-memory-budget-chars` | `2000` | agent memory prompt 预算 |
| `--max-iterations` | `50` | 单题最大模型/tool-loop 迭代数 |
| `--vikingbot-workspace` | 内置 bootstrap | 默认使用 `plugins/vikingbot/bootstrap/` 中固定的原始 `SOUL.md` 和 `TOOLS.md` 快照 |

### 2.8 Judge 参数

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--judge-model` | 同 `--llm-model` | Judge LLM 模型名 |
| `--judge-api-key` | 同 `--llm-api-key` | Judge API Key |
| `--judge-base-url` | 同 `--llm-base-url` | Judge base URL |
| `--judge-concurrency` | `4` | Judge 并发数；结果仍按原始题目顺序写入 |
| `--judge-checkpoint-interval` | `10` | 每完成 N 题写一次 `judge_results.checkpoint.csv`；0 表示关闭 |

> 参数归属：benchmark 只定义数据集参数、Judge 参数和评测基础设施参数；LLM
> 参数、QA 检索参数、记忆后端参数和插件特有参数均由所选插件及其记忆后端
> 声明。切换 `--agent-plugin` 后可用参数会变化，以
> `python run_eval.py --dataset locomo --help` 为准。

## 3. 输出文件

每次评测在 `benchmarks/locomo/results/<timestamp>/` 下生成：
- `config.json` / `run.log` - 评测配置和完整日志
- `import_results.csv` / `memory_provenance.json` - 导入结果与数据集指纹/URI manifest
- `backend_logs.json` - 后端日志（需提供 `--echomem-log-access-key` / `ECHOMEM_LOG_ACCESS_KEY`，否则仅含 403 错误）
- `qa_results.csv`（+ `qa_results.checkpoint.csv`）- QA 结果，含 tool_call_count、iterations、qa_profile
- `qa_resume_manifest.json` - 恢复兼容性 hash（数据集、身份、模型、QA 参数、本地 prompt/tool/runtime contract）
- `judge_results.csv`（+ `judge_results.checkpoint.csv`）/ `judge_resume_manifest.json` - Judge 结果与判分口径指纹
- `diagnosis.json` / `retrieval_traces.jsonl` - 失败分类与检索 trace
- `agent_traces/*.json` - VikingBot 初始 prompt、逐轮模型消息、工具协议 hash、工具参数/结果、原始与清洗后答案
- `summary.json` - 汇总指标（memory_source、qa_profile、tool_call_total、avg_iterations、诊断摘要）
- `strict_blackbox_metrics.json` / `strict_blackbox_report.md` - 仅用外部可观测状态的指标与报告

## 4. 追问工作流（执行前必问）

用 `AskUserQuestion` 分轮追问，每轮 ≤4 题。**先解释参数含义再给选项**（含义见
第 2 节表格），推荐值标注在选项里；自由文本参数（API key、URL、路径）用 Other
输入。用户已明确给过某项时不要重复问；用户选择「全部默认」时跳过对应轮次。
**先问 R1（插件）再问后续轮次**——插件决定 2.4 节哪套后端参数适用。

### R1 插件与运行范围（4 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q1 用哪个 agent 插件？ | `--agent-plugin` | vikingbot（推荐，默认）/ echomem_mcp / echo_agent / echoagent_live / bare_llm（无记忆基线） |
| Q2 用哪个 QA profile？ | `--qa-profile` | 自动（推荐：开工具=vikingboat0411，关=vikingboat0411-natural-no-tools）/ 显式指定 |
| Q3 筛选哪个 sample？ | `--sample` | all（推荐）/ 具体 sample id（如 conv-30 / sample_0） |
| Q4 跑多少题？ | `--questions` | 0=全部（推荐）/ N 题快速验证 |

### R2 题集与数据源（4 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q5 指定题目 ID？ | `--question-ids` | 空（推荐）/ 逗号分隔 ID 列表 |
| Q6 数据集路径？ | `--dataset-path` | 内置 locomo10.json（推荐）/ 显式 JSON 路径 |
| Q7 会话组织方式？ | `--session-mode` | auto（推荐）/ locomo / single |
| Q8 每 sample 导入会话数上限？ | `--max-sessions` | 0=全部（推荐）/ N |

### R3 运行方式（2 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q9 运行方式？（三选一，互斥） | `--resume` / `--reuse-memory-from` | 全新运行（推荐）/ 续跑 `--resume <run-dir>`（复用身份，跳过已导入批次，恢复健康 QA 答案与一致 Judge 判定，只补缺失/失败部分）/ 复用旧记忆重跑 `--reuse-memory-from <run-dir>`（复用身份+已注入记忆，QA/Judge 全量重跑，指标只算本轮）。**续跑与复用旧记忆重跑只能选一个** |
| Q10 结果目录？ | `--out-dir` | results（推荐）/ 其他目录 |

### R4 LLM 配置（4 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q13 LLM base URL？ | `--llm-base-url` | 留空用环境变量 `LLM_BASE_URL`（推荐）/ 显式 URL |
| Q14 LLM 模型？ | `--llm-model` | doubao-seed-2.0-pro（推荐）/ 其他模型 |
| Q15 LLM API Key？ | `--llm-api-key` | 从环境变量 `LLM_API_KEY` 取（推荐）/ Other 输入 |
| Q16 生成细节用默认？ | `--llm-temperature` / `--llm-max-tokens` / `--llm-timeout-s` / `--llm-retries` | 用推荐默认 0.7 / profile 决定 / 120s / 3（推荐）/ Other 覆盖 |

### R5 记忆后端（4 题，按 R1 所选插件问对应参数）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q17 记忆后端？ | `--memory-backend`（vikingbot/echo_agent） | echomem（推荐）/ openviking |
| Q18 后端地址？ | `--echomem-url` | http://127.0.0.1:8010（推荐）/ 其他地址 |
| Q19 后端鉴权与日志 key？ | `--echomem-auth-key`（+ echomem_mcp 的 `--mcp-url` http://127.0.0.1:8001 / `--mcp-auth-key` 留空回退）；`--echomem-log-access-key`（env `ECHOMEM_LOG_ACCESS_KEY`，缺失则 `backend_logs.json` 仅含 403 错误） | 鉴权从 `ECHOMEM_AUTH_KEY`、日志 key 从 `ECHOMEM_LOG_ACCESS_KEY` 取（推荐）/ Other 输入 |
| Q20 身份与 workspace？ | `--account` / `--user-id` / `--agent-id` / `--workspace` | 全用 default（推荐）/ Other 覆盖 |

echo_agent / echoagent_live 插件另需确认：`--echoagent-url`（本地
http://127.0.0.1:31020 / 外网 https://echo-agent.online）、`--username`
test_user、`--password`、`--memory-engine-endpoint`（本地
http://127.0.0.1:31030 / 外网 http://8.134.127.8:31030）、echoagent_live 的
`--echoagent-api-prefix`（/api）。

### R6 后端行为细节（1 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q21 Commit/HTTP 行为用默认？ | `--commit-timeout-s` / `--commit-poll-interval-s` / `--timeout-s` / `--max-retries` | 用推荐默认 0 / 2.0s / 60s / 3（推荐）/ Other 覆盖 |

### R7 检索与工具（4 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q22 检索条数？ | `--top-k` | 25（推荐）/ 其他 |
| Q23 记忆字符预算？ | `--memory-budget-chars` | 6000（推荐）/ 其他 |
| Q24 开启工具调用？ | `--tool-calling`（echomem_mcp 另问 `--mcp-read-mode` disabled/allow） | 关（推荐，单次模型调用）/ 开（多轮工具检索） |
| Q25 检索分数/预算细节用默认？ | `--tool-search-limit` / `--initial-min-score` / `--tool-min-score` / `--tool-search-pool-multiplier` / `--user-memory-budget-chars` / `--agent-memory-budget-chars` / `--max-iterations` / `--tool-set` | 用推荐默认 25 / 0.1 / 0.35 / 1 / 4000 / 2000 / 50 / vikingbot_echo_native（推荐）/ Other 覆盖 |

### R8 Judge（4 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q26 Judge 模型？ | `--judge-model` | 同 `--llm-model`（推荐）/ 独立模型（如 gpt-4o） |
| Q27 Judge API Key？ | `--judge-api-key` | 同 `--llm-api-key`（推荐）/ Other 输入 |
| Q28 Judge base URL？ | `--judge-base-url` | 同 `--llm-base-url`（推荐）/ 显式 URL |
| Q29 Judge 并发与 checkpoint？ | `--judge-concurrency` / `--judge-checkpoint-interval` | 4 / 10（推荐）/ Other 覆盖 |

### R9 评测基础设施（3 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q30 QA 并发数？ | `--concurrency` | 4（推荐）/ 8 / 1 |
| Q31 checkpoint 间隔？ | `--checkpoint-interval` | 10（推荐）/ 0=关闭 / 其他 |
| Q32 运行标识确认？ | （汇总） | 展示最终命令，请用户确认后执行 / 修改 |

## 5. 前置检查

1. EchoMem MCP 场景：`.env` 中 `ECHOMEM_WORKSPACE` 指向 EchoMem workspace、
   `ECHOMEM_AUTO_START=1`；EchoMem `config.json` 的 `model.llm` 与
   `model.embedding` 必须用真实 provider（正式运行不要用 `fake-llm`/
   `fake-embedding`）。
2. 记忆后端存活：echomem `http://127.0.0.1:8010`（或用户指定的后端/MCP
   URL）可访问。
3. LLM 凭据齐备：`--llm-api-key` 或 `LLM_API_KEY` 已提供。
4. 后端日志 key：`--echomem-log-access-key` 或 `ECHOMEM_LOG_ACCESS_KEY` 已提供
   （否则结果目录的 `backend_logs.json` 只有 403 错误）。
5. 数据集：确认 `benchmarks/locomo/data/locomo10.json` 存在（不指定
   `--dataset-path` 时）。
6. 续跑场景：确认目标运行目录存在，`qa_resume_manifest.json` 中记录的身份、
   模型、QA 参数与本次一致。

## 6. 生成并执行

确认配置（第 4 节）后，在 `Memory-System-Eval-Harness/` 下组装命令并执行：

```bash
python run_eval.py --dataset locomo \
  [--dataset-path <path>] [--sample <sample>] [--questions <N>] \
  [--session-mode auto] [--max-sessions 0] \
  [--agent-plugin vikingbot] [--qa-profile <profile>] \
  [--concurrency 4] [--checkpoint-interval 10] \
  [--resume <run-dir>] [--reuse-memory-from <run-dir>] \
  [--out-dir results] \
  --llm-api-key <KEY> \
  [--llm-base-url <URL>] [--llm-model <model>] [--llm-temperature 0.7] \
  [--memory-backend echomem] [--echomem-url http://127.0.0.1:8010] \
  [--echomem-auth-key <KEY>] [--echomem-log-access-key <KEY>] \
  [--top-k 25] [--memory-budget-chars 6000] [--tool-calling] \
  [--judge-model <model>] [--judge-api-key <KEY>] [--judge-base-url <URL>] \
  [--judge-concurrency 4] [--judge-checkpoint-interval 10]
```

执行期间不要中断（除非故意留 `--resume` 续跑点）。

## 7. 交付

- 结果目录：`benchmarks/locomo/results/<timestamp>/`（第 3 节文件清单）。
- 向用户汇报：跑了哪些参数（插件、qa_profile、题数、模型、并发、Judge）、
  CORRECT/WRONG 判定与准确率、tool_call_total / avg_iterations、失败/缺失题数
  及原因（`diagnosis.json`）、strict black-box 指标要点。
- 独立分析：`benchmarks/locomo/blackbox.py`（重建 strict black-box 报告）、
  `benchmarks/locomo/compare.py`（对比两个运行）、`benchmarks/locomo/retry.py`
  （失败/缺失题重跑参数）、`scripts/validate_evidence.py`（检索证据校验）。

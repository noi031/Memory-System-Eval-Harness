---
name: dynamic
description: >
  启动动态评测（仿真 Agent+记忆系统 线上效果）。当用户要求「跑 dynamic 评测 /
  启动 dynamic benchmark / 动态评测 / 生成 dynamic 评测命令」时使用。
  本 skill 负责：列出 dynamic 的全部可配置参数（generate/replay 两种模式、
  各 agent 插件变体与质量评估参数），向用户逐项确认（先解释参数含义、给出推荐/
  默认值），再生成并执行评测命令、交付结果。
---

# 动态评测（run_eval.py --dataset dynamic）

被测系统：仿真用户（打字模拟）+ Agent（默认 `echo_agent` 插件 → EchoAgent 后端
31020 + 记忆引擎 31030 + EchoMem 8010）+ LLM。入口 `python run_eval.py
--dataset dynamic`（评测代码在 `dynamic/`，参数归属设计见
`dynamic/docs/设计意图.md`）。与 benchmark（locomo/hotpotqa/longmemeval）的差异：
**无独立 Judge**，质量评估复用 `--llm-*` 配置的 LLM；**串行执行**，不支持
`--concurrency`。

**工作流（必做）**：先向用户追问全部可配置参数（第 4 节轮次清单），拿到答案后
再生成命令并执行；不要替用户拍板模式、插件、LLM 与质量评估配置。

---

## 1. 评测流程

- **generate 模式**（默认，不传 `--dataset-path`）：场景生成 LLM
  （`--scenario-*`）生成背景记忆与用户提问；用户模拟器（`--user-simulator-config`）
  按 `--typing-speed-ms` / `--typing-jitter-ms` 仿真打字触发 prefill 管线；
  agent 端到端响应；质量评估 LLM（`--llm-*`）按 `--evaluator-config` 的维度
  逐条打分（各维度 0-100 加权，总分 0-100）。generate 模式概率性开新会话
  （`--new-session-ratio`）以测试跨会话记忆。
- **replay 模式**（传 `--dataset-path`）：外部数据集注入对话后逐题 QA，跨
  session 检索，同样走质量评估。查询来自数据集（jobs），不经过场景生成 LLM。
- 两种模式均串行执行；QA 走 EchoAgent 管线（默认插件 `echo_agent` 端到端，
  插件不直接做检索）；质量评估用 `--llm-*` 的 LLM 客户端（内部 temperature
  0.3、max_tokens 4096）。

## 2. 全部可配置参数

### 2.1 必填参数

| 参数 | 含义 |
|---|---|
| `--llm-api-key` | 质量评估 LLM API Key（也可通过环境变量 `LLM_API_KEY` 设置） |
| generate 模式另需 | `--scenario-api-key`（也可通过 `ECHOAGENT_TEST_SCENARIO_API_KEY` 设置） |

### 2.2 模式与数据集参数

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--dataset-path` | 空 | 数据集路径。指定则进入 replay 模式；不指定则 generate 模式 |
| `--sample` | `all` | 筛选 sample（replay 模式） |
| `--questions` | `0` | 限制 QA 数量（0=全部） |

### 2.3 评测器配置（两种模式共用）

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--evaluator-config` | `configs/evaluator_template.yaml` | 评测器配置 YAML（相对 run_eval.py）。定义评估维度（名称/最高分/描述），各维度分数和应为 100 |

### 2.4 Generate 模式参数

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--num-memories` | `5` | 生成的背景记忆数 |
| `--num-queries` | `10` | 生成的提问数 |
| `--new-session-ratio` | `0.3` | 概率性开新会话的比例（测跨会话记忆） |
| `--typing-speed-ms` | `200` | 打字模拟速度（每字符毫秒数） |
| `--typing-jitter-ms` | `20` | 打字模拟抖动 |
| `--user-simulator-config` | `configs/user_simulator_default.yaml` | 用户模拟器配置 YAML（相对 run_eval.py） |

### 2.5 场景生成 LLM（仅 generate 模式，用于生成背景记忆和 query）

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--scenario-model` | `deepseek-v4-flash-0731` | 场景生成模型名（也可通过 `ECHOAGENT_TEST_SCENARIO_MODEL` 设置） |
| `--scenario-base-url` | 空 | 场景生成 LLM base URL（也可通过 `ECHOAGENT_TEST_SCENARIO_BASE_URL` 设置） |
| `--scenario-api-key` | 空 | 场景生成 LLM API Key（也可通过 `ECHOAGENT_TEST_SCENARIO_API_KEY` 设置） |

### 2.6 插件与质量评估 LLM（通过插件声明）

默认插件 `echo_agent`；可用 `--agent-plugin` 切换（bare_llm / vikingbot /
echomem_mcp / echoagent_live 等，见 `plugins/`）。插件声明质量评估 LLM 参数
（`--llm-*`）、记忆后端参数与插件特有参数。

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--llm-base-url` | 空 | 质量评估 LLM base URL（也可通过 `LLM_BASE_URL` 设置） |
| `--llm-model` | `doubao-seed-2.0-pro` | 质量评估 LLM 模型名 |
| `--llm-api-key` | 空 | 质量评估 LLM API Key（也可通过 `LLM_API_KEY` 设置） |
| `--llm-temperature` | `0.7` | 质量评估温度（dynamic 内部 LLMClient 固定 0.3） |
| `--llm-max-tokens` | 插件决定 | 质量评估最大生成 token 数 |
| `--llm-timeout-s` | `120.0` | LLM 请求超时（秒） |
| `--llm-retries` | `3` | LLM 请求重试次数 |

### 2.7 记忆后端参数（echo_agent 插件，`add_memory_backend_args`）

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--memory-backend` | `echomem` | 记忆后端选择：`echomem` 或 `openviking`（openviking 时 `--echomem-url` 指向 `http://127.0.0.1:19080`） |
| `--echomem-url` | `http://127.0.0.1:8010` | 后端 HTTP 地址 |
| `--echomem-auth-key` | 空 | 后端 X-Auth-Key（留空时自动从 echoagent 插件解析） |
| `--echomem-log-access-key` | 空 | 特权日志查询 key（`GET /api/logs` 的 X-Log-Access-Key；也可通过 `ECHOMEM_LOG_ACCESS_KEY` 设置）。不提供则 `backend_logs.json` 仅含 403 错误 |
| `--account` | `default` | 后端 account |
| `--user-id` | `default` | 后端 user_id |
| `--agent-id` | `default` | 后端 agent_id（默认自动设为 `echoagent`） |
| `--workspace` | 空 | 后端 workspace 路径 |
| `--commit-timeout-s` | `0` | Commit 轮询超时（秒），0 表示无限等待 |
| `--commit-poll-interval-s` | `2.0` | Commit 轮询间隔（秒） |
| `--timeout-s` | `60.0` | 后端 HTTP 请求超时（秒） |
| `--max-retries` | `3` | 后端 HTTP 请求最大重试次数 |

### 2.8 echo_agent 插件特有参数

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--echoagent-url` | `http://127.0.0.1:31020` | EchoAgent 后端地址（环境变量 `ECHOAGENT_URL`） |
| `--username` | `test_user` | EchoAgent 登录用户名（环境变量 `ECHOAGENT_TEST_USERNAME`） |
| `--password` | 空 | EchoAgent 登录密码（环境变量 `ECHOAGENT_TEST_PASSWORD`） |
| `--memory-engine-endpoint` | `http://127.0.0.1:31030` | echoagent 插件地址（环境变量 `GLOBAL_MEMORY_ENGINE_ENDPOINT`） |

> 参数归属：dynamic 只定义模式/数据集、评测器、generate 模式、场景生成 LLM 和
> `--out-dir`；LLM 参数、记忆后端参数与插件特有参数由所选插件声明。切换
> `--agent-plugin` 后可用参数会变化，以
> `python run_eval.py --dataset dynamic --help` 为准。

## 3. 输出文件

每次评测在 `dynamic/results/<timestamp>/` 下生成：

- `config.json` / `run.log` - 评测配置和完整日志
- `dataset.json` - generate 模式生成的背景记忆/查询（replay 模式为数据集镜像）
- `dynamic_results.json` - 逐条查询的回答、记忆召回与 token 明细
- `quality_report.json` - 质量评估报告（按 evaluator 配置各维度 0-100 评分）
- `summary.json` - 汇总指标（avg_ttft、queries、errors 等）
- `backend_logs.json` - 后端日志（需提供 `--echomem-log-access-key` /
  `ECHOMEM_LOG_ACCESS_KEY`，否则仅含 403 错误）

## 4. 追问工作流（执行前必问）

用 `AskUserQuestion` 分轮追问，每轮 ≤4 题。**先解释参数含义再给选项**（含义见
第 2 节表格），推荐值标注在选项里；自由文本参数（API key、URL、路径）用 Other
输入。用户已明确给过某项时不要重复问；用户选择「全部默认」时跳过对应轮次。
**先问 R1（模式与插件）再问后续轮次**——模式决定 generate 参数是否适用，插件
决定 2.7 节哪套后端参数适用。

### R1 模式、插件与规模（4 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q1 评测模式？ | `--dataset-path` | generate（推荐，不传路径）/ replay（传数据集 JSON） |
| Q2 用哪个 agent 插件？ | `--agent-plugin` | echo_agent（推荐，默认，端到端 EchoAgent 管线）/ echoagent_live / vikingbot / echomem_mcp / bare_llm（无记忆基线） |
| Q3 生成规模？ | `--num-memories` / `--num-queries` | 5 / 10（推荐）/ N 快速验证 |
| Q4 跑多少题？ | `--questions` | 0=全部（推荐）/ N |

### R2 评测器与模拟器（2 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q5 评测器配置？ | `--evaluator-config` | 默认 `configs/evaluator_template.yaml`（推荐）/ 其他 YAML |
| Q6 用户模拟器配置？ | `--user-simulator-config` | 默认 `configs/user_simulator_default.yaml`（推荐）/ complex / 其他 YAML |

### R3 场景生成 LLM（generate 模式必问，3 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q7 场景 LLM base URL？ | `--scenario-base-url` | 留空用环境变量 `ECHOAGENT_TEST_SCENARIO_BASE_URL`（推荐）/ 显式 URL |
| Q8 场景 LLM 模型？ | `--scenario-model` | deepseek-v4-flash-0731（推荐）/ 其他 |
| Q9 场景 LLM API Key？ | `--scenario-api-key` | 从环境变量 `ECHOAGENT_TEST_SCENARIO_API_KEY` 取（推荐）/ Other 输入 |

### R4 质量评估 LLM（4 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q10 LLM base URL？ | `--llm-base-url` | 留空用环境变量 `LLM_BASE_URL`（推荐）/ 显式 URL |
| Q11 LLM 模型？ | `--llm-model` | doubao-seed-2.0-pro（推荐）/ 其他模型 |
| Q12 LLM API Key？ | `--llm-api-key` | 从环境变量 `LLM_API_KEY` 取（推荐）/ Other 输入 |
| Q13 生成细节用默认？ | `--llm-temperature` / `--llm-max-tokens` / `--llm-timeout-s` / `--llm-retries` | 用推荐默认 0.7 / 插件决定 / 120s / 3（推荐）/ Other 覆盖 |

### R5 记忆后端（4 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q14 记忆后端？ | `--memory-backend`（echo_agent/echoagent_live/vikingbot） | echomem（推荐）/ openviking |
| Q15 后端地址？ | `--echomem-url` | http://127.0.0.1:8010（推荐）/ 其他地址 |
| Q16 后端鉴权与日志 key？ | `--echomem-auth-key`（留空自动解析）；`--echomem-log-access-key`（env `ECHOMEM_LOG_ACCESS_KEY`，缺失则 `backend_logs.json` 仅含 403 错误） | 鉴权自动解析、日志 key 从 `ECHOMEM_LOG_ACCESS_KEY` 取（推荐）/ Other 输入 |
| Q17 身份与 workspace？ | `--account` / `--user-id` / `--agent-id` / `--workspace` | 全用 default（推荐）/ Other 覆盖 |

echo_agent 插件另需确认：`--echoagent-url`（本地 http://127.0.0.1:31020 /
外网 https://echo-agent.online）、`--username` test_user、`--password`、
`--memory-engine-endpoint`（本地 http://127.0.0.1:31030 / 外网
http://8.134.127.8:31030）。

### R6 后端行为细节（1 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q18 Commit/HTTP 行为用默认？ | `--commit-timeout-s` / `--commit-poll-interval-s` / `--timeout-s` / `--max-retries` | 用推荐默认 0 / 2.0s / 60s / 3（推荐）/ Other 覆盖 |

### R7 打字模拟细节（2 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q19 打字模拟用默认？ | `--typing-speed-ms` / `--typing-jitter-ms` | 200 / 20（推荐）/ Other 覆盖 |
| Q20 新会话比例？ | `--new-session-ratio` | 0.3（推荐）/ 0=不建新会话 / Other |

### R8 运行标识确认（1 题）

| 题 | 参数 | 推荐选项 |
|---|---|---|
| Q21 运行确认？ | `--out-dir`（默认 results，不支持并发） | 展示最终命令，请用户确认后执行 / 修改 |

## 5. 前置检查

1. 服务存活（默认插件 echo_agent）：EchoAgent `http://127.0.0.1:31020`、
   记忆引擎 `http://127.0.0.1:31030`、EchoMem `http://127.0.0.1:8010` 可访问
   （用其他插件时按对应后端检查）。
2. LLM 凭据齐备：`--llm-api-key` 或 `LLM_API_KEY` 已提供。
3. 后端日志 key：`--echomem-log-access-key` 或 `ECHOMEM_LOG_ACCESS_KEY` 已提供
   （否则结果目录的 `backend_logs.json` 只有 403 错误）。
4. generate 模式：`--scenario-base-url` / `--scenario-api-key` /
   `--scenario-model` 齐备；`configs/evaluator_template.yaml` 与
   `configs/user_simulator_default.yaml` 存在。
5. replay 模式：`--dataset-path` 指向的 JSON 文件存在。

## 6. 生成并执行

确认配置（第 4 节）后，在 `Memory-System-Eval-Harness/` 下组装命令并执行：

```bash
# generate 模式（默认，EchoMem 后端）
python run_eval.py --dataset dynamic \
  --agent-plugin echo_agent \
  [--num-memories 5] [--num-queries 10] [--new-session-ratio 0.3] \
  [--typing-speed-ms 200] [--typing-jitter-ms 20] \
  [--evaluator-config configs/evaluator_template.yaml] \
  [--user-simulator-config configs/user_simulator_default.yaml] \
  --llm-api-key <KEY> [--llm-base-url <URL>] [--llm-model <model>] \
  --scenario-base-url <URL> --scenario-api-key <KEY> \
  [--scenario-model <model>] \
  [--memory-backend echomem] [--echomem-url http://127.0.0.1:8010] \
  [--echomem-auth-key <KEY>] [--echomem-log-access-key <KEY>] \
  [--echoagent-url http://127.0.0.1:31020] [--username test_user] \
  [--password <PWD>] [--memory-engine-endpoint http://127.0.0.1:31030] \
  [--out-dir results]

# replay 模式
python run_eval.py --dataset dynamic \
  --agent-plugin echo_agent \
  --dataset-path <dataset.json> [--sample <sample>] [--questions <N>] \
  --llm-api-key <KEY> [--llm-base-url <URL>] [--llm-model <model>] \
  [--memory-backend echomem] [--echomem-url http://127.0.0.1:8010] \
  [--echomem-auth-key <KEY>] [--echomem-log-access-key <KEY>] \
  [--echoagent-url http://127.0.0.1:31020] [--username test_user] \
  [--password <PWD>] [--memory-engine-endpoint http://127.0.0.1:31030] \
  [--out-dir results]
```

执行期间不要中断（dynamic 串行执行，无 checkpoint 续跑机制）。

## 7. 交付

- 结果目录：`dynamic/results/<timestamp>/`（第 3 节文件清单）。
- 向用户汇报：跑了哪些参数（模式 generate/replay、插件、生成规模、模型、
  EchoAgent 配置）、质量评估各维度得分与总分、avg_ttft / queries / errors、
  失败/缺失查询数及原因（`dynamic_results.json`）、`backend_logs.json` 是否成功
  拉取。
- 独立分析：`dynamic/` 下无独立分析脚本；质量明细见 `quality_report.json` 与
  `dynamic_results.json`。

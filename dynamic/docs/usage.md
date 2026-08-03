# 动态评测

## 目标

通过 agent 插件评测不同 agent 的记忆系统效果。默认使用 `echo_agent` 插件, 仿真 EchoAgent + EchoMem 线上真实效果, 测试端到端记忆召回质量和 prefill (KV-cache 预热) 延迟。

可用插件:
- `echo_agent` (默认) -- EchoAgent + EchoMem 完整管线
- `bare_llm` -- 无记忆系统基线 (记忆拼入 system prompt)

每个插件通过 `add_arguments` classmethod 声明自己的 CLI 参数, `--help` 只显示当前插件相关参数。详见 `plugins/README.md`。

## 两种模式

推荐从仓库根目录使用统一入口。首次运行会自动创建 `.venv` 并安装依赖：

```bash
# 验证本地配置、EchoAgent 登录、credential 映射和 EchoMem 健康状态，
# 不生成场景、不注入数据、不执行 QA。
./eval.sh dynamic --check \
  --username test_user --password YOUR_PASSWORD
```

### Generate 模式 (默认)

LLM 生成场景: 生成背景记忆 -> 注入 EchoMem -> 逐轮生成 query -> 模拟打字 (prefetch tick) -> 发消息 -> 读 SSE 回复 -> 收集指标

```bash
./eval.sh dynamic \
  --echoagent-url http://127.0.0.1:31020 \
  --username test_user --password YOUR_PASSWORD \
  --num-memories 5 --num-queries 10 \
  --scenario-model deepseek-v4-flash \
  --llm-api-key YOUR_API_KEY
```

### Replay 模式

回放 generate 模式导出的数据集: 先注入背景记忆到 EchoMem -> 新 session QA (经 EchoAgent) -> 测试跨 session 召回

注入阶段不经 EchoAgent, 直接调 EchoMem 的 `open_session` -> `add_message` -> `commit_session` -> `poll_commit` 流程, 不触发 LLM 生成。
QA 阶段创建新 session 经 EchoAgent 发送 query, 测试完整管线 (含 prefill/TTFT)。

**跳过重复注入**: replay 模式从数据集读取 `inject_session_id`, 注入前先查 `GET /api/sessions/{id}/archives`。若该 session 已有 archive (说明之前已注入并 commit), 直接跳过注入阶段, 省去 EchoMem 重新抽取的时间。首次 replay 时正常注入, 后续 replay 同一数据集时自动跳过。

```bash
./eval.sh dynamic \
  --echoagent-url http://127.0.0.1:31020 \
  --username test_user --password YOUR_PASSWORD \
  --dataset /path/to/dataset.json \
  --questions 10 \
  --llm-api-key YOUR_API_KEY
```

## Agent 插件

评测流程通过 `AgentPlugin` 接口与被测 agent 交互, 不直接调用 agent 特定的 HTTP API。每个 agent 对应一个插件, 通过 `--agent-plugin` 选择。

### 可用插件

| 插件 | 说明 | 记忆注入 | 打字模拟 | 依赖 |
|---|---|---|---|---|
| `echo_agent` (默认) | EchoAgent + EchoMem 完整管线 | memory 插件直连 EchoMem | 支持 (prefetch tick/finalize) | EchoAgent 后端 + EchoMem |
| `bare_llm` | 无记忆系统基线 | 无 | 不支持 | 仅 LLM API |

### 插件接口

```python
class AgentPlugin(ABC):
    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None: ...
    def setup(self, config: dict) -> None: ...
    def create_session(self, title: str = "") -> str: ...
    def send_message(self, session_id: str, message: str, context_path: str = "/") -> AgentResponse: ...
    @property
    def supports_typing_simulation(self) -> bool: ...
    def simulate_typing(self, session_id, context_path, text, speed_ms, jitter_ms) -> TypingResult | None: ...
    def teardown(self) -> None: ...
```

- `add_arguments(parser)` (classmethod): 声明该插件所需的 CLI 参数。`run_eval.py` 根据 `--agent-plugin` 值动态调用此方法, `--help` 只显示当前插件相关参数。
- `setup(config)`: 初始化客户端。`config` 是所有 CLI 参数的扁平 dict。

`AgentResponse` 标准字段: `text`, `ttft_ms`, `prompt_tokens`, `cached_tokens`, `prefetch_committed`, `memory_items`, `error`。

### 自定义插件

1. 在 `plugins/` 下创建目录 (如 `plugins/my_agent/`), 包含 `__init__.py` (空即可)
2. 创建 `plugin.py`, 实现 `AgentPlugin` 子类
3. 实现 `add_arguments` classmethod, 声明该插件所需的 CLI 参数
4. 运行: `python dynamic/run_eval.py --agent-plugin my_agent ...`

`registry.py` 自动扫描 `plugins.<name>.plugin` 模块中 `AgentPlugin` 的子类, 无需手动注册。详见 `plugins/README.md`。

## 容错与回退 (echo_agent 插件)

所有 EchoAgent API 调用都有容错:
- **prefetch/tick** 返回 404 -> 跳过打字模拟, 直接发消息
- **prefetch/finalize** 返回 404 -> 跳过, 不影响消息发送
- **memory-engine/test** 失败 -> 忽略, 继续使用 session
- **stream_reply** 失败 -> 记录 error, 继续下一轮
- **send_message** seq 冲突 -> 自动重试 (3 次)

## 注入身份自动解析 (echo_agent 插件)

注入阶段直连 EchoMem, QA 阶段经 EchoAgent -> echoagent 插件 -> EchoMem。
两者必须使用相同的 `auth_key` 和 `agent_id`, 否则记忆存到一个身份下, 召回用另一个身份查, 永远找不到。

**自动解析逻辑** (两种模式共用):

1. 登录 EchoAgent 后获取用户信息
2. `agent_id` 默认设为 `echoagent` (与 echoagent 插件固定使用的值一致)
3. 若 `--echomem-auth-key` 未指定, 调用 echoagent 插件的 `credential` 接口 (`POST {memory_engine_endpoint}` body `{"mode":"credential","userId":"anonymous"}`)
   - EchoAgent 的 transform 模式不传 userId, echoagent 插件默认用 `anonymous` 解析 auth_key
   - 注入必须用同一个 auth_key, 否则记忆存到一个身份下, 召回用另一个身份查, 永远找不到
4. 解析到的 auth_key 同时用于注入和保存到 config.json

> 显式指定 `--echomem-auth-key` 时跳过自动解析, 使用用户指定的值。
> 显式指定 `--agent-id` (非 `default`) 时跳过自动设置。

## 指标

| 指标 | 说明 |
|---|---|
| `ttft_ms` | Time To First Token (首 token 延迟) |
| `cached_tokens` | KV-cache 命中的 token 数 (prefill 效果) |
| `prompt_tokens` | 总 prompt token 数 |
| `prefetch_committed` | prefill 是否成功 commit |
| `score` | 配置驱动评测器评分 (0-100, 按维度细分) |

## 评测器配置

两种模式都支持 `--evaluator-config`, 默认加载 `dynamic/configs/evaluator_template.yaml`。

配置文件定义:
- **dimensions**: 评估维度列表, 每个维度含 `name`/`display_name`/`max_score`/`description`
- **evaluate_prompt**: 评估 prompt 模板, 支持占位符 `{query}`/`{reply}`/`{ground_facts}`/`{recalled_memories}`/`{dimension_criteria}`

质量评估流程:
1. 对每条有回复的 query, 用配置的 prompt 模板渲染后调用 LLM
2. LLM 返回 JSON, 提取总分 (0-100) 和各维度分数 (钳制到 max_score 以内)
3. 汇总为 `quality_report.json`

未指定 `--evaluator-config` 且默认配置文件不存在时, 直接报错退出。

## 用户模拟器配置 (仅 Generate 模式)

Generate 模式支持 `--user-simulator-config`, 默认加载 `dynamic/configs/user_simulator_default.yaml`。

配置文件定义:
- **background_memories_prompt**: 背景事实生成 prompt 模板
- **persona_prompt**: 用户画像 prompt 模板, 生成下一轮 query

该配置传递给 `dynamic.simulator.MemoryDynamicEvaluator`, 影响场景生成和查询生成的行为。

动态 CLI 已按职责拆分：`client.py` 负责 EchoAgent HTTP/SSE/prefetch，
`simulator.py` 负责场景和 query 生成，`workflows.py` 统一 generate/replay
执行路径，`metrics.py` 负责质量评估，`artifacts.py` 负责输出文件。

## 参数说明

> **参数归属**: run_eval 只定义评测参数 (模式选择、评测器配置、Generate 模式参数、
> 场景生成 LLM、评测基础设施)。LLM 参数、记忆后端参数和插件特有参数均由所选
> 插件通过 `add_arguments()` 声明。切换 `--agent-plugin` 后可用参数会变化，使用
> `--help` 查看。参数归属设计详见 `benchmarks/doc/设计意图.md`。

### Agent 插件
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--agent-plugin` | `echo_agent` | agent 插件名称 (`echo_agent` / `bare_llm`) |

### 评测参数 (run_eval 自身)

#### 模式选择
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--dataset` | (空) | 数据集路径 (指定则进入 replay 模式; 不指定则 generate 模式) |
| `--sample` | `all` | 筛选 sample |
| `--questions` | `0` | Replay 模式: QA 数量上限 (0=全部) |

#### 评测器配置
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--evaluator-config` | `configs/evaluator_template.yaml` | 评测器配置 YAML, 路径相对于 `run_eval.py` 所在目录 |

#### Generate 模式参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--num-memories` | `5` | 生成的背景记忆数 |
| `--num-queries` | `10` | 生成的提问数 |
| `--new-session-ratio` | `0.3` | 新开 session 概率 |
| `--typing-speed-ms` | `200` | 打字速度 (毫秒/字符) |
| `--typing-jitter-ms` | `20` | 打字抖动 (毫秒) |
| `--user-simulator-config` | `configs/user_simulator_default.yaml` | 用户模拟器配置 YAML, 路径相对于 `run_eval.py` 所在目录 |

#### 场景生成 LLM (仅 generate 模式)
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--scenario-model` | `deepseek-v4-flash` | 场景生成 LLM 模型名 |
| `--scenario-base-url` | (空) | 场景生成 base URL |
| `--scenario-api-key` | (空) | 场景生成 API Key |

#### 评测基础设施
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--concurrency` | `4` | 并发数 |
| `--out-dir` | `results` | 结果目录 (默认 `dynamic/results/<timestamp>`) |
| `--allow-diagnostics` | false | 导入未完成或 provenance 不一致仍继续，仅限诊断 |

### LLM 参数 (通过插件声明)
LLM 凭据和参数，由所选插件通过 `add_llm_args()` 声明。质量评估 LLM 使用
`--llm-base-url` / `--llm-api-key` / `--llm-model`，与场景生成 LLM 互补。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--llm-base-url` | (空) | LLM API base URL (也可通过 `LLM_BASE_URL` 设置) |
| `--llm-model` | `doubao-seed-2.0-pro` | LLM 模型名 |
| `--llm-api-key` | (空) | LLM API Key (也可通过 `LLM_API_KEY` 设置) |
| `--llm-temperature` | `0.7` | 生成温度 |
| `--llm-max-tokens` | `2048` | 最大生成 token |
| `--llm-timeout-s` | `120.0` | LLM 请求超时 (秒) |
| `--llm-retries` | `3` | LLM 请求重试次数 |

> **base_url / api_key 互补**: scenario 和 LLM 的 base_url / api_key, 一个有值另一个为空时, 空的自动复制有值的。两者都设置了则各自使用。

### 记忆后端参数 (通过插件声明)
EchoMem/OpenViking 连接和身份管理参数，由所选插件通过 `add_memory_backend_args()` 声明。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--memory-backend` | `echomem` | 记忆后端: `echomem` 或 `openviking` |
| `--echomem-url` | `http://127.0.0.1:8010` | 记忆后端 HTTP 地址 |
| `--echomem-auth-key` | (空) | 后端 X-Auth-Key (echo_agent 留空时自动解析) |
| `--account` | `default` | 后端 account |
| `--user-id` | `default` | 后端 user_id |
| `--agent-id` | `default` | 后端 agent_id (echo_agent 默认自动设为 `echoagent`) |
| `--workspace` | (空) | 后端 workspace 路径 |
| `--echomem-log-dir` | (空) | 后端日志目录 (用于收集日志到评测结果) |
| `--commit-timeout-s` | `0` | 注入 commit 轮询超时 (秒)，0 表示无限等待 |
| `--commit-poll-interval-s` | `2.0` | 注入 commit 轮询间隔 (秒) |

### echo_agent 插件参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--echoagent-url` | `http://127.0.0.1:31020` | EchoAgent 后端地址 |
| `--username` | `test_user` | 登录用户名 |
| `--password` | (必填) | 登录密码 (也可通过 `ECHOAGENT_TEST_PASSWORD` 设置) |
| `--memory-engine-endpoint` | `http://127.0.0.1:31030` | 记忆引擎端点 |

> `bare_llm` 插件仅声明 LLM 参数 (`add_llm_args`) 和 QA 参数 (`add_qa_args`)，
> 不声明记忆后端参数，适用于无记忆系统基线测试。

## 输出文件

`dynamic/results/<timestamp>/` 下:
- `config.json`, `run.log` - 配置和日志
- `dataset.json` - v2 格式数据集 (含 theme/background_memories/dataset_queries/samples 对话 turns)
- `dynamic_results.json` - 完整结果 (含每轮指标)
- `dynamic_results.csv` - CSV 格式结果
- `quality_report.json` - v2 格式质量评估报告 (含 avg_quality_score/avg_dimension_scores/每条 result 的 quality_score/dimension_info/quality_reason/strengths/weaknesses 等)
- `summary.json` - 汇总指标
- `echomem_logs/` - EchoMem 日志

### dataset.json 格式 (参考 origin/v2 前端)

```jsonc
{
  "exported_at": "<ISO 8601>",
  "theme": "<string>",
  "inject_session_id": "<string>|null",
  "inject_user_id": "<string>|null",
  "background_memories": [
    { "id": "f1", "text": "...", "source_round": -1 }
  ],
  "dataset_queries": [
    { "query": "...", "ground_facts": ["f1"], "complexity": "medium", "reasoning": "..." }
  ],
  "samples": [
    {
      "sample_id": "dynamic_eval_<timestamp>",
      "conversation": {
        "<session_id>": {
          "session_id": "<string>",
          "is_new": true,
          "turns": [
            { "round_id": "r0", "speaker": "user", "text": "<query>", "ground_facts": ["f1"] },
            { "round_id": "r0", "speaker": "assistant", "text": "<reply>", "recalled_memories": [...], "quality_score": 85 }
          ]
        }
      },
      "metadata": { "total_rounds": 10, "new_session_count": 3, "avg_quality_score": 72.5 }
    }
  ]
}
```

### quality_report.json 格式 (参考 origin/v2 前端)

```jsonc
{
  "exported_at": "<ISO 8601>",
  "theme": "<string>",
  "total_queries": 10,
  "avg_ttft_ms": 450,
  "avg_cached_tokens": 1200,
  "new_session_count": 3,
  "summary": {
    "avg_quality_score": 72.5,
    "avg_dimension_scores": { "task_completion_score": 12.3, "fact_coverage_score": 10.5, ... },
    "total_recalled_memories": 45
  },
  "results": [
    {
      "round_id": "r0",
      "query": "...",
      "reply": "...",
      "session_id": "...",
      "is_new_session": true,
      "quality_score": 85,
      "dimension_scores": { "task_completion_score": 13, ... },
      "dimension_info": { "task_completion_score": { "display_name": "任务完成度", "max_score": 15 }, ... },
      "quality_reason": "回复正确使用了所有事实...",
      "strengths": ["事实覆盖完整"],
      "weaknesses": ["回复稍显冗长"],
      "hallucination_detected": false,
      "task_completed": true,
      "ttft_ms": 450,
      "cached_tokens": 1200,
      "prompt_tokens": 8000,
      "recalled_memories_count": 5,
      "ground_facts_count": 3,
      "relevant_memory": [...]
    }
  ]
}
```

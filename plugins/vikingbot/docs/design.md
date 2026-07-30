# vikingbot 插件

## 设计意图

VikingBot 是一个工具调用 agent，通过 OpenAI 兼容的 tool 定义搜索记忆系统，迭代调用工具直到找到答案，然后返回最终回复。它是 LoCoMo benchmark 的默认 agent 插件。

与 `bare_llm` 的区别：`bare_llm` 在 QA 前一次性把所有背景记忆拼入 system prompt；VikingBot 只在 system prompt 中注入初始记忆（可选），然后让 LLM 自主决定何时调用 `memory_search`、`memory_read_many` 等工具检索更多记忆。这使得 VikingBot 能评测 **agent 的主动检索能力**，而非被动注入。

与 `echo_agent` 的区别：`echo_agent` 走完整的 EchoAgent 后端管线（HTTP + SSE + prefill），VikingBot 直接在评测进程内运行工具调用循环，不依赖 EchoAgent 后端。

## 插件自管理依赖

VikingBot 在 `setup()` 中创建自己的 `LLMClient` 和 `MemoryClient`：

1. **memory_client**（由 `setup()` 根据 `--memory-backend` 创建）：VikingBot 用它构造工具函数，LLM 调用工具时实际调用 `memory_client.search()` / `fs_read()` 等。
2. **llm**（由 `setup()` 创建）：用于 LLM 推理。

`send_message` 是 `answer_one_vikingbot_question` 的薄包装，将 `QAResult` 转换为 `AgentResponse`。当记忆后端为 `none` 时，工具调用返回空结果，VikingBot 退化为无记忆的 LLM 对话。

## 工具调用循环

`answer_one_vikingbot_question`（由 `send_message` 调用）对每个 QA task 执行：

1. 从 workspace 加载 `SOUL.md`（系统提示词）和 `TOOLS.md`（工具描述）
2. 构造 OpenAI tool 定义，绑定 memory_client 方法
3. 发送 system + user 消息给 LLM
4. 若 LLM 返回 tool_calls，执行工具，把结果作为 tool 消息追加
5. 重复 3-4，直到 LLM 不再调用工具或达到 `--max-iterations`
6. 返回最终回复

## 配置参数

所有 VikingBot 参数默认为 `None`，由 LoCoMo profile（`benchmarks/locomo/profiles/`）通过 `apply_locomo_cli_defaults` 填充。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--vikingbot-workspace` | `plugins/vikingbot/bootstrap/` | SOUL.md 和 TOOLS.md 所在目录 |
| `--tool-search-limit` | profile | 工具检索返回条数上限 |
| `--user-memory-budget-chars` | profile | 注入 user 消息的记忆字符上限 |
| `--agent-memory-budget-chars` | profile | 注入 system 消息的记忆字符上限 |
| `--max-iterations` | profile | 工具调用循环最大轮数 |
| `--initial-min-score` | profile | 初始检索最低分数阈值 |
| `--tool-min-score` | profile | 工具检索最低分数阈值 |
| `--tool-search-pool-multiplier` | profile | 工具检索池倍数 |
| `--tool-set` | profile | 工具集: `search_read` / `vikingbot_native_safe` / `vikingbot_echo_native` |
| `--tools` / `--no-tools` | `True` | 是否暴露记忆工具给 LLM；`--no-tools` 退化为单轮对话 |

## 使用方式

```bash
# LoCoMo benchmark（默认使用 vikingbot）
python benchmarks/locomo/run_eval.py --llm-api-key YOUR_KEY

# 搭配 echomem 记忆后端
python benchmarks/locomo/run_eval.py --memory-backend echomem --echomem-url http://127.0.0.1:8010

# 搭配 openviking 记忆后端
python benchmarks/locomo/run_eval.py --memory-backend openviking
```

## 线程安全

`send_message()` 是无状态调用，支持并发。`setup()` 创建 `LLMClient` 和 `MemoryClient` 并完成身份隔离。

## 依赖

- LLM API（通过 `--llm-*` 参数配置）
- 可选：记忆后端（通过 `--memory-backend` 配置，choices: echomem/openviking）

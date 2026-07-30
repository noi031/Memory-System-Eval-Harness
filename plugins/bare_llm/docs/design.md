# bare_llm 插件

## 设计意图

纯 LLM 基线插件：只有 system prompt 和用户查询，不查询任何记忆。每次调用都是无状态的 -- 无工具调用循环、无 prefill、无会话管理、无记忆检索。插件在 `setup()` 中创建自己的 `LLMClient`，`send_message` 用固定 system prompt 发起单轮 LLM 调用。要评测记忆检索系统的能力，请使用 `echomem_mcp`（MCP 工具调用）或 `openviking_mcp`（MemoryClient 工具调用）插件。

## QA 流程

`send_message()` 组装 system + question prompt，调用 LLM 一次。无检索步骤，无记忆上下文注入。

## 配置参数

仅需 LLM 配置，不需要 EchoMem 或任何记忆后端：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `llm_base_url` / `llm_api_key` / `llm_model` | - | LLM 配置 |
| `llm_temperature` | `0.7` | 生成温度 |
| `llm_max_tokens` | `2048` | 最大生成 token |
| `llm_timeout_s` / `llm_retries` | `120` / `3` | 超时与重试 |

## 使用方式

```bash
# benchmark（纯 LLM 基线，无记忆）
python benchmarks/locomo/run_eval.py --agent-plugin bare_llm --llm-api-key YOUR_KEY

# 动态评测
python dynamic/run_eval.py --agent-plugin bare_llm --llm-api-key YOUR_KEY
```

> 要评测记忆检索能力，请改用 `echomem_mcp` 或 `openviking_mcp` 插件。

## 线程安全

`send_message()` 是无状态调用，支持并发。`supports_typing_simulation = False` -- 无 prefill、无打字模拟。

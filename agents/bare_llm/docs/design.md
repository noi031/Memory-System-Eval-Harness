# bare_llm 插件

## 设计意图

基线 LLM 插件：只有 system prompt 和记忆/用户查询组装。每次调用都是无状态的 -- 无工具调用循环、无 prefill、无会话管理。

记忆系统的指定由外部运行命令（`--memory-plugin`）决定。当搭配 `echomemory` 记忆插件时，benchmark 模式下的 `run_qa` 通过 memory client 做单轮检索增强，拼入 LLM prompt 后调用 LLM；与 `bare_llm` + `none` 对比，差值即为记忆检索系统的价值。

## QA 流程

### Benchmark 模式

`run_qa()` 使用本插件 `agents/bare_llm/qa.py` 中的单轮检索->组装->LLM 流程：通过 memory client 检索相关记忆，拼入 system+memory+question prompt，调用 LLM 一次。当 memory 插件为 `none` 时，检索返回空列表，等同于无记忆基线。

### Dynamic 模式

`send_message()` 发起无状态的 LLM 调用，使用固定 system prompt (`"You are a helpful assistant."`)，不注入任何背景记忆。记忆注入（如果需要）由 memory 插件完成，不经此插件。

## 配置参数

仅需 LLM 配置，不需要 EchoMem：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `llm_base_url` / `llm_api_key` / `llm_model` | - | LLM 配置 |
| `llm_temperature` | `0.7` | 生成温度 |
| `llm_max_tokens` | `2048` | 最大生成 token |
| `llm_timeout_s` / `llm_retries` | `120` / `3` | 超时与重试 |

## 使用方式

```bash
# benchmark (搭配 echomemory 记忆插件 -> 检索增强基线)
python benchmarks/locomo/run_eval.py --agent-plugin bare_llm --memory-plugin echomemory --llm-api-key YOUR_KEY

# benchmark (搭配 none 记忆插件 -> 无记忆基线)
python benchmarks/locomo/run_eval.py --agent-plugin bare_llm --memory-plugin none --llm-api-key YOUR_KEY

# 动态评测
python dynamic/run_eval.py --agent-plugin bare_llm --llm-api-key YOUR_KEY
```

## 线程安全

`send_message()` 是无状态调用，支持并发。`supports_typing_simulation = False` -- 无 prefill、无打字模拟。

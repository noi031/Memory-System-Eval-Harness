# bare_llm 插件

## 设计意图

无记忆系统的纯 LLM 基线插件。所有背景记忆在注入时存入内存，QA 时直接拼入 system prompt，不做任何检索。

它存在的意义是提供一个**下界基线**：衡量「无检索、全塞 prompt」的效果。与 `baseline_mem` 对比，两者的差值即为记忆检索系统的价值。如果 `bare_llm` 的得分接近 `baseline_mem`，说明该 benchmark 的题目不需要精准检索（答案在上下文中显而易见），benchmark 的区分度不足。

## 记忆注入

`inject_memories()` 不调用 EchoMem，而是把记忆列表存到内存 dict：

```python
self._memories_by_session[session_id] = [m for m in memories if m.get("text")]
```

按 `session_id` 隔离，支持 benchmark 的 `per_question` 模式（每题各自注入不同的 context）。

## QA 流程

`send_message()` 执行两步：

1. **拼接 prompt**：取出该 session 的记忆，用 `"\n"` 连接成纯文本，填入 system prompt 模板的 `{memories}` 占位符
2. **生成**：`llm.chat([{system}, {user}])` 返回回答

无检索、无 EchoMem 调用、无 commit。`memory_items` 始终为空列表。

system prompt 模板：
```
You are a helpful assistant. Answer the user's question based on the
background information provided below. If the answer is not in the
background information, say you don't know.

Background information:
- {memory_1}
- {memory_2}
...
```

## 配置参数

仅需 LLM 配置，不需要 EchoMem：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `llm_base_url` / `llm_api_key` / `llm_model` | — | LLM 配置 |
| `llm_temperature` | `0.7` | 生成温度 |
| `llm_max_tokens` | `2048` | 最大生成 token |
| `llm_timeout_s` / `llm_retries` | `120` / `3` | 超时与重试 |

## 使用方式

```bash
# benchmark
python benchmarks/locomo/run_eval.py --agent-plugin bare_llm --llm-api-key YOUR_KEY

# 动态评测
python dynamic/run_eval.py --agent-plugin bare_llm --llm-api-key YOUR_KEY
```

## 线程安全

`send_message()` 对 `_memories_by_session` 是只读访问，支持并发。注意 `inject_memories()` 写 dict，但 benchmark 流程中所有注入在 QA 之前串行完成，不存在竞态。

`supports_typing_simulation = False`——无 prefill、无打字模拟。

## 局限

- 记忆量大时会撑爆 context window（无截断、无检索筛选）
- 不区分记忆的时间/来源，全部平铺
- 无 EchoMem 依赖，因此无法评测记忆系统的检索质量

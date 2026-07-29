# baseline_mem 插件

## 设计意图

benchmark 评测的默认 agent 插件。封装「EchoMem 检索 + LLM 生成」这一最小可用流程，不含任何 agent 框架开销（无 EchoAgent、无 prefill、无 SSE 流式）。

它存在的意义是提供一个**可控基线**：在相同记忆系统和相同 LLM 的前提下，衡量「纯检索质量 + 纯生成质量」的合并效果。其他插件（`echo_agent`）在此之上叠加 agent 框架，两者的分差即为框架本身的开销/增益。

## 记忆注入

`inject_memories()` 直连 EchoMem，走完整的 `open -> add_messages -> commit -> poll_commit` 流程：

- 每条记忆的 `text`/`content` 字段作为消息内容写入
- `created_at`/`time` 作为时间戳，`role`/`role_id`/`speaker` 作为身份信息
- commit 后轮询直到 `completed`，超时或失败则抛 `RuntimeError`
- 若 `session_id` 已有 archive，跳过注入（replay 模式优化）

注入完成后，记忆在 EchoMem 中建好索引，后续 QA 阶段通过 `search` 检索。

## QA 流程

`send_message()` 执行三步：

1. **检索**：`echomem.search(question, top_k, session_id)` 返回 `SearchResult` 列表
2. **组装 prompt**：`build_qa_prompt()` 把检索结果拼成 `[{system}, {user}]` 格式，记忆按 `memory_budget_chars` 截断
3. **生成**：`llm.chat(messages)` 返回回答

返回 `AgentResponse`，`extra` 中携带 `retrieval_error` 和 `elapsed_s`。

## 配置参数

从 CLI 参数的扁平 dict 读取（`vars(args)`）：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `echomem_url` | `http://127.0.0.1:8010` | EchoMem 地址 |
| `echomem_auth_key` | (空) | X-Auth-Key |
| `account` / `user_id` / `agent_id` | `default` | EchoMem 身份 |
| `workspace` | (空) | EchoMem workspace 路径 |
| `llm_base_url` / `llm_api_key` / `llm_model` | — | LLM 配置 |
| `llm_temperature` | `0.7` | 生成温度 |
| `llm_max_tokens` | `2048` | 最大生成 token |
| `llm_timeout_s` / `llm_retries` | `120` / `3` | 超时与重试 |
| `top_k` | `10` | 检索条数 |
| `memory_budget_chars` | `8000` | 记忆注入 prompt 的字符上限 |
| `commit_timeout_s` | `0` | commit 轮询超时（0=无限） |
| `commit_poll_interval_s` | `2.0` | 轮询间隔 |

## 使用方式

```bash
# benchmark（默认插件，无需显式指定）
python benchmarks/locomo/run_eval.py --llm-api-key YOUR_KEY

# 显式指定
python benchmarks/locomo/run_eval.py --agent-plugin baseline_mem --llm-api-key YOUR_KEY

# 动态评测
python dynamic/run_eval.py --agent-plugin baseline_mem --llm-api-key YOUR_KEY
```

## 线程安全

`send_message()` 是无状态 HTTP 调用（EchoMem search + LLM chat），支持 `ThreadPoolExecutor` 并发。

`supports_typing_simulation = False`——不模拟打字，无 prefill。

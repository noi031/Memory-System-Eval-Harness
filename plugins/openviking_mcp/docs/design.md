# openviking_mcp Agent Plugin

## 设计意图

测试 LLM 通过工具调用检索记忆的能力。与 `bare_llm` 的单轮检索不同，`openviking_mcp`
让 LLM 自主决定何时检索、检索什么 -- 通过 OpenAI function-calling 调用记忆操作工具
（`memory_search`、`memory_read`、`memory_list`、`memory_glob`），模拟真实 agent 与记忆
系统的交互。

## 与 echomem_mcp 的区别

| 维度 | echomem_mcp | openviking_mcp |
|------|-------------|----------------|
| 传输协议 | MCP JSON-RPC over SSE | MemoryClient 协议 (HTTP REST) |
| 连接目标 | EchoMem MCP 服务 (端口 8001) | memory_client 指向的后端 |
| 使用 memory_client | 否（直接连接 MCP 服务） | 是（通过 memory_client 执行工具） |
| 后端 | 仅 EchoMem | 任意实现 MemoryClient 的后端 |

OpenViking 没有独立的 MCP 服务端点，其记忆操作通过 REST API（端口 19080）暴露。因此
`openviking_mcp` 不使用 MCP 协议，而是通过 `MemoryClient` 协议调用 `search`、`fs_read`、
`fs_list`、`fs_glob` 方法执行工具。当与 `openviking` 记忆插件配对时，工具调用命中
OpenViking 的 REST API；与 `echomemory` 配对时，命中 EchoMem 的 HTTP 端点。

## 工具定义

四个 OpenAI function-calling 工具：

1. **memory_search** -- 语义检索记忆，返回 URI + 分数 + 预览
2. **memory_read** -- 按 URI 读取完整内容（支持逗号分隔多 URI）
3. **memory_list** -- 列出 URI 前缀下的条目
4. **memory_glob** -- 按 glob 模式查找条目

## 工具调用循环

1. 构造 `[system, user]` 消息
2. 调用 `llm.chat_with_tools(messages, MEMORY_TOOLS)`
3. 若返回 `tool_calls`，逐个通过 `memory_client` 执行，追加 `tool` 角色消息
4. 循环直到无 `tool_calls` 或达到 `max_iterations`
5. 达到上限后强制无工具生成最终答案

## 并发

使用 `ThreadPoolExecutor` 并发处理多个问题。`memory_client` 实例在所有线程间共享 --
OpenVikingClient 使用 urllib（无持久连接），并发读取是安全的。不传 `agent_id` 给
`search()`，避免实例属性竞态。

## CLI 参数

- `--ov-max-iterations`：每题最大工具调用迭代数（默认 10）
- `--ov-search-limit`：每次 `memory_search` 返回的最大结果数（默认 8）

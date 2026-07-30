# echomem_mcp Agent Plugin

## 设计意图

测试 agent 通过 EchoMem MCP 协议检索记忆的能力。与 bare_llm 的单轮检索不同，echomem_mcp 让 LLM 自主决定何时检索、检索什么 -- 通过 OpenAI function-calling 调用 MCP 工具（`memory_query`、`read`、`list`、`glob`），模拟真实 agent 与记忆系统的交互。

## 工作原理

1. 每个问题创建一个 MCP 会话（`initialize` -> 获取 `Mcp-Session-Id`）
2. LLM 被赋予 4 个 MCP 工具作为 function-calling 定义
3. 工具调用循环：
   - LLM 生成 -> 若有 `tool_calls`，逐个通过 `McpClient.call_tool` 转发到 MCP 服务器
   - 将工具结果追加到对话
   - 继续循环，直到 LLM 不再调用工具（给出最终答案）或达到最大迭代数
4. 达到最大迭代时，强制 LLM 不再调用工具，直接回答

## 不使用 memory_client

`run_qa` 接收 `memory_client` 参数但不使用。记忆由 memory 插件（如 echomemory）在 QA 前注入 EchoMem 后端，MCP 服务器读取同一后端。auth key 回退到 `echomem_auth_key`（若 memory 插件是 echomemory）。

## 前置条件

- EchoMem MCP 服务必须运行（workspace config `mcp.enabled=true`，默认端口 8001）
- 若 memory 插件是 echomemory，auth key 自动复用

## 文件结构

| 文件 | 职责 |
|---|---|
| `plugin.py` | 插件入口：`add_arguments`、`setup`、`run_qa` |
| `mcp_client.py` | 最小 MCP 客户端：JSON-RPC over HTTP + SSE 解析 + 会话管理 |
| `runtime.py` | 工具调用循环 + 并发执行 |
| `docs/design.md` | 本文件 |

## CLI 参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--mcp-url` | `http://127.0.0.1:8001` | MCP 服务器地址 |
| `--mcp-auth-key` | `""` | X-Auth-Key，空则回退到 `--echomem-auth-key` |
| `--mcp-max-iterations` | `10` | 每个问题的最大工具调用迭代数 |

# Agent Plugins

Agent 插件让动态评测框架支持评测不同的 agent。评测流程只调用 `AgentPlugin` 接口, 不直接接触 agent 特定的 HTTP API。

## 目录结构

```
agents/
  __init__.py          # 导出 AgentPlugin, AgentResponse, TypingResult, load_agent_plugin, get_plugin_class
  base.py              # AgentPlugin ABC + AgentResponse / TypingResult
  registry.py          # load_agent_plugin(name, config) / get_plugin_class(name) -- 按名动态加载
  vikingbot/           # VikingBot 工具调用 agent (LoCoMo benchmark 默认)
    __init__.py
    plugin.py          # VikingBotPlugin
    prompting.py       # 系统提示词构建
    runtime.py         # 工具调用循环
    tools.py           # 工具定义
    bootstrap/         # 启动脚本
    docs/design.md     # 使用说明与设计意图
  echo_agent/          # EchoAgent + EchoMem 完整管线 (动态评测默认)
    __init__.py
    plugin.py          # EchoAgentPlugin
    client.py          # EchoAgentClient (HTTP 客户端)
    docs/design.md     # 使用说明与设计意图
  echomem_mcp/         # LLM 通过 EchoMem MCP 工具检索记忆
    __init__.py
    plugin.py          # EchoMemMCPPlugin
    mcp_client.py      # MCP 客户端 (JSON-RPC + SSE)
    runtime.py         # 工具调用循环 + 并发
    docs/design.md     # 设计意图
  openviking_mcp/      # LLM 通过 MemoryClient 工具检索记忆 (OpenViking 等)
    __init__.py
    plugin.py          # OpenVikingMCPPlugin
    runtime.py         # 工具调用循环 + 并发
    docs/design.md     # 设计意图
  bare_llm/            # 基线 LLM (system prompt + 记忆/查询组装)
    __init__.py
    plugin.py          # BareLLMPlugin
    qa.py              # 单轮检索->组装->LLM QA 流程
    docs/design.md     # 使用说明与设计意图
```

## 可用插件

| 插件 | 说明 | QA 策略 | 记忆注入方式 | 打字模拟 | 线程安全 | 依赖 |
|---|---|---|---|---|---|---|
| `vikingbot` | VikingBot 工具调用 agent (LoCoMo 默认) | 工具调用循环 (直连 EchoMem REST) | memory 插件直连 EchoMem | 不支持 | 是 | LLM API (+ EchoMem 如有记忆插件) |
| `echo_agent` | EchoAgent + EchoMem 完整管线 | 不支持 benchmark QA | memory 插件直连 EchoMem | 支持 (prefetch tick/finalize) | 否 (有 typing 实例状态) | EchoAgent 后端 + EchoMem |
| `echomem_mcp` | LLM 通过 MCP 工具检索记忆 | 工具调用循环 (MCP 协议) | memory 插件直连 EchoMem | 不支持 | 是 | LLM API + EchoMem MCP 服务 (8001) |
| `openviking_mcp` | LLM 通过 MemoryClient 工具检索记忆 | 工具调用循环 (MemoryClient 协议) | memory 插件直连后端 | 不支持 | 是 | LLM API + memory 插件 |
| `bare_llm` | 基线 LLM (system prompt + 记忆/查询组装) | 单轮检索->组装->LLM | memory 插件直连后端 | 不支持 | 是 | 仅 LLM API |

> **线程安全**: benchmark 评测使用 `ThreadPoolExecutor` 并发 QA。`vikingbot`、`echomem_mcp`、`openviking_mcp` 和 `bare_llm` 的调用是无状态的, 支持并发。`echo_agent` 有 typing 实例状态, benchmark 使用时需 `--concurrency 1`。

## 接口

```python
class AgentPlugin(ABC):
    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None
    def setup(self, config: dict) -> None
    def create_session(self, title: str = "") -> str
    def send_message(self, session_id: str, message: str, context_path: str = "/") -> AgentResponse
    @property
    def supports_typing_simulation(self) -> bool
    def simulate_typing(self, session_id, context_path, text, speed_ms, jitter_ms) -> TypingResult | None
    def teardown(self) -> None
```

- `add_arguments(parser)` (classmethod): 声明该插件所需的 CLI 参数 (如 `--echomem-url`、`--llm-api-key` 等)。`run_eval.py` 根据 `--agent-plugin` 值动态调用此方法, 因此 `--help` 只显示当前插件相关参数。默认空实现 (无额外参数)。
- `setup(config)`: 初始化客户端 (登录、解析凭据等)。`config` 是所有 CLI 参数的扁平 dict。
- `create_session(title)`: 创建 QA 会话。返回 session ID。
- `send_message(session_id, message, context_path)`: 发送消息, 返回 `AgentResponse`。
- `simulate_typing(...)`: 可选, 模拟打字触发 prefill。返回 `TypingResult` 或 `None`。
- `teardown()`: 释放资源。

> 记忆注入不在此接口上。注入由 memory 插件的 client 完成 (open_session / add_message / commit / poll_commit), 不经 agent 插件。

`AgentResponse` 字段: `text`, `ttft_ms`, `prompt_tokens`, `completion_tokens`, `cached_tokens`, `prefetch_committed`, `memory_items`, `error`, `extra`。

## 新增插件

1. 创建目录 `agents/<name>/`
2. 创建 `__init__.py` (空即可)
3. 创建 `plugin.py`, 实现 `AgentPlugin` 子类:

```python
from agents.base import AgentPlugin, AgentResponse

class MyAgentPlugin(AgentPlugin):
    def setup(self, config: dict) -> None:
        ...
    def create_session(self, title=""):
        ...
    def send_message(self, session_id, message, context_path="/"):
        ...
```

4. 实现 `add_arguments` classmethod, 声明该插件所需的 CLI 参数:

```python
import argparse

class MyAgentPlugin(AgentPlugin):
    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser):
        parser.add_argument("--my-agent-url", default="http://127.0.0.1:9000")
        parser.add_argument("--my-agent-key", default="")
```

`run_eval.py` 根据 `--agent-plugin` 值动态调用此方法注册参数, `--help` 只显示当前插件相关参数。可复用 `shared/eval_base` 中的 `add_echomem_args`、`add_llm_args` 等辅助函数批量添加通用参数组。

`registry.py` 自动扫描 `agents.<name>.plugin` 模块中 `AgentPlugin` 的子类, 无需手动注册。

5. 运行: `python dynamic/run_eval.py --agent-plugin <name> ...`

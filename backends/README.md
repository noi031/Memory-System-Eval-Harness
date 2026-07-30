# Memory Backends

记忆客户端定义目录，与 `shared/` 同级。`shared/` 放评测框架的通用工具，`backends/` 专门放记忆后端客户端的实现。

## 设计意图

一个记忆后端可以被多个 plugin 调用。当前 `echomem_mcp` 插件只实现 echomem 后端，`openviking_mcp` 只实现 openviking 后端，但 `echo_agent` 和 `vikingbot` 通过 `--memory-backend` 参数同时支持两者。把客户端从 plugin 目录提取到共享的 `backends/` 目录，避免了多个 plugin 各自持有一份相同代码的问题。

所有客户端实现 `MemoryClient` 协议（定义在 `backends/memory_types.py`），并继承 `BaseHTTPMemoryClient` 获得共享的 HTTP 传输层（重试、超时、commit 轮询模板方法）。不支持记忆注入的插件（如 `bare_llm`）使用 `NullMemoryClient`。

## 目录结构

```
backends/
  __init__.py
  echomem/
    __init__.py
    client.py            # EchoMemClient -- EchoMem REST API 客户端
    docs/设计意图.md      # 设计意图
  openviking/
    __init__.py
    client.py            # OpenVikingClient -- OpenViking REST API 客户端
    docs/设计意图.md      # 设计意图
```

## 客户端类

| 类 | 文件 | 说明 |
|---|---|---|
| `EchoMemClient` | `backends/echomem/client.py` | EchoMem HTTP API（端口 8010），含身份隔离、commit 轮询、search + fs 操作 |
| `OpenVikingClient` | `backends/openviking/client.py` | OpenViking HTTP API（端口 19080），含双域搜索（user + agent）、本地文件系统 fs 操作 |
| `NullMemoryClient` | `backends/memory_types.py` | 空实现，用于不支持记忆注入的插件 |

## 共享基础设施

| 模块 | 说明 |
|---|---|
| `backends/memory_types.py` | `MemoryClient` 协议、`BaseHTTPMemoryClient` 抽象基类、`SearchResult` / `CommitResult` 数据类、`NullMemoryClient` |
| `backends/memory_args.py` | `add_memory_backend_args()` -- 记忆后端连接 CLI 参数（`--echomem-url`、`--echomem-auth-key`、`--workspace` 等） |

## 插件如何使用后端

```python
from backends.echomem.client import EchoMemClient
from backends.openviking.client import OpenVikingClient
from backends.memory_types import NullMemoryClient

class MyPlugin(AgentPlugin):
    def setup(self, config: dict) -> None:
        if config["memory_backend"] == "echomem":
            self.memory_client = EchoMemClient(...)
        elif config["memory_backend"] == "openviking":
            self.memory_client = OpenVikingClient(...)
        else:
            self.memory_client = NullMemoryClient()
```

## 新增后端

1. 创建目录 `backends/<name>/`
2. 创建 `__init__.py`（空即可）
3. 创建 `client.py`，实现 `BaseHTTPMemoryClient` 子类，覆盖 `_headers()` 和 `_fetch_commit_status()` 等抽象方法
4. 实现 `search` / `fs_read` / `fs_list` / `fs_glob` 等检索方法
5. 在 `backends/memory_args.py` 的 `add_memory_backend_args()` 中添加对应后端的连接参数（如需）
6. 在使用该后端的 plugin 的 `setup()` 中实例化客户端

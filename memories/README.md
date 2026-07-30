# Memory Plugins

记忆插件让评测框架支持评测任意记忆系统。评测流程只调用 `MemoryPlugin` 的四个通用抽象方法（`add_arguments`、`setup`、`inject_memories`、`teardown`），不直接接触记忆系统特定的 API。身份隔离、客户端构造、会话生命周期等细节由每个插件内部实现。

## 目录结构

```
memories/
  __init__.py          # 导出 MemoryPlugin, load_memory_plugin 等
  base.py              # MemoryPlugin ABC + 结果类型 (CommitResult / SearchResult)
  registry.py          # load_memory_plugin(name, config) -- 按名动态加载
  <name>/              # 每个记忆系统一个子目录
    __init__.py
    plugin.py          # <Name>MemoryPlugin (实现 4 方法)
    docs/design.md     # 设计意图
```

## 可用插件

| 插件 | 说明 | 身份隔离 | 依赖 |
|---|---|---|---|
| `echomemory` | EchoMem 记忆系统 | 支持 | EchoMem 服务 |
| `openviking` | OpenViking 记忆系统 | 支持 | OpenViking 服务 |
| `none` | 无记忆系统基线 | 不支持 | 无 |

> 不指定 `--memory-plugin` 时默认为 `none`（无记忆系统）。benchmark 和 dynamic 的 `run_eval` 默认值可能不同，以各自脚本为准。

## 接口

评测平台只调用以下四个方法：

```python
class MemoryPlugin(ABC):
    descriptor: MemoryDescriptor

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None: ...

    @abstractmethod
    def setup(self, config: dict) -> None: ...

    @abstractmethod
    def inject_memories(self, memories: list[dict], session_id: str = "") -> str: ...

    def teardown(self) -> None: ...
```

- `add_arguments(parser)` (classmethod): 声明该插件所需的 CLI 参数（如 `--echomem-url`、`--echomem-auth-key` 等）。`run_eval.py` 根据 `--memory-plugin` 值动态调用此方法，因此 `--help` 只显示当前插件相关参数。默认空实现。
- `setup(config)`: 初始化插件状态。创建 `self.client`（公开属性，benchmark 代码通过 `memory_plugin.client` 访问记忆系统做检索和复杂数据导入），并完成身份隔离。`config` 是所有 CLI 参数的扁平 dict，包含 `benchmark_name` 和 `run_id` 用于身份标签。若 `benchmark_name`/`run_id` 缺失则跳过身份隔离（测试/验证场景）。
- `inject_memories(memories, session_id="")`: 注入背景记忆。打开会话（或复用 `session_id`），逐条添加消息，提交并轮询直到抽取完成。每个 dict 至少有 `"text"`，可选 `"time"` 作为 `created_at`。返回 `session_id`。dynamic 工作流直接调用此方法注入背景记忆。
- `teardown()`: 释放资源。默认 no-op。

身份隔离、客户端构造、会话生命周期等细节不进通用抽象，由每个插件在 `setup()` / `inject_memories()` / `teardown()` 中自行实现。`base.py` 提供了 `BaseHTTPMemoryClient`（urllib 传输 + commit 轮询模板）和 `MemoryClient` 协议供基于 HTTP 的插件复用，但不强制使用——非 HTTP 记忆系统可以完全自管客户端。

## 与 agent 插件的协作

评测系统同时配置 agent 插件和记忆插件，两者独立：

- **agent 插件** 负责对接被测 agent（发送查询、接收回复）
- **记忆插件** 负责对接记忆系统（注入背景记忆、检索相关记忆）

记忆注入通过 `MemoryPlugin.inject_memories()` 或 `memory_plugin.client` 完成，不经 agent 插件。agent 插件不参与记忆注入。记忆系统的指定由外部运行命令（`--memory-plugin`）决定，与 agent 插件无关。

## 新增插件

1. 创建目录 `memories/<name>/`
2. 创建 `__init__.py`（空即可）
3. 创建 `plugin.py`，实现 `MemoryPlugin` 子类：

```python
from memories.base import MemoryPlugin, MemoryDescriptor

class MyMemoryPlugin(MemoryPlugin):
    descriptor = MemoryDescriptor(id="my_memory", name="My Memory", ...)

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument("--my-memory-url", default="http://127.0.0.1:9000")
        parser.add_argument("--my-memory-key", default="")

    def setup(self, config: dict) -> None:
        # 构造客户端、身份隔离等
        self.client = ...
        # 身份隔离: 读 config["benchmark_name"] / config["run_id"]
        ...

    def inject_memories(self, memories: list[dict], session_id: str = "") -> str:
        # open/add/commit/poll (或等价的写入 + 等待流程)
        ...
        return session_id

    def teardown(self) -> None:
        # 释放资源 (如有)
        ...
```

4. 创建 `docs/design.md`，描述设计意图。
5. 运行: `python dynamic/run_eval.py --memory-plugin <name> ...`

`registry.py` 自动扫描 `memories.<name>.plugin` 模块中 `MemoryPlugin` 的子类，无需手动注册。

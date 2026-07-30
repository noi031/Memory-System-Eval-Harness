# none 记忆插件

## 设计意图

`none` 是默认记忆插件，用于评测不需要记忆系统的 agent。`setup()` 创建 `self.client`（`NullMemoryClient` 实例），`NullMemoryClient` 实现了 `MemoryClient` 协议的所有方法，全部返回空结果或无操作。这样 benchmark 代码中调用 `memory_client.search(...)` 等方法不需要条件分支即可正常工作。`inject_memories()` 返回空 session_id（无操作可注入）。`teardown()` 是 no-op。

记忆系统的指定由外部运行命令（`--memory-plugin`）决定，与 agent 插件无关。`bare_llm` 是只有 system prompt 和记忆/查询组装的基线 LLM，搭配 `none` 即为纯 LLM 基线。

## NullMemoryClient 行为

| 方法 | 返回值 |
|---|---|
| `search()` | `[]` |
| `fs_read()` | `""` |
| `fs_list()` / `fs_glob()` | `[]` |
| `open_session()` | `""` |
| `add_message()` | `{}` |
| `commit_session()` | `""` |
| `poll_commit()` | `CommitResult(status="completed")` |
| `has_archives()` | `False` |
| `health()` | `{"status": "ok"}` |

## 配置参数

无。`none` 插件不声明任何 CLI 参数。

## 使用方式

```bash
# 显式指定（也是默认值）
python benchmarks/locomo/run_eval.py --memory-plugin none

# 等效：不传 --memory-plugin，默认就是 none
python benchmarks/locomo/run_eval.py
```

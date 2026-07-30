# openviking 记忆插件

## 设计意图

OpenViking 是 OpenViking 记忆系统的评测适配插件。结构镜像 echomemory 插件--拥有自己的 CLI 参数，`setup()` 创建 `self.client`（`OpenVikingClient` 实例）并完成身份隔离。`inject_memories()` 实现 open/add/commit/poll 原语。`teardown()` 是 no-op。

OpenViking 与 EchoMem 共用大部分 HTTP 协议（session/commit/search），但 base URL 默认指向 19080 端口，且内容读取通过本地 workspace 文件系统（`viking://` URI）而非 HTTP。

## 身份隔离

`setup()` 读取 `config["benchmark_name"]` 和 `config["run_id"]`，调用 `self.client.provision_isolated_identity(label)` 生成唯一 account 名。OpenViking 没有服务端 tenant 删除，数据持久在 workspace 中。`--reuse-memory-account` 跳过隔离。

## 记忆注入

`inject_memories(memories, session_id="")` 实现 open/add/commit/poll。与 echomemory 不同，OpenViking 没有 `has_archives()` 检查，始终执行完整注入流程。

## 与 agent 插件的协作

与 echomemory 相同：openviking 只负责连接和身份管理，记忆注入通过 `inject_memories()` 或 `memory_plugin.client` 完成，不经 agent 插件。

## 配置参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--echomem-url` | `http://127.0.0.1:19080` | OpenViking HTTP 地址 |
| `--echomem-auth-key` | (空) | OpenViking API key |
| `--account` | `default` | 账号 |
| `--user-id` | `default` | 用户 ID |
| `--agent-id` | `default` | Agent ID |
| `--workspace` | (空) | OpenViking workspace 路径 |
| `--reuse-memory-account` | `False` | 复用已有身份 |
| `--keep-memory-account` | `False` | 保留临时身份 |

> 参数名与 echomemory 插件相同，因为两者共用 `add_memory_plugin_args` 的参数注册机制。切换插件时同名参数指向不同的后端。

## 使用方式

```bash
python benchmarks/locomo/run_eval.py \
  --memory-plugin openviking \
  --echomem-url http://127.0.0.1:19080
```

## 依赖

- OpenViking 服务（19080 端口）必须运行

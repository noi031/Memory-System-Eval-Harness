# echomemory 记忆插件

## 设计意图

EchoMemory 是 EchoMem 记忆系统的评测适配插件。它拥有所有与 EchoMem HTTP 服务相关的 CLI 参数，并管理评测身份的生命周期（隔离创建 / 运行后删除）。

`setup()` 创建 `self.client`（`EchoMemClient` 实例）并完成身份隔离。`inject_memories()` 是简单原语（open/add/commit/poll），供 dynamic 工作流直接调用。`teardown()` 是 no-op--身份清理通过 `cleanup_pending_identities()` + `atexit` handler + `eval.py` finally 块作为安全网处理。

## 身份隔离

评测运行时默认创建临时身份（tenant/user/agent），确保不同运行之间互不干扰：

1. `setup(config)` 读取 `config["benchmark_name"]` 和 `config["run_id"]`，调用 `self.client.provision_isolated_identity(label)` 创建临时 tenant
2. 运行结束后 `cleanup_pending_identities()`（atexit 注册 + `eval.py` finally 调用）自动删除临时身份
3. `--reuse-memory-account` 跳过隔离，复用已配置的身份
4. `--keep-memory-account` 保留临时身份用于诊断
5. 若 `benchmark_name`/`run_id` 缺失则跳过身份隔离（测试/验证场景）

## 记忆注入

`inject_memories(memories, session_id="")` 实现简单原语：
1. 打开会话（或复用传入的 `session_id`）
2. 逐条添加消息（`add_message`），`time` 字段传为 `created_at`
3. 提交会话（`commit_session`）
4. 轮询直到抽取完成（`poll_commit`），失败则抛 `RuntimeError`

dynamic 工作流直接调用此方法。benchmark 代码使用 `memory_plugin.client` 做更复杂的导入操作。

## 与 agent 插件的协作

EchoMemory 插件只负责记忆系统的 HTTP 连接和身份管理，不关心 agent 如何使用记忆：

- benchmark QA：agent 插件通过 `run_qa` 接收 `memory_client`，用它做检索
- dynamic 评测：记忆注入通过 `inject_memories()` 完成，QA 走 EchoAgent 完整管线

## 配置参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--echomem-url` | `http://127.0.0.1:8010` | EchoMem HTTP 地址 |
| `--echomem-auth-key` | (空) | X-Auth-Key（动态评测中由 echo_agent 自动解析） |
| `--account` | `default` | EchoMem 账号 |
| `--user-id` | `default` | EchoMem 用户 ID |
| `--agent-id` | `default` | EchoMem Agent ID |
| `--workspace` | (空) | EchoMem workspace 路径 |
| `--echomem-log-dir` | (空) | EchoMem 日志目录（运行后收集到结果目录） |
| `--reuse-memory-account` | `False` | 复用已有身份，不创建临时身份 |
| `--keep-memory-account` | `False` | 保留临时身份（用于诊断） |

## 使用方式

```bash
# benchmark + EchoMemory
python benchmarks/locomo/run_eval.py \
  --memory-plugin echomemory \
  --echomem-url http://127.0.0.1:8010 \
  --echomem-auth-key YOUR_KEY

# 动态评测（auth_key 自动从 EchoAgent 解析）
python dynamic/run_eval.py --username test_user --password YOUR_PASSWORD
```

## 依赖

- EchoMem 服务（8010 端口）必须运行

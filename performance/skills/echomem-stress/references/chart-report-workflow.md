# 便携图表报告工作流

## 1. 让任何编码助手执行同一实现

请助手读取检出套件中的 `performance/skills/echomem-stress/SKILL.md` 及其
引用文件。这不需要自动安装 skill。使用助手常规的终端与文件工具；Codex
浏览器、MCP、SSH 与特定 home 目录都不是前置条件。没有 shell 权限时，
返回命令并声明它们未被执行。

助手（例如 Kimi 或 Codex）编排测试。EchoMem 实际调用的真实模型在服务侧
单独配置。不要仅仅为了生成图表而替换该模型、使用假 provider 或修改其
凭据。

示例请求：

> 读取本仓库 performance/skills/echomem-stress/SKILL.md。用所选 profile
> 与用户指定的范围测试我的本地 EchoMem。开始前先展示命令，把失败留在分母
> 里，并用仓库渲染器生成 report.html。结论在前，用图表与精确值，说明
> 测试方法与模块级问题，折叠技术证据。不要用远端服务器，也不要编造缺失
> 的测量。

对既有结果，改为请求：

> 只从 RESULT_DIRECTORY 重新生成图表报告；不要重跑测试或调用模型。
> 选择与持久化证据匹配的渲染器。仅在提供 BASELINE_DIRECTORY 时与之对比，
> 并解释配置差异。

## 2. 运行前先解析路径与范围

从仓库根目录工作。使用其配置的 Python 环境（通常是 `.venv/bin/python`）；
不要嵌入其他开发者的绝对路径。记录 Git 修订，并在执行前验证所选模块的
`--help`。渲染器缺失时请求包含它的修订；不要静默切换分支或自己重造
HTML。

本地服务搭建、profile 与密钥 env 文件准备遵循
`performance/targets/echomem/README.md`。使用实际本地资源，不假设 4U8G
（host-default 与无容器降级均合法，对应指标按证据规则降级）。不要把
API key 或租户凭据复制进报告或可分发的归档。

范围与参数确认后（执行前必须先向请求者展示实际范围、参数与命令并获得
确认——耗额度/负载步骤不得免确认）：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run --help
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles "$PROFILE" --env-file "$ENV_FILE" \
  --metrics M1,M2,M3 --out-dir "$OUTPUT"
```

PROFILE、ENV_FILE 与 OUTPUT 必须先解析为用户实际路径并经确认；其中
OUTPUT 必须位于套件仓库 `performance/targets/echomem/results/` 之下（如
`$HARNESS/performance/targets/echomem/results/<run-name>/`），禁止写到
仓库根 `results/` 或临时目录。规范输出是
`$OUTPUT/report.html`。其图表遵循 `interactive-workflow.md` 的六指标展示
契约。不要把六指标证据喂给有界 Commit 渲染器。纯报告请求不要用
`--resume`：它可能执行未完成的工作负载。

## 3. 复现有界 Commit 诊断图表报告

这是用于 16/64 并发对比的渲染器，不是完整 M1-M6 报告。它只读既有数据，
不做任何模型调用：

```bash
.venv/bin/python -m scripts.build_commit_diagnostic_report --help
.venv/bin/python -m scripts.build_commit_diagnostic_report \
  --root "$OUTPUT" --out "$OUTPUT/report.html"
```

与更早诊断运行的可选对比：

```bash
.venv/bin/python -m scripts.build_commit_diagnostic_report \
  --root "$OUTPUT" --compare-root "$BASELINE" --out "$OUTPUT/report.html"
```

每个 root 必须恰好包含一个 `topology-N` 目录。必需实测输入是
`diagnostic.json` 与 `concurrency-topology.json`；采集到时也保留
`model-preflight.json`、`service-diagnostics.json`、
`deployment-parameters.json`、`container-evidence.json`、`manifest.json`、
`execution.json` 与 `resources.json`。缺失证据就是缺失，不是零。被阻塞的
preflight 产生不了实测并发图表。不要伪造文件去满足 schema。

当前有界诊断是特定四用户异构负载，不是通用部署命令。
`scripts/run_commit_diagnostic.py` 暴露 Python 函数，不是独立 CLI。不要
假装调用该文件就启动了测试。其报告当前含场景特定资源、超时与清理措辞：
交付前对照新 manifest 验证。若这些事实不符，先修渲染器消费实测元数据；
不要声称另一台用户的机器是原始 4U8G 服务器。

## 4. 规定阅读顺序与图表语义

1. 总体结论：完成、等待、被拒、劣化或未验证；实际并发、请求数与实测
   时长。
2. Commit 结果图：完成 / 服务端失败 / 观测超时 / 未知。单独展示
   HTTP202 接受。调用观测与唯一任务分母不同，都有时一并展示。
3. Search 质量图：健康命中 / 劣化命中 / 未命中 / 缺失证据。HTTP200 不够。
   已知时区分传输错误与 provider 错误。
4. 对比条：完成比例、Commit 操作 P95、Search P95、健康命中比例。展示
   单位与精确值并标注每个刻度。说明模型、输入长度、资源、配置、时长与
   负载规模的差异。其他变量变化时不要把结果变化单独归因于并发。
5. 按模块的平实解释：队列/准入、召回/路由、内存引擎、外部 provider 或
   证据收集器。区分观测到的错误与假设。没有观测到配额错误不等于配额
   无限。
6. 测试方法与折叠的技术证据。说明用户、会话、每会话并发、Commit 文本
   大小、Search 问题/事实设置与超时窗口。说明 P95 不是均值也不是纯模型
   时长。

图表必须保留精确表格/图例与文本状态，不能只靠颜色。分组单元格不是时间
线。禁止用端到端测量相减推导内部模块时间。禁止把既往运行值硬编码进新
报告。

## 5. 验证与交付

### 四拓扑短对比

用户明确要求短四拓扑对比时，用 `scripts.run_quick_topology_matrix`，不用
六指标口径。准备私有目录：真实目标 `config.json`、`tenants.json` 中 64
个独立开通身份，并用规范搭建指南加载 provider/租户环境变量。不要发布该
私有目录。

```bash
.venv/bin/python -m scripts.run_quick_topology_matrix \
  --out "$PRIVATE_RUN" --base-url "$ECHOMEM_BASE_URL"
.venv/bin/python -m scripts.build_quick_topology_report --root "$PRIVATE_RUN"
```

该预设含十个有界组：共享 Search 基线 at4、三种 Search 拓扑 at16/64、
异构 Search/Commit at4/16/64。Search 为 10s 预热加 45s 实测到达；混合组
为 60s 到达与每个 Commit 最长 90s 终态观测。混合组用独立 Search/Commit
worker、512/4096 字符输入与新 Commit 会话。记录每个用户的实际到达：
闭环到达率不保证相等。只在同规模组内比较 Jain。保留全部灌种可见性尝试、
未命中、劣化与运维失败。灌种质量失败限定时延结论，pending seed commit
阻塞负载以防污染。未解决的混合 Commit 积压即停止。`FINISHED` 表示已采集，
不是全部通过。

各组共享一个服务并按固定顺序运行。客户端超时后未清的服务器工作可能影响
下一组；不要把它描述成隔离 A/B 测试或最大容量证明。新渲染器消费
`quick-matrix.json` 与可选 `manifest.json`/`service-diagnostics.json`，
不是旧有界诊断 schema。纯报告渲染不做模型调用。

确认 report.html 存在、新生成并与输入运行和计数匹配。有浏览器工具时检查
桌面与移动宽度：无横向溢出、图表标签可读、证据披露可用。没有浏览器工具
时说明未做视觉验证；不要因此省略生成的工件。

有界渲染器改动后运行：

```bash
.venv/bin/python -m pytest tests/test_commit_diagnostic_dashboard.py \
  tests/test_commit_failure_evidence.py -q
```

返回绝对 HTML 路径（支持时给出可点击链接）、两三条实测发现与任何缺口。
静态 HTML 不需要开发服务器。测试失败仍要给出诚实报告；有数据却缺图表是
`REPORT_CONTRACT_GAP`，不是编造结果或手写临时 HTML 的许可。
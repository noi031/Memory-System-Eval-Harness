# Memory-System-Eval-Harness

记忆系统评测框架。全 CLI，无网页 UI。通过 Python 脚本完成数据集加载、记忆注入、
Agent 问答、Judge 评分和结果报告；同一套评测流程可在不同 agent 插件与记忆后端上
跑出可比、可复现、可审计的结果。

框架由三套评测体系组成，各自有独立的 skill 操作手册与设计文档：

| 子系统 | 目录 | 做什么 |
|---|---|---|
| 静态数据集评测 | `benchmarks/` | LoCoMo / HotpotQA / LongMemEval 数据集上的问答与评分 |
| 动态评测 | `dynamic/` | LLM 生成场景（generate）或回放对话（replay），端到端走 EchoAgent 管线 |
| 性能压测 | `performance/` | EchoMem M1-M6 黑盒观测压测与通用场景引擎 |

统一入口 `python run_eval.py --dataset <name>`，CLI 参数即配置。快速开始与全参数
配置不在此展开，见各子系统的 skill 操作手册；评测系统架构见各子系统的设计文档
（[子系统入口](#子系统入口)）。

## 设计目标

### 1. 支撑业界所有 agent 的评测

框架支撑业界所有 agent 的评测：被测 agent 通过统一插件协议接入，同一套评测流程
可在不同 agent 与记忆后端上跑出可比结果，确保结果可复现、可审计。

- **AgentPlugin 协议**：所有被测 agent 实现统一接口（`setup -> inject_memories ->
  create_session -> send_message -> getlog`），评测流程只调用接口，不接触 agent
  特定的 HTTP API。新增 agent 只需创建插件目录，无需改动框架。
- **双记忆后端**：`echomem` 和 `openviking` 两个后端实现同一 `MemoryClient`
  协议，通过 `--memory-backend` 切换，保证同一套评测可以在不同后端上跑出可比
  结果。
- **LLM Judge + provenance**：LoCoMo / LongMemEval 使用 LLM Judge 评分，
  HotpotQA 使用官方 F1/EM 指标。每次运行产出 `summary.json`、`config.json`、
  `memory_provenance.json` 和逐题 `agent_traces/*.json`，记录数据集 SHA-256、
  身份、prompt 来源和工具调用链，确保结果可复现、可审计。

### 2. 支撑内部需求

支撑压测、精度测试、定位算法改进点等内部场景。

- **动态评测**：`generate` 模式由 LLM 生成场景和提问，端到端走 EchoAgent 完整
  管线（含 prefill / TTFT）；`replay` 模式回放数据集对话，测试跨 session 召回。
- **多维质量评分**：动态评测通过 YAML 配置定义 10 个评分维度（任务完成度、
  事实覆盖、信息准确性等，满分 100），由 LLM 逐轮打分并输出诊断。
- **诊断与定位**：LoCoMo 产出 `diagnosis.json`、`retrieval_traces.jsonl` 和
  `retrieval_coverage`，标注失败题、可重试题和检索覆盖缺口。`blackbox.py` 和
  `compare.py` 支持黑盒指标导出和两次运行对比。
- **断点续跑**：QA 和 Judge 均支持 `--resume` 续跑，健康行不
  重复调用模型；`--checkpoint-interval` 定期落盘部分结果。

### 3. 简单易用 / AI 入口

直接 Python 调用，CLI 参数即配置，AI 友好。

- **直接启动**：`python run_eval.py --dataset <name>`，一条命令完成全流程，
  无需额外包装层。
- **CLI 参数驱动**：所有连接地址、模型配置、记忆后端、插件选择通过 CLI 参数
  传入，可写在 `.bat` / `.sh` 脚本中固化。环境变量作为默认值，CLI 参数覆盖。
- **Skill 交互式产品说明**：`benchmarks/skills/`（`locomo` / `hotpotqa` /
  `longmemeval`）、`dynamic/skills/`（`dynamic`）与 `performance/skills` 提供以
  skill 形式编写的交互式操作手册。AI 助手收到「跑评测 / 压测」请求时加载对应
  `SKILL.md`：
  先列出该数据集全部可配置参数，逐项向用户追问并解释参数含义、给出默认/推荐值
  （不替用户拍板），再生成评测命令并执行、交付结果。人类用户也可直接阅读
  `SKILL.md` 当作带参数说明的操作手册。
- **预检**：评测启动时自动验证数据集、记忆后端连通性和模型配置，通过后才进入
  正式评测流程。

### 4. 生产一致

确保评测结果与生产环境完全一致。

- **真实记忆注入**：评测通过 `inject_memories()` 将数据集对话写入真实 EchoMem
  或 OpenViking 后端（`open_session -> add_message -> commit -> poll`），不使用
  mock 或旁路。
- **身份隔离**：每次评测新开独立 tenant / user / agent 身份，`--resume` /
  `--reuse-memory-from` 时复用原有身份。身份信息（account / user_id / auth_key）
  记录在 resume manifest 中，auth key 仅掩码保存。
- **数据完整性校验**：LoCoMo 在 QA 前校验数据集 SHA-256 和实际 session
  manifest，session 数量不匹配时拒绝运行，防止复用 tenant 被污染。
- **生产管线**：动态评测的 QA 阶段走 EchoAgent 完整 HTTP 管线，含 prefill /
  typing simulation / TTFT 采集，与线上行为一致。

## 子系统入口

### benchmarks/ — 静态数据集评测

- **评测系统架构**：[benchmarks/doc/设计意图.md](benchmarks/doc/设计意图.md)
- **快速开始与全参数配置**（交互式 skill，逐数据集）：
  [locomo](benchmarks/skills/locomo/SKILL.md) ·
  [hotpotqa](benchmarks/skills/hotpotqa/SKILL.md) ·
  [longmemeval](benchmarks/skills/longmemeval/SKILL.md)
- 各数据集详细参数：[benchmarks/locomo/docs/usage.md](benchmarks/locomo/docs/usage.md) ·
  [benchmarks/hotpotqa/docs/usage.md](benchmarks/hotpotqa/docs/usage.md) ·
  [benchmarks/longmemeval/docs/usage.md](benchmarks/longmemeval/docs/usage.md)

### dynamic/ — 动态评测

- **评测系统架构**：[dynamic/docs/设计意图.md](dynamic/docs/设计意图.md)
- **快速开始与全参数配置**（交互式 skill）：[dynamic/skills/SKILL.md](dynamic/skills/SKILL.md)

### performance/ — EchoMem 性能压测

- **评测系统架构**：[performance/docs/设计意图.md](performance/docs/设计意图.md)
- EchoMem M1-M6 部署与六项运行手册：
  [performance/targets/echomem/README.md](performance/targets/echomem/README.md)；
  公开单文件指南：[docs/echomem-six-metric-local-guide.md](docs/echomem-six-metric-local-guide.md)
- **快速开始与全参数配置**（交互式 skill）：
  [performance/skills/echomem-stress/SKILL.md](performance/skills/echomem-stress/SKILL.md)
- 通用场景引擎（不针对具体系统的临时压测）：
  [performance/targets/general/README.md](performance/targets/general/README.md)

### 插件与记忆后端

- Agent 插件协议：[plugins/README.md](plugins/README.md)
- 记忆后端客户端：[backends/README.md](backends/README.md)

## 维护约定

benchmark（locomo / hotpotqa / longmemeval）、dynamic 与 performance 任一方的
评估指标或产物字段发生增删、含义改变时，必须同步更新 `scripts/memory-eval-improve`
skill 中对应的特有字段描述（`references/benchmark-specific-fields.md` 与
`references/analysis-dimensions.md`），避免分析报告基于过时的字段定义得出结论。

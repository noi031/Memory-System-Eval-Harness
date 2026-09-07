---
name: echomem-stress
description: >
  运行 EchoMem 正式压测（objective acceptance suite + 探针编排）。
  当用户要求「对 EchoMem 压测 / 跑压测场景 / 跑完所有场景 / 续跑中断的压测 /
  看验收指标 O1-O7 / 出压测报告」时使用。本 skill 负责：列出当前 echomem 的
  全部测试负载与探针场景，让用户选择执行哪些场景（或全部执行），
  并向用户确认所有可配置参数（给出推荐值/默认值），再执行并交付报告。
---

# EchoMem 正式压测（performance --target echomem）

被测系统：EchoMem 记忆服务（HTTP :8010）。入口
`python run.py --target echomem`（`performance/run.py` 顶层分发到
`targets/echomem/main.py`）。完整事实核对见
`performance/targets/echomem/README.md`，本节是执行用的操作手册。

**工作流（必做）**：先向用户**确认配置**（第 3 节问题清单），拿到答案后再
生成命令并执行；不要替用户拍板场景集合与时长。

---

## 1. 全部测试负载

### 1.1 负载场景（`scenes/`，7 个）

场景 = Python 模块，导出 `tasks`/`task`（可选 `schedule` 阶段注入、`report`
质量钩子）。写事务全程真实 HTTP：`/api/sessions/open` → `/messages` →
`/commit` → poll `/commits/{archive}`；检索走 `/api/retrieval/search`
（`X-Auth-Key` 鉴权）。

| 场景 | 文件 | 语义 |
|---|---|---|
| A 纯读基线 | `scene_a_pure_read.py` | 全部 worker 循环 `POST /api/retrieval/search`，query 从查询池轮询 |
| B 纯写注入 | `scene_b_write_injection.py` | 写事务 = open → add×N → commit submit → poll done（四阶段独立计时；末条带 anchor） |
| C 读写混合 | `scene_c_mixed.py` | 总 worker 按 `profile.load.mix` 权重拆成读者组 + 写者组并发 |
| K 固定速率容量 | `scene_capacity.py` | 读写混合持续打满，到达率按固定 rps 控制；baseline/mixed/commit-storm/search-storm/soak/capacity-N 共用 |
| S/H Commit barrier 风暴 | `scene_barrier.py` | 读负载全程打满；`barrier_at_s` 处一次性并发注入 `barrier_count` 个写事务，按租户分布（uniform/zipf/explicit），支持多波与公平性下限 |
| D 注入洪峰 | `scene_d_burst.py` | 持续读负载；中途启动 `burst_commits` 个写事务（max_workers=8，记录 `extra="burst"`） |
| D 多波变体 | `scene_burst_waves.py` | 洪峰多波注入（`burst_waves` 波，间隔 `burst_cooldown_s`） |

### 1.2 正式 case 矩阵（`orchestrator/suites.py`，三个目录）

case = 矩阵最小单元 `{label, scene, tenants, duration_s, search_rps,
commit_rpm, sessions_per_tenant, messages_per_session, barrier/burst 系列,
search_workers/commit_workers, read_only, ...}`。显式常量，不做动态叉乘。

| 目录 | 例数 | 内容 |
|---|---|---|
| `complete_cases()` | **26 例** | report(6) 12 例（并发 1/2 两组 × A/B/C8:1/C4:1/C1:1/D）+ PR421 场景集 14 例（baseline / mixed / commit-storm / commit-barrier / saturation / tenant-skew / capacity-16/2/4/8/32 / search-priority-blackbox / search-storm / soak=1800s） |
| `four_u8g_cases()` | **22 例** | 4U8G bounded 目录：report(6) 12 + baseline / mixed / commit-barrier / saturation / tenant-skew / search-priority-blackbox / capacity-2/4/8（强置 quick_commit_rpm=0）+ fairness-bounded |
| `QUICK_SCENARIOS` | **7 例** | quick 默认子集：baseline, fairness-bounded, search-priority-blackbox, saturation, capacity-2, capacity-4, capacity-8 |

**quick 收敛**（`--quick` 时）：`duration_s = min(原值, cap)`、barrier 计数
双 cap、`quick_commit_rpm` 覆盖 commit_rpm（capacity-* 为 0）、sessions 压到
1；默认跳过真实模型灌种（`--quick-include-seed` 打开）。

### 1.3 全部可执行 label（按需 `--scenarios` 过滤，保序）

`A@1 B@1 C8:1@1 C4:1@1 C1:1@1 D@1 A@2 B@2 C8:1@2 C4:1@2 C1:1@2 D@2
baseline mixed commit-storm commit-barrier saturation tenant-skew
capacity-16 capacity-2 capacity-4 capacity-8 capacity-32
search-priority-blackbox search-storm soak fairness-bounded`
（26 例全集 + fairness-bounded；4U8G 目录少 `commit-storm/capacity-16/capacity-32/search-storm/soak`，多 `fairness-bounded`）

---

## 2. 全部探针场景

### 2.1 配置化探针（`orchestrator/probes.py`，8 类编排）

探针段存在（dict）即启用（`missing_cases` / `concurrent_commit` /
`fault_isolation` 的 `enabled` 默认 true）；`blackbox_contract` 无显式配置段，
本轮有完成 Commit 且有租户配置时自动运行。

| 探针段 | 内容 | 本机可跑 |
|---|---|---|
| `capability_probe` | 可选契约探测（health/metrics/cursor 等；显式 404 才视为未实现） | ✅ |
| `blackbox_contract` | 复用压测记录的会话/归档复查公开 history/archive/status/cursor/指标（条件式自动运行） | ✅ |
| `missing_cases` | PR397 可观测性：写后读一致性、commit 状态机、冷/热检索延迟 | ✅ |
| `concurrent_commit` | 同会话 N 次并发 commit：接受/拒绝、operation/archive 唯一性、终态收敛 | ✅ |
| `fault_isolation` | 真实租户故障期间旁观租户 Search P95 劣化（≤20% PASS） | ❌ 需故障控制端点 |
| `commit_recovery` / `recovery` | 容器/进程 Commit 中途或 kill-9 后恢复、消息对账、幂等重放 | ❌ 需 Docker |
| `fault_plan` | 按 plan JSON 编排故障 + 恢复 + cursor 对账 case | ❌ 需故障注入 |
| `limit_failure_sweep` | 有界负载扫描（levels 档并发 wave + 恢复），数据采集 | ✅ |

探针统一契约：`run(ctx)` + `ctx.check` 四态断言（PASS / FAIL /
NOT_IMPLEMENTED / INCONCLUSIVE）；缺前置条件记 INCONCLUSIVE，不把环境问题
归因为 EchoMem 缺陷。

### 2.2 独立探针文件（`probes/`，不在配置段编排内）

`nxn_isolation`（N×N 租户隔离，由 `acceptance/features.py` 特性 11 依据
`op="isolation_probe"` 记录判定）/ `cursor_reconcile` / `disconnect_recovery` /
`auth_preflight` / `limit_failure` / `fault_injection` / `fault_suite`
（fault_plan 段使用）。

### 2.3 默认建议（本地单机）

配置 `capability_probe / missing_cases / concurrent_commit /
limit_failure_sweep`（`blackbox_contract` 在有完成 Commit 时自动运行）；
`fault_isolation / commit_recovery / fault_plan` 需要特殊环境，默认关闭。

---

## 3. 可配置参数（执行前必问）

### 3.1 问用户的 6 个问题（AskUserQuestion，先解释参数含义，再给选项）

**Q1 场景集合。**
含义：case 矩阵决定跑哪些负载组合（1.2 节）。quick 7 例是收敛子集
（分钟级冒烟）；完整 26 例是正式验收目录（含 soak 1800s，数小时）；
4U8G bounded 22 例是 4U8G 规格的 bounded 目录；也可自定义 label 列表
（第 1.3 节）。

**Q2 运行模式 `--quick`。**
含义：`--quick` 会把每个场景的时长压缩到 `--quick-duration-cap-s`（默认
30s）、barrier 计数双 cap、`commit_rpm` 覆盖（capacity-* 置 0）、灌种会话
压到 1——适合快速冒烟；不加则按矩阵原始时长和负载跑（正式验收）。

**Q3 输出目录与 `--resume` 续跑。**
含义：`--out-dir` 是结果根目录（case 目录为 `<out>/<profile>/<label>`）；
`--resume` 跳过已有 `summary.json` 的已完成场景、把历史 run 合并进报告、
从第一个未完成场景继续——中断过的压测选这个。**续跑必须与原 run 用相同
的 `--quick` 设置**（否则 22/26 例矩阵切换，label 对不上）。

**Q4 灌种体积 `seed_sessions` / `seed_messages`。**
含义：压测前先为每个租户写入真实会话作检索数据——每租户打开
`seed_sessions` 个会话、每会话写 `seed_messages` 条含锚词的消息并 commit，
检索 query 池由种子消息生成；负载参数（rps/barrier）与灌种体积无关。
不设置时按 case 矩阵最大值取（完整矩阵为 200 会话 × 10 消息/会话）；
本机冒烟建议 2 会话 × 5 消息。

**Q5 单 case 超时。**
含义：单个场景超过该时长未完成记 TIMEOUT 并继续下一个。
`--timeout-s`（默认 7200）用于完整矩阵；quick 模式用
`--quick-case-timeout-s`（默认 120）。

**Q6 探针开关。**
含义：探针在压测请求之后运行，做契约/一致性/故障类行为验证（第 2 节）。
本机默认只跑 4 个无需特殊环境的探针段；`fault_isolation`（需故障控制端点）、
`commit_recovery`（需 Docker 容器 kill/restart）、`fault_plan`（需故障注入）
默认关闭——用户要求这些时须先确认环境可用。

> 用户已明确选择过某项时不要重复问（例如已给 `--resume` 和结果目录）。

### 3.2 CLI 参数表（`python run.py --target echomem`）

| 参数 | 默认/推荐 | 说明 |
|---|---|---|
| `--profiles <json>` | 必填 | instance-profiles JSON；`${ENV:-default}` 占位展开 |
| `--profile <name>` | 全部 | 只跑指定 profile（如 `4U8G`） |
| `--out-dir <dir>` | 必填 | 输出根；suite 目录 `<out>/<profile 名>`，case 目录 `<suite>/<label>` |
| `--quick` | 关 | bounded smoke 矩阵（QUICK_SCENARIOS + 收敛） |
| `--scenarios a,b,c` | 全量 | 按 label 过滤并保序 |
| `--quick-duration-cap-s` | 30 | quick 场景时长 cap |
| `--quick-case-timeout-s` | 120 | quick 单 case 超时 |
| `--quick-barrier-count-cap` | 32 | quick 模式 barrier Commit 上限 |
| `--quick-include-seed` | 关 | quick 默认压 seed_sessions=1；打开保留灌种 |
| `--resume` | 关 | 跳过 case 目录已有 `summary.json` 的场景，历史 run 合并进报告，从第一个未完成场景继续 |
| `--timeout-s` | 7200 | 单 case 超时 |
| `--skip-run` / `--suite-path` | — | 只读已有 suite.json 重新生成报告，不发压测请求 |
| `--env-file <file>` | — | 加载 KEY=VALUE 环境文件供探针用 |

### 3.3 profile JSON 字段（instance-profiles.json，每 profile）

顶层：`name / base_url / tenant_config / preflight_config / auth_header
(X-Auth-Key) / allow_partial_tenants / metrics_enabled / quick_include_seed /
seed_sessions / seed_messages / prepare_command / resource_profile /
params(top_k, queries)`。探针段：`capability_probe / missing_cases /
concurrent_commit / commit_recovery / fault_isolation / fault_plan /
limit_failure_sweep / observability(lanes+fanout_engines) /
fairness_expectations`。完整示例：`profiles/instance-profiles.example.json`；
租户清单：`profiles/tenants.example.json`（`auth_key_env` 引用环境变量）。

---

## 4. 执行步骤

### 4.1 前置检查

1. EchoMem 8010 存活（`netstat -ano | findstr ":8010 "` 或 curl `/health`）。
2. 有 `instance-profiles.json`（用 `profiles/instance-profiles.example.json`
   起步；续跑时复用目标结果目录里已有的 `_config/instance-profiles.json`）。
3. 续跑场景：确认目标结果目录存在、`summary.json` 判定已完成 case 会被跳过。

### 4.2 生成并执行

确认配置（第 3.1 节）后生成 CLI 命令并执行：

```bash
python performance/run.py --target echomem \
    --profiles <out>/_config/instance-profiles.json \
    --out-dir <out> \
    [--profile 4U8G] [--quick] [--scenarios a,b,c] [--resume] \
    [--timeout-s 7200]
```

执行期间不要中断（除非故意留 `--resume` 续跑点）。

### 4.3 交付

- 报告：`<out>/objective-suite.html`（O1–O7 可视化）+ `objective-suite.json`。
- 每 case：`<suite>/<label>/summary.json` + `records.csv` +
  `commit_results.csv` / `search_results.csv`（对账证据）+ `metrics_samples.csv`
  （`metrics_enabled` 时）。
- 向用户汇报：跑了哪些 label、completed/submitted 计数、O1–O7 状态、失败/
  INCONCLUSIVE 项及原因。
- **内存泄漏诊断（自动）**：压测收尾（`_finalize_suite`）自动从各 case 的
  `metrics_samples.csv` 的 resident memory 序列算 RSS 斜率（MB/min），判定结果
  挂在 `manifest["memory_leak"]` 并渲染进报告「内存泄漏诊断」节。判定口径：
  斜率 ≥ `RSS_LEAK_SLOPE_MB_PER_MIN`（5 MB/min）判 FAIL；观测窗口
  < `MIN_LEAK_WINDOW_S`（600s）判 INCONCLUSIVE（短窗口斜率受预热/GC 主导，
  不判泄漏）；全部 pass 才 PASS。诊断逻辑在通用模块
  `performance/memory_leak.py`，任何 target 的 suite 收尾都会自动获得。
  报告生成可用 `python -m performance.targets.echomem.rebuild_report
  --results-dir <results>` 随时从磁盘重建（含诊断）。

---

## 5. 验收指标（判定口径）

- **O1–O7**（`acceptance/objectives.py`）：容量阶梯 / 多规格调度 / 单租户故障
  P95 劣化 ≤20% / 双维 Jain ≥0.9 / Commit 洪泛时 Search P95 ≤5s / kill-9
  恢复后 100% 重放不丢序 / 五 lane 四元组可观测。状态 PASS / FAIL /
  INCONCLUSIVE。
- **PR421 门禁**（`acceptance/evaluate.py`）：B7 lane/fan-out 覆盖；Search
  成功率 ≥0.99（有洪泛样本时）；report(6) 检索须有确定性锚词断言；饱和时
  显式 429/503 + Retry-After + reason_code。
- **延迟参考阈值**：Search P95 ≤ 2.5s、Commit 端到端 ≤ 10s（`runner.py`
  摘要 parameters）。
- 调度 7 检查、13 特性见 README §4；全部基于真实压测证据，缺失记
  INCONCLUSIVE，不静默放行。

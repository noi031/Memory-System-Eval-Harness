# 黑盒指标统计使用指南

本文档面向评测平台使用者，说明如何从 QA 结果 CSV 生成严格黑盒指标报告，以及各字段的来源和统计边界。

## 1. 统计原则

严格黑盒指标只使用评测平台在 API 边界实际记录的数据：

- Judge 结果；
- QA 各阶段最终状态；
- 评测平台记录的墙钟时间；
- 回答模型 API 返回的 `usage`；
- 后端导入摘要直接返回的状态和消息数量。

以下数据不做估算：

- 记忆系统内部抽取、总结、Embedding 或重排 Token；
- 缺少后台完成事件时的初始记忆导入时间；
- 模型网关或记忆系统内部未暴露的重试和耗时。

缺少权威观测字段时，报告应显示 `N/A`，不能通过字符数、文本长度或经验公式换算。

## 2. 输入文件

### QA 结果 CSV

QA 结果 CSV 是必需输入。常见文件名：

```text
echomemory_memory_qa_results.csv
openviking_memory_qa_results.csv
```

不同指标需要以下字段：

| 指标 | CSV 字段 |
| --- | --- |
| 准确率、分类准确率 | `result`, `category` |
| QA 请求成功率、最终失败率 | `retrieval_status`, `answer_status`, `model_status`, `health_status` |
| 空召回率 | `retrieval_count`，缺失时使用 `memory_hit_count` |
| 外部可见模型重试率 | `model_retry_count` |
| 端到端 QA 时延 | `end_to_end_ms` |
| 记忆检索时延 | `retrieval_latency_ms` |
| QA 侧编排注入时延 | `injection_total_ms` |
| 回答模型时延 | `llm_total_ms` |
| 回答模型 Token | `answer_prompt_tokens`, `answer_completion_tokens`, `answer_total_tokens` |
| 每个正确答案 Token | `answer_total_tokens`, `result` |

### 导入摘要 JSON

导入摘要是可选输入，用于统计：

| 指标 | JSON 字段 |
| --- | --- |
| 消息提交率 | `submitted_messages`, `expected_messages` |
| 记忆导入状态 | `status` |

没有导入摘要时，这两个指标显示 `N/A`。

## 3. 生成报告

在仓库根目录执行：

```bash
python3 scripts/generate_html_report.py \
  /path/to/results.csv \
  --import-summary /path/to/import_summary.json \
  --output /path/to/blackbox_report.html \
  --name "EchoMemory 黑盒评测报告"
```

没有导入摘要时：

```bash
python3 scripts/generate_html_report.py \
  /path/to/results.csv \
  --output /path/to/blackbox_report.html
```

可用参数：

| 参数 | 必需 | 说明 |
| --- | --- | --- |
| `csv_path` | 是 | QA 结果 CSV |
| `--import-summary` | 否 | 后端导入摘要 JSON |
| `--output`, `-o` | 否 | 输出 HTML 路径 |
| `--name`, `-n` | 否 | 报告标题 |

不指定 `--output` 时，默认在 CSV 同目录生成：

```text
<CSV 文件名>_report.html
```

## 4. 指标计算

### 准确率

```text
CORRECT / (CORRECT + WRONG)
```

`UNSCORED` 不进入分母。分类准确率按 `category` 分组后使用相同公式。

### QA 请求成功率

```text
retrieval_status、answer_status、model_status、health_status 全部为 ok 的题数
/
四个状态字段均完整的题数
```

### 空召回率

```text
retrieval_count = 0 的题数
/
存在召回计数字段的题数
```

非空召回不等于召回了正确证据。

### 最终失败率

```text
未满足 QA 请求成功条件的完整状态行数
/
四个状态字段均完整的题数
```

### 外部可见模型重试率

```text
model_retry_count > 0 的题数
/
存在 model_retry_count 的题数
```

模型网关内部未暴露的重试不计入。

### 时延

时延指标从对应的逐题毫秒字段计算：

- 平均值；
- P50；
- P95；
- P99；
- 最大值。

P50 是中位数，表示约 50% 的题目耗时不超过该值。当前实现使用线性插值计算百分位，因此偶数样本时结果可能不是某一道题的原始耗时。

端到端 QA 时延从单题开始处理计时，覆盖检索、记忆格式化、消息构造、回答模型调用和可见的回答修正流程。它不包含初始背景记忆导入、Judge 判分和整场报告生成。

### 回答模型 Token

单次模型调用读取回答模型 API 返回的：

```text
usage.prompt_tokens
usage.completion_tokens
usage.total_tokens
```

兼容 `input_tokens` 和 `output_tokens`。工具调用循环中多次成功返回的模型 usage 会累加，答案 refinement 的 usage 也会加入单题 Token。

整次运行：

```text
回答总 Token = 所有题 answer_total_tokens 之和
```

每个正确答案 Token：

```text
回答总 Token / Judge 判为 CORRECT 的题数
```

失败、超时或限流请求如果没有返回 usage，其实际消耗无法黑盒统计。使用结果前应确认模型服务确实返回了权威 `usage`；不能把缺失 usage 当作真实零消耗。

### 消息提交率

```text
submitted_messages / expected_messages
```

消息提交成功不表示后台记忆抽取和索引已经完成。

### 不可黑盒计算项

以下指标当前必须显示 `N/A`：

- 内部记忆注入 Token：黑盒 API 没有返回完整、权威的内部 LLM 和 Embedding usage；
- 初始记忆导入时间：没有可靠的后台完成事件，无法确定结束时刻。

## 5. 验证统计链路

修改指标代码后运行：

```bash
python3 scripts/smoke_blackbox_report.py
python3 scripts/smoke-strict-blackbox-api.py
node scripts/smoke-strict-blackbox-ui.mjs
npm run check
```

前三个命令是开发校验脚本，不是正式报告生成入口。

## 6. 对外说明模板

可以在评测结果中附上以下说明：

> 本报告只统计评测平台在 API 边界实际观测到的 Judge 结果、最终状态、墙钟时间和模型 usage。缺失权威字段的指标显示 N/A，不使用字符数换算或其他估算。模型 Token 仅包含回答模型接口实际返回并成功记录的 usage，不包含记忆系统内部 Token 或未返回 usage 的失败请求。


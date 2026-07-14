#!/usr/bin/env node
import { renderStrictBlackboxMetrics } from "../src/render/strict-blackbox.js";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const stats = {avg: 10, p50: 9, p95: 15, p99: 18, max: 20, sum: 100};
const definitions = Array.from({length: 20}, (_, index) => ({
  name: `指标 ${index + 1}`,
  kind: index > 12 ? "不可黑盒计算" : "严格计算",
  formula: index > 12 ? "N/A" : "observed / total",
  source: "CSV",
  meaning: "含义",
  boundary: "边界",
}));
const html = renderStrictBlackboxMetrics({
  row_count: 2,
  artifact_path: "/tmp/run/strict_blackbox_metrics.json",
  report_path: "/tmp/run/strict_blackbox_report.html",
  generated_at: "2026-07-14T12:00:00+00:00",
  metrics: {
    accuracy: 0.5,
    graded_count: 2,
    correct_count: 1,
    wrong_count: 1,
    request_success_rate: 1,
    request_success_count: 2,
    request_status_count: 2,
    empty_retrieval_rate: 0.5,
    empty_retrieval_count: 1,
    retrieval_observed_count: 2,
    failure_rate: 0,
    failure_count: 0,
    retry_rate: 0.5,
    retried_count: 1,
    retry_observed_count: 2,
    tokens_per_correct: 60,
    submission_rate: null,
    submitted_messages: null,
    expected_messages: null,
    import_status: "N/A",
    qa_parallelism: 4,
    batch_wall_clock_s: 8,
    qa_throughput_qps: 0.25,
    categories: {"multi-hop": {correct: 1, wrong: 1, total: 2}},
    end_to_end_s: stats,
    retrieval_latency_s: stats,
    injection_total_s: stats,
    llm_total_s: stats,
    answer_prompt_tokens: stats,
    answer_completion_tokens: stats,
    answer_total_tokens: stats,
    judge_prompt_tokens: stats,
    judge_completion_tokens: stats,
    judge_total_tokens: stats,
    judge_usage_observed_count: 2,
    judge_usage_expected_count: 2,
    judge_usage_complete: true,
    visible_model_total_tokens: 200,
  },
  definitions,
}, {escapeHtml});

assert(html.includes("严格黑盒指标"), "strict panel title must render");
assert(html.includes("准确率"), "strict accuracy must render");
assert(html.includes("本次运行指标快照"), "persisted metrics artifact must render");
assert(html.includes("打开 JSON"), "metrics artifact action must render");
assert(html.includes("打开 HTML 报告"), "standalone HTML report action must render");
assert(html.includes("strict_blackbox_metrics.json"), "metrics artifact path must render");
assert(html.includes("指标定义与黑盒边界"), "strict definitions section must render");
assert(html.includes("20 项"), "all strict metric definitions must be counted");
assert(html.includes("QA 并发数"), "QA parallelism must render");
assert(html.includes("整批评测墙钟时间"), "batch wall-clock time must render");
assert(html.includes("QA 吞吐量"), "QA throughput must render");
assert(html.includes("0.250 题/秒"), "QA throughput value must render");
assert(html.includes("Judge 模型 Token"), "Judge API usage must render");
assert(html.includes("外部可见模型总 Token"), "visible model token total must render");
assert(html.includes("10.000 秒"), "latencies must render in seconds");
assert(!html.includes(" ms"), "latencies must not render in milliseconds");
assert(html.includes("QA 侧编排注入"), "QA-side orchestration latency must be named precisely");
assert(html.includes("内部记忆注入 Token"), "unavailable internal injection token must be explicit");
assert(html.includes("初始记忆导入时间"), "unavailable initial import time must be explicit");
assert(html.includes("N/A"), "unavailable metrics must render as N/A");
assert(!html.includes("retrieval_tokens_est"), "estimated token fields must never render");
console.log("strict black-box UI smoke passed");

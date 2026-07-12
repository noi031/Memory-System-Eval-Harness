#!/usr/bin/env node
import { summarizeBenchmarkRun } from "../src/run-metrics.js";
import {
  buildHotpotQaPreviewModel,
  buildJudgeMetricItems,
  buildLongMemEvalPreviewModel,
  buildReportMetricItems,
} from "../src/benchmark-view-models.js";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const openvikingLocomoMetrics = summarizeBenchmarkRun(
  "locomo",
  { kind: "openviking_qa", run_dir: "/tmp/ov-locomo", output_file: "/tmp/ov-locomo/results.csv" },
  null,
  {
    summary: {
      rows: 10,
      graded: 10,
      correct: 7,
      wrong: 3,
      accuracy: 0.7,
      answer_total_tokens: 2676,
      internal_memory_import_total_tokens: 258817,
      retrieval_tokens_est: 912,
    },
  },
);

assert(openvikingLocomoMetrics.importTotalTokens === 258817, "OpenViking LoCoMo import tokens must fall back to internal_memory_import_total_tokens");
assert(openvikingLocomoMetrics.importLlmTotalTokens === 258817, "OpenViking LoCoMo import LLM tokens must reuse internal import totals when no split is available");
assert(openvikingLocomoMetrics.retrievalTotalTokens === 912, "OpenViking LoCoMo retrieval tokens must be exposed");
assert(openvikingLocomoMetrics.totalTokens === 262405, "OpenViking LoCoMo total tokens must sum import, retrieval, and answer tokens");

const locomoReportItems = buildReportMetricItems("locomo", openvikingLocomoMetrics);
const locomoJudgeItems = buildJudgeMetricItems("locomo", openvikingLocomoMetrics);
const locomoLabels = new Set([...locomoReportItems, ...locomoJudgeItems].map((item) => item.label));
assert(locomoLabels.has("导入 Tokens"), "LoCoMo report/judge metrics must include import tokens");
assert(locomoLabels.has("检索 Tokens"), "LoCoMo report/judge metrics must include retrieval tokens when present");
assert(locomoLabels.has("答案 Tokens"), "LoCoMo report/judge metrics must include answer tokens");

const longMemMetrics = summarizeBenchmarkRun(
  "longmemeval",
  { kind: "echomemory_generic_qa", run_dir: "/tmp/longmem", output_file: "/tmp/longmem/results.csv" },
  null,
  {
    summary: {
      rows: 1,
      answer_total_tokens: 3289,
      import_total_tokens: 13189,
      import_llm_total_tokens: 13189,
      official_overall_accuracy: 1,
      official_correct: 1,
      official_graded: 1,
      summary_json: {
        official_eval: {
          summary: {
            graded: 1,
            correct: 1,
            overall_accuracy: 1,
          },
        },
      },
    },
  },
);

const longMemReportItems = buildReportMetricItems("longmemeval", longMemMetrics);
const longMemLabels = new Set(longMemReportItems.map((item) => item.label));
assert(longMemLabels.has("导入 Tokens"), "LongMemEval report metrics must include import tokens");
assert(longMemLabels.has("答案 Tokens"), "LongMemEval report metrics must include answer tokens");

function firstValue(...values) {
  return values.find((value) => value !== undefined && value !== null && String(value).trim() !== "") || "";
}

const hotpotPreview = buildHotpotQaPreviewModel({
  backendId: "echomemory",
  cfg: {},
  currentBenchmark: { defaultData: "./dataset/full/hotpotqa_dev_distractor.json" },
  currentDatasetRecord: { path: "./dataset/full/hotpotqa_dev_distractor.json" },
  firstValue,
  metrics: {
    accuracy: 0.3333,
    answerF1: 1,
    supportF1: 0.3333,
    runDurationS: 12,
    importTotalTokens: 2100,
    importLlmTotalTokens: 1800,
    importEmbeddingTotalTokens: 300,
    retrievalTotalTokens: 90,
    answerTotalTokens: 120,
  },
  run: { name: "hotpot smoke" },
  statusLabel: "已完成",
  taskConfig: {},
});
const hotpotPreviewLabels = new Set((hotpotPreview.summaryItems || []).map((item) => item.label));
assert(hotpotPreviewLabels.has("导入 Tokens"), "HotpotQA preview must include import tokens");
assert(hotpotPreviewLabels.has("导入 LLM Tokens"), "HotpotQA preview must include import LLM tokens");
assert(hotpotPreviewLabels.has("导入 Embedding Tokens"), "HotpotQA preview must include import embedding tokens");
assert(hotpotPreviewLabels.has("检索 Tokens"), "HotpotQA preview must include retrieval tokens");
assert(hotpotPreviewLabels.has("答案 Tokens"), "HotpotQA preview must include answer tokens");

const longMemPreview = buildLongMemEvalPreviewModel({
  backendId: "openviking",
  cfg: {},
  currentBenchmark: { defaultData: "./dataset/full/longmemeval_s_cleaned.json" },
  currentDatasetRecord: { path: "./dataset/full/longmemeval_s_cleaned.json" },
  firstValue,
  metrics: {
    accuracy: 1,
    officialTaskAveragedAccuracy: 1,
    correct: 10,
    runDurationS: 20,
    importTotalTokens: 3100,
    importLlmTotalTokens: 2500,
    importEmbeddingTotalTokens: 600,
    retrievalTotalTokens: 140,
    answerTotalTokens: 220,
  },
  run: { name: "longmem smoke" },
  statusLabel: "已完成",
  taskConfig: {},
});
const longMemPreviewLabels = new Set((longMemPreview.summaryItems || []).map((item) => item.label));
assert(longMemPreviewLabels.has("导入 Tokens"), "LongMemEval preview must include import tokens");
assert(longMemPreviewLabels.has("导入 LLM Tokens"), "LongMemEval preview must include import LLM tokens");
assert(longMemPreviewLabels.has("导入 Embedding Tokens"), "LongMemEval preview must include import embedding tokens");
assert(longMemPreviewLabels.has("检索 Tokens"), "LongMemEval preview must include retrieval tokens");
assert(longMemPreviewLabels.has("答案 Tokens"), "LongMemEval preview must include answer tokens");

console.log("token metrics smoke passed");

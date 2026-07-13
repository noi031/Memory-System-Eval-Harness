import { BENCHMARKS } from "./config.js";
import { benchmarkHasOfficialEval, getBenchmark } from "./benchmark-registry.js";

function asNumber(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" && !value.trim()) return null;
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function firstNumber() {
  for (let i = 0; i < arguments.length; i += 1) {
    const num = asNumber(arguments[i]);
    if (num !== null) return num;
  }
  return null;
}

function sumNumbers() {
  let total = 0;
  let hasValue = false;
  for (let i = 0; i < arguments.length; i += 1) {
    const num = asNumber(arguments[i]);
    if (num === null) continue;
    total += num;
    hasValue = true;
  }
  return hasValue ? total : null;
}

export function isOfficialEvalBenchmarkId(benchmarkId) {
  return benchmarkHasOfficialEval(getBenchmark(BENCHMARKS, String(benchmarkId || "").toLowerCase()));
}

function officialEvalMeta(benchmarkId) {
  return BENCHMARKS[String(benchmarkId || "").toLowerCase()]?.officialEvalMeta || null;
}

function officialMetricNumber(mode, values) {
  if (mode === "official_then_summary") {
    return firstNumber(values.official, values.summary);
  }
  if (mode === "summary_official_then_summary") {
    return firstNumber(values.summaryOfficial, values.official, values.summary);
  }
  if (mode === "answer_f1") {
    return firstNumber(values.score, values.answerF1, values.accuracy);
  }
  if (mode === "overall_accuracy") {
    return firstNumber(values.score, values.overallAccuracy, values.accuracy);
  }
  if (mode === "answer_em_rows") {
    return firstNumber(values.summary, values.derived);
  }
  if (mode === "official_correct") {
    return firstNumber(values.summaryOfficial, values.official, values.summary);
  }
  if (mode === "official_wrong") {
    return firstNumber(values.summaryOfficial, values.official, values.derived);
  }
  if (mode === "rows_minus_correct") {
    return firstNumber(values.summary, values.derived);
  }
  return firstNumber(values.summary, values.official, values.score, values.accuracy);
}

export function pickRunSummary(run, detail, result) {
  return result?.summary || detail?.record?.summary || run?.summary || {};
}

export function pickOfficialEvalSummary(summary) {
  return summary?.summary_json?.official_eval?.summary || null;
}

export function isHotpotImportOnlySummary(summary, runName = "") {
  const summaryJson = summary?.summary_json || {};
  const status = String(summaryJson.status || "").toUpperCase();
  if (status.includes("IMPORT_ONLY")) return true;
  if (summaryJson.import_only === true) return true;
  if (summaryJson.official_eval_after === false && Number(summaryJson.rows || 0) === 0) return true;
  return /hotpotqa import/i.test(String(runName || ""));
}

export function isImportOnlySummary(benchmarkId, summary, runName = "") {
  const id = String(benchmarkId || "").toLowerCase();
  if (id === "hotpotqa") return isHotpotImportOnlySummary(summary, runName);
  if (!isOfficialEvalBenchmarkId(id)) return false;
  const summaryJson = summary?.summary_json || {};
  const status = String(summaryJson.status || "").toUpperCase();
  if (status.includes("IMPORT_ONLY")) return true;
  if (summaryJson.import_only === true) return true;
  if (summaryJson.official_eval_after === false && Number(summaryJson.rows || 0) === 0) return true;
  const pattern = officialEvalMeta(id)?.importRunNamePattern || `${id} import`;
  return new RegExp(pattern, "i").test(String(runName || ""));
}

export function hasOfficialEvalSummaryMetrics(benchmarkId, metrics) {
  const id = String(benchmarkId || "").toLowerCase();
  const mode = officialEvalMeta(id)?.summaryReadyMode || "";
  if (mode === "hotpotqa") {
    return Boolean(metrics?.official)
      || metrics?.answerF1 !== null
      || metrics?.jointF1 !== null
      || metrics?.supportF1 !== null;
  }
  if (mode === "longmemeval") {
    return Boolean(metrics?.official) || metrics?.officialOverallAccuracy !== null || metrics?.officialTaskAveragedAccuracy !== null;
  }
  return false;
}

export function officialSummaryReadyForMetrics(benchmarkId, metrics) {
  if (!isOfficialEvalBenchmarkId(benchmarkId)) return false;
  return hasOfficialEvalSummaryMetrics(benchmarkId, metrics);
}

export function summarizeBenchmarkRun(benchmarkId, run, detail, result) {
  const meta = officialEvalMeta(benchmarkId);
  const summary = pickRunSummary(run, detail, result);
  const official = pickOfficialEvalSummary(summary);
  const importSummary = summary?.import_summary || summary?.summary_json?.import_summary || null;
  const benchmarkKey = String(benchmarkId || "").toLowerCase();
  const locomoSample = benchmarkKey === "locomo"
    ? String(
      detail?.record?.sample
      || summary?.summary_json?.sample
      || run?.sample
      || ""
    ).trim()
    : "";
  const locomoSampleCounts = benchmarkKey === "locomo" && locomoSample && locomoSample !== "all"
    ? (summary?.samples?.[locomoSample] || null)
    : null;
  const locomoScopedRows = locomoSampleCounts
    ? firstNumber(
      Number(locomoSampleCounts.CORRECT || 0) + Number(locomoSampleCounts.WRONG || 0) + Number(locomoSampleCounts.UNSCORED || 0),
    )
    : null;
  const locomoScopedCorrect = locomoSampleCounts ? firstNumber(locomoSampleCounts.CORRECT) : null;
  const locomoScopedWrong = locomoSampleCounts ? firstNumber(locomoSampleCounts.WRONG) : null;
  const locomoScopedPending = locomoSampleCounts ? firstNumber(locomoSampleCounts.UNSCORED) : null;
  const locomoScopedGraded = locomoScopedCorrect !== null || locomoScopedWrong !== null
    ? firstNumber((locomoScopedCorrect || 0) + (locomoScopedWrong || 0))
    : null;
  const rows = firstNumber(locomoScopedRows, summary.rows, official?.graded);
  const graded = firstNumber(
    locomoScopedGraded,
    officialMetricNumber(meta?.gradedMode, {
      official: official?.graded,
      summary: summary.graded,
      summaryOfficial: summary.official_graded,
    }),
  );
  const answerEm = firstNumber(summary.official_answer_em, official?.answer_em, summary.exact_match_reference);
  const answerF1 = firstNumber(summary.official_answer_f1, official?.answer_f1);
  const supportEm = firstNumber(summary.official_supporting_facts_em, official?.supporting_facts_em);
  const supportF1 = firstNumber(summary.official_supporting_facts_f1, official?.supporting_facts_f1);
  const jointEm = firstNumber(summary.official_joint_em, official?.joint_em);
  const jointF1 = firstNumber(summary.official_joint_f1, official?.joint_f1);
  const officialOverallAccuracy = firstNumber(summary.official_overall_accuracy, official?.overall_accuracy);
  const officialTaskAveragedAccuracy = firstNumber(summary.official_task_averaged_accuracy, official?.task_averaged_accuracy);
  const correct = firstNumber(
    locomoScopedCorrect,
    officialMetricNumber(meta?.correctMode, {
      summary: summary.correct,
      official: official?.correct,
      summaryOfficial: summary.official_correct,
      derived: answerEm !== null && rows !== null ? Math.round(answerEm * rows) : null,
    }),
  );
  const wrong = firstNumber(
    locomoScopedWrong,
    officialMetricNumber(meta?.wrongMode, {
      summary: summary.wrong,
      official: official?.wrong,
      summaryOfficial: summary.official_wrong,
      derived: rows !== null && correct !== null ? Math.max(0, rows - correct) : null,
    }),
  );
  const pending = firstNumber(
    locomoScopedPending,
    summary.result_counts?.UNSCORED,
    rows !== null && graded !== null ? Math.max(0, rows - graded) : null,
  );
  const accuracy = firstNumber(
    locomoScopedGraded && locomoScopedCorrect !== null ? locomoScopedCorrect / locomoScopedGraded : null,
    officialMetricNumber(meta?.scoreMode, {
      score: summary.official_score,
      answerF1,
      overallAccuracy: officialOverallAccuracy,
      accuracy: summary.accuracy,
    }),
  );
  const answerTotalTokens = firstNumber(summary.answer_total_tokens);
  const importLlmTotalTokens = firstNumber(
    summary.import_llm_total_tokens,
    importSummary?.import_llm_total_tokens,
  );
  const importEmbeddingTotalTokens = firstNumber(summary.import_embedding_total_tokens, importSummary?.import_embedding_total_tokens);
  const importTotalTokens = firstNumber(
    summary.import_total_tokens,
    importSummary?.import_total_tokens,
  );
  const retrievalTotalTokens = firstNumber(
    summary.retrieval_total_tokens,
    summary.run_retrieval_total_tokens,
  );

  return {
    summary,
    official,
    rows,
    graded,
    correct,
    wrong,
    pending,
    accuracy,
    answerEm,
    answerF1,
    supportEm,
    supportF1,
    jointEm,
    jointF1,
    officialOverallAccuracy,
    officialTaskAveragedAccuracy,
    toolCallTotal: firstNumber(summary.tool_call_total),
    avgMemoryHitCount: firstNumber(summary.avg_memory_hit_count),
    avgQaTimeS: firstNumber(summary.avg_qa_time_s, summary.avg_time),
    runDurationS: firstNumber(detail?.record?.duration_s, run?.duration),
    avgMemoryInjectionTimeS: firstNumber(summary.avg_memory_injection_time_s),
    totalEndToEndTimeS: firstNumber(summary.total_end_to_end_time_s, summary.avg_end_to_end_time_s),
    answerTotalTokens,
    importLlmTotalTokens,
    importEmbeddingTotalTokens,
    importTotalTokens,
    retrievalTotalTokens,
    totalTokens: sumNumbers(importTotalTokens, retrievalTotalTokens, answerTotalTokens),
    officialMetric: summary?.official_metric || official?.official_metric || "",
    officialMetricScope: official?.metric_scope || "",
    officialNote: official?.official_metric_note || "",
    officialSummaryPath: summary?.summary_json?.official_eval?.summary_path || "",
    reportHtmlFile: run?.run_dir ? `${run.run_dir}/report.html` : "",
    dataPath: detail?.record?.dataset_path || run?.dataset_path || "",
  };
}

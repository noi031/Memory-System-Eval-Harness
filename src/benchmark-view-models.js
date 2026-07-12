import { benchmarkHasOfficialEval, getBenchmark } from "./benchmark-registry.js";
import { BENCHMARKS } from "./config.js";
import { isImportOnlySummary, officialSummaryReadyForMetrics } from "./run-metrics.js";

function isRunActivePhase(phase) {
  return phase === "running" || phase === "finalizing";
}

function hotpotPrimaryMetricLabel(metrics) {
  if (metrics?.jointF1 !== null) return "Joint F1";
  if (metrics?.answerF1 !== null) return "Answer F1";
  return "官方分数";
}

function longMemPrimaryMetricLabel() {
  return "Overall Accuracy";
}

function officialScopeLabel(scope) {
  const normalized = String(scope || "").trim().toLowerCase();
  if (!normalized) return "official";
  if (normalized === "answer_only") return "answer only";
  if (normalized === "answer_and_supporting_facts") return "answer + supporting facts";
  return normalized.replace(/_/g, " ");
}

function officialBenchmarkName(benchmarkId) {
  return officialBenchmarkMeta(benchmarkId)?.benchmarkName || "Official Benchmark";
}

function importTokenMetricItem(metrics, fallbackLabel = "模型工具调用", fallbackValue = null) {
  if (metrics?.importTotalTokens !== null && metrics?.importTotalTokens !== undefined) {
    return {label: "导入 Tokens", type: "int", value: metrics.importTotalTokens};
  }
  return {label: fallbackLabel, type: "int", value: fallbackValue};
}

function answerTokenMetricItem(metrics, fallbackLabel = "模型工具调用", fallbackValue = null) {
  if (metrics?.answerTotalTokens !== null && metrics?.answerTotalTokens !== undefined) {
    return {label: "答案 Tokens", type: "int", value: metrics.answerTotalTokens};
  }
  return {label: fallbackLabel, type: "int", value: fallbackValue};
}

function hasDistinctImportLlmTokens(metrics) {
  if (metrics?.importLlmTotalTokens === null || metrics?.importLlmTotalTokens === undefined) return false;
  if (metrics?.importEmbeddingTotalTokens !== null && metrics?.importEmbeddingTotalTokens !== undefined) return true;
  if (metrics?.importTotalTokens === null || metrics?.importTotalTokens === undefined) return true;
  return Number(metrics.importLlmTotalTokens) !== Number(metrics.importTotalTokens);
}

function tokenBreakdownMetricItems(metrics, {
  includeImportTotal = true,
  includeAnswer = true,
  includeRetrieval = true,
} = {}) {
  const items = [];
  if (includeImportTotal && metrics?.importTotalTokens !== null && metrics?.importTotalTokens !== undefined) {
    items.push({label: "导入 Tokens", type: "int", value: metrics.importTotalTokens});
  }
  if (hasDistinctImportLlmTokens(metrics)) {
    items.push({label: "导入 LLM Tokens", type: "int", value: metrics.importLlmTotalTokens});
  }
  if (metrics?.importEmbeddingTotalTokens !== null && metrics?.importEmbeddingTotalTokens !== undefined) {
    items.push({label: "导入 Embedding Tokens", type: "int", value: metrics.importEmbeddingTotalTokens});
  }
  if (includeRetrieval && metrics?.retrievalTotalTokens !== null && metrics?.retrievalTotalTokens !== undefined) {
    items.push({label: "检索 Tokens", type: "int", value: metrics.retrievalTotalTokens});
  }
  if (includeAnswer && metrics?.answerTotalTokens !== null && metrics?.answerTotalTokens !== undefined) {
    items.push({label: "答案 Tokens", type: "int", value: metrics.answerTotalTokens});
  }
  return items;
}

function previewTokenMetricItems(metrics) {
  return tokenBreakdownMetricItems(metrics, {
    includeImportTotal: true,
    includeRetrieval: true,
    includeAnswer: true,
  });
}

function hotpotEvidenceMetricItem(metrics) {
  if (metrics?.supportF1 !== null) {
    return {label: "Support F1", type: "pct", value: metrics.supportF1};
  }
  return {label: "Answer EM", type: "pct", value: metrics?.answerEm};
}

function longMemSecondaryMetricItem(metrics) {
  return {label: "Task Avg Accuracy", type: "pct", value: metrics?.officialTaskAveragedAccuracy};
}

const OFFICIAL_VIEW_MODEL_META = {
  answer_f1_bundle: {
    primaryMetricLabel: (metrics) => hotpotPrimaryMetricLabel(metrics),
    secondaryMetricItem: (metrics) => hotpotEvidenceMetricItem(metrics),
    runCardSummary: (metrics) => ({
      rows: metrics.rows,
      official_score: metrics.accuracy,
      official_answer_f1: metrics.answerF1,
      official_joint_f1: metrics.jointF1,
      official_metric: metrics.officialMetric,
    }),
    judgeMetricItems: (metrics, toolCallLabel) => ([
      {label: hotpotPrimaryMetricLabel(metrics), type: "pct", value: metrics.accuracy},
      {label: "Answer F1", type: "pct", value: metrics.answerF1},
      hotpotEvidenceMetricItem(metrics),
      importTokenMetricItem(metrics, toolCallLabel, metrics.toolCallTotal),
      ...tokenBreakdownMetricItems(metrics, {includeImportTotal: false, includeRetrieval: false}),
    ]),
    reportMetricItems: (metrics, toolCallLabel) => ([
      {label: "结果题数", type: "int", value: metrics.rows},
      {label: hotpotPrimaryMetricLabel(metrics), type: "pct", value: metrics.accuracy},
      {label: "Answer F1", type: "pct", value: metrics.answerF1},
      hotpotEvidenceMetricItem(metrics),
      importTokenMetricItem(metrics, toolCallLabel, metrics.toolCallTotal),
      ...tokenBreakdownMetricItems(metrics, {includeImportTotal: false, includeRetrieval: false}),
    ]),
  },
  overall_accuracy_bundle: {
    primaryMetricLabel: () => "Overall Accuracy",
    secondaryMetricItem: (metrics) => longMemSecondaryMetricItem(metrics),
    runCardSummary: (metrics) => ({
      rows: metrics.rows,
      official_score: metrics.accuracy,
      official_overall_accuracy: metrics.officialOverallAccuracy,
      official_task_averaged_accuracy: metrics.officialTaskAveragedAccuracy,
      official_metric: metrics.officialMetric,
    }),
    judgeMetricItems: (metrics, toolCallLabel) => ([
      {label: "Overall Accuracy", type: "pct", value: metrics.accuracy},
      longMemSecondaryMetricItem(metrics),
      {label: "Correct", type: "int", value: metrics.correct},
      importTokenMetricItem(metrics, toolCallLabel, metrics.toolCallTotal),
      ...tokenBreakdownMetricItems(metrics, {includeImportTotal: false}),
    ]),
    reportMetricItems: (metrics, toolCallLabel) => ([
      {label: "结果题数", type: "int", value: metrics.rows},
      {label: "Overall Accuracy", type: "pct", value: metrics.accuracy},
      longMemSecondaryMetricItem(metrics),
      {label: "Correct", type: "int", value: metrics.correct},
      {label: "Graded", type: "int", value: metrics.graded},
      importTokenMetricItem(metrics, toolCallLabel, metrics.toolCallTotal),
      ...tokenBreakdownMetricItems(metrics, {includeImportTotal: false}),
    ]),
  },
};

function officialBenchmarkMeta(benchmarkId) {
  const id = String(benchmarkId || "").toLowerCase();
  const benchmarkMeta = BENCHMARKS[id]?.officialEvalMeta || null;
  const viewModelMeta = benchmarkMeta?.viewModelMode
    ? OFFICIAL_VIEW_MODEL_META[benchmarkMeta.viewModelMode] || null
    : null;
  if (!benchmarkMeta || !viewModelMeta) return null;
  return {
    ...benchmarkMeta,
    ...viewModelMeta,
  };
}

function officialRunSummaryViewModel({
  benchmarkId,
  metrics,
  toolCallLabel = "模型工具调用",
}) {
  const meta = officialBenchmarkMeta(benchmarkId);
  if (!meta) return null;
  return {
    runCardSummary: meta.runCardSummary(metrics),
    judgeMetricItems: meta.judgeMetricItems(metrics, toolCallLabel),
    reportMetricItems: meta.reportMetricItems(metrics, toolCallLabel),
  };
}

function buildOfficialPreviewBase({
  benchmarkId,
  run,
  statusLabel,
  config,
  summaryItems,
  configLines,
  metrics,
}) {
  const meta = officialBenchmarkMeta(benchmarkId);
  return {
    title: meta?.previewTitle || "当前结果",
    subtitle: `${run?.name || "尚未开始测试"} · ${statusLabel}${meta?.previewSubtitleScope && metrics.officialMetricScope ? ` · ${officialScopeLabel(metrics.officialMetricScope)}` : ""}`,
    path: config.dataPath || "-",
    summaryItems,
    configLines,
  };
}

function buildOfficialConfigStateBase({
  dataPath,
  questionCount,
  topK,
  toolEnabled,
  qaParallelism = "",
  toolSearchLimit,
  maxIterations,
  retrievalMode,
  toolSet,
  toolMinScore,
  questionTimeoutS,
  officialEvalAfter,
  backendIsEchoMemory,
  extras = {},
}) {
  return {
    dataPath,
    questionCount,
    topK,
    toolEnabled,
    qaParallelism,
    toolSearchLimit,
    maxIterations,
    retrievalMode,
    toolSet,
    toolMinScore,
    questionTimeoutS,
    officialEvalAfter,
    backendIsEchoMemory,
    ...extras,
  };
}

function buildOfficialConfigState({
  benchmarkId,
  backendId,
  benchmark,
  cfg,
  currentDatasetRecord,
  firstValue,
  taskConfig,
}) {
  const meta = officialBenchmarkMeta(benchmarkId);
  const mode = meta?.configMode || meta?.previewMode || meta?.viewModelMode || "";
  const dataPath = firstValue(taskConfig.data, currentDatasetRecord?.path, benchmark.defaultData);
  const genericToolEnabled = typeof taskConfig.vikingboat_tool_loop === "boolean"
    ? taskConfig.vikingboat_tool_loop
    : typeof taskConfig.openviking_tool_loop === "boolean"
      ? taskConfig.openviking_tool_loop
      : typeof taskConfig.tool_loop === "boolean"
        ? taskConfig.tool_loop
        : (taskConfig.prompt_mode ? String(taskConfig.prompt_mode) !== "one_shot" : true);
  if (mode === "hotpotqa") {
    const toolEnabled = taskConfig.prompt_mode
      ? String(taskConfig.prompt_mode) !== "one_shot"
      : cfg.hotpotQaUseTools !== false;
    return buildOfficialConfigStateBase({
      dataPath,
      questionCount: firstValue(taskConfig.count, cfg.hotpotQaCount, "10"),
      topK: firstValue(taskConfig.top_k, cfg.hotpotQaTopK, "8"),
      toolEnabled,
      toolSearchLimit: firstValue(taskConfig.tool_search_limit, cfg.hotpotQaToolSearchLimit, "8"),
      maxIterations: firstValue(taskConfig.max_iterations, cfg.hotpotQaMaxIterations, "8"),
      retrievalMode: firstValue(taskConfig.retrieval_mode, cfg.hotpotQaRetrievalMode, "search"),
      toolSet: firstValue(taskConfig.tool_set, cfg.hotpotQaToolSet, "vikingbot_native_safe"),
      toolMinScore: firstValue(taskConfig.tool_min_score, cfg.hotpotQaToolMinScore, "0.35"),
      questionTimeoutS: firstValue(taskConfig.question_timeout_s, cfg.hotpotQaQuestionTimeout, "180"),
      officialEvalAfter: taskConfig.official_eval_after !== undefined
        ? taskConfig.official_eval_after !== false
        : true,
      backendIsEchoMemory: backendId === "echomemory",
      extras: {
        hotpotqaCorpusMode: firstValue(taskConfig.hotpotqa_corpus_mode, cfg.hotpotQaCorpusMode, "global_sentence_corpus"),
        hotpotqaGlobalImportMode: firstValue(taskConfig.hotpotqa_global_import_mode, cfg.hotpotQaGlobalImportMode, "projection"),
        checkpointInterval: firstValue(taskConfig.checkpoint_interval, cfg.hotpotQaCheckpointInterval, "5"),
      },
    });
  }
  if (mode === "longmemeval") {
    const toolEnabled = taskConfig.prompt_mode
      ? String(taskConfig.prompt_mode) !== "one_shot"
      : cfg.longMemEvalUseTools !== false;
    return buildOfficialConfigStateBase({
      dataPath,
      questionCount: firstValue(taskConfig.count, cfg.longMemEvalCount, cfg.hotpotQaCount, "10"),
      topK: firstValue(taskConfig.top_k, cfg.longMemEvalTopK, cfg.hotpotQaTopK, "5"),
      toolEnabled,
      qaParallelism: firstValue(taskConfig.qa_parallelism, cfg.longMemEvalQaParallelism, cfg.echomemQaParallelism, "10"),
      toolSearchLimit: firstValue(taskConfig.tool_search_limit, cfg.longMemEvalToolSearchLimit, cfg.hotpotQaToolSearchLimit, "5"),
      maxIterations: firstValue(taskConfig.max_iterations, cfg.longMemEvalMaxIterations, cfg.hotpotQaMaxIterations, "8"),
      retrievalMode: firstValue(taskConfig.retrieval_mode, cfg.longMemEvalRetrievalMode, cfg.hotpotQaRetrievalMode, "search"),
      toolSet: firstValue(taskConfig.tool_set, cfg.longMemEvalToolSet, cfg.hotpotQaToolSet, "vikingbot_native_safe"),
      toolMinScore: firstValue(taskConfig.tool_min_score, cfg.longMemEvalToolMinScore, cfg.hotpotQaToolMinScore, "0.35"),
      questionTimeoutS: firstValue(taskConfig.question_timeout_s, cfg.longMemEvalQuestionTimeout, cfg.hotpotQaQuestionTimeout, "180"),
      officialEvalAfter: taskConfig.official_eval_after !== undefined
        ? taskConfig.official_eval_after !== false
        : true,
      backendIsEchoMemory: backendId === "echomemory",
    });
  }
  return buildOfficialConfigStateBase({
    dataPath,
    questionCount: firstValue(taskConfig.count, "10"),
    topK: firstValue(taskConfig.top_k, "8"),
    toolEnabled: genericToolEnabled,
    toolSearchLimit: firstValue(taskConfig.tool_search_limit, "8"),
    maxIterations: firstValue(taskConfig.max_iterations, "8"),
    retrievalMode: firstValue(taskConfig.retrieval_mode, "search"),
    toolSet: firstValue(taskConfig.tool_set, "vikingbot_native_safe"),
    toolMinScore: firstValue(taskConfig.tool_min_score, "0.35"),
    questionTimeoutS: firstValue(taskConfig.question_timeout_s, "180"),
    officialEvalAfter: taskConfig.official_eval_after !== false,
    backendIsEchoMemory: backendId === "echomemory",
  });
}

function buildOfficialConfigLines({
  config,
  backendId,
  headline,
  echoPrimarySuffix = "",
  echoSecondaryLine,
  nonEchoPrimaryLine,
}) {
  const parallelPart = config.qaParallelism ? ` · QA 并发 ${String(config.qaParallelism)}` : "";
  const echoPrimaryLine = `检索调用 ${config.toolEnabled ? "开启" : "关闭"} · 模型工具循环 ${config.toolEnabled ? "按当前配置执行" : "关闭"} · 工具召回 ${String(config.toolSearchLimit)} · 最大迭代 ${String(config.maxIterations)}${parallelPart}${echoPrimarySuffix}`;
  const fallbackLine = `当前后端 ${backendId === "openviking" ? "OpenViking" : backendId} · 结果以运行产物为准`;
  return [
    headline,
    config.backendIsEchoMemory ? echoPrimaryLine : nonEchoPrimaryLine,
    config.backendIsEchoMemory ? echoSecondaryLine : fallbackLine,
  ];
}

function buildOfficialPreviewPresentation({
  benchmarkId,
  backendId,
  config,
  metrics,
}) {
  const meta = officialBenchmarkMeta(benchmarkId);
  const mode = meta?.previewMode || meta?.viewModelMode || "";
  if (mode === "answer_f1_bundle" || mode === "hotpotqa") {
    return {
      summaryItems: [
        {label: hotpotPrimaryMetricLabel(metrics), type: "pct", value: metrics.accuracy},
        {label: "Answer F1", type: "pct", value: metrics.answerF1},
        hotpotEvidenceMetricItem(metrics),
        {label: "运行时长", type: "duration", value: metrics.runDurationS},
        ...previewTokenMetricItems(metrics),
      ],
      configLines: buildOfficialConfigLines({
        config,
        backendId,
        headline: `题量 ${String(config.questionCount)} · Top K ${String(config.topK)} · 语料 ${String(config.hotpotqaCorpusMode)} · 官方评测 ${config.officialEvalAfter ? "开启" : "关闭"}${metrics.officialMetricScope ? ` · ${officialScopeLabel(metrics.officialMetricScope)}` : ""}`,
        echoPrimarySuffix: ` · checkpoint ${String(config.checkpointInterval)}`,
        echoSecondaryLine: `检索模式 ${String(config.retrievalMode)} · 导入 ${String(config.hotpotqaGlobalImportMode)} · 工具集 ${String(config.toolSet)} · 最低分 ${String(config.toolMinScore)} · 超时 ${String(config.questionTimeoutS)}s`,
        nonEchoPrimaryLine: `检索增强 ${config.toolEnabled ? "开启" : "关闭"} · 当前后端不会使用 EchoMemory 检索参数`,
      }),
    };
  }
  if (mode === "overall_accuracy_bundle" || mode === "longmemeval") {
    return {
      summaryItems: [
        {label: longMemPrimaryMetricLabel(metrics), type: "pct", value: metrics.accuracy},
        longMemSecondaryMetricItem(metrics),
        {label: "Correct", type: "int", value: metrics.correct},
        {label: "运行时长", type: "duration", value: metrics.runDurationS},
        ...previewTokenMetricItems(metrics),
      ],
      configLines: buildOfficialConfigLines({
        config,
        backendId,
        headline: `题量 ${String(config.questionCount)} · Top K ${String(config.topK)} · 官方评测 ${config.officialEvalAfter ? "开启" : "关闭"}`,
        echoSecondaryLine: `检索模式 ${String(config.retrievalMode)} · 工具集 ${String(config.toolSet)} · 最低分 ${String(config.toolMinScore)} · 超时 ${String(config.questionTimeoutS)}s`,
        nonEchoPrimaryLine: `检索增强 ${config.toolEnabled ? "开启" : "关闭"} · 当前后端不会使用 EchoMemory 检索参数`,
      }),
    };
  }
  return {
    summaryItems: [
      {label: "准确率", type: "pct", value: metrics.accuracy},
      {label: "模型工具调用", type: "int", value: metrics.toolCallTotal},
      ...previewTokenMetricItems(metrics),
    ],
    configLines: buildOfficialConfigLines({
      config,
      backendId,
      headline: `题量 ${String(config.questionCount)} · Top K ${String(config.topK)} · 官方评测 ${config.officialEvalAfter ? "开启" : "关闭"}`,
      echoSecondaryLine: `检索模式 ${String(config.retrievalMode)} · 工具集 ${String(config.toolSet)}`,
      nonEchoPrimaryLine: `检索增强 ${config.toolEnabled ? "开启" : "关闭"} · 当前后端不会使用 EchoMemory 检索参数`,
    }),
  };
}

export function buildBenchmarkRunCardSummary(benchmarkId, metrics) {
  const official = officialRunSummaryViewModel({ benchmarkId, metrics });
  if (official) {
    return official.runCardSummary;
  }
  return {
    rows: metrics.rows,
    official_score: metrics.accuracy,
    accuracy: metrics.accuracy,
  };
}

export function buildBenchmarkRunCardSubtitle(benchmarkId, summary, { formatInt, formatPct }) {
  const rows = formatInt(summary.rows);
  const meta = officialBenchmarkMeta(benchmarkId);
  const mode = meta?.viewModelMode || "";
  if (mode === "answer_f1_bundle") {
    const jointF1 = formatPct(summary.official_joint_f1 ?? summary.joint_f1);
    const answerF1 = formatPct(summary.official_answer_f1 ?? summary.answer_f1);
    const officialScore = formatPct(summary.official_score ?? summary.accuracy);
    if (jointF1 !== "-") return `${rows} 题 · Joint F1 ${jointF1}`;
    if (answerF1 !== "-") return `${rows} 题 · Answer F1 ${answerF1}`;
    if (officialScore !== "-") return `${rows} 题 · 官方分数 ${officialScore}`;
    return `${rows} 题 · ${meta?.benchmarkName || "Official Benchmark"}`;
  }
  if (mode === "overall_accuracy_bundle") {
    const overall = formatPct(summary.official_overall_accuracy ?? summary.official_score ?? summary.accuracy);
    const taskAvg = formatPct(summary.official_task_averaged_accuracy);
    if (overall !== "-" && taskAvg !== "-") return `${rows} 题 · Overall ${overall} · Task Avg ${taskAvg}`;
    if (overall !== "-") return `${rows} 题 · Overall ${overall}`;
    return `${rows} 题 · ${meta?.benchmarkName || "Official Benchmark"}`;
  }
  return `${rows} 题 · ${formatPct(summary.official_score ?? summary.accuracy)}`;
}

export function buildJudgeMetricItems(benchmarkId, metrics) {
  const official = officialRunSummaryViewModel({ benchmarkId, metrics });
  if (official) {
    return official.judgeMetricItems;
  }
  return [
    {label: "正确", type: "int", value: metrics.correct},
    {label: "错误", type: "int", value: metrics.wrong},
    {label: "待判分", type: "int", value: metrics.pending},
    importTokenMetricItem(metrics, "答案 Tokens", metrics.answerTotalTokens),
    ...tokenBreakdownMetricItems(metrics, {includeImportTotal: false}),
  ];
}

export function buildJudgeActionModel(benchmarkId, metrics) {
  if (benchmarkId === "locomo") {
    return {
      title: "评分操作",
      description: "优先对当前 LoCoMo QA 结果做正式判分或抽样判分。",
      buttons: [
        {id: "wbRunJudge", label: "正式评分", tone: "primary"},
        {id: "wbRunJudgeSmoke", label: "抽样评分", tone: "secondary"},
      ],
    };
  }
  const meta = officialBenchmarkMeta(benchmarkId);
  const benchmarkName = meta?.benchmarkName || officialBenchmarkName(benchmarkId);
  const description = meta?.judgeDescriptionMode === "scope_prefixed"
    ? `${officialScopeLabel(metrics.officialMetricScope)} · ${benchmarkName} 不走单独 judge 任务。`
    : `${benchmarkName} 不走单独 judge 任务，结果以 official-style summary 为主。`;
  return {
    title: "官方评测结果",
    description,
    note: metrics.officialNote || meta?.judgeNoteDefault || `结果以运行后自动生成的官方 ${benchmarkName} 评测为主。`,
    pathButtons: [
      metrics.officialSummaryPath ? {label: "打开 summary", path: metrics.officialSummaryPath, tone: "secondary"} : null,
      metrics.reportHtmlFile ? {label: "打开 report.html", path: metrics.reportHtmlFile, tone: "ghost"} : null,
    ].filter(Boolean),
  };
}

export function buildJudgeActionViewModel({
  benchmarkId,
  metrics,
  phase,
  run,
}) {
  const actionModel = buildJudgeActionModel(benchmarkId, metrics);
  if (benchmarkId === "locomo") {
    return {
      title: actionModel.title,
      subtitle: actionModel.description,
      buttons: actionModel.buttons,
      pathButtons: [],
    };
  }
  const isRunning = isRunActivePhase(phase);
  const benchmarkName = officialBenchmarkName(benchmarkId);
  const summaryText = phase === "finalizing"
    ? `当前问答已经提交完成，正在等待最终 summary 和官方 ${benchmarkName} 评测结果落盘。`
    : (phase === "running"
      ? `当前任务仍在运行，官方 ${benchmarkName} 评测结果会在完成后出现。`
      : actionModel.description);
  const noteText = phase === "finalizing"
    ? "当前可先打开运行目录查看 report.html、CSV 和 recall 产物。"
    : (phase === "running"
      ? "当前可先查看运行目录或等待 summary 产物生成。"
      : (actionModel.note || ""));
  return {
    title: actionModel.title,
    subtitle: summaryText,
    body: noteText,
    buttons: [],
    pathButtons: [
      isRunning && run?.run_dir ? {label: "打开当前目录", path: run.run_dir, tone: "secondary"} : null,
      ...actionModel.pathButtons,
    ].filter(Boolean),
  };
}

export function buildReportMetricItems(benchmarkId, metrics) {
  const official = officialRunSummaryViewModel({ benchmarkId, metrics });
  if (official) {
    return official.reportMetricItems;
  }
  return [
    {label: "结果题数", type: "int", value: metrics.rows},
    {label: "准确率", type: "pct", value: metrics.accuracy},
    {label: "运行时长", type: "duration", value: metrics.runDurationS},
    {label: "平均 QA", type: "duration", value: metrics.avgQaTimeS},
    importTokenMetricItem(metrics, "答案 Tokens", metrics.answerTotalTokens),
    ...tokenBreakdownMetricItems(metrics, {includeImportTotal: false}),
  ];
}

export function buildReportActionModel({
  benchmarkId,
  metrics,
  phase,
  run,
}) {
  if (!benchmarkHasOfficialEval(getBenchmark(BENCHMARKS, benchmarkId))) {
    const pending = Number(metrics?.pending || 0);
    if (phase === "finalizing") {
      return {
        title: "报告产物",
        subtitle: "Judge 已启动，正在等待待判分结果回写后生成最终报告。",
        exportLabel: "刷新报告入口",
        pathButtons: [
          run?.run_dir ? {label: "打开当前目录", path: run.run_dir, tone: "secondary"} : null,
        ].filter(Boolean),
      };
    }
    if (phase === "waiting_judge" || pending > 0) {
      return {
        title: "报告产物",
        subtitle: `当前还有 ${pending} 行待判分，建议先完成 Judge 再查看最终报告。`,
        exportLabel: "打开当前产物",
        pathButtons: [
          run?.run_dir ? {label: "打开当前目录", path: run.run_dir, tone: "secondary"} : null,
        ].filter(Boolean),
      };
    }
    return {
      title: "报告产物",
      subtitle: "当前结果已经完成，可以直接打开或刷新 HTML 报告产物。",
      exportLabel: "打开当前产物",
      pathButtons: [
        run?.run_dir ? {label: "打开当前目录", path: run.run_dir, tone: "secondary"} : null,
      ].filter(Boolean),
    };
  }
  const isRunning = isRunActivePhase(phase);
  const meta = officialBenchmarkMeta(benchmarkId);
  const summaryReady = officialSummaryReadyForMetrics(benchmarkId, metrics);
  const benchmarkName = meta?.benchmarkName || officialBenchmarkName(benchmarkId);
  const statusText = phase === "finalizing"
    ? `当前问答已经跑完，正在等待最终 report 和官方 ${benchmarkName} summary 落盘。`
    : (phase === "running"
      ? `当前任务仍在运行，report 和官方 ${benchmarkName} summary 会持续刷新。`
      : (summaryReady ? "当前结果目录里的 HTML 报告和官方 summary 已可直接打开。" : "当前结果已完成，正在等待最终 summary 刷新到页面。"));
  return {
    title: "报告产物",
    subtitle: statusText,
    exportLabel: isRunning ? "刷新报告入口" : (summaryReady ? "打开结果产物" : "刷新最终产物"),
    pathButtons: [
      isRunning && run?.run_dir ? {label: "打开当前目录", path: run.run_dir, tone: "secondary"} : null,
    ].filter(Boolean),
  };
}

export function buildQaTaskList(benchmarkId, tasks) {
  return (tasks || []).filter((task) => {
    if (!/qa|generic|judge/i.test(String(task.kind || ""))) return false;
    if (!benchmarkHasOfficialEval(getBenchmark(BENCHMARKS, benchmarkId))) return true;
    return task?.meta?.config?.import_only !== true;
  });
}

export function buildHotpotQaConfigState(args) {
  return buildOfficialConfigState({
    ...args,
    benchmarkId: "hotpotqa",
  });
}

export function buildLongMemEvalConfigState(args) {
  return buildOfficialConfigState({
    ...args,
    benchmarkId: "longmemeval",
  });
}

export function buildHotpotQaPreviewModel({
  backendId,
  cfg,
  currentBenchmark,
  currentDatasetRecord,
  firstValue,
  metrics,
  run,
  statusLabel,
  taskConfig,
}) {
  const config = buildHotpotQaConfigState({
    backendId,
    benchmark: currentBenchmark,
    cfg,
    currentDatasetRecord,
    firstValue,
    taskConfig,
  });
  const presentation = buildOfficialPreviewPresentation({
    benchmarkId: "hotpotqa",
    backendId,
    config,
    metrics,
  });
  return buildOfficialPreviewBase({
    benchmarkId: "hotpotqa",
    run,
    statusLabel,
    config,
    summaryItems: presentation.summaryItems,
    configLines: presentation.configLines,
    metrics,
  });
}

export function buildLongMemEvalPreviewModel({
  backendId,
  cfg,
  currentBenchmark,
  currentDatasetRecord,
  firstValue,
  metrics,
  run,
  statusLabel,
  taskConfig,
}) {
  const config = buildLongMemEvalConfigState({
    backendId,
    benchmark: currentBenchmark,
    cfg,
    currentDatasetRecord,
    firstValue,
    taskConfig,
  });
  const presentation = buildOfficialPreviewPresentation({
    benchmarkId: "longmemeval",
    backendId,
    config,
    metrics,
  });
  return buildOfficialPreviewBase({
    benchmarkId: "longmemeval",
    run,
    statusLabel,
    config,
    summaryItems: presentation.summaryItems,
    configLines: presentation.configLines,
    metrics,
  });
}

export function findLatestImportOnlyRun(benchmarkId, runs, runDetails) {
  return (runs || []).find((run) => {
    if (/import/i.test(String(run?.kind || ""))) return true;
    const detail = runDetails?.[run.run_dir] || null;
    const summary = detail?.record?.summary || run.summary || {};
    return isImportOnlySummary(benchmarkId, summary, run.name);
  }) || null;
}

export function buildImportProgressModel({
  importTask,
  importRun,
  flowStatus = null,
}) {
  if (!importTask) {
    if (importRun) {
      const rawStatus = String(importRun.status || "-").trim() || "-";
      const normalizedStatus = rawStatus.toLowerCase();
      const failed = ["fail", "failed", "error", "interrupted"].includes(normalizedStatus);
      const waiting = ["queued", "running", "pending", "stopping"].includes(normalizedStatus);
      const stageValue = failed ? "导入失败" : (waiting ? "导入收尾中" : "导入完成");
      const processedValue = failed ? "失败" : (waiting ? "收尾中" : "已完成");
      return {
        title: importRun.name || "最近一次导入",
        detail: failed
          ? `${rawStatus} · 最近一次导入未完成`
          : waiting
            ? `${rawStatus} · 导入状态待刷新`
            : `${rawStatus} · 已完成导入任务`,
        percent: failed ? 0 : (waiting ? 90 : 100),
        stage: stageValue,
        processedText: processedValue,
        stats: [
          {label: "状态", value: rawStatus},
          {label: "输出目录", value: importRun.run_dir || "-"},
          {label: "结果文件", value: importRun.output_file || "-"},
          {label: "任务类型", value: "import_only run"},
          {label: "当前阶段", value: stageValue},
          {label: "已处理 / 总数", value: processedValue},
        ],
        log: failed
          ? "最近一次导入任务失败。请先检查运行日志或重新发起导入。"
          : waiting
            ? "最近一次导入任务仍在收尾或状态尚未完全刷新。"
            : "最近一次导入已完成。当前没有运行中的注入任务。",
      };
    }
    const imported = flowStatus?.artifacts?.imported || {};
    const sessionCount = Number(imported?.session_count || 0);
    const summaryCount = Number(imported?.summary_count || 0);
    const completeCount = Number(imported?.complete_count || 0);
    const integrityStatus = String(imported?.integrity_status || "").trim().toLowerCase();
    const sampleCoverageDetail = String(imported?.sample_coverage_detail || "").trim();
    const sampleLabel = String(imported?.sample || "").trim() || "当前 scope";
    if (sessionCount > 0 || summaryCount > 0) {
      const scopeComplete = integrityStatus === "complete" && Boolean(imported?.sample_coverage_complete);
      return {
        title: "Workspace 导入状态",
        detail: scopeComplete
          ? `${sampleLabel} · 已检测到完整导入产物`
          : `${sampleLabel} · 已检测到导入产物，仍需补全范围`,
        percent: scopeComplete ? 100 : 85,
        stage: scopeComplete ? "导入完成" : "导入产物已存在",
        processedText: `${completeCount || summaryCount} / ${summaryCount || 1}`,
        stats: [
          {label: "状态", value: scopeComplete ? "workspace-ready" : "workspace-partial"},
          {label: "输出目录", value: imported?.account_path || "-"},
          {label: "结果文件", value: (Array.isArray(imported?.summaries) && imported.summaries[0]?.summary_path) || "-"},
          {label: "任务类型", value: "workspace probe"},
          {label: "当前阶段", value: scopeComplete ? "导入完成" : (sampleCoverageDetail || "已检测到导入产物")},
          {label: "已处理 / 总数", value: `${sessionCount} sessions / ${summaryCount} summaries`},
        ],
        log: scopeComplete
          ? "当前 workspace 已检测到 LoCoMo 导入产物，可以直接进入 QA。"
          : `当前 workspace 已检测到 LoCoMo 导入产物，但范围仍未完全覆盖。${sampleCoverageDetail || "建议先对齐 sample 后再继续 QA。"}`,
      };
    }
    return {
      title: "等待任务",
      detail: "当前还没有注入任务。",
      percent: 0,
      stage: "等待任务",
      processedText: "0 / 0",
      stats: [
        {label: "状态", value: "未启动"},
        {label: "输出目录", value: "-"},
        {label: "结果文件", value: "-"},
        {label: "任务类型", value: "-"},
        {label: "当前阶段", value: "等待任务"},
        {label: "已处理 / 总数", value: "0 / 0"},
      ],
      log: "等待任务...",
    };
  }

  const current = Number(importTask.progress?.current || 0);
  const total = Number(importTask.progress?.total || 0);
  const processedText = total > 0 ? `${current} / ${total}` : `${current} / ${total}`;
  const percent = total > 0 ? Math.max(0, Math.min(100, Math.round((current / total) * 100))) : 0;
  const stage = importTask.progress?.detail || "正在注入记忆";
  return {
    title: importTask.name || importTask.kind || "import",
    detail: `${importTask.status || "-"} · ${String(importTask.progress?.current || 0)}/${String(importTask.progress?.total || 0)}`,
    percent,
    stage,
    processedText,
    stats: [
      {label: "状态", value: importTask.status || "-"},
      {label: "输出目录", value: importTask.output_dir || importTask.run_dir || "-"},
      {label: "结果文件", value: importTask.output_file || "-"},
      {label: "任务类型", value: importTask.kind || "-"},
      {label: "当前阶段", value: stage},
      {label: "已处理 / 总数", value: processedText},
    ],
    log: "",
  };
}

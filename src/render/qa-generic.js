import { currentWorkbench, metricValueFormatter } from "./shared.js";

function getOfficialQaDraft(state, benchmarkId) {
  state.officialQaDrafts = state.officialQaDrafts || {};
  const key = String(benchmarkId || "").trim().toLowerCase();
  if (!state.officialQaDrafts[key] || typeof state.officialQaDrafts[key] !== "object") {
    state.officialQaDrafts[key] = {};
  }
  return state.officialQaDrafts[key];
}

function draftValue(draft, id, fallback = "") {
  if (Object.prototype.hasOwnProperty.call(draft, id)) {
    return String(draft[id] ?? "");
  }
  return String(fallback ?? "");
}

function draftChecked(draft, id, fallback = false) {
  if (Object.prototype.hasOwnProperty.call(draft, id)) {
    return Boolean(draft[id]);
  }
  return Boolean(fallback);
}

function liveOrDraftValue(_$, _queryAll, draft, id, fallback = "") {
  return draftValue(draft, id, fallback);
}

function liveOrDraftChecked(_$, _queryAll, draft, id, fallback = false) {
  return draftChecked(draft, id, fallback);
}

export function officialQaFormProfile(benchmarkId, { config, escapeHtml, field, check }) {
  if (benchmarkId === "hotpotqa") {
    return {
      datasetLabel: "HotpotQA 数据集",
      dataPlaceholder: "./dataset/full/hotpotqa_dev_distractor.json",
      countId: "wbHotpotCount",
      preTopKFields: [
        field("记忆组织", `<select id="wbHotpotQaCorpusMode"><option value="global_sentence_corpus" ${String(config.hotpotqaCorpusMode) === "global_sentence_corpus" ? "selected" : ""}>全局句子语料（推荐）</option><option value="per_question_documents" ${String(config.hotpotqaCorpusMode) === "per_question_documents" ? "selected" : ""}>逐题文档注入</option></select>`, escapeHtml),
      ],
      echoFields: [
        field("全局导入模式", `<select id="wbHotpotQaGlobalImportMode"><option value="projection" ${String(config.hotpotqaGlobalImportMode) === "projection" ? "selected" : ""}>projection 快速导入</option><option value="messages" ${String(config.hotpotqaGlobalImportMode) === "messages" ? "selected" : ""}>messages 完整导入</option></select>`, escapeHtml),
        field("评测 checkpoint", `<input id="wbHotpotQaCheckpointInterval" type="number" min="0" step="1" value="${escapeHtml(config.checkpointInterval)}">`, escapeHtml),
        field("检索模式", `<select id="wbQaRetrievalMode"><option value="search" ${String(config.retrievalMode) === "search" ? "selected" : ""}>search</option><option value="hybrid" ${String(config.retrievalMode) === "hybrid" ? "selected" : ""}>hybrid</option><option value="dense" ${String(config.retrievalMode) === "dense" ? "selected" : ""}>dense</option></select>`, escapeHtml),
        field("工具集", `<input id="wbQaToolSet" type="text" value="${escapeHtml(config.toolSet)}" placeholder="vikingbot_native_safe">`, escapeHtml),
        field("单题超时（秒）", `<input id="wbQaQuestionTimeout" type="number" min="1" step="1" value="${escapeHtml(config.questionTimeoutS)}">`, escapeHtml),
      ],
      toolEnabledLabel: "启用 EchoMemory 检索调用",
      toolLoopLabel: "模型工具循环（HotpotQA 固定关闭，避免答案污染）",
      supportsSelected: true,
    };
  }
  if (benchmarkId === "longmemeval") {
    return {
      datasetLabel: "LongMemEval 数据集",
      dataPlaceholder: "./dataset/full/longmemeval_s_cleaned.json",
      countId: "wbLongMemEvalCount",
      preTopKFields: [],
      echoFields: [
        field("QA 并发数", `<input id="wbQaParallelism" type="number" min="1" step="1" value="${escapeHtml(config.qaParallelism || "10")}">`, escapeHtml),
        field("检索模式", `<select id="wbQaRetrievalMode"><option value="search" ${String(config.retrievalMode) === "search" ? "selected" : ""}>search</option><option value="hybrid" ${String(config.retrievalMode) === "hybrid" ? "selected" : ""}>hybrid</option><option value="dense" ${String(config.retrievalMode) === "dense" ? "selected" : ""}>dense</option></select>`, escapeHtml),
        field("工具集", `<input id="wbQaToolSet" type="text" value="${escapeHtml(config.toolSet)}" placeholder="vikingbot_native_safe">`, escapeHtml),
        field("单题超时（秒）", `<input id="wbQaQuestionTimeout" type="number" min="1" step="1" value="${escapeHtml(config.questionTimeoutS)}">`, escapeHtml),
      ],
      toolEnabledLabel: "启用 EchoMemory 检索调用",
      toolLoopLabel: "模型工具循环（LongMemEval 固定关闭，避免答案污染）",
      supportsSelected: true,
    };
  }
  return {
    datasetLabel: "数据集",
    dataPlaceholder: "",
    countId: "wbQaCount",
    preTopKFields: [],
    echoFields: [],
    toolEnabledLabel: "启用检索调用",
    toolLoopLabel: "模型工具循环",
    supportsSelected: false,
  };
}

export function renderOfficialQaConfig({
  $,
  queryAll,
  check,
  escapeHtml,
  field,
  benchmarkId,
  state,
  config,
  datasetLabel,
  datasetOptionsHtml,
  dataPlaceholder,
  countId,
  countLabel = "题量",
  preTopKFields = [],
  echoFields = [],
  toolEnabledLabel,
  toolLoopLabel,
  supportsSelected = false,
}) {
  const draft = getOfficialQaDraft(state, benchmarkId);
  const dataPathValue = liveOrDraftValue($, queryAll, draft, "wbDataPath", config.dataPath);
  const countValue = liveOrDraftValue($, queryAll, draft, countId, config.questionCount);
  const modeValue = liveOrDraftValue($, queryAll, draft, "wbQaMode", "full") || "full";
  const selectedQuestionIds = liveOrDraftValue($, queryAll, draft, "wbQaQuestionIds", "");
  const topKValue = liveOrDraftValue($, queryAll, draft, "wbQaTopK", config.topK);
  const toolEnabled = liveOrDraftChecked($, queryAll, draft, "wbQaUseTools", config.toolEnabled);
  const maxIterations = liveOrDraftValue($, queryAll, draft, "wbQaMaxIterations", config.maxIterations);
  const toolSearchLimit = liveOrDraftValue($, queryAll, draft, "wbQaToolSearchLimit", config.toolSearchLimit);
  const officialEvalAfter = liveOrDraftChecked($, queryAll, draft, "wbOfficialEval", config.officialEvalAfter);
  const gate = state.officialQaGates?.[benchmarkId] || null;
  const launchBlockedCheck = gate?.ok === false
    ? ((gate.checks || []).find((item) => item.ok === false) || null)
    : null;
  const launchDisabled = gate?.ok === false;
  const launchTitle = gate?.ok === true
    ? ""
    : (gate?.ok === false
      ? (launchBlockedCheck?.message || "QA 启动检查未通过")
      : "启动时会自动执行前检查");
  const fields = [
    field(datasetLabel, `<select id="wbDatasetPreset">${datasetOptionsHtml}</select>`, escapeHtml),
    field("数据文件", `<input id="wbDataPath" type="text" value="${escapeHtml(dataPathValue)}" placeholder="${escapeHtml(dataPlaceholder)}">`, escapeHtml),
    field(countLabel, `<input id="${escapeHtml(countId)}" type="number" min="1" step="1" value="${escapeHtml(countValue)}">`, escapeHtml),
    ...(supportsSelected ? [
      field("题目范围", `<select id="wbQaMode"><option value="full"${modeValue === "full" ? " selected" : ""}>当前数据集范围</option><option value="selected"${modeValue === "selected" ? " selected" : ""}>指定题号</option></select>`, escapeHtml),
      field("Question IDs", `<input id="wbQaQuestionIds" type="text" value="${escapeHtml(selectedQuestionIds)}" placeholder="q1,q2">`, escapeHtml),
    ] : []),
    ...preTopKFields,
    field("Top K", `<input id="wbQaTopK" type="number" min="1" step="1" value="${escapeHtml(topKValue)}">`, escapeHtml),
    check(toolEnabledLabel, "wbQaUseTools", toolEnabled, false, escapeHtml),
    check(toolLoopLabel, "wbQaToolLoop", false, true, escapeHtml),
    field("最大迭代次数", `<input id="wbQaMaxIterations" type="number" min="1" step="1" value="${escapeHtml(maxIterations)}">`, escapeHtml),
    field("工具召回数", `<input id="wbQaToolSearchLimit" type="number" min="1" step="1" value="${escapeHtml(toolSearchLimit)}">`, escapeHtml),
  ];
  if (config.backendIsEchoMemory) {
    fields.push(...echoFields);
  }
  fields.push(check("运行后追加官方评测", "wbOfficialEval", officialEvalAfter, false, escapeHtml));
  fields.push(`
    <div class="wb-panel-head-actions wb-official-qa-actions">
      <button id="wbRunQaCurrentScope" class="wb-button primary" type="button"${launchDisabled ? " disabled" : ""}${launchTitle ? ` title="${escapeHtml(launchTitle)}"` : ""}>启动当前模式</button>
      ${supportsSelected ? `<button id="wbRunQaSelected" class="wb-button secondary" type="button"${modeValue === "selected" && !String(selectedQuestionIds || "").trim() ? " disabled title=\"请先填写 question ids\"" : ""}>运行指定题</button>` : ""}
    </div>
  `);
  $("wbQaConfig").innerHTML = fields.join("");
}

export function renderOfficialQaPreview({
  $,
  compactPath,
  escapeHtml,
  formatDurationSeconds,
  formatInt,
  formatPct,
  preview,
}) {
  const summaryHtml = currentWorkbench({
    title: preview.title,
    subtitle: preview.subtitle,
    path: preview.path,
    items: preview.summaryItems,
    renderMetricValue: metricValueFormatter({ formatDurationSeconds, formatInt, formatPct }),
    escapeHtml,
    compactPath,
    sections: [
      {
        title: "当前配置",
        lines: preview.configLines,
      },
    ],
  });
  $("wbQaPreview").innerHTML = summaryHtml;
}

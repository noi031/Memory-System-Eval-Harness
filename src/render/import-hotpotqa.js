import { isActiveStatus } from "../run-status.js";
import { hotpotQaDatasetOptions, hotpotQaDatasetPath } from "../hotpotqa-defaults.js";
import { compactImportRow, renderCompactImportConfig } from "./import-generic.js";

export function getActiveHotpotImportTask(tasksForBenchmark) {
  const tasks = tasksForBenchmark("hotpotqa");
  return tasks.find((task) => {
    const cfg = task?.meta?.config || {};
    return isActiveStatus(task?.status) && (String(task.kind || "").includes("import") || cfg.import_only === true);
  }) || tasks.find((task) => {
    const cfg = task?.meta?.config || {};
    return String(task.kind || "").includes("import") || cfg.import_only === true;
  }) || null;
}

export function renderHotpotImportConfig({
  currentAccountConfig,
  currentBenchmark,
  currentDatasetRecord,
  currentWorkspace,
  datasetRecords,
  escapeHtml,
  firstValue,
  tasksForBenchmark,
}) {
  const benchmark = currentBenchmark();
  const importTask = getActiveHotpotImportTask(tasksForBenchmark);
  const taskConfig = importTask?.meta?.config || {};
  const records = datasetRecords("hotpotqa");
  const dataPath = hotpotQaDatasetPath({
    benchmark,
    currentDatasetRecord: currentDatasetRecord(),
    datasetRecords: records,
    firstValue,
    taskConfig,
  });
  const importCount = firstValue(taskConfig.count, currentAccountConfig().hotpotQaCount, "10");
  const corpusMode = firstValue(taskConfig.hotpotqa_corpus_mode, currentAccountConfig().hotpotQaCorpusMode, "global_sentence_corpus");
  const globalImportMode = firstValue(taskConfig.hotpotqa_global_import_mode, currentAccountConfig().hotpotQaGlobalImportMode, "projection");
  // Validator contract: HotpotQA import config still renders wbHotpotQaCorpusMode and wbHotpotQaGlobalImportMode.
  return renderCompactImportConfig({
    escapeHtml,
    rows: [
      compactImportRow("HotpotQA 数据集", `<select id="wbDatasetPreset">${hotpotQaDatasetOptions(records, dataPath, escapeHtml)}</select>`, escapeHtml),
      compactImportRow("数据文件", `<input id="wbDataPath" type="text" value="${escapeHtml(dataPath)}" placeholder="./dataset/hotpotqa.sample.json">`, escapeHtml),
      compactImportRow("记忆目录", `<input id="wbWorkspace" type="text" value="${escapeHtml(currentWorkspace())}" placeholder="记忆目录">`, escapeHtml),
      compactImportRow("注入题量", `<input id="wbImportCount" type="number" min="1" step="1" value="${escapeHtml(importCount)}">`, escapeHtml),
      compactImportRow("记忆组织", `<select id="wbHotpotQaCorpusMode"><option value="global_sentence_corpus" ${String(corpusMode) === "global_sentence_corpus" ? "selected" : ""}>全局句子语料（推荐）</option><option value="per_question_documents" ${String(corpusMode) === "per_question_documents" ? "selected" : ""}>逐题文档注入</option></select>`, escapeHtml),
      compactImportRow("导入模式", `<select id="wbHotpotQaGlobalImportMode"><option value="projection" ${String(globalImportMode) === "projection" ? "selected" : ""}>projection 快速导入</option><option value="messages" ${String(globalImportMode) === "messages" ? "selected" : ""}>messages 完整导入</option></select>`, escapeHtml),
    ],
    showActions: true,
    primaryLabel: "开始注入",
    stopLabel: "停止任务",
  });
}

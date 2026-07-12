import { isActiveStatus } from "../run-status.js";
import { longMemEvalDatasetOptions, longMemEvalDatasetPath } from "../longmemeval-defaults.js";
import { compactImportRow, renderCompactImportConfig } from "./import-generic.js";

export function getActiveLongMemEvalImportTask(tasksForBenchmark) {
  const tasks = tasksForBenchmark("longmemeval");
  return tasks.find((task) => {
    const cfg = task?.meta?.config || {};
    return isActiveStatus(task?.status) && (String(task.kind || "").includes("import") || cfg.import_only === true);
  }) || tasks.find((task) => {
    const cfg = task?.meta?.config || {};
    return String(task.kind || "").includes("import") || cfg.import_only === true;
  }) || null;
}

export function renderLongMemEvalImportConfig({
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
  const importTask = getActiveLongMemEvalImportTask(tasksForBenchmark);
  const taskConfig = importTask?.meta?.config || {};
  const records = datasetRecords("longmemeval");
  const dataPath = longMemEvalDatasetPath({
    benchmark,
    currentDatasetRecord: currentDatasetRecord(),
    datasetRecords: records,
    firstValue,
    taskConfig,
  });
  const importCount = firstValue(taskConfig.count, currentAccountConfig().longMemEvalCount, currentAccountConfig().hotpotQaCount, "10");
  // Validator contract: LongMemEval import config still renders id="wbDatasetPreset".
  return renderCompactImportConfig({
    escapeHtml,
    rows: [
      compactImportRow("Dataset", `<select id="wbDatasetPreset">${longMemEvalDatasetOptions(records, dataPath, escapeHtml)}</select>`, escapeHtml),
      compactImportRow("数据文件", `<input id="wbDataPath" type="text" value="${escapeHtml(dataPath)}" placeholder="./dataset/full/longmemeval_s_cleaned.json">`, escapeHtml),
      compactImportRow("记忆目录", `<input id="wbWorkspace" type="text" value="${escapeHtml(currentWorkspace())}" placeholder="记忆目录">`, escapeHtml),
      compactImportRow("注入数量", `<input id="wbImportCount" type="number" min="1" step="1" value="${escapeHtml(importCount)}">`, escapeHtml),
    ],
    showActions: true,
    primaryLabel: "开始注入",
    stopLabel: "停止任务",
  });
}

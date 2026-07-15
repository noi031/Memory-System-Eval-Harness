import { renderCompactImportProgress } from "./import-generic.js";
import { buildImportProgressModel, findLatestImportOnlyRun } from "../benchmark-view-models.js";
import { renderLocomoImportConfig, renderLocomoImportProgress } from "./import-locomo.js";
import { getActiveHotpotImportTask, renderHotpotImportConfig } from "./import-hotpotqa.js";
import { getActiveLongMemEvalImportTask, renderLongMemEvalImportConfig } from "./import-longmemeval.js";
import { renderEchoAgentLiveImportConfig } from "./import-echoagent.js";

export function createImportRenderers({
  $,
  state,
  currentAccountConfig,
  currentBenchmark,
  currentDatasetRecord,
  currentWorkspace,
  datasetRecords,
  escapeHtml,
  firstValue,
  runsForBenchmark,
  tasksForBenchmark,
  visibleRunsForBenchmark,
  compactPath,
}) {
  function latestImportRun(benchmarkId) {
    const scopedRuns = typeof visibleRunsForBenchmark === "function"
      ? visibleRunsForBenchmark(benchmarkId, {
          includeImportOnly: true,
          includeIncomplete: true,
        })
      : runsForBenchmark(benchmarkId);
    return findLatestImportOnlyRun(benchmarkId, scopedRuns, state.runDetails)
      || (benchmarkId === "locomo" ? state.locomoPersistedImportRun || null : null);
  }

  const benchmarkImportRenderers = {
    locomo: () => renderLocomoImportConfig({
      currentAccountConfig,
      currentBenchmark,
      currentDatasetRecord,
      currentWorkspace,
      escapeHtml,
      firstValue,
      state,
    }),
    hotpotqa: () => renderHotpotImportConfig({
      currentAccountConfig,
      currentBenchmark,
      currentDatasetRecord,
      currentWorkspace,
      datasetRecords,
      escapeHtml,
      firstValue,
      tasksForBenchmark,
    }),
    longmemeval: () => renderLongMemEvalImportConfig({
      currentAccountConfig,
      currentBenchmark,
      currentDatasetRecord,
      currentWorkspace,
      datasetRecords,
      escapeHtml,
      firstValue,
      tasksForBenchmark,
    }),
    echoagent_live: () => renderEchoAgentLiveImportConfig({
      currentAccountConfig,
      escapeHtml,
      firstValue,
    }),
  };
  const benchmarkImportProgress = {
    locomo: (tasks) => ({
      importTask: tasks.find((task) => String(task.kind || "").includes("import")) || null,
      importRun: latestImportRun("locomo"),
    }),
    hotpotqa: () => ({
      importTask: getActiveHotpotImportTask(tasksForBenchmark),
      importRun: latestImportRun("hotpotqa"),
    }),
    longmemeval: () => ({
      importTask: getActiveLongMemEvalImportTask(tasksForBenchmark),
      importRun: latestImportRun("longmemeval"),
    }),
    echoagent_live: (tasks) => ({
      importTask: tasks.find((task) => task.kind === "echoagent_live") || null,
      importRun: null,
    }),
  };

  function renderImportConfig() {
    const benchmarkId = currentBenchmark().id;
    const renderer = benchmarkImportRenderers[benchmarkId] || benchmarkImportRenderers.locomo;
    const html = renderer();
    if (typeof html === "string") {
      $("wbImportConfig").innerHTML = html;
    }
  }

  function formatProgressStats(stats) {
    return (stats || []).map((item) => {
      const rawValue = item.value || "-";
      const displayValue = ["输出目录", "结果文件"].includes(item.label) ? compactPath(rawValue || "-") : rawValue;
      return [item.label, displayValue, rawValue];
    });
  }

  function renderImportProgress(activeBenchmark) {
    const tasks = tasksForBenchmark(activeBenchmark);
    const progressResolver = benchmarkImportProgress[activeBenchmark] || benchmarkImportProgress.locomo;
    const {importTask, importRun} = progressResolver(tasks);
    const model = buildImportProgressModel({
      importTask,
      importRun,
      flowStatus: activeBenchmark === "locomo" ? state.locomoFlowStatus : null,
    });
    $("wbImportProgress").innerHTML = activeBenchmark === "locomo"
      ? renderLocomoImportProgress({
          compactPath,
          escapeHtml,
          flowStatus: state.locomoFlowStatus,
          importRun,
          importTask,
          model,
        })
      : renderCompactImportProgress({
          percent: model.percent,
          rows: formatProgressStats(model.stats),
          escapeHtml,
        });
    if (model.log) {
      $("wbImportLogBody").textContent = model.log;
    } else {
      $("wbImportLogBody").textContent = model.stage || "";
    }
  }

  return {
    renderImportConfig,
    renderImportProgress,
  };
}

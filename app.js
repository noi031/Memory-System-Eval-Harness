import { BENCHMARKS, resolveReferenceUrl, resolveStandaloneApiBase } from "./src/config.js";
import { createApiClient } from "./src/api.js";
import { defaultBenchmarkId, getBenchmarkIds } from "./src/benchmark-registry.js";
import { createWorkbenchController } from "./src/controller.js";
import { createInitialState } from "./src/state.js";
import { $, alertUser, clearTimer, copyText, delay, localStorageAdapter, onDocument, openReferenceUrl, queryAll } from "./src/dom.js";
import {
  compactPath,
  escapeHtml,
  firstValue,
  formatDurationSeconds,
  formatInt,
  formatPct,
  tonePill,
} from "./src/utils.js";
import { createSelectors } from "./src/selectors.js";
import { createRenderers } from "./src/renderers.js";
import { createActions } from "./src/actions.js";
import { createFormReaders } from "./src/form-readers.js";

(function () {
  const standaloneApiBase = resolveStandaloneApiBase();
  const referenceUrl = resolveReferenceUrl();
  const benchmarkIds = getBenchmarkIds(BENCHMARKS);
  const initialBenchmarkId = defaultBenchmarkId(BENCHMARKS);
  const state = createInitialState({
    benchmarkIds,
    defaultBenchmarkId: initialBenchmarkId,
  });
  const { api } = createApiClient(standaloneApiBase);

  const selectors = createSelectors({ state, BENCHMARKS, $, queryAll, firstValue });
  const formReaders = createFormReaders({
    $,
    queryAll,
    currentBenchmark: selectors.currentBenchmark,
    currentWorkspace: selectors.currentWorkspace,
    state,
  });

  let renderAll = () => {};
  let refreshAllRunner = async () => {};
  let pollLogRunner = async () => {};

  const renderers = createRenderers({
    $,
    state,
    standaloneApiBase,
    BENCHMARKS,
    backendId: selectors.backendId,
    backendName: selectors.backendName,
    compactPath,
    currentAccountConfig: selectors.currentAccountConfig,
    currentBenchmark: selectors.currentBenchmark,
    currentDatasetRecord: selectors.currentDatasetRecord,
    currentRun: selectors.currentRun,
    currentWorkspace: selectors.currentWorkspace,
    datasetRecords: selectors.datasetRecords,
    escapeHtml,
    firstValue,
    formatDurationSeconds,
    formatInt,
    formatPct,
    hasRunningTasks: selectors.hasRunningTasks,
    queryAll,
    runsForBenchmark: selectors.runsForBenchmark,
    tasksForBenchmark: selectors.tasksForBenchmark,
    visibleRunsForBenchmark: selectors.visibleRunsForBenchmark,
    tonePill,
  });

  const actions = createActions({
    $,
    BENCHMARKS,
    api,
    backendId: selectors.backendId,
    compactPath,
    currentAccountConfig: selectors.currentAccountConfig,
    currentBenchmark: selectors.currentBenchmark,
    currentRun: selectors.currentRun,
    currentWorkspace: selectors.currentWorkspace,
    escapeHtml,
    firstValue,
    formReaders,
    genericQaKind: selectors.genericQaKind,
    onBootstrapState: () => renderAll(),
    qaKind: selectors.qaKind,
    prefetchLimitForBenchmark: selectors.prefetchLimitForBenchmark,
    preferredRunForBenchmark: selectors.preferredRunForBenchmark,
    runsForBenchmark: selectors.runsForBenchmark,
    state,
    tasksForBenchmark: selectors.tasksForBenchmark,
  });

  renderAll = () => renderers.renderAll();

  const controller = createWorkbenchController({
    $,
    alertUser,
    actions,
    clearTimer,
    currentBenchmark: selectors.currentBenchmark,
    currentRun: selectors.currentRun,
    defaultBenchmarkId: initialBenchmarkId,
    delay,
    legacyReferenceUrl: referenceUrl,
    localStorageAdapter,
    onDocument,
    openReferenceUrl,
    prefetchLimitForBenchmark: selectors.prefetchLimitForBenchmark,
    qaKind: selectors.qaKind,
    queryAll,
    renderQaConfig: renderers.renderQaConfig,
    renderQaPreview: renderers.renderQaPreview,
    renderAll: () => renderAll(),
    renderReportExportResult: renderers.renderReportExportResult,
    state,
    tasksForBenchmark: selectors.tasksForBenchmark,
    copyText,
  });

  async function loadBootstrapRunner(options = {}) {
    clearTimer(state.refreshTimer);
    await actions.loadBootstrap(options);
    renderAll();
    if (selectors.hasRunningTasks()) {
      state.refreshTimer = delay(() => refreshAllRunner().catch(() => {}), 3000);
    }
  }

  refreshAllRunner = async () => {
    clearTimer(state.logPollTimer);
    await actions.refreshAll(loadBootstrapRunner);
  };
  pollLogRunner = async (task, targetId) => {
    clearTimer(state.logPollTimer);
    const result = await actions.pollLog(task);
    if (targetId) $(targetId).textContent = result.text || "";
    if (result.active) {
      state.logPollTimer = delay(() => refreshAllRunner().catch(() => {}), 2500);
      return;
    }
    state.logPollTimer = delay(() => refreshAllRunner().catch(() => {}), 1200);
  };
  controller.start({ loadBootstrapRunner, refreshAllRunner, pollLogRunner });
})();

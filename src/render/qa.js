import { check, checklistCard, field, renderEmptyState, taskCard } from "./shared.js";
import { benchmarkDefaultVisibleRunOptions, getBenchmark } from "../benchmark-registry.js";
import { buildQaTaskList } from "../benchmark-view-models.js";
import { renderLocomoQaConfig, renderLocomoQaPreview } from "./qa-locomo.js";
import { renderHotpotQaConfig, renderHotpotQaPreview } from "./qa-hotpotqa.js";
import { renderLongMemEvalQaConfig, renderLongMemEvalQaPreview } from "./qa-longmemeval.js";
import { renderBenchmarkRunCards } from "./run-list.js";
import { BENCHMARKS } from "../config.js";

export function createQaRenderers({
  $,
  queryAll,
  state,
  currentAccountConfig,
  currentBenchmark,
  currentRun,
  currentDatasetRecord,
  datasetRecords,
  escapeHtml,
  formatDurationSeconds,
  firstValue,
  formatInt,
  formatPct,
  backendId,
  runsForBenchmark,
  tasksForBenchmark,
  visibleRunsForBenchmark,
  tonePill,
  compactPath,
}) {
  const benchmarkQaRenderers = {
    locomo: {
      renderConfig: () => renderLocomoQaConfig({
        $,
        currentAccountConfig,
        currentBenchmark,
        currentDatasetRecord,
        currentRun,
        escapeHtml,
        firstValue,
        check,
        field,
        state,
      }),
      renderPreview: () => renderLocomoQaPreview({
        $,
        compactPath,
        currentAccountConfig,
        currentRun,
        escapeHtml,
        firstValue,
        state,
      }),
    },
    hotpotqa: {
      renderConfig: () => renderHotpotQaConfig({
        $,
        queryAll,
        check,
        currentAccountConfig,
        currentBenchmark,
        currentRun,
        currentDatasetRecord,
        datasetRecords,
        backendId,
        escapeHtml,
        field,
        firstValue,
        state,
        tasksForBenchmark,
      }),
      renderPreview: () => renderHotpotQaPreview({
        $,
        compactPath,
        currentAccountConfig,
        currentBenchmark,
        currentDatasetRecord,
        datasetRecords,
        currentRun,
        backendId,
        escapeHtml,
        firstValue,
        formatDurationSeconds,
        formatInt,
        formatPct,
        state,
        tasksForBenchmark,
      }),
    },
    longmemeval: {
      renderConfig: () => renderLongMemEvalQaConfig({
        $,
        queryAll,
        check,
        currentAccountConfig,
        currentBenchmark,
        currentRun,
        currentDatasetRecord,
        datasetRecords,
        backendId,
        escapeHtml,
        field,
        firstValue,
        state,
        tasksForBenchmark,
      }),
      renderPreview: () => renderLongMemEvalQaPreview({
        $,
        compactPath,
        currentAccountConfig,
        currentBenchmark,
        currentDatasetRecord,
        datasetRecords,
        currentRun,
        backendId,
        escapeHtml,
        firstValue,
        formatDurationSeconds,
        formatInt,
        formatPct,
        state,
        tasksForBenchmark,
      }),
    },
  };

  function renderQaConfig() {
    const renderer = benchmarkQaRenderers[currentBenchmark().id] || benchmarkQaRenderers.locomo;
    renderer.renderConfig();
  }

  function renderQaTasks(activeBenchmark) {
    const qaTasks = buildQaTaskList(activeBenchmark, tasksForBenchmark(activeBenchmark));
    $("wbQaTasks").innerHTML = qaTasks.length
      ? qaTasks.map((task) => taskCard(task, { escapeHtml, compactPath, tonePill })).join("")
      : renderEmptyState("当前没有运行中的问答任务。", escapeHtml);
  }

  function renderQaPreview(activeBenchmark) {
    const previewPanel = $("wbQaPreview")?.closest("details");
    const previewTitle = previewPanel?.querySelector("h3 span:last-child");
    const previewSubtitle = previewPanel?.querySelector(".wb-subpanel-head-copy p");
    const previewToggleHint = previewPanel?.querySelector("summary > span:last-child");
    const qaWorkbenchGrid = $("wbQaTasks")?.closest(".wb-qa-workbench-grid");
    const qaTasksPanel = $("wbQaTasks")?.closest(".wb-subpanel");
    const activeQaTasks = buildQaTaskList(activeBenchmark, tasksForBenchmark(activeBenchmark));
    if (activeBenchmark !== "locomo") {
      const diagnosticsPanel = $("wbQaDiagnostics")?.closest("details");
      const recallPanel = $("wbQaRecallWorkbench")?.closest("details");
      if (diagnosticsPanel) diagnosticsPanel.hidden = true;
      if (recallPanel) recallPanel.hidden = true;
      if (previewPanel) previewPanel.hidden = false;
      if (previewPanel) previewPanel.open = true;
      if (previewTitle) previewTitle.textContent = "当前结果";
      if (previewSubtitle) previewSubtitle.textContent = "优先确认当前结果指标与配置摘要，再决定是否重跑或切换历史结果。";
      if (previewToggleHint) previewToggleHint.textContent = "当前展开";
      if (qaWorkbenchGrid) {
        qaWorkbenchGrid.dataset.hasTasks = activeQaTasks.length ? "true" : "false";
      }
      if (qaTasksPanel) {
        qaTasksPanel.dataset.empty = activeQaTasks.length ? "false" : "true";
      }
      $("wbQaDiagnostics").innerHTML = "";
      $("wbQaRecallWorkbench").innerHTML = "";
      const gate = state.officialQaGates?.[activeBenchmark] || null;
      $("wbQaGate").innerHTML = checklistCard({
        title: gate?.title || "QA 启动检查",
        subtitle: gate?.subtitle || "先确认数据路径、工作目录和模型连通性，再开始运行。",
        checks: gate?.checks || [],
        actions: [{ id: "wbRunQaGate", label: "运行前检查", tone: "secondary" }],
        escapeHtml,
      });
    } else {
      const diagnosticsPanel = $("wbQaDiagnostics")?.closest("details");
      const recallPanel = $("wbQaRecallWorkbench")?.closest("details");
      if (diagnosticsPanel) diagnosticsPanel.hidden = false;
      if (recallPanel) recallPanel.hidden = false;
      if (previewPanel) previewPanel.hidden = false;
      if (previewTitle) previewTitle.textContent = "题目工作台";
      if (previewSubtitle) previewSubtitle.textContent = "默认收起；只有在需要指定题、筛选或批量补跑时再展开。";
      if (previewToggleHint) previewToggleHint.textContent = "按需展开";
      if (qaWorkbenchGrid) qaWorkbenchGrid.dataset.hasTasks = "true";
      if (qaTasksPanel) qaTasksPanel.dataset.empty = "false";
    }
    const renderer = benchmarkQaRenderers[activeBenchmark] || benchmarkQaRenderers.locomo;
    renderer.renderPreview();
  }

  function renderQaRuns(activeBenchmark) {
    const benchmark = getBenchmark(BENCHMARKS, activeBenchmark);
    const runs = visibleRunsForBenchmark(activeBenchmark, {
      ...benchmarkDefaultVisibleRunOptions(benchmark),
      limit: 8,
    });
    $("wbQaRuns").innerHTML = renderBenchmarkRunCards({
      activeBenchmark,
      compactPath,
      currentRun,
      emptyMessage: "当前没有历史结果。",
      escapeHtml,
      formatInt,
      formatPct,
      runs: runs.slice(0, 8),
      state,
      tasksForBenchmark,
    });
  }

  return {
    renderQaConfig,
    renderQaPreview,
    renderQaRuns,
    renderQaTasks,
  };
}

import { benchmarkDefaultVisibleRunOptions, getBenchmark } from "../benchmark-registry.js";
import { summarizeBenchmarkRun } from "../run-metrics.js";
import { buildReportActionModel, buildReportMetricItems } from "../benchmark-view-models.js";
import { getRunPhase, getRunStatusLabel } from "../run-status.js";
import { renderBenchmarkRunCards } from "./run-list.js";
import { renderOfficialStageActions, renderOfficialStageCurrent } from "./official-stage.js";
import { BENCHMARKS } from "../config.js";
import { renderStrictBlackboxMetrics } from "./strict-blackbox.js";

export function createReportRenderers({
  $,
  state,
  currentBenchmark,
  currentRun,
  escapeHtml,
  compactPath,
  formatDurationSeconds,
  formatInt,
  formatPct,
  runsForBenchmark,
  tasksForBenchmark,
  visibleRunsForBenchmark,
}) {
  function renderReportCurrent() {
    const run = currentRun();
    const detail = run ? state.runDetails[run.run_dir] : null;
    const result = run ? state.resultSummaries[run.output_file] : null;
    const metrics = summarizeBenchmarkRun(state.activeBenchmark, run, detail, result);
    const statusLabel = getRunStatusLabel(state.activeBenchmark, run, metrics, tasksForBenchmark(state.activeBenchmark));
    renderOfficialStageCurrent({
      $,
      targetId: "wbReportCurrent",
      run,
      emptyMessage: "当前还没有结果可生成报告。",
      subtitle: `${statusLabel} · ${currentBenchmark().title}`,
      path: run?.run_dir || "-",
      items: buildReportMetricItems(state.activeBenchmark, metrics),
      noteTitle: metrics.officialNote ? "评测口径" : "",
      noteBody: metrics.officialNote || "",
      escapeHtml,
      compactPath,
      formatDurationSeconds,
      formatInt,
      formatPct,
    });
    if (metrics.strictBlackbox) {
      $("wbReportCurrent").insertAdjacentHTML("beforeend", renderStrictBlackboxMetrics(metrics.strictBlackbox, {
        escapeHtml,
      }));
    }
  }

  function renderReportActions() {
    const run = currentRun();
    const detail = run ? state.runDetails[run.run_dir] : null;
    const result = run?.output_file ? state.resultSummaries[run.output_file] : null;
    const metrics = summarizeBenchmarkRun(state.activeBenchmark, run, detail, result);
    const phase = getRunPhase(state.activeBenchmark, run, metrics, tasksForBenchmark(state.activeBenchmark));
    const actionModel = buildReportActionModel({
      benchmarkId: state.activeBenchmark,
      metrics,
      phase,
      run,
    });
    const savedExportResult = state.reportExportResult || null;
    const exportResult = savedExportResult
      && savedExportResult.benchmarkId === state.activeBenchmark
      && savedExportResult.runDir === (run?.run_dir || "")
      ? savedExportResult
      : null;
    const body = exportResult
      ? `${exportResult.title || "报告已生成"} · ${exportResult.subtitle || ""}`
      : "";
    renderOfficialStageActions({
      $,
      targetId: "wbReportActions",
      title: actionModel.title,
      subtitle: actionModel.subtitle,
      body,
      actions: [
        ...(exportResult?.actions || []),
        ...actionModel.pathButtons.map((button) => ({
          ...button,
          action: "open-path",
        })),
        {id: "wbExportReport", label: actionModel.exportLabel, tone: "primary"},
      ],
      escapeHtml,
    });
  }

  function renderReportExportResult(model) {
    const run = currentRun();
    state.reportExportResult = model
      ? {
        ...model,
        benchmarkId: state.activeBenchmark,
        runDir: run?.run_dir || "",
      }
      : null;
    renderReportActions();
  }

  function renderReportRuns(activeBenchmark) {
    const benchmark = getBenchmark(BENCHMARKS, activeBenchmark);
    const runs = visibleRunsForBenchmark(activeBenchmark, {
      ...benchmarkDefaultVisibleRunOptions(benchmark),
      limit: 12,
    });
    $("wbReportRuns").innerHTML = renderBenchmarkRunCards({
      activeBenchmark,
      compactPath,
      currentRun,
      emptyMessage: "当前没有历史结果。",
      escapeHtml,
      formatInt,
      formatPct,
      runs: runs.slice(0, 12),
      state,
      tasksForBenchmark,
    });
  }

  return {
    renderReportActions,
    renderReportCurrent,
    renderReportExportResult,
    renderReportRuns,
  };
}

import { summarizeBenchmarkRun } from "../run-metrics.js";
import { buildBenchmarkRunCardSummary } from "../benchmark-view-models.js";
import { getRunStatusLabel } from "../run-status.js";
import { renderRunList, runCard } from "./shared.js";

export function renderBenchmarkRunCards({
  activeBenchmark,
  compactPath,
  currentRun,
  emptyMessage,
  escapeHtml,
  formatInt,
  formatPct,
  runs,
  state,
  tasksForBenchmark,
}) {
  const selectedRunDir = currentRun()?.run_dir || "";
  return renderRunList(runs, (run) => {
    const detail = state.runDetails[run.run_dir] || null;
    const result = run.output_file ? state.resultSummaries[run.output_file] || null : null;
    const metrics = summarizeBenchmarkRun(activeBenchmark, run, detail, result);
    const summary = buildBenchmarkRunCardSummary(activeBenchmark, metrics);
    const statusLabel = getRunStatusLabel(activeBenchmark, run, metrics, tasksForBenchmark(activeBenchmark));
    return runCard(run, run.run_dir === selectedRunDir, activeBenchmark, summary, {
      escapeHtml,
      compactPath,
      formatInt,
      formatPct,
      statusLabel,
    });
  }, emptyMessage, escapeHtml);
}

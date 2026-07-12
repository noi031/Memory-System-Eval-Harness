import { actionCard, currentWorkbench, metricValueFormatter, renderEmptyState } from "./shared.js";

export function renderOfficialStageCurrent({
  $,
  targetId,
  run,
  emptyMessage,
  subtitle,
  path,
  items,
  noteTitle = "",
  noteBody = "",
  escapeHtml,
  compactPath,
  formatDurationSeconds,
  formatInt,
  formatPct,
}) {
  if (!run) {
    $(targetId).innerHTML = renderEmptyState(emptyMessage, escapeHtml);
    return;
  }
  $(targetId).innerHTML = currentWorkbench({
    title: run.name || run.id || "-",
    subtitle,
    path,
    items,
    renderMetricValue: metricValueFormatter({ formatDurationSeconds, formatInt, formatPct }),
    escapeHtml,
    compactPath,
    sections: noteTitle || noteBody ? [{ title: noteTitle || "说明", body: noteBody || "" }] : [],
  });
}

export function renderOfficialStageActions({
  $,
  targetId,
  title,
  subtitle,
  body = "",
  actions = [],
  escapeHtml,
}) {
  $(targetId).innerHTML = actionCard({
    title,
    subtitle,
    body,
    actions,
    escapeHtml,
  });
}

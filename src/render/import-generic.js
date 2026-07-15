import { actionButtons } from "./shared.js";

export function compactImportRow(label, controlHtml, escapeHtml) {
  return `
    <label class="wb-compact-row">
      <span class="wb-compact-label">${escapeHtml(label)}</span>
      <span class="wb-compact-control">${controlHtml}</span>
    </label>
  `;
}

export function compactImportHeader(label, escapeHtml) {
  return `
    <div class="wb-compact-header">${escapeHtml(label)}</div>
  `;
}

export function renderCompactImportConfig({
  escapeHtml,
  rows = [],
  showActions = false,
  primaryLabel = "开始注入",
  stopLabel = "停止任务",
}) {
  const renderedRows = rows.map((row) => {
    if (row.isHeader) {
      return compactImportHeader(row.label, escapeHtml);
    }
    return row;
  });
  const controls = showActions
    ? actionButtons([
        { label: primaryLabel, tone: "primary", action: "run-primary" },
        { label: stopLabel, tone: "danger-outline", action: "stop-tasks" },
      ], escapeHtml)
    : "";
  return `
    <div class="wb-compact-form">
      ${renderedRows.join("")}
      ${controls ? `<div class="wb-compact-actions">${controls}</div>` : ""}
    </div>
  `;
}

export function renderCompactImportProgress({
  percent = 0,
  rows = [],
  escapeHtml,
}) {
  return `
    <div class="wb-progress-shell">
      <div class="wb-progress-track"><span class="wb-progress-bar" style="width:${escapeHtml(String(percent))}%"></span></div>
      <div class="wb-progress-meta">${escapeHtml(String(percent))}%</div>
      <dl class="wb-progress-list">
        ${rows.map(([label, value, title]) => `
          <div class="wb-progress-row">
            <dt>${escapeHtml(label)}</dt>
            <dd title="${escapeHtml(String(title || value || "-"))}">${escapeHtml(String(value || "-"))}</dd>
          </div>
        `).join("")}
      </dl>
    </div>
  `;
}
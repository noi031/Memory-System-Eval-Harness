import { buildBenchmarkRunCardSubtitle } from "../benchmark-view-models.js";

export function field(label, inputHtml, escapeHtml) {
  return `<label class="wb-field"><span>${escapeHtml(label)}</span>${inputHtml}</label>`;
}

export function check(label, id, checked, disabled, escapeHtml) {
  return `
    <label class="wb-check">
      <input id="${escapeHtml(id)}" type="checkbox" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}>
      <span>${escapeHtml(label)}</span>
    </label>
  `;
}

export function progressCard(title, detail, stats, escapeHtml) {
  return `
    <article class="wb-card selected">
      <strong>${escapeHtml(title)}</strong>
      <small>${escapeHtml(detail)}</small>
    </article>
    <div class="wb-kv">
      ${stats.map((item) => `
        <article class="wb-kv-item">
          <span>${escapeHtml(item.label)}</span>
          <strong title="${escapeHtml(item.title || item.value)}">${escapeHtml(item.value)}</strong>
        </article>
      `).join("")}
    </div>
  `;
}

export function selectedRunSummaryCard({ title, subtitle, path, escapeHtml, compactPath }) {
  return `
    <article class="wb-card selected">
      <strong>${escapeHtml(title || "-")}</strong>
      <small>${escapeHtml(subtitle || "-")}</small>
      <p title="${escapeHtml(path || "-")}">${escapeHtml(compactPath(path || "-"))}</p>
    </article>
  `;
}

export function metricGrid(items, renderValue, escapeHtml) {
  return `
    <div class="wb-kv">
      ${items.map((item) => `
        <article class="wb-kv-item"><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(renderValue(item))}</strong></article>
      `).join("")}
    </div>
  `;
}

export function actionButton(button, escapeHtml) {
  const tone = escapeHtml(button?.tone || "secondary");
  const label = escapeHtml(button?.label || "-");
  const title = button?.title ? `title="${escapeHtml(button.title)}"` : "";
  const disabled = button?.disabled ? "disabled" : "";
  if (button?.href) {
    return `<a class="wb-button ${tone}" href="${escapeHtml(button.href)}" target="_blank" rel="noreferrer">${label}</a>`;
  }
  const attrs = [
    button?.id ? `id="${escapeHtml(button.id)}"` : "",
    'type="button"',
    button?.action ? `data-action="${escapeHtml(button.action)}"` : "",
    button?.path ? `data-path="${escapeHtml(button.path)}"` : "",
    button?.runDir ? `data-run-dir="${escapeHtml(button.runDir)}"` : "",
    title,
    disabled,
  ].filter(Boolean).join(" ");
  return `<button ${attrs} class="wb-button ${tone}">${label}</button>`;
}

export function actionButtons(buttons, escapeHtml) {
  return (buttons || []).filter(Boolean).map((button) => actionButton(button, escapeHtml)).join("");
}

export function actionCard({
  title,
  subtitle,
  body,
  bodyHtml,
  path,
  selected = false,
  actions = [],
  escapeHtml,
  compactPath,
}) {
  const renderedActions = actionButtons(actions, escapeHtml);
  return `
    <article class="wb-card ${selected ? "selected" : ""}">
      <strong>${escapeHtml(title || "-")}</strong>
      ${subtitle ? `<small>${escapeHtml(subtitle)}</small>` : ""}
      ${path ? `<p title="${escapeHtml(path)}">${escapeHtml(compactPath ? compactPath(path) : path)}</p>` : ""}
      ${bodyHtml ? `<div class="wb-card-body">${bodyHtml}</div>` : ""}
      ${body ? `<p>${escapeHtml(body)}</p>` : ""}
      ${renderedActions ? `<div class="wb-panel-head-actions">${renderedActions}</div>` : ""}
    </article>
  `;
}

export function noteListCard({
  title,
  subtitle,
  lines = [],
  escapeHtml,
}) {
  const renderedLines = (lines || []).filter(Boolean).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  return actionCard({
    title,
    subtitle,
    bodyHtml: renderedLines ? `<ul class="wb-note-list">${renderedLines}</ul>` : "",
    escapeHtml,
  });
}

export function checklistCard({
  title,
  subtitle,
  checks = [],
  actions = [],
  escapeHtml,
}) {
  const renderedChecks = (checks || []).map((item) => {
    const tone = item.ok === false ? "fail" : (item.ok === true ? "ok" : "");
    const label = item.ok === false ? "失败" : (item.ok === true ? "通过" : "待检查");
    return `
      <li class="wb-checklist-item">
        <span class="wb-checklist-head">
          <strong>${escapeHtml(item.name || "-")}</strong>
          <span class="wb-pill ${tone}">${escapeHtml(label)}</span>
        </span>
        <small>${escapeHtml(item.message || "-")}</small>
      </li>
    `;
  }).join("");
  return actionCard({
    title,
    subtitle,
    bodyHtml: renderedChecks ? `<ul class="wb-note-list wb-checklist">${renderedChecks}</ul>` : "",
    actions,
    escapeHtml,
  });
}

export function summaryStack({
  title,
  subtitle,
  path,
  items,
  renderMetricValue,
  noteTitle,
  noteBody,
  escapeHtml,
  compactPath,
}) {
  const segments = [
    selectedRunSummaryCard({ title, subtitle, path, escapeHtml, compactPath }),
    metricGrid(items || [], renderMetricValue, escapeHtml),
  ];
  if (noteTitle || noteBody) {
    segments.push(actionCard({ title: noteTitle || "-", body: noteBody || "", escapeHtml }));
  }
  return segments.join("");
}

export function currentWorkbench({
  title,
  subtitle,
  path,
  items = [],
  renderMetricValue,
  escapeHtml,
  compactPath,
  sections = [],
}) {
  const renderedMetrics = (items || []).map((item) => `
    <article class="wb-current-workbench-metric">
      <span>${escapeHtml(item.label || "-")}</span>
      <strong>${escapeHtml(renderMetricValue(item))}</strong>
    </article>
  `).join("");
  const renderedSections = (sections || []).filter((section) => {
    if (!section) return false;
    if (section.body) return true;
    if (Array.isArray(section.lines) && section.lines.length) return true;
    return false;
  }).map((section) => `
    <section class="wb-current-workbench-side-section">
      <header>${escapeHtml(section.title || "-")}</header>
      ${section.body ? `<p>${escapeHtml(section.body)}</p>` : ""}
      ${Array.isArray(section.lines) && section.lines.length ? `
        <ul class="wb-current-workbench-line-list">
          ${section.lines.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}
        </ul>
      ` : ""}
    </section>
  `).join("");
  return `
    <section class="wb-current-workbench">
      <article class="wb-current-workbench-main">
        <div class="wb-current-workbench-copy">
          <span class="wb-current-workbench-kicker">当前结果</span>
          <strong title="${escapeHtml(title || "-")}">${escapeHtml(title || "-")}</strong>
          ${subtitle ? `<small>${escapeHtml(subtitle)}</small>` : ""}
          ${path ? `<p title="${escapeHtml(path)}">${escapeHtml(compactPath ? compactPath(path) : path)}</p>` : ""}
        </div>
      </article>
      <div class="wb-current-workbench-grid">
        <section class="wb-current-workbench-metrics-shell">
          <header>关键指标</header>
          <div class="wb-current-workbench-metrics">
            ${renderedMetrics}
          </div>
        </section>
        ${renderedSections ? `<aside class="wb-current-workbench-side">${renderedSections}</aside>` : ""}
      </div>
    </section>
  `;
}

export function metricValueFormatter({
  formatDurationSeconds,
  formatInt,
  formatPct,
}) {
  return (item) => {
    if (item.type === "pct") return formatPct(item.value);
    if (item.type === "duration") return formatDurationSeconds(item.value);
    return formatInt(item.value);
  };
}

export function questionListCard({ title, subtitle, questions, escapeHtml }) {
  return `
    <article class="wb-card selected">
      <strong>${escapeHtml(title || "-")}</strong>
      <small>${escapeHtml(subtitle || "-")}</small>
      <div class="wb-question-list">
        ${(questions || []).map((item) => `
          <div class="wb-question-item">
            <span class="wb-question-id">${escapeHtml(item.question_id || "-")}</span>
            <p>${escapeHtml(item.question || "-")}</p>
          </div>
        `).join("")}
      </div>
    </article>
  `;
}

export function taskCard(task, { escapeHtml, compactPath, tonePill }) {
  const progress = task.progress || {};
  const preview = progress.qa_preview || null;
  const detail = String(progress.detail || "").trim();
  return `
    <article class="wb-card">
      <div class="wb-card-row">
        <div class="wb-card-copy">
          <strong>${escapeHtml(task.name || task.kind || "-")}</strong>
          <small>${escapeHtml([task.kind || "-", task.status || "-", `${progress.current || 0}/${progress.total || 0}`].filter(Boolean).join(" · "))}</small>
          <p title="${escapeHtml(task.output_file || task.run_dir || "-")}">${escapeHtml(compactPath(task.output_file || task.run_dir || "-"))}</p>
          ${detail ? `<p>${escapeHtml(detail)}</p>` : ""}
          ${preview ? `<p><strong>当前题目</strong> ${escapeHtml(preview.question || preview.question_id || "-")}${preview.answer ? ` · <strong>当前答案</strong> ${escapeHtml(preview.answer)}` : ""}</p>` : ""}
        </div>
        ${tonePill(task.status || "-", task.status || "")}
      </div>
    </article>
  `;
}

export function runCard(run, selected, benchmarkId, summaryOverride, { escapeHtml, compactPath, formatInt, formatPct, statusLabel }) {
  const summary = summaryOverride || run.summary || {};
  return `
    <article class="wb-card ${selected ? "selected" : ""}">
      <div class="wb-card-row">
        <div class="wb-card-copy">
          <strong>${escapeHtml(run.name || run.id || "-")}</strong>
          <small>${escapeHtml(statusLabel)} · ${escapeHtml(buildBenchmarkRunCardSubtitle(benchmarkId, summary, { formatInt, formatPct }))}</small>
          <p title="${escapeHtml(run.output_file || run.run_dir || "-")}">${escapeHtml(compactPath(run.output_file || run.run_dir || "-"))}</p>
        </div>
        <div class="wb-card-actions">
          ${actionButton({label: "设为当前", tone: "ghost", action: "select-run", runDir: run.run_dir || ""}, escapeHtml)}
          ${actionButton({label: "打开目录", tone: "ghost", action: "open-path", path: run.run_dir || ""}, escapeHtml)}
        </div>
      </div>
    </article>
  `;
}

export function renderEmptyState(message, escapeHtml) {
  return `<p class="wb-empty">${escapeHtml(message)}</p>`;
}

export function renderRunList(runs, renderRunCard, emptyMessage, escapeHtml) {
  if (!runs.length) return renderEmptyState(emptyMessage, escapeHtml);
  return runs.map(renderRunCard).join("");
}

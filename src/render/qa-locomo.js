import { icon } from "../icons.js";
import { normalizeLocomoAccountConfig } from "../locomo-qa-defaults.js";
import { summarizeBenchmarkRun } from "../run-metrics.js";
import { actionButton, actionCard, checklistCard, renderEmptyState } from "./shared.js";

function percentText(value) {
  if (value == null || value === "") return "-";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "-";
  return `${Math.round(numeric * 100)}%`;
}

function traceTone(hit) {
  const normalized = String(hit || "").trim().toLowerCase();
  if (normalized === "hit") return "ok";
  if (normalized === "miss") return "fail";
  return "warn";
}

function traceLabel(hit) {
  const normalized = String(hit || "").trim().toLowerCase();
  if (normalized === "hit") return "命中";
  if (normalized === "miss") return "未命中";
  return normalized || "部分命中";
}

function parseEvidence(raw) {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  if (typeof raw !== "string") return [];
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed;
    if (parsed && typeof parsed === "object") {
      for (const key of ["memories", "items", "results", "relevant_memory"]) {
        if (Array.isArray(parsed[key])) return parsed[key];
      }
      return [parsed];
    }
  } catch {}
  return [];
}

function compactText(value, limit = 220) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text.length <= limit) return text;
  return `${text.slice(0, Math.max(0, limit - 1))}...`;
}

function formatDurationLabel(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric < 0) return "-";
  const seconds = Math.round(numeric);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remain = seconds % 60;
  if (minutes < 60) return `${minutes}m ${remain}s`;
  const hours = Math.floor(minutes / 60);
  const minuteRemain = minutes % 60;
  return `${hours}h ${minuteRemain}m`;
}

function formatCountLabel(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "-";
  return Math.round(numeric).toLocaleString("en-US");
}

function formSection(title, bodyHtml, hint = "") {
  return `
    <section class="wb-form-section">
      <div class="wb-form-section-head">
        <strong>${title}</strong>
        ${hint ? `<small>${hint}</small>` : ""}
      </div>
      <div class="wb-form-section-body">${bodyHtml}</div>
    </section>
  `;
}

function sectionTitle(iconName, label) {
  return `
    <span class="wb-section-title">
      <span class="wb-section-title-icon" aria-hidden="true">${icon(iconName)}</span>
      <span>${label}</span>
    </span>
  `;
}

function formDetailsSection(title, bodyHtml, hint = "", { open = false, className = "" } = {}) {
  const detailsClass = ["wb-form-section", "wb-form-section-collapsible", className].filter(Boolean).join(" ");
  return `
    <details class="${detailsClass}"${open ? " open" : ""}>
      <summary class="wb-form-section-head">
        <strong>${title}</strong>
        ${hint ? `<small>${hint}</small>` : ""}
      </summary>
      <div class="wb-form-section-body">${bodyHtml}</div>
    </details>
  `;
}

function checkGrid(items) {
  return `<div class="wb-check-grid">${items.join("")}</div>`;
}

function fieldGrid(items, className = "") {
  const gridClass = ["wb-form-grid", className].filter(Boolean).join(" ");
  return `<div class="${gridClass}">${items.join("")}</div>`;
}

function chipList(items) {
  return `<div class="wb-locomo-chip-list">${items.join("")}</div>`;
}

function textChip(label, value, tone = "default", title = "") {
  return `
    <article class="wb-locomo-chip wb-locomo-chip-tone-${tone}"${title ? ` title="${title}"` : ""}>
      <span>${label}</span>
      <strong>${value}</strong>
    </article>
  `;
}

function configField(label, inputHtml, escapeHtml, { full = false } = {}) {
  return `
    <label class="wb-field${full ? " wb-field-full" : ""}">
      <span>${escapeHtml(label)}</span>
      ${inputHtml}
    </label>
  `;
}

function panelSection({ title, subtitle = "", bodyHtml = "", actionsHtml = "", className = "" }) {
  const sectionClass = ["wb-locomo-panel", className].filter(Boolean).join(" ");
  return `
    <section class="${sectionClass}">
      <div class="wb-locomo-panel-header">
        <div class="wb-locomo-panel-copy">
          <strong>${title}</strong>
          ${subtitle ? `<small>${subtitle}</small>` : ""}
        </div>
        ${actionsHtml ? `<div class="wb-locomo-panel-actions">${actionsHtml}</div>` : ""}
      </div>
      ${bodyHtml ? `<div class="wb-locomo-panel-body">${bodyHtml}</div>` : ""}
    </section>
  `;
}

function qaModeLabel(mode) {
  const normalized = String(mode || "").trim();
  if (normalized === "time") return "时间题 quick test";
  if (normalized === "selected") return "指定题号";
  if (normalized === "wrong_csv") return "错题 CSV";
  return "当前会话全量";
}

function defaultWrongCsvPath(run) {
  const output = String(run?.output_file || "").trim();
  if (!output) return "";
  const slash = Math.max(output.lastIndexOf("/"), output.lastIndexOf("\\"));
  if (slash < 0) return "wrong_questions_brief.csv";
  return `${output.slice(0, slash + 1)}wrong_questions_brief.csv`;
}

function renderConfigSummaryBar({
  currentMode,
  currentSample,
  currentQuestionLimit,
  currentQuestionIds,
  currentWrongCsv,
  currentMemoryUser,
  currentMemoryAgent,
  actionsHtml = "",
  escapeHtml,
}) {
  const selectedCount = parseQuestionIds(currentQuestionIds).length;
  const modeDetail = currentMode === "time"
    ? `${currentQuestionLimit || "0"} 题`
    : currentMode === "selected"
      ? (selectedCount ? `${selectedCount} 个 question id` : "未填写 question ids")
      : currentMode === "wrong_csv"
        ? (currentWrongCsv ? compactText(currentWrongCsv, 56) : "未填写错题 CSV")
        : "当前 sample 全量";
  const items = [
    { label: "运行模式", value: qaModeLabel(currentMode) },
    { label: "测试会话", value: currentSample === "all" ? "全部对话" : currentSample },
    { label: "当前范围", value: modeDetail },
    { label: "Memory User", value: currentMemoryUser || "default" },
    { label: "Memory Agent", value: currentMemoryAgent || "default" },
  ];
  return `
    <section class="wb-locomo-config-summary-bar">
      <div class="wb-locomo-config-summary-head">
        <div class="wb-locomo-config-summary-meta">
          <strong>当前配置</strong>
          <small>先确认范围和 identity，再决定是否做 quick test、指定题或错题恢复。</small>
        </div>
        ${actionsHtml ? `<div class="wb-locomo-config-summary-actions">${actionsHtml}</div>` : ""}
      </div>
      <div class="wb-locomo-config-summary">
        ${items.map((item) => `
          <article class="wb-locomo-config-summary-item">
            <span>${escapeHtml(item.label)}</span>
            <strong title="${escapeHtml(String(item.value || "-"))}">${escapeHtml(String(item.value || "-"))}</strong>
          </article>
        `).join("")}
      </div>
    </section>
  `;
}

function legacyDiagnosisLabels(row = {}, evidence = [], detail = null) {
  if (Array.isArray(detail?.diagnostics?.diagnosis) && detail.diagnostics.diagnosis.length) {
    return [...new Set(detail.diagnostics.diagnosis.map((item) => String(item || "").trim()).filter(Boolean))];
  }
  const result = String(row.result || row.simple_grade || detail?.row?.result || detail?.row?.simple_grade || "").toUpperCase();
  if (result === "CORRECT" || result === "MATCH") return ["Correct"];
  const retrievalStatus = String(row.retrieval_status || detail?.diagnostics?.retrieval_status || "").toLowerCase();
  const answerStatus = String(row.answer_status || detail?.diagnostics?.answer_status || "").toLowerCase();
  const reasoning = String(row.reasoning || detail?.judge?.reasoning || detail?.judge?.reason || row.judge_reason || row.judge_reasoning || "").toLowerCase();
  const retrievalCount = Number(row.retrieval_count || detail?.diagnostics?.retrieval_count || evidence.length || 0);
  const memoryCount = Number(row.memory_hit_count || detail?.diagnostics?.memory_hit_count || 0);
  const response = String(row.response || "").trim().toLowerCase();
  const gold = String(row.answer || row.gold_answer || "").trim().toLowerCase();
  const labels = [];
  if (!retrievalCount || !evidence.length) labels.push("Empty Retrieval");
  else if (!memoryCount || (retrievalStatus && retrievalStatus !== "ok")) labels.push("Retrieval Miss");
  if (labels.length === 0 && evidence.length && result === "WRONG") labels.push("Evidence Found But Unused");
  if (
    reasoning.includes("format")
    || reasoning.includes("same date")
    || (response && gold && response.replace(/[*_`]/g, "") === gold.replace(/[*_`]/g, "") && result === "WRONG")
  ) {
    labels.push("Answer Format Error");
  }
  if (answerStatus === "failed") labels.push("Reasoning Failure");
  if (reasoning.includes("considered correct") && result === "WRONG") labels.push("Judge Ambiguous");
  if (!labels.length) labels.push("Judge Ambiguous");
  return [...new Set(labels)];
}

function renderDiagnosticsCard({ diagnostics, escapeHtml, compactPath, currentRun }) {
  if (!diagnostics) {
    return panelSection({
      title: "QA 诊断摘要",
      subtitle: "运行 QA 后，这里会显示缺失题、失败题和覆盖率诊断。",
      actionsHtml: actionButton({ id: "wbRefreshQaDiagnostics", label: "刷新诊断", tone: "secondary" }, escapeHtml),
      className: "wb-locomo-panel-diagnostics",
    });
  }
  const actions = [
    actionButton({ id: "wbRefreshQaDiagnostics", label: "刷新诊断", tone: "secondary" }, escapeHtml),
  ];
  if (Number(diagnostics.retryable_failed_questions || 0) > 0) {
    actions.push(actionButton({ id: "wbLoadFailedToSelected", label: "失败题转 selected", tone: "ghost" }, escapeHtml));
  }
  if (Number(diagnostics.missing_questions_count || 0) > 0) {
    actions.push(actionButton({ id: "wbLoadMissingToSelected", label: "缺失题转 selected", tone: "ghost" }, escapeHtml));
  }
  const diag = diagnostics.diagnosis_summary || {};
  const breakdown = Array.isArray(diag.failure_breakdown) ? diag.failure_breakdown : [];
  const pending = Number(diagnostics.summary?.result_counts?.UNSCORED || 0);
  const summaryItems = [
    { label: "Expected", value: diagnostics.expected_questions ?? "-" },
    { label: "Unique", value: diagnostics.unique_question_ids ?? "-" },
    { label: "Missing", value: diagnostics.missing_questions_count || 0 },
    { label: "Retry Failed", value: diagnostics.retryable_failed_questions || 0 },
    { label: "Duplicate IDs", value: diagnostics.duplicate_question_ids_count || 0 },
    { label: "Pending", value: pending },
    { label: "Accuracy", value: diag.accuracy == null ? "待判分" : percentText(diag.accuracy) },
    { label: "Coverage", value: percentText(diag.memory_coverage) },
    { label: "Hit Rate", value: percentText(diag.retrieval_hit_rate) },
    { label: "Evidence Used", value: percentText(diag.evidence_used_rate) },
    { label: "Empty Retrieval", value: diag.empty_retrieval_count || 0 },
    { label: "Retrieval Miss", value: diag.retrieval_miss_count || 0 },
    { label: "Memory Missing", value: diag.memory_missing_count || 0 },
    { label: "Reasoning Failure", value: diag.reasoning_failure_count || 0 },
    { label: "Answer Format Error", value: diag.answer_format_error_count || 0 },
    { label: "Judge Ambiguous", value: diag.judge_ambiguous_count || 0 },
  ];
  const headlineItems = [
    { label: "Accuracy", value: diag.accuracy == null ? "待判分" : percentText(diag.accuracy) },
    { label: "Coverage", value: percentText(diag.memory_coverage) },
    { label: "Hit Rate", value: percentText(diag.retrieval_hit_rate) },
    { label: "Missing", value: diagnostics.missing_questions_count || 0 },
    { label: "Retry Failed", value: diagnostics.retryable_failed_questions || 0 },
  ];
  const issueStripItems = [
    { label: "Empty Retrieval", value: diag.empty_retrieval_count || 0, tone: (diag.empty_retrieval_count || 0) > 0 ? "warn" : "ok" },
    { label: "Retrieval Miss", value: diag.retrieval_miss_count || 0, tone: (diag.retrieval_miss_count || 0) > 0 ? "warn" : "ok" },
    { label: "Reasoning Failure", value: diag.reasoning_failure_count || 0, tone: (diag.reasoning_failure_count || 0) > 0 ? "fail" : "ok" },
    { label: "Judge Ambiguous", value: diag.judge_ambiguous_count || 0, tone: (diag.judge_ambiguous_count || 0) > 0 ? "warn" : "ok" },
  ];
  const secondaryItems = summaryItems.filter((item) => !headlineItems.some((headline) => headline.label === item.label));
  const issueItems = [];
  if (Array.isArray(diagnostics.missing_examples) && diagnostics.missing_examples.length) {
    issueItems.push(`<li><strong>缺失示例</strong> ${escapeHtml(diagnostics.missing_examples.slice(0, 3).map((item) => item.question_id).filter(Boolean).join(" / "))}</li>`);
  }
  if (Array.isArray(diagnostics.retryable_failed_examples) && diagnostics.retryable_failed_examples.length) {
    issueItems.push(`<li><strong>失败示例</strong> ${escapeHtml(diagnostics.retryable_failed_examples.slice(0, 3).map((item) => item.question_id).filter(Boolean).join(" / "))}</li>`);
  }
  const breakdownHtml = breakdown.length
    ? `
      <div class="wb-locomo-breakdown">
        ${breakdown.slice(0, 6).map((item) => `
          <div class="wb-locomo-breakdown-row">
            <strong>${escapeHtml(item.label || item.mode || "-")}</strong>
            <span>${escapeHtml(String(item.count || 0))}</span>
            <span>${escapeHtml(`${Number(item.percentage || 0).toFixed(1)}%`)}</span>
            <small title="${escapeHtml(item.example_question || "")}">${escapeHtml(item.example_question || "-")}</small>
          </div>
        `).join("")}
      </div>
    `
    : `<p class="wb-empty">当前没有 failure breakdown 预览。</p>`;
  const artifactButtons = [
    { label: "结果 CSV", path: diagnostics?.artifacts?.qa_results_csv || currentRun?.output_file || "" },
    { label: "Judge JSON", path: diagnostics?.artifacts?.judge_results_json || "" },
    { label: "诊断摘要", path: diagnostics?.artifacts?.diagnosis_summary_json || "" },
    { label: "失败分析", path: diagnostics?.artifacts?.failure_diagnosis_json || "" },
    { label: "Trace JSONL", path: diagnostics?.artifacts?.retrieval_traces_jsonl || "" },
  ].filter((item) => String(item.path || "").trim());
  const artifactHtml = artifactButtons.length
    ? `
      <details class="wb-locomo-subsection wb-locomo-diagnostics-artifacts">
        <summary class="wb-locomo-subsection-summary">
          <strong>Artifacts</strong>
          <small>直接打开当前结果相关产物</small>
        </summary>
        <div class="wb-locomo-subsection-body">
          <div class="wb-panel-head-actions">
            ${artifactButtons.map((item) => actionButton({
              label: item.label,
              tone: "ghost",
              action: "open-path",
              path: item.path,
            }, escapeHtml)).join("")}
          </div>
        </div>
      </details>
    `
    : "";
  return panelSection({
    title: "QA 诊断摘要",
    subtitle: currentRun?.output_file ? `当前结果 ${escapeHtml(compactPath(currentRun.output_file))}` : "来自 /api/qa-diagnostics 的当前结果诊断。",
    actionsHtml: actions.join(""),
    className: "wb-locomo-panel-diagnostics",
    bodyHtml: `
      <div class="wb-locomo-diagnostics-layout">
        <section class="wb-locomo-diagnostics-primary">
      <div class="wb-locomo-headline-metrics">
        ${headlineItems.map((item) => `
          <article class="wb-locomo-headline-item">
            <span>${escapeHtml(item.label)}</span>
            <strong>${escapeHtml(String(item.value))}</strong>
          </article>
        `).join("")}
      </div>
      <div class="wb-locomo-issue-strip">
        ${issueStripItems.map((item) => `
          <article class="wb-locomo-issue-item ${escapeHtml(item.tone)}">
            <span>${escapeHtml(item.label)}</span>
            <strong>${escapeHtml(String(item.value))}</strong>
          </article>
        `).join("")}
      </div>
      <div class="wb-locomo-diag-grid">
        ${secondaryItems.map((item) => `
          <article class="wb-locomo-diag-item">
            <span>${escapeHtml(item.label)}</span>
            <strong>${escapeHtml(String(item.value))}</strong>
          </article>
        `).join("")}
      </div>
      <p class="wb-locomo-summary-line">
        <strong>链路诊断</strong> · 期望 ${escapeHtml(String(diagnostics.expected_questions ?? "-"))} 题 · 当前唯一题 ${escapeHtml(String(diagnostics.unique_question_ids ?? "-"))} ·
        缺失 ${escapeHtml(String(diagnostics.missing_questions_count || 0))} · 可重跑失败 ${escapeHtml(String(diagnostics.retryable_failed_questions || 0))} ·
        重复 ${escapeHtml(String(diagnostics.duplicate_question_ids_count || 0))} · 待判 ${escapeHtml(String(pending))}
      </p>
        </section>
        <section class="wb-locomo-diagnostics-secondary">
          <details class="wb-locomo-subsection wb-locomo-diagnostics-breakdown">
            <summary class="wb-locomo-subsection-summary">
              <strong>Failure Breakdown</strong>
              <small>优先显示当前结果里最主要的失败类型</small>
            </summary>
            <div class="wb-locomo-subsection-body">
              ${breakdownHtml}
            </div>
          </details>
          ${issueItems.length ? `
            <details class="wb-locomo-subsection wb-locomo-diagnostics-notes">
              <summary class="wb-locomo-subsection-summary">
                <strong>Examples</strong>
                <small>保留缺失题和失败题示例</small>
              </summary>
              <div class="wb-locomo-subsection-body">
                <ul class="wb-note-list wb-note-list-compact">${issueItems.join("")}</ul>
              </div>
            </details>
          ` : ""}
          ${artifactHtml}
        </section>
      </div>
    `,
  });
}

function renderRecallWorkbench({ diagnostics, recallPreview, detail, selection, currentRun, escapeHtml, filters, compactPath }) {
  const traceRows = Array.isArray(diagnostics?.retrieval_trace_preview) ? diagnostics.retrieval_trace_preview : [];
  const previewRows = Array.isArray(recallPreview?.rows) ? recallPreview.rows : [];
  const baseRows = previewRows.length ? previewRows : traceRows;
  if (!baseRows.length) {
    return panelSection({
      title: "Recall Workbench",
      subtitle: "运行 QA 后，这里会优先读取 retrieval trace；没有 trace 时回退到当前 CSV 结果预览。",
      bodyHtml: `<p class="wb-empty">当前结果还没有 retrieval trace 预览。</p>`,
      actionsHtml: actionButton({ id: "wbRefreshQaRecallDetail", label: "刷新明细", tone: "secondary" }, escapeHtml),
      className: "wb-locomo-panel-recall",
    });
  }
  const recallQuery = String(filters?.query || "").trim().toLowerCase();
  const rows = recallQuery
    ? baseRows.filter((row) => {
      const previewEvidence = Array.isArray(row.top_k) && row.top_k.length ? row.top_k : parseEvidence(row.relevant_memory);
      const labels = legacyDiagnosisLabels(row, previewEvidence, null).join(" ");
      const text = [
        row.question_id,
        row.sample_id,
        row.category,
        row.question,
        row.gold_answer,
        row.answer,
        row.prediction,
        row.response,
        row.retrieval_query,
        row.retrieval_query_plan,
        labels,
      ].join("\n").toLowerCase();
      return text.includes(recallQuery);
    })
    : baseRows;
  const run = currentRun ? currentRun() : null;
  const toolbarHtml = `
    <div class="wb-locomo-recall-toolbar">
      <label class="wb-field wb-recall-select">
        <span>筛选题目</span>
        <input id="wbRecallSearch" type="text" value="${escapeHtml(String(filters?.query || ""))}" placeholder="按 question id / 文本 / diagnosis 搜索">
      </label>
      <div class="wb-locomo-pill-row">
        <span class="wb-pill">${escapeHtml(`${rows.length}/${baseRows.length} visible`)}</span>
      </div>
    </div>
  `;
  if (!rows.length) {
    return panelSection({
      title: "Recall Workbench",
      subtitle: escapeHtml(`当前筛选没有命中；总 trace ${baseRows.length} 条。`),
      actionsHtml: actionButton({ id: "wbRefreshQaRecallDetail", label: "刷新明细", tone: "secondary" }, escapeHtml),
      className: "wb-locomo-panel-recall wb-locomo-recall-workbench",
      bodyHtml: `
        ${toolbarHtml}
        <p class="wb-empty">没有匹配当前筛选条件的 recall 题目。</p>
      `,
    });
  }
  const selectionPath = String(selection?.path || "").trim();
  const currentPath = String(run?.output_file || "").trim();
  const currentQuestionId = selectionPath === currentPath ? String(selection?.questionId || "").trim() : "";
  const currentIndex = selectionPath === currentPath ? String(selection?.index ?? "").trim() : "";
  const selectedRow = rows.find((row) => String(row.question_id || "").trim() === currentQuestionId)
    || rows.find((row) => String(row._row_index ?? "").trim() === currentIndex)
    || rows[0];
  const currentDetail = detail?.row ? detail : null;
  const detailRow = currentDetail?.row || {};
  const detailDiagnostics = currentDetail?.diagnostics || {};
  const relevantMemory = Array.isArray(currentDetail?.relevant_memory) ? currentDetail.relevant_memory : [];
  const evidenceRows = relevantMemory.length ? relevantMemory : (Array.isArray(selectedRow.top_k) ? selectedRow.top_k : parseEvidence(selectedRow.relevant_memory));
  const labels = legacyDiagnosisLabels(selectedRow, evidenceRows, currentDetail);
  const healthStatus = detailDiagnostics.health_status || selectedRow.health_status || "-";
  const retrievalStatus = detailDiagnostics.retrieval_status || selectedRow.retrieval_status || "-";
  const answerStatus = detailDiagnostics.answer_status || selectedRow.answer_status || "-";
  const modelStatus = detailDiagnostics.model_status || selectedRow.model_status || "-";
  const evidenceMode = detailDiagnostics.retrieval_mode || selectedRow.retrieval_mode || selectedRow.retrieval_query_mode || "-";
  const memoryHitCount = detailDiagnostics.memory_hit_count ?? selectedRow.memory_hit_count ?? "-";
  const retrievalTokens = detailDiagnostics.retrieval_tokens_est ?? selectedRow.retrieval_tokens_est ?? "-";
  const answerTokens = detailDiagnostics.answer_total_tokens ?? selectedRow.answer_total_tokens ?? "-";
  const archiveFallbackCount = detailDiagnostics.archive_fallback_count ?? selectedRow.archive_fallback_count ?? 0;
  const retrievalError = String(detailDiagnostics.retrieval_error || selectedRow.retrieval_error || "").trim();
  const modelError = String(detailDiagnostics.model_error || selectedRow.model_error || "").trim();
  const contextPreview = String(currentDetail?.context || detailRow.context_preview || selectedRow.context_preview || "").trim();
  const selectorQuestionId = String(selectedRow?.question_id || "").trim();
  const selectorIndex = String(selectedRow?._row_index ?? "");
  const selector = `
    <label class="wb-field wb-recall-select">
      <span>查看题目</span>
      <select id="wbRecallQuestion">
        ${rows.map((row) => {
          const qid = String(row.question_id || "").trim();
          const rowIndex = String(row._row_index ?? "");
          const selected = (qid && qid === selectorQuestionId) || (!qid && rowIndex === selectorIndex) ? "selected" : "";
          return `<option value="${escapeHtml(rowIndex)}" data-question-id="${escapeHtml(qid)}" ${selected}>${escapeHtml(`${qid || `row-${rowIndex || "-"}`} · ${row.sample_id || "-"} · ${compactText(row.question || "-", 56)}`)}</option>`;
        }).join("")}
      </select>
    </label>
  `;
  const judgeResult = currentDetail?.judge?.result || detailRow.result || detailRow.simple_grade || "-";
  const judgeReason = currentDetail?.judge?.reasoning || detailRow.reasoning || detailRow.judge_reason || detailDiagnostics.diagnosis_reason || "-";
  const questionText = detailRow.question || selectedRow.question || "-";
  const retrievalQueryText = currentDetail?.retrieval_query
    || detailRow.retrieval_query
    || detailRow.retrieval_query_plan
    || selectedRow.retrieval_query
    || selectedRow.retrieval_query_plan
    || selectedRow.native_prompt
    || selectedRow.question
    || "-";
  const predictionText = detailRow.response || selectedRow.prediction || selectedRow.response || "-";
  const goldText = detailRow.answer || selectedRow.gold_answer || selectedRow.answer || "-";
  const comparisonItems = [
    { label: "Question ID", value: selectedRow.question_id || "-" },
    { label: "Sample", value: selectedRow.sample_id || "-" },
    { label: "Category", value: selectedRow.category ? `C${selectedRow.category}` : "-" },
    { label: "Judge", value: judgeResult },
    { label: "Hit Count", value: currentDetail?.diagnostics?.memory_hit_count || detailRow.memory_hit_count || "-" },
    { label: "Top K", value: currentDetail?.top_k || detailRow.top_k || detailRow.tool_search_limit || "-" },
  ];
  const tracePreviewRows = rows;
  const tracePreview = tracePreviewRows.length
    ? `
      <div class="wb-locomo-trace-list">
        ${tracePreviewRows.slice(0, 8).map((row, rowIndex) => {
          const previewEvidence = Array.isArray(row.top_k) && row.top_k.length ? row.top_k : parseEvidence(row.relevant_memory);
          const rowLabels = legacyDiagnosisLabels(row, previewEvidence, null);
          return `
            <details class="wb-locomo-trace-card" ${String(row.question_id || "").trim() === String(selectedRow.question_id || "").trim() ? "open" : ""}>
              <summary class="wb-locomo-trace-summary">
                <strong>${escapeHtml(row.question_id || `row-${rowIndex + 1}`)} · ${escapeHtml(row.sample_id || "-")} · C${escapeHtml(row.category || "-")}</strong>
                <span>${rowLabels.slice(0, 4).map((label) => `<span class="wb-pill ${traceTone(label)}">${escapeHtml(label)}</span>`).join("")}</span>
              </summary>
              <div class="wb-locomo-trace-body-stack">
                <div class="wb-locomo-qa-pairs">
                  <div class="wb-locomo-qa-pair">
                    <span>Question</span>
                    <p>${escapeHtml(row.question || "-")}</p>
                  </div>
                  <div class="wb-locomo-qa-pair">
                    <span>Retrieval Query</span>
                    <p>${escapeHtml(compactText(String(row.retrieval_query || row.retrieval_query_plan || row.native_prompt || row.question || ""), 260))}</p>
                  </div>
                  <div class="wb-locomo-qa-pair">
                    <span>Prediction</span>
                    <p>${escapeHtml(row.prediction || row.response || "-")}</p>
                  </div>
                  <div class="wb-locomo-qa-pair">
                    <span>Gold</span>
                    <p>${escapeHtml(row.gold_answer || row.answer || "-")}</p>
                  </div>
                </div>
                ${previewEvidence.length ? `
                  <div class="wb-locomo-trace-table">
                    <div class="wb-locomo-trace-head wb-locomo-trace-head-wide">
                      <span>Rank</span>
                      <span>Score</span>
                      <span>Memory</span>
                      <span>Source</span>
                      <span>Hit</span>
                    </div>
                    ${previewEvidence.slice(0, 5).map((item, index) => {
                      const memoryId = item.memory_id || item.id || item.uri || item.ref || "-";
                      const score = item.score == null || item.score === "" ? "-" : Number(item.score).toFixed(3);
                      const source = item.source || item.session || item.conversation_id || item.segment || "-";
                      const hit = item.hit || item.label || "-";
                      return `
                        <div class="wb-locomo-trace-row wb-locomo-trace-row-wide">
                          <span>${escapeHtml(String(item.rank || index + 1))}</span>
                          <span>${escapeHtml(score)}</span>
                          <span title="${escapeHtml(String(memoryId))}">${escapeHtml(compactText(String(memoryId), 42) || "-")}</span>
                          <span title="${escapeHtml(String(source))}">${escapeHtml(compactText(String(source), 38) || "-")}</span>
                          <span class="wb-pill ${traceTone(hit)}">${escapeHtml(traceLabel(hit))}</span>
                        </div>
                        ${item.content || item.text || item.body ? `<p class="wb-locomo-trace-body">${escapeHtml(compactText(item.content || item.text || item.body || "", 260))}</p>` : ""}
                      `;
                    }).join("")}
                  </div>
                ` : `<p class="wb-empty">这道题当前没有可展开的 recall memory。</p>`}
              </div>
            </details>
          `;
        }).join("")}
      </div>
    `
    : "";
  const healthGrid = [
    { label: "Health", value: healthStatus || "-" },
    { label: "Retrieval", value: retrievalStatus || "-" },
    { label: "Answer", value: answerStatus || "-" },
    { label: "Model", value: modelStatus || "-" },
    { label: "Mode", value: evidenceMode || "-" },
    { label: "Memory Hits", value: memoryHitCount == null || memoryHitCount === "" ? "-" : String(memoryHitCount) },
    { label: "Retrieval Tokens", value: retrievalTokens == null || retrievalTokens === "" ? "-" : String(retrievalTokens) },
    { label: "Answer Tokens", value: answerTokens == null || answerTokens === "" ? "-" : String(answerTokens) },
    { label: "Archive Fallback", value: String(archiveFallbackCount || 0) },
  ];
  const topKTable = evidenceRows.length
    ? `
      <div class="wb-locomo-trace-table">
        <div class="wb-locomo-trace-head wb-locomo-trace-head-wide">
          <span>Rank</span>
          <span>Score</span>
          <span>Memory</span>
          <span>Source</span>
          <span>Hit</span>
        </div>
        ${evidenceRows.slice(0, 8).map((item, index) => {
          const memoryId = item.memory_id || item.id || item.uri || item.ref || "-";
          const score = item.score == null || item.score === "" ? "-" : Number(item.score).toFixed(3);
          const source = item.source || item.session || item.conversation_id || "-";
          const hit = item.hit || item.label || "-";
          return `
            <div class="wb-locomo-trace-row wb-locomo-trace-row-wide">
              <span>${escapeHtml(String(item.rank || index + 1))}</span>
              <span>${escapeHtml(score)}</span>
              <span title="${escapeHtml(String(memoryId))}">${escapeHtml(String(memoryId))}</span>
              <span title="${escapeHtml(String(source))}">${escapeHtml(compactText(String(source), 40) || "-")}</span>
              <span class="wb-pill ${traceTone(hit)}">${escapeHtml(traceLabel(hit))}</span>
            </div>
            ${item.content || item.text || item.body ? `<p class="wb-locomo-trace-body">${escapeHtml(compactText(item.content || item.text || item.body || "", 280))}</p>` : ""}
          `;
        }).join("")}
      </div>
    `
    : `<p class="wb-empty">这道题当前没有可展开的 recall memory。</p>`;
  const artifactButtons = [
    { label: "结果 CSV", path: diagnostics?.artifacts?.qa_results_csv || currentPath || "" },
    { label: "Trace JSONL", path: diagnostics?.artifacts?.retrieval_traces_jsonl || "" },
    { label: "失败分析", path: diagnostics?.artifacts?.failure_diagnosis_json || "" },
  ].filter((item) => String(item.path || "").trim());
  return panelSection({
    title: "Recall Workbench",
    subtitle: escapeHtml(`${rows.length} 条 trace 预览；优先查看当前选中题目的检索和判因。`),
    actionsHtml: actionButton({ id: "wbRefreshQaRecallDetail", label: "刷新明细", tone: "secondary" }, escapeHtml),
    className: "wb-locomo-panel-recall wb-locomo-recall-workbench",
    bodyHtml: `
      <div class="wb-locomo-recall-overview">
        <div class="wb-locomo-recall-toolbar">
          ${selector}
          <label class="wb-field wb-recall-select">
            <span>筛选题目</span>
            <input id="wbRecallSearch" type="text" value="${escapeHtml(String(filters?.query || ""))}" placeholder="按 question id / 文本 / diagnosis 搜索">
          </label>
          <div class="wb-locomo-pill-row">
            <span class="wb-pill">${escapeHtml(`${rows.length}/${baseRows.length} visible`)}</span>
            ${labels.slice(0, 4).map((label) => `<span class="wb-pill warn">${escapeHtml(String(label))}</span>`).join("")}
          </div>
        </div>
        <div class="wb-locomo-headline-metrics wb-locomo-headline-metrics-compact">
          ${comparisonItems.map((item) => `
            <article class="wb-locomo-headline-item">
              <span>${escapeHtml(item.label)}</span>
              <strong title="${escapeHtml(String(item.value || "-"))}">${escapeHtml(String(item.value || "-"))}</strong>
            </article>
          `).join("")}
        </div>
      </div>
      <div class="wb-locomo-recall-shell">
        <div class="wb-locomo-focus-grid">
          <section class="wb-locomo-focus-main wb-locomo-recall-focus">
            <div class="wb-locomo-block wb-locomo-recall-question">
              <div class="wb-locomo-block-head">
                <strong>Question / Answer Review</strong>
                <small>先看题目、预测答案和标准答案，再判断检索还是推理出了问题。</small>
              </div>
              <div class="wb-locomo-qa-pairs wb-locomo-qa-pairs-single">
                <div class="wb-locomo-qa-pair">
                  <span>Question</span>
                  <p>${escapeHtml(questionText)}</p>
                </div>
                <div class="wb-locomo-qa-pair">
                  <span>Retrieval Query</span>
                  <p>${escapeHtml(retrievalQueryText)}</p>
                </div>
              </div>
              <div class="wb-locomo-answer-compare">
                <section class="wb-locomo-answer-card">
                  <span>Prediction</span>
                  <p>${escapeHtml(predictionText)}</p>
                </section>
                <section class="wb-locomo-answer-card">
                  <span>Gold Answer</span>
                  <p>${escapeHtml(goldText)}</p>
                </section>
              </div>
            </div>
            <div class="wb-locomo-block wb-locomo-recall-evidence">
              <div class="wb-locomo-block-head">
                <strong>Retrieved Evidence</strong>
                <small>优先显示当前题的 recall memory 与 hit 状态。</small>
              </div>
              ${topKTable}
            </div>
          </section>
          <section class="wb-locomo-focus-side wb-locomo-recall-side">
            <div class="wb-locomo-block wb-locomo-recall-judge">
              <div class="wb-locomo-block-head">
                <strong>Judge / Diagnostics</strong>
                <small>结合结果行和问题明细接口给出当前题的状态。</small>
              </div>
              <div class="wb-locomo-qa-pairs wb-locomo-qa-pairs-single">
                <div class="wb-locomo-qa-pair">
                  <span>Judge Result</span>
                  <p>${escapeHtml(judgeResult)}</p>
                </div>
                <div class="wb-locomo-qa-pair">
                  <span>Diagnosis Labels</span>
                  <p>${escapeHtml(labels.join(" / ") || "-")}</p>
                </div>
                <div class="wb-locomo-qa-pair">
                  <span>Judge Reason</span>
                  <p>${escapeHtml(judgeReason)}</p>
                </div>
              </div>
              <div class="wb-locomo-diag-grid wb-locomo-diag-grid-compact">
                ${healthGrid.map((item) => `
                  <article class="wb-locomo-diag-item">
                    <span>${escapeHtml(item.label)}</span>
                    <strong title="${escapeHtml(String(item.value || "-"))}">${escapeHtml(String(item.value || "-"))}</strong>
                  </article>
                `).join("")}
              </div>
              ${retrievalError ? `<p class="wb-locomo-summary-line"><strong>检索错误</strong> · ${escapeHtml(retrievalError)}</p>` : ""}
              ${modelError ? `<p class="wb-locomo-summary-line"><strong>模型错误</strong> · ${escapeHtml(modelError)}</p>` : ""}
            </div>
            ${contextPreview ? `
              <details class="wb-locomo-subsection wb-locomo-recall-context" open>
                <summary class="wb-locomo-subsection-summary">
                  <strong>Context Preview</strong>
                  <small>当前题的上下文片段与判因一起看更容易定位问题。</small>
                </summary>
                <div class="wb-locomo-subsection-body">
                  <p class="wb-locomo-query">${escapeHtml(contextPreview)}</p>
                </div>
              </details>
            ` : ""}
          </section>
        </div>
      </div>
      ${tracePreview ? `
        <details class="wb-locomo-subsection wb-locomo-recall-trace-section">
          <summary class="wb-locomo-subsection-summary">
            <strong>Retrieval Trace Preview</strong>
            <small>按题浏览当前结果里的 retrieval trace 预览。</small>
          </summary>
          <div class="wb-locomo-subsection-body">
            ${tracePreview}
          </div>
        </details>
      ` : ""}
      ${artifactButtons.length ? `
        <details class="wb-locomo-subsection wb-locomo-recall-artifacts">
          <summary class="wb-locomo-subsection-summary">
            <strong>Artifacts</strong>
            <small>直接打开当前题相关结果产物</small>
          </summary>
          <div class="wb-locomo-subsection-body">
            <div class="wb-panel-head-actions">
              ${artifactButtons.map((item) => actionButton({
                label: item.label,
                tone: "ghost",
                action: "open-path",
                path: item.path,
              }, escapeHtml)).join("")}
            </div>
            <p class="wb-locomo-summary-line">${escapeHtml(compactPath ? compactPath(currentPath || "-") : (currentPath || "-"))}</p>
          </div>
        </details>
      ` : ""}
    `,
  });
}

function parseQuestionIds(rawValue) {
  return String(rawValue || "")
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function dedupeQuestions(rows = []) {
  const seen = new Set();
  const deduped = [];
  for (const row of rows) {
    const key = String(row?.question_id || `${row?.sample_id || ""}::${row?.question || ""}`).trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    deduped.push(row);
  }
  return deduped;
}

function questionCategoryLabel(category) {
  const normalized = String(category || "").trim();
  return normalized ? `C${normalized}` : "未分类";
}

function syncSelectedQuestionIds($, state) {
  const questionInput = $("wbQaQuestionIds");
  if (!questionInput) return;
  questionInput.value = [...(state.locomoSelectedQuestions || new Set())].join(",");
}

function resultScopeLabel({ diagnostics, runConfig, run }) {
  const sample = String(diagnostics?.sample || runConfig?.sample || run?.sample || "").trim();
  return sample || "all";
}

function resolveRunScopedDatasetPath({ runDetail, runConfig, run, activeDatasetPath = "" }) {
  return String(
    runDetail?.record?.dataset_path
    || runConfig?.data
    || run?.dataset_path
    || activeDatasetPath
    || ""
  ).trim();
}

function resolveRunScopedSample({ diagnostics, runDetail, runConfig, run, activeSample = "" }) {
  return String(
    diagnostics?.sample
    || runDetail?.record?.sample
    || runConfig?.sample
    || run?.sample
    || activeSample
    || "all"
  ).trim() || "all";
}

function renderCurrentQaResultCard({ run, diagnostics, runConfig, metrics, compactPath, escapeHtml, importDurationS = null }) {
  if (!run?.output_file && !run?.run_dir) {
    return `
      <article class="wb-current-result-strip wb-current-result-card is-empty">
        <div class="wb-current-result-main">
          <div class="wb-current-result-copy">
            <span>当前结果</span>
            <strong>尚未选择结果</strong>
          </div>
          <small>完成 QA 后，这里会显示可补跑、判分和导出的结果文件。</small>
        </div>
      </article>
    `;
  }
  const rows = Number(
    metrics?.rows
    ?? diagnostics?.summary?.rows
    ?? run?.summary?.rows
    ?? 0,
  );
  const graded = Number(
    metrics?.graded
    ?? diagnostics?.summary?.graded
    ?? run?.summary?.graded
    ?? 0,
  );
  const pending = Number(
    metrics?.pending
    ?? diagnostics?.summary?.result_counts?.UNSCORED
    ?? Math.max(0, rows - graded),
  );
  const missing = Number(diagnostics?.missing_questions_count || 0);
  const retryFailed = Number(diagnostics?.retryable_failed_questions || 0);
  const accuracyRaw = metrics?.accuracy ?? diagnostics?.summary?.accuracy ?? run?.summary?.accuracy;
  const accuracy = Number.isFinite(Number(accuracyRaw)) ? `${(Number(accuracyRaw) * 100).toFixed(1)}%` : "-";
  const scope = resultScopeLabel({ diagnostics, runConfig, run });
  const runDuration = formatDurationLabel(metrics?.runDurationS);
  const importDuration = formatDurationLabel(importDurationS);
  const avgQa = formatDurationLabel(metrics?.avgQaTimeS);
  const tokenSummaryItems = [
    metrics?.totalTokens != null ? { label: "总 Tokens", value: formatCountLabel(metrics.totalTokens), tone: "is-total" } : null,
    metrics?.importTotalTokens != null ? { label: "导入", value: formatCountLabel(metrics.importTotalTokens) } : null,
    metrics?.retrievalTotalTokens != null ? { label: "检索", value: formatCountLabel(metrics.retrievalTotalTokens) } : null,
    metrics?.answerTotalTokens != null ? { label: "回答", value: formatCountLabel(metrics.answerTotalTokens) } : null,
    metrics?.importEmbeddingTotalTokens != null ? { label: "Embedding", value: formatCountLabel(metrics.importEmbeddingTotalTokens) } : null,
  ].filter(Boolean);
  const primaryMetricItems = [
    { value: escapeHtml(scope), label: "Scope" },
    { value: escapeHtml(String(rows || 0)), label: "Rows" },
    { value: escapeHtml(String(graded || 0)), label: "Graded" },
    { value: escapeHtml(String(pending || 0)), label: "Pending" },
    { value: escapeHtml(accuracy), label: "Accuracy" },
    { value: escapeHtml(runDuration), label: "Run" },
    { value: escapeHtml(avgQa), label: "Avg QA" },
    { value: escapeHtml(importDuration), label: "Import" },
    { value: escapeHtml(String(missing || 0)), label: "Missing" },
    { value: escapeHtml(String(retryFailed || 0)), label: "Retry Failed" },
  ];
  const allMetricItems = primaryMetricItems;
  return `
    <article class="wb-current-result-strip wb-current-result-card">
      <div class="wb-current-result-main">
        <div class="wb-current-result-copy">
          <span>当前结果</span>
          <strong title="${escapeHtml(run.name || run.id || "-")}">${escapeHtml(compactText(run.name || run.id || "-", 52))}</strong>
        </div>
        <small title="${escapeHtml(run.output_file || run.run_dir || "-")}">${escapeHtml(compactPath(run.output_file || run.run_dir || "-"))}</small>
        ${tokenSummaryItems.length ? `
          <div class="wb-current-result-token-strip" aria-label="当前结果 token 消耗">
            ${tokenSummaryItems.map((item) => `
              <span class="wb-current-result-token-chip ${escapeHtml(item.tone || "")}">
                <small>${escapeHtml(item.label)}</small>
                <strong>${escapeHtml(item.value)}</strong>
              </span>
            `).join("")}
          </div>
        ` : ""}
        <div class="wb-current-result-metrics">
          ${allMetricItems.map((item) => `<span><strong>${item.value}</strong><small>${escapeHtml(item.label)}</small></span>`).join("")}
        </div>
      </div>
      <div class="wb-current-result-actions">
        <button id="wbRefreshQaCurrentResult" class="wb-button ghost" type="button">刷新当前结果</button>
      </div>
    </article>
  `;
}

function renderRecoveryStrip({
  selectedIds,
  wrongCsvValue,
  wrongCsvEnabled,
  wrongCsvTitle = "",
  retryFailedCount,
  retryMissingCount,
  retryMissingEnabled = retryMissingCount > 0,
  retryMissingValue = retryMissingCount > 0 ? `${retryMissingCount} 题` : "无缺失题",
  retryMissingTitle = "",
  compactPath,
  escapeHtml,
}) {
  const items = [
    {
      label: "Selected",
      value: selectedIds.length ? `${selectedIds.length} 题` : "未选择",
      ok: selectedIds.length > 0,
      action: { id: "wbRunQaSelected", label: "运行指定题", tone: "ghost", disabled: selectedIds.length === 0, title: selectedIds.length === 0 ? "请先填写 question ids" : "" },
    },
    {
      label: "Wrong CSV",
      value: wrongCsvEnabled && wrongCsvValue ? compactPath(wrongCsvValue) : "无错题 CSV",
      ok: Boolean(wrongCsvEnabled && wrongCsvValue),
      action: {
        id: "wbRunQaWrongCsv",
        label: "运行错题 CSV",
        tone: "ghost",
        disabled: !wrongCsvEnabled,
        title: wrongCsvEnabled ? wrongCsvTitle : (wrongCsvTitle || "当前结果没有可重跑的错题 CSV"),
      },
    },
    {
      label: "Retry Failed",
      value: retryFailedCount > 0 ? `${retryFailedCount} 题` : "无失败题",
      ok: retryFailedCount > 0,
      action: { id: "wbRunQaRetryFailed", label: "重跑失败题", tone: "secondary", disabled: retryFailedCount === 0, title: retryFailedCount === 0 ? "当前结果没有可恢复失败题" : "" },
    },
    {
      label: "Retry Missing",
      value: retryMissingValue,
      ok: retryMissingEnabled,
      action: { id: "wbRunQaRetryMissing", label: "补跑缺失题", tone: "secondary", disabled: !retryMissingEnabled, title: retryMissingTitle },
    },
  ];
  return `
    <article class="wb-recovery-strip wb-recovery-card">
      <div class="wb-recovery-strip-main">
        <div class="wb-recovery-strip-copy">
          <span>补跑与恢复</span>
          <small>保留当前选题、错题 CSV、失败题和缺失题的恢复入口。</small>
        </div>
        <div class="wb-recovery-strip-items">
        ${items.map((item) => `
          <div class="wb-recovery-item ${item.ok ? "ok" : ""}">
            <span>${escapeHtml(item.label)}</span>
            <strong title="${escapeHtml(item.value)}">${escapeHtml(item.value)}</strong>
          </div>
        `).join("")}
        </div>
      </div>
      <div class="wb-recovery-actions">
        ${items.map((item) => actionButton(item.action, escapeHtml)).join("")}
      </div>
    </article>
  `;
}

function renderQuestionWorkbench({ $, state, escapeHtml }) {
  const selectedSet = new Set(state.locomoSelectedQuestions || []);
  const sample = String(
    state.locomoQaDraft?.wbQaSample
    || state.questionSamples?.locomo
    || $("#wbQaSample")?.value
    || "all"
  ).trim() || "all";
  const filters = state.locomoQuestionFilters || { category: "all", query: "" };
  const query = String(filters.query || "").trim().toLowerCase();
  const category = String(filters.category || "all").trim() || "all";
  const knownQuestionIds = new Set((state.questions || []).map((row) => String(row.question_id || "").trim()).filter(Boolean));
  const visualSelectedSet = new Set([...selectedSet].filter((id) => knownQuestionIds.has(id)));
  const allRows = dedupeQuestions((state.questions || []).filter((row) => {
    if (sample !== "all" && String(row.sample_id || "").trim() !== sample) return false;
    if (category !== "all" && String(row.category || "").trim() !== category) return false;
    if (!query) return true;
    const searchText = [
      row.question_id,
      row.sample_id,
      row.question,
      row.answer,
      questionCategoryLabel(row.category),
    ].join("\n").toLowerCase();
    return searchText.includes(query);
  }));
  const limit = sample === "all" ? 200 : 600;
  const visibleRows = allRows.slice(0, limit);
  const selectedVisibleCount = visibleRows.filter((row) => visualSelectedSet.has(String(row.question_id || "").trim())).length;
  const allVisibleSelected = visibleRows.length > 0 && selectedVisibleCount === visibleRows.length;
  const someVisibleSelected = selectedVisibleCount > 0 && !allVisibleSelected;
  const categories = Array.from(new Set((state.questions || []).map((row) => String(row.category || "").trim()).filter(Boolean))).sort();
  const categoryOptions = [`<option value="all"${category === "all" ? " selected" : ""}>全部类别</option>`]
    .concat(categories.map((item) => `<option value="${escapeHtml(item)}"${category === item ? " selected" : ""}>${escapeHtml(questionCategoryLabel(item))}</option>`))
    .join("");
  const selectedTotal = selectedSet.size;
  const limitHint = allRows.length > visibleRows.length
    ? `<p class="wb-question-workbench-note">当前只展示前 ${escapeHtml(String(visibleRows.length))} / ${escapeHtml(String(allRows.length))} 题。继续缩小 sample、类别或搜索词可以看到更完整列表。</p>`
    : "";
  const emptyState = query || category !== "all"
    ? "当前筛选条件下没有可选题目。"
    : "当前范围没有可选题目。";
  const body = visibleRows.length ? `
    <div class="wb-question-workbench-toolbar">
      <label class="wb-field wb-question-workbench-search">
        <span>搜索</span>
        <input id="wbQaQuestionSearch" type="text" value="${escapeHtml(filters.query || "")}" placeholder="question / question_id / conv / answer">
      </label>
      <label class="wb-field wb-question-workbench-category">
        <span>类别</span>
        <select id="wbQaQuestionCategory">${categoryOptions}</select>
      </label>
      <div class="wb-question-workbench-actions">
        <button id="wbQaSelectVisibleQuestions" class="wb-button ghost" type="button">全选可见题</button>
        <button id="wbQaClearSelectedQuestions" class="wb-button ghost" type="button">清空选题</button>
      </div>
    </div>
    <div class="wb-question-workbench-meta">
      <strong>${escapeHtml(sample === "all" ? "全部对话范围" : sample)}</strong>
      <span>筛选后 ${escapeHtml(String(allRows.length))} 题</span>
      <span>当前可见 ${escapeHtml(String(visibleRows.length))} 题</span>
      <span>已选 ${escapeHtml(String(selectedTotal))} 题</span>
      <span>已选可见 ${escapeHtml(String(selectedVisibleCount))} 题</span>
    </div>
    ${limitHint}
    <div class="wb-question-workbench-table-shell">
      <table class="wb-question-workbench-table">
        <thead>
          <tr>
            <th class="wb-question-check-col"><input id="wbQaToggleVisibleQuestions" type="checkbox" aria-label="批量勾选当前可见题目"${allVisibleSelected ? " checked" : ""}></th>
            <th>Question</th>
            <th>Conv</th>
            <th>Question ID</th>
            <th>Category</th>
            <th>Gold</th>
          </tr>
        </thead>
        <tbody>
          ${visibleRows.map((row) => {
            const qid = String(row.question_id || "").trim();
            const checked = visualSelectedSet.has(qid);
            return `
              <tr class="wb-question-workbench-row${checked ? " selected" : ""}">
                <td class="wb-question-check-col"><input type="checkbox" data-question-id="${escapeHtml(qid)}"${checked ? " checked" : ""}></td>
                <td class="wb-question-cell-main"><strong>${escapeHtml(row.question || "-")}</strong></td>
                <td class="wb-question-cell-mono">${escapeHtml(row.sample_id || "-")}</td>
                <td class="wb-question-cell-mono">${escapeHtml(qid || "-")}</td>
                <td><span class="wb-pill ${checked ? "ok" : "neutral"}">${escapeHtml(questionCategoryLabel(row.category))}</span></td>
                <td class="wb-question-cell-answer">${escapeHtml(compactText(row.answer || "-", 72))}</td>
              </tr>
            `;
          }).join("")}
        </tbody>
      </table>
    </div>
  ` : `
    <div class="wb-question-workbench-toolbar">
      <label class="wb-field wb-question-workbench-search">
        <span>搜索</span>
        <input id="wbQaQuestionSearch" type="text" value="${escapeHtml(filters.query || "")}" placeholder="question / question_id / conv / answer">
      </label>
      <label class="wb-field wb-question-workbench-category">
        <span>类别</span>
        <select id="wbQaQuestionCategory">${categoryOptions}</select>
      </label>
      <div class="wb-question-workbench-actions">
        <button id="wbQaSelectVisibleQuestions" class="wb-button ghost" type="button" disabled>全选可见题</button>
        <button id="wbQaClearSelectedQuestions" class="wb-button ghost" type="button"${selectedTotal ? "" : " disabled"}>清空选题</button>
      </div>
    </div>
    <p class="wb-empty">${escapeHtml(emptyState)}</p>
  `;
  const card = actionCard({
    title: "题目工作台",
    subtitle: "搜索、筛选、批量勾选当前范围题目，并把选择同步到 selected 模式。",
    bodyHtml: body,
    escapeHtml,
  });
  const toggleHint = someVisibleSelected ? `<p class="wb-question-workbench-note">当前可见题里只选中了部分题目。</p>` : "";
  return `${card}${toggleHint}`;
}

function getLocomoQaDraft(state) {
  if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") {
    state.locomoQaDraft = {};
  }
  return state.locomoQaDraft;
}

function readLocomoQaDraftValue({ $, state, id, fallback = "" }) {
  const node = $(id);
  const draft = getLocomoQaDraft(state);
  if (node) {
    const value = String(node.value || "");
    draft[id] = value;
    return value;
  }
  if (Object.prototype.hasOwnProperty.call(draft, id)) {
    return String(draft[id] || "");
  }
  return String(fallback || "");
}

export function renderLocomoQaConfig({
  $,
  currentAccountConfig,
  currentBenchmark,
  currentDatasetRecord,
  currentRun,
  currentWorkspace,
  escapeHtml,
  firstValue,
  check,
  field,
  state,
}) {
  const cfg = normalizeLocomoAccountConfig(currentAccountConfig());
  const activeBackend = String(cfg.memoryBackend || state.config?.memoryBackend || "echomemory").trim() || "echomemory";
  const benchmark = currentBenchmark();
  const run = currentRun ? currentRun() : null;
  const runSnapshot = run?.run_dir ? (state.runConfigSnapshots?.[run.run_dir] || null) : null;
  const runConfig = runSnapshot?.config || runSnapshot || null;
  const qaDraft = getLocomoQaDraft(state);
  const liveValue = (id, fallback = "") => {
    const node = $(id);
    if (node) {
      const value = String(node.value || "");
      const trimmed = value.trim();
      if (trimmed || Object.prototype.hasOwnProperty.call(qaDraft, id)) {
        qaDraft[id] = value;
        return trimmed;
      }
      return String(fallback || "").trim();
    }
    if (Object.prototype.hasOwnProperty.call(qaDraft, id)) {
      return String(qaDraft[id] || "").trim();
    }
    return String(fallback || "");
  };
  const liveChecked = (id, fallback = false) => {
    const node = $(id);
    if (node) {
      const checked = Boolean(node.checked);
      qaDraft[id] = checked;
      return checked;
    }
    if (Object.prototype.hasOwnProperty.call(qaDraft, id)) {
      return Boolean(qaDraft[id]);
    }
    return Boolean(fallback);
  };
  const preferredSample = String(
    state.questionSamples?.locomo
    || qaDraft.wbQaSample
    || runConfig?.sample
    || run?.sample
    || "all"
  ).trim() || "all";
  const sampleNode = $("wbQaSample");
  const sampleExplicit = qaDraft.wbQaSampleExplicit === true;
  let currentSample = preferredSample;
  if (sampleNode) {
    const nodeSample = String(sampleNode.value || "").trim() || "all";
    if (sampleExplicit) {
      currentSample = nodeSample;
    } else if (nodeSample !== "all") {
      currentSample = nodeSample;
    } else {
      currentSample = preferredSample || nodeSample || "all";
    }
  } else if (sampleExplicit && String(qaDraft.wbQaSample || "").trim()) {
    currentSample = String(qaDraft.wbQaSample || "").trim();
  }
  currentSample = currentSample || "all";
  qaDraft.wbQaSample = currentSample;
  const currentMode = liveValue("wbQaMode", qaDraft.wbQaMode || "full") || "full";
  qaDraft.wbQaMode = currentMode;
  const sampleSet = new Set((state.questions || []).map((item) => String(item.sample_id || "").trim()).filter(Boolean));
  if (currentSample && currentSample !== "all") sampleSet.add(currentSample);
  if (String(runConfig?.sample || "").trim() && String(runConfig?.sample || "").trim() !== "all") sampleSet.add(String(runConfig.sample).trim());
  if (String(run?.sample || "").trim() && String(run?.sample || "").trim() !== "all") sampleSet.add(String(run.sample).trim());
  const samples = Array.from(sampleSet).sort();
  const sampleOptions = samples.map((sample) => `<option value="${escapeHtml(sample)}"${currentSample === sample ? " selected" : ""}>${escapeHtml(sample)}</option>`).join("");
  const currentEchomemBaseUrl = liveValue("wbQaEchomemBaseUrl", cfg.echomemBaseUrl || "");
  const currentPromptMode = "vikingboat_lite";
  const currentRetrievalMode = "search";
  const retrievalModeHint = "LoCoMo EchoMemory 已固定对齐 OpenViking v0.4.7 / benchmark/locomo/vikingbot：search 检索、VikingBoat 问题模板、关闭平台侧 local summaries / atoms / prefetch / compat。";
  const currentUseTools = liveChecked("wbQaUseTools", cfg.locomoQaUseTools !== false);
  const currentToolLoop = liveChecked("wbQaToolLoop", cfg.echomemQaToolLoop !== false);
  const currentQaMemoryInjection = activeBackend === "echomemory"
    ? true
    : liveChecked("wbQaMemoryInjection", true);
  const currentQuestionLimit = readLocomoQaDraftValue({
    $,
    state,
    id: "wbQaQuestionLimit",
    fallback: qaDraft.wbQaQuestionLimit ?? firstValue(cfg.locomoQaQuestionLimit, "0"),
  }).trim();
  const currentQuestionIds = readLocomoQaDraftValue({
    $,
    state,
    id: "wbQaQuestionIds",
    fallback: qaDraft.wbQaQuestionIds ?? [...(state.locomoSelectedQuestions || new Set())].join(","),
  }).trim();
  const currentWrongCsv = readLocomoQaDraftValue({
    $,
    state,
    id: "wbQaWrongCsv",
    fallback: qaDraft.wbQaWrongCsv ?? state.locomoWrongCsv ?? defaultWrongCsvPath(run),
  }).trim();
  state.locomoWrongCsv = currentWrongCsv;
  const currentMemoryUser = liveValue("wbQaMemoryUserId", firstValue(cfg.memoryUserId, "default"));
  const currentMemoryAgent = liveValue("wbQaMemoryAgentId", firstValue(cfg.memoryAgentId, "default"));
  const launchDisabled = (currentMode === "selected" && !parseQuestionIds(currentQuestionIds).length)
    || (currentMode === "wrong_csv" && !String(currentWrongCsv || "").trim());
  const launchTitle = currentMode === "selected" && !parseQuestionIds(currentQuestionIds).length
    ? "selected 模式需要 question ids"
    : currentMode === "wrong_csv" && !String(currentWrongCsv || "").trim()
      ? "wrong_csv 模式需要错题 CSV"
      : "";
  const modeSpecificFields = [];
  if (currentMode === "time") {
    modeSpecificFields.push(
      configField("Quick Test 题数", `<input id="wbQaQuestionLimit" type="number" min="0" step="1" value="${escapeHtml(currentQuestionLimit)}">`, escapeHtml, { full: true }),
    );
  }
  if (currentMode === "selected") {
    modeSpecificFields.push(
      configField("指定题号", `<input id="wbQaQuestionIds" type="text" value="${escapeHtml(currentQuestionIds)}" placeholder="conv-30_q1,conv-30_q2">`, escapeHtml, { full: true }),
    );
  }
  if (currentMode === "wrong_csv") {
    modeSpecificFields.push(
      configField("错题 CSV", `<input id="wbQaWrongCsv" type="text" value="${escapeHtml(currentWrongCsv)}" placeholder="/path/to/wrong_questions_brief.csv">`, escapeHtml, { full: true }),
    );
  }
  const actionsHtml = `<div class="wb-panel-head-actions wb-locomo-config-actions">
    <button id="wbSaveLocomoQaConfig" class="wb-button secondary" type="button">保存参数</button>
    <button id="wbRunQaGate" class="wb-button ghost" type="button">运行前检查</button>
    <button id="wbRunQaCurrentScope" class="wb-button primary" type="button"${launchDisabled ? " disabled" : ""}${launchTitle ? ` title="${escapeHtml(launchTitle)}"` : ""}>启动当前模式</button>
  </div>`;
  const parameterSections = [
    `
      <div class="wb-locomo-param-block">
        <div class="wb-locomo-param-head">
          <strong>${icon("settings2", { className: "wb-inline-icon" })}<span>旧系统核心参数</span></strong>
          <small>${retrievalModeHint}</small>
        </div>
        ${chipList([
          textChip("Prompt", escapeHtml(currentPromptMode)),
          textChip("Retrieval", escapeHtml(currentRetrievalMode)),
          textChip("Use Tools", currentUseTools ? "on" : "off", currentUseTools ? "ok" : "muted"),
          textChip("Tool Loop", currentToolLoop ? "on" : "off", currentToolLoop ? "ok" : "muted"),
          textChip("Memory Injection", currentQaMemoryInjection ? "on" : "off", currentQaMemoryInjection ? "ok" : "muted"),
          textChip("Tool Set", escapeHtml(compactText(liveValue("wbQaToolSet", firstValue(cfg.echomemQaToolSet, "vikingbot_native_safe")), 26)), "default", escapeHtml(liveValue("wbQaToolSet", firstValue(cfg.echomemQaToolSet, "vikingbot_native_safe")))),
        ])}
        ${fieldGrid([
          ...(activeBackend === "echomemory" ? [
            configField("EchoMemory URL", `<input id="wbQaEchomemBaseUrl" type="text" value="${escapeHtml(currentEchomemBaseUrl)}" placeholder="http://127.0.0.1:8015">`, escapeHtml, { full: true }),
          ] : []),
          configField("Top K", `<input id="wbQaTopK" type="number" min="1" step="1" value="${escapeHtml(liveValue("wbQaTopK", firstValue(cfg.echomemQaTopK, cfg.chatTopK, "30")))}">`, escapeHtml),
          configField("工具集", `<input id="wbQaToolSet" type="text" value="${escapeHtml(liveValue("wbQaToolSet", firstValue(cfg.echomemQaToolSet, "vikingbot_native_safe")))}" placeholder="vikingbot_native_safe">`, escapeHtml),
          configField("开启工具调用", `<input id="wbQaUseTools" type="checkbox"${currentUseTools ? " checked" : ""}>`, escapeHtml),
          configField("允许多轮工具调用", `<input id="wbQaToolLoop" type="checkbox"${currentToolLoop ? " checked" : ""}>`, escapeHtml),
          configField("工具召回数", `<input id="wbQaToolSearchLimit" type="number" min="1" step="1" value="${escapeHtml(liveValue("wbQaToolSearchLimit", firstValue(cfg.echomemQaToolSearchLimit, "20")))}">`, escapeHtml),
          configField("最大迭代次数", `<input id="wbQaMaxIterations" type="number" min="1" step="1" value="${escapeHtml(liveValue("wbQaMaxIterations", firstValue(cfg.echomemQaMaxIterations, "50")))}">`, escapeHtml),
          configField("模型重试次数", `<input id="wbQaModelRetries" type="number" min="0" step="1" value="${escapeHtml(liveValue("wbQaModelRetries", firstValue(cfg.echomemQaModelRetries, "5")))}">`, escapeHtml),
          configField("单题超时（秒）", `<input id="wbQaQuestionTimeout" type="number" min="30" step="1" value="${escapeHtml(liveValue("wbQaQuestionTimeout", firstValue(cfg.echomemQaQuestionTimeout, "600")))}">`, escapeHtml),
          ...(activeBackend === "echomemory" ? [
            configField("QA 并发数（v2）", `<input id="wbQaParallelism" type="number" min="1" step="1" value="${escapeHtml(liveValue("wbQaParallelism", firstValue(cfg.echomemQaParallelism, "10")))}">`, escapeHtml),
          ] : []),
        ])}
      </div>
    `,
    ...(activeBackend === "echomemory" ? [
      `
        <div class="wb-locomo-param-block">
          <div class="wb-locomo-param-head">
            <strong>${icon("database", { className: "wb-inline-icon" })}<span>初始证据上下文与预取</span></strong>
            <small>仅限制 QA 首轮提示词中的证据字符数，不限制记忆注入数量。</small>
          </div>
          ${fieldGrid([
            configField("初始证据总字符数", `<input id="wbQaMemoryBudgetChars" type="number" min="0" step="100" value="${escapeHtml(liveValue("wbQaMemoryBudgetChars", firstValue(cfg.echomemQaMemoryBudgetChars, "6000")))}">`, escapeHtml),
            configField("用户证据字符数", `<input id="wbQaUserMemoryBudgetChars" type="number" min="0" step="100" value="${escapeHtml(liveValue("wbQaUserMemoryBudgetChars", firstValue(cfg.echomemQaUserMemoryBudgetChars, "4000")))}">`, escapeHtml),
            configField("代理证据字符数", `<input id="wbQaAgentMemoryBudgetChars" type="number" min="0" step="100" value="${escapeHtml(liveValue("wbQaAgentMemoryBudgetChars", firstValue(cfg.echomemQaAgentMemoryBudgetChars, "2000")))}">`, escapeHtml),
            configField("Prefetch Read Count", `<input id="wbQaPrefetchReadCount" type="number" min="0" step="1" value="${escapeHtml(liveValue("wbQaPrefetchReadCount", firstValue(cfg.echomemQaPrefetchReadCount, "4")))}">`, escapeHtml),
            configField("Prefetch Context Chars", `<input id="wbQaPrefetchContextChars" type="number" min="0" step="100" value="${escapeHtml(liveValue("wbQaPrefetchContextChars", firstValue(cfg.echomemQaPrefetchContextChars, "5000")))}">`, escapeHtml),
            configField("Tool Log Chars", `<input id="wbQaToolLogChars" type="number" min="200" step="100" value="${escapeHtml(liveValue("wbQaToolLogChars", firstValue(cfg.echomemQaToolLogChars, "1200")))}">`, escapeHtml),
          ])}
        </div>
      `,
    ] : []),
  ];
  $("wbQaConfig").innerHTML = [
    formSection(sectionTitle("idCard", "运行范围与身份"), [
      fieldGrid([
        configField("LoCoMo JSON", `<input id="wbDataPath" type="text" value="${escapeHtml(liveValue("wbDataPath", firstValue(state.questionDataPaths?.locomo, currentDatasetRecord()?.path, benchmark.defaultData)))}" placeholder="./dataset/locomo10.json">`, escapeHtml, { full: true }),
        configField("测试会话", `<select id="wbQaSample"><option value="all"${currentSample === "all" ? " selected" : ""}>全部对话</option>${sampleOptions}</select>`, escapeHtml),
        configField("题目范围", `<select id="wbQaMode"><option value="full"${currentMode === "full" ? " selected" : ""}>当前会话全量</option><option value="time"${currentMode === "time" ? " selected" : ""}>时间题 quick test</option><option value="selected"${currentMode === "selected" ? " selected" : ""}>指定题号</option><option value="wrong_csv"${currentMode === "wrong_csv" ? " selected" : ""}>错题 CSV</option></select>`, escapeHtml),
        configField("记忆目录", `<input id="wbWorkspace" type="text" value="${escapeHtml(liveValue("wbWorkspace", currentWorkspace ? currentWorkspace() : ""))}" placeholder="/path/to/echomem_workspace">`, escapeHtml, { full: true }),
        configField("Memory User", `<input id="wbQaMemoryUserId" type="text" value="${escapeHtml(currentMemoryUser)}" placeholder="default">`, escapeHtml),
        configField("Memory Agent", `<input id="wbQaMemoryAgentId" type="text" value="${escapeHtml(currentMemoryAgent)}" placeholder="default">`, escapeHtml),
        ...modeSpecificFields,
      ]),
      actionsHtml,
    ].join(""), "先确定范围、样本和 User / Agent 身份，再决定是全量跑、时间题 quick test，还是对问题集重跑。"),
    formDetailsSection(activeBackend === "echomemory" ? sectionTitle("folderCog", "EchoMemory 参数") : sectionTitle("folderCog", "运行参数"), parameterSections.join(""), "字段保持完整，只把结构收紧，减少重复摘要和多层边框。", { open: true, className: "wb-locomo-qa-params" }),
  ].join("");
}

export function renderLocomoQaPreview({
  $,
  compactPath,
  currentAccountConfig,
  currentRun,
  escapeHtml,
  firstValue,
  state,
}) {
  const run = currentRun ? currentRun() : null;
  const runDetail = run?.run_dir ? (state.runDetails?.[run.run_dir] || null) : null;
  const runResult = run?.output_file ? (state.resultSummaries?.[run.output_file] || null) : null;
  const metrics = summarizeBenchmarkRun("locomo", run, runDetail, runResult);
  const cfg = currentAccountConfig ? currentAccountConfig() : {};
  const activeBackend = String(cfg.memoryBackend || state.config?.memoryBackend || "echomemory").trim() || "echomemory";
  const runSnapshot = run?.run_dir ? (state.runConfigSnapshots?.[run.run_dir] || null) : null;
  const runConfig = runSnapshot?.config || runSnapshot || null;
  const activeDatasetPath = String($("#wbDataPath")?.value || run?.dataset_path || runConfig?.data || "").trim();
  const activeSample = String($("#wbQaSample")?.value || runConfig?.sample || "all").trim() || "all";
  const runScopedDatasetPath = resolveRunScopedDatasetPath({
    runDetail,
    runConfig,
    run,
    activeDatasetPath,
  });
  const runScopedSample = resolveRunScopedSample({
    runDetail,
    runConfig,
    run,
    activeSample,
  });
  const qaDraft = getLocomoQaDraft(state);
  const diagnosticsCacheKey = [String(run?.output_file || "").trim(), runScopedDatasetPath, runScopedSample].join("::");
  const diagnostics = run?.output_file
    ? (
      state.qaDiagnosticsCache?.[diagnosticsCacheKey]
      || state.qaDiagnosticsCache?.[[String(run?.output_file || "").trim(), activeDatasetPath, activeSample].join("::")]
      || state.qaDiagnosticsCache?.[run.output_file]
      || null
    )
    : null;
  const recallPreview = run?.output_file ? (state.locomoRecallPreview || null) : null;
  const gate = state.locomoQaGate || null;
  const matchedImportRunDir = String(runDetail?.record?.matched_import_run_dir || "").trim();
  const matchedImportDetail = matchedImportRunDir ? (state.runDetails?.[matchedImportRunDir] || null) : null;
  const matchedImportRun = matchedImportRunDir
    ? (
      (state.runs || []).find((item) => String(item?.run_dir || "").trim() === matchedImportRunDir)
      || null
    )
    : null;
  const importDurationS = Number(
    matchedImportDetail?.record?.duration_s
    ?? matchedImportRun?.duration
    ?? matchedImportRun?.duration_s
    ?? 0
  ) || null;
  const recallDetail = state.locomoRecallDetail || null;
  const recallSelection = state.locomoRecallSelection || {};
  const selectedIds = parseQuestionIds(
    $("#wbQaQuestionIds")?.value
      || qaDraft.wbQaQuestionIds
      || [...(state.locomoSelectedQuestions || new Set())].join(","),
  );
  const wrongCsvValue = String($("#wbQaWrongCsv")?.value || qaDraft.wbQaWrongCsv || state.locomoWrongCsv || defaultWrongCsvPath(run)).trim();
  const wrongAnswerCount = Number(
    metrics?.wrong
    ?? metrics?.summary?.result_counts?.WRONG
    ?? metrics?.summary?.wrong
    ??
    diagnostics?.summary?.wrong
    ?? diagnostics?.summary?.result_counts?.WRONG
    ?? run?.summary?.wrong
    ?? run?.summary?.result_counts?.WRONG
    ?? 0,
  );
  const wrongCsvEnabled = Boolean(wrongCsvValue) && wrongAnswerCount > 0;
  const wrongCsvTitle = wrongCsvEnabled
    ? ""
    : (wrongAnswerCount > 0 ? "请先填写错题 CSV 或先选择当前结果" : "当前结果没有可重跑的错题 CSV");
  const retryFailedCount = Number(diagnostics?.retryable_failed_questions || 0);
  const retryMissingCount = Number(diagnostics?.missing_questions_count || 0);
  const retryMissingEnabled = retryMissingCount > 0 || (activeBackend === "openviking" && retryFailedCount > 0);
  const retryMissingValue = retryMissingCount > 0
    ? `${retryMissingCount} 题`
    : (activeBackend === "openviking" && retryFailedCount > 0 ? "回退失败题" : "无缺失题");
  const retryMissingTitle = retryMissingEnabled
    ? ""
    : "当前结果没有缺失题";
  $("wbQaGate").innerHTML = checklistCard({
    title: gate?.title || "QA 启动检查",
    subtitle: gate?.subtitle || "开始 LoCoMo QA 前，先检查当前范围、数据路径和运行依赖。",
    checks: gate?.checks || [],
    actions: [],
    escapeHtml,
  });
  const currentResultCard = renderCurrentQaResultCard({
    run,
    diagnostics,
    runConfig,
    metrics,
    compactPath,
    escapeHtml,
    importDurationS,
  });
  const recoveryStrip = renderRecoveryStrip({
    selectedIds,
    wrongCsvValue,
    wrongCsvEnabled,
    wrongCsvTitle,
    retryFailedCount,
    retryMissingCount,
    retryMissingEnabled,
    retryMissingValue,
    retryMissingTitle,
    compactPath,
    escapeHtml,
  });
  const questionPreviewCard = state.questions.length
    ? renderQuestionWorkbench({ $, state, escapeHtml })
    : renderEmptyState("切到问答测试后，这里会显示当前会话的题目预览。", escapeHtml);
  const diagnosticsCard = renderDiagnosticsCard({
    diagnostics,
    compactPath,
    currentRun: run,
    escapeHtml,
  });
  const recallWorkbench = renderRecallWorkbench({
    diagnostics,
    recallPreview,
    detail: recallDetail,
    selection: recallSelection,
    currentRun,
    escapeHtml,
    filters: state.locomoRecallFilters || {},
    compactPath,
  });
  const qaTasksHost = $("wbQaTasks");
  qaTasksHost.querySelector(".wb-locomo-task-rail-extras")?.remove();
  qaTasksHost.insertAdjacentHTML("beforeend", `
    <div class="wb-locomo-preview-cluster wb-locomo-preview-cluster-results wb-locomo-task-rail-extras">
      ${currentResultCard}
      ${recoveryStrip}
    </div>
  `);
  $("wbQaPreview").innerHTML = `
    <div class="wb-locomo-preview-stack">
      <section class="wb-locomo-preview-cluster wb-locomo-preview-cluster-questions">
        ${questionPreviewCard}
      </section>
    </div>
  `;
  $("wbQaDiagnostics").innerHTML = diagnosticsCard;
  $("wbQaRecallWorkbench").innerHTML = recallWorkbench;
}

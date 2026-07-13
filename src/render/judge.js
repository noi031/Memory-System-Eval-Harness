import { summarizeBenchmarkRun } from "../run-metrics.js";
import { buildJudgeActionViewModel, buildJudgeMetricItems } from "../benchmark-view-models.js";
import { getRunPhase, getRunStatusLabel } from "../run-status.js";
import { actionCard, checklistCard, renderEmptyState } from "./shared.js";
import { renderOfficialStageActions, renderOfficialStageCurrent } from "./official-stage.js";
import { renderStrictBlackboxMetrics } from "./strict-blackbox.js";

export function createJudgeRenderers({
  $,
  state,
  currentRun,
  escapeHtml,
  compactPath,
  formatDurationSeconds,
  formatInt,
  formatPct,
  tasksForBenchmark,
}) {
  function renderPendingWorkbench() {
    const host = $("wbJudgePending");
    const pendingSection = host.closest("details");
    if (state.activeBenchmark !== "locomo") {
      if (pendingSection) pendingSection.hidden = true;
      if (pendingSection) pendingSection.open = false;
      host.innerHTML = "";
      return;
    }
    if (pendingSection) pendingSection.hidden = false;
    const run = currentRun();
    if (!run?.output_file) {
      if (pendingSection) pendingSection.open = false;
      host.innerHTML = renderEmptyState("当前没有可预览的待判分结果。", escapeHtml);
      return;
    }
    const preview = state.locomoPendingPreview || null;
    const filters = preview?.filters || state.locomoPendingFilters || {};
    const totalPending = Number(preview?.total_pending || 0);
    const rows = Array.isArray(preview?.rows) ? preview.rows : [];
    if (!preview) {
      if (pendingSection) pendingSection.open = false;
      host.innerHTML = actionCard({
        title: "待判分工作台",
        subtitle: "读取当前结果的 pending preview、筛选条件和导出入口。",
        actions: [{ id: "wbRefreshPendingPreview", label: "刷新示例", tone: "secondary" }],
        escapeHtml,
      });
      return;
    }
    if (!totalPending) {
      if (pendingSection) pendingSection.open = false;
      host.innerHTML = actionCard({
        title: "待判分工作台",
        subtitle: "当前结果没有待判分样本，可以直接查看报告或切换其他结果。",
        actions: [{ id: "wbRefreshPendingPreview", label: "刷新示例", tone: "secondary" }],
        escapeHtml,
      });
      return;
    }
    if (pendingSection) pendingSection.open = true;
    host.innerHTML = `
      <article class="wb-card">
        <div class="wb-card-row">
          <div class="wb-card-copy">
            <strong>待判分工作台</strong>
            <small>${escapeHtml(`${totalPending} 行待判分；当前只展示少量示例。`)}</small>
            <p title="${escapeHtml(run.output_file)}">${escapeHtml(compactPath(run.output_file))}</p>
          </div>
          <div class="wb-panel-head-actions">
            <button id="wbRefreshPendingPreview" class="wb-button secondary">刷新示例</button>
            <button id="wbExportPendingCsv" class="wb-button ghost">导出待判 CSV</button>
            <button id="wbRunJudgePending" class="wb-button primary">判分全部待判行</button>
          </div>
        </div>
        <div class="wb-pending-filter-grid">
          <label class="wb-field">
            <span>类别</span>
            <select id="wbPendingCategory">
              <option value="" ${!filters.category ? "selected" : ""}>全部</option>
              <option value="1" ${filters.category === "1" ? "selected" : ""}>C1</option>
              <option value="2" ${filters.category === "2" ? "selected" : ""}>C2</option>
              <option value="3" ${filters.category === "3" ? "selected" : ""}>C3</option>
              <option value="4" ${filters.category === "4" ? "selected" : ""}>C4</option>
            </select>
          </label>
          <label class="wb-field">
            <span>搜索</span>
            <input id="wbPendingSearch" type="text" value="${escapeHtml(filters.query || "")}" placeholder="question_id / 关键词">
          </label>
          <label class="wb-field">
            <span>最小 Token</span>
            <input id="wbPendingMinTokens" type="number" min="0" step="1" value="${escapeHtml(filters.min_tokens || "")}">
          </label>
          <label class="wb-field">
            <span>最大 Token</span>
            <input id="wbPendingMaxTokens" type="number" min="0" step="1" value="${escapeHtml(filters.max_tokens || "")}">
          </label>
        </div>
        <p class="wb-pending-note">筛选只影响当前预览和导出，不改变正式判分的范围。</p>
        <div class="wb-pending-list">
          ${rows.map((row) => `
            <article class="wb-pending-row">
              <header>
                <strong>${escapeHtml(row.question_id || `row-${Number(row._row_index || 0) + 1}`)}</strong>
                <span>${escapeHtml(`${row.sample_id || "-"} · C${row.category || "-"} · tokens ${row.injection_tokens_est || "-"}`)}</span>
              </header>
              <p>${escapeHtml(row.question || "-")}</p>
              <div class="wb-pending-answer-grid">
                <section>
                  <span>标准答案</span>
                  <p>${escapeHtml(row.answer || "-")}</p>
                </section>
                <section>
                  <span>模型回答</span>
                  <p>${escapeHtml(row.response || "-")}</p>
                </section>
              </div>
            </article>
          `).join("")}
        </div>
      </article>
    `;
  }

  function renderJudgeCurrent() {
    const run = currentRun();
    const detail = run ? state.runDetails[run.run_dir] : null;
    const result = run ? state.resultSummaries[run.output_file] : null;
    const metrics = summarizeBenchmarkRun(state.activeBenchmark, run, detail, result);
    const statusLabel = getRunStatusLabel(state.activeBenchmark, run, metrics, tasksForBenchmark(state.activeBenchmark));
    renderOfficialStageCurrent({
      $,
      targetId: "wbJudgeCurrent",
      run,
      emptyMessage: "当前还没有可评分的结果。",
      subtitle: `${statusLabel} · ${formatInt(metrics.rows)} 题`,
      path: run?.output_file || "-",
      items: buildJudgeMetricItems(state.activeBenchmark, metrics),
      noteTitle: "当前判断焦点",
      noteBody: `待判分 ${formatInt(metrics.pending)} · 正确 ${formatInt(metrics.correct)} · 错误 ${formatInt(metrics.wrong)}`,
      escapeHtml,
      compactPath,
      formatDurationSeconds,
      formatInt,
      formatPct,
    });
    if (metrics.strictBlackbox) {
      $("wbJudgeCurrent").insertAdjacentHTML("beforeend", renderStrictBlackboxMetrics(metrics.strictBlackbox, {
        escapeHtml,
        compact: true,
      }));
    }
  }

  function renderJudgeActions(activeBenchmark) {
    const run = currentRun();
    const detail = run ? state.runDetails[run.run_dir] : null;
    const result = run?.output_file ? state.resultSummaries[run.output_file] : null;
    const metrics = summarizeBenchmarkRun(activeBenchmark, run, detail, result);
    const phase = getRunPhase(activeBenchmark, run, metrics, tasksForBenchmark(activeBenchmark));
    const actionModel = buildJudgeActionViewModel({
      benchmarkId: activeBenchmark,
      metrics,
      phase,
      run,
    });
    if (activeBenchmark === "locomo") {
      const preflight = state.locomoJudgePreflight || null;
      $("wbJudgePreflight").innerHTML = "";
      $("wbJudgeActions").innerHTML = `
        <div class="wb-judge-workbench">
          ${checklistCard({
            title: preflight?.title || "Judge 预检查",
            subtitle: preflight?.subtitle || "正式判分前先确认结果文件、CSV 字段和输出目录状态。",
            checks: preflight?.checks || [],
            actions: [{ id: "wbRunJudgePreflight", label: "运行预检查", tone: "secondary" }],
            escapeHtml,
          })}
          ${actionCard({
            title: actionModel.title,
            subtitle: actionModel.subtitle,
            body: "先完成预检查，再决定是正式评分还是抽样评分。",
            actions: actionModel.buttons,
            escapeHtml,
          })}
        </div>
      `;
      return;
    }
    const preflight = state.officialJudgePreflights?.[activeBenchmark] || null;
    $("wbJudgePreflight").innerHTML = "";
    if (preflight) {
      $("wbJudgeActions").innerHTML = `
        <div class="wb-judge-workbench">
          ${checklistCard({
            title: preflight.title || "官方评测预检查",
            subtitle: preflight.subtitle || "确认当前结果和官方评测摘要状态。",
            checks: preflight.checks || [],
            actions: [{ id: "wbRunJudgePreflight", label: "运行预检查", tone: "secondary" }],
            escapeHtml,
          })}
          ${actionCard({
            title: actionModel.title,
            subtitle: actionModel.subtitle,
            body: actionModel.body,
            actions: (actionModel.pathButtons || []).map((button) => ({
              ...button,
              action: "open-path",
            })),
            escapeHtml,
          })}
        </div>
      `;
      return;
    }
    if (actionModel.buttons?.length) {
      $("wbJudgeActions").innerHTML = actionCard({
        title: actionModel.title,
        subtitle: actionModel.subtitle,
        actions: actionModel.buttons,
        escapeHtml,
      });
      return;
    }
    if (!run) {
      $("wbJudgeActions").innerHTML = renderEmptyState("当前还没有可评分的结果。", escapeHtml);
      return;
    }
    renderOfficialStageActions({
      $,
      targetId: "wbJudgeActions",
      title: actionModel.title,
      subtitle: actionModel.subtitle,
      body: actionModel.body,
      actions: actionModel.pathButtons.map((button) => ({
        ...button,
        action: "open-path",
      })),
      escapeHtml,
    });
  }

  return {
    renderJudgeActions,
    renderJudgeCurrent,
    renderPendingWorkbench,
  };
}

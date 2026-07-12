import { activeTaskDataPath, buildOverviewModel, buildShellTextModel, buildTopbarModel } from "../shell-view-models.js";
import { benchmarkDefaultVisibleRunOptions, getBenchmark } from "../benchmark-registry.js";
import { summarizeBenchmarkRun } from "../run-metrics.js";
import { getRunStatusLabel } from "../run-status.js";
import { icon } from "../icons.js";
import { BENCHMARKS } from "../config.js";

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function createShellRenderers({
  $,
  queryAll,
  state,
  standaloneApiBase,
  backendName,
  compactPath,
  currentBenchmark,
  currentDatasetRecord,
  currentRun,
  formatInt,
  hasRunningTasks,
  runsForBenchmark,
  tasksForBenchmark,
  visibleRunsForBenchmark,
}) {
  function applySharedShellHooks(model) {
    const shell = $("wbShell");
    const hero = $("wbPageTitle").closest(".wb-hero");
    const heroCopy = $("wbPageTitle").closest(".wb-hero-copy");
    const topbarAnchor = $("wbAccountSelect") || $("wbAccountName");
    const topbar = topbarAnchor?.closest(".wb-account-bar");
    const flowNav = queryAll(".wb-flow-step")[0]?.closest(".wb-flow-nav");
    const workbenchPanel = $("wbWorkflowTitle").closest(".wb-panel");
    const actionRow = $("wbRunPrimary").closest(".wb-panel-head-actions");
    const overview = $("wbOverviewStrip");

    shell.dataset.layout = model.shellLayout;
    shell.dataset.family = model.benchmarkFamily;
    shell.dataset.activeStageTone = model.activeStageTone;

    hero?.classList.add("wb-shell-hero");
    hero?.setAttribute("data-shell-layout", model.shellLayout);
    heroCopy?.classList.add("wb-shell-hero-copy");
    topbar?.classList.add("wb-shell-toolbar");
    topbar?.setAttribute("data-shell-layout", model.shellLayout);
    flowNav?.classList.add("wb-shell-stage-nav");
    flowNav?.setAttribute("data-shell-layout", model.shellLayout);
    overview.classList.add("wb-shell-overview");
    overview.dataset.shellLayout = model.shellLayout;
    workbenchPanel?.classList.add("wb-shell-workbench");
    workbenchPanel?.setAttribute("data-shell-layout", model.shellLayout);
    actionRow?.classList.add("wb-shell-workbench-actions");
    actionRow?.setAttribute("data-stage", state.activeStage);

    $("wbPageTitleWrap").classList.add("wb-shell-hero-title");
    $("wbPageSubtitle").classList.add("wb-shell-hero-subtitle");
    $("wbWorkflowTitle").classList.add("wb-shell-workbench-title");
    $("wbWorkflowSubtitle").classList.add("wb-shell-workbench-subtitle");

    queryAll(".wb-stage").forEach((panel) => {
      panel.classList.add("wb-stage-panel", `wb-stage-panel--${panel.dataset.stagePanel || "unknown"}`);
      panel.dataset.shellLayout = model.shellLayout;
    });
  }

  function renderShellText() {
    const model = buildShellTextModel({
      benchmark: currentBenchmark(),
      activeStage: state.activeStage,
    });
    $("wbShell").dataset.benchmark = currentBenchmark().id;
    $("wbShell").dataset.stage = state.activeStage;
    $("wbShell").classList.toggle("wb-shell-compact", model.shellLayout !== "default-workbench");
    applySharedShellHooks(model);
    $("wbPageTitle").textContent = model.pageTitle;
    $("wbPageSubtitle").textContent = model.pageSubtitle;
    $("wbPageSubtitle").dataset.kicker = model.heroKicker;
    const kicker = $("wbPageTitleWrap")?.closest(".wb-hero-copy")?.querySelector(".wb-topbar-kicker");
    if (kicker) kicker.textContent = model.heroKicker;
    $("wbWorkflowTitle").textContent = model.workflowTitle;
    $("wbWorkflowSubtitle").textContent = model.workflowSubtitle;
    $("wbRunPrimary").dataset.stage = state.activeStage;
    $("wbRunPrimary").dataset.stageTone = model.activeStageTone;
    $("wbRunPrimary").innerHTML = `<span class="wb-button-icon wb-button-icon-leading" aria-hidden="true">${icon(model.primaryActionIcon)}</span><span>${escapeHtml(model.primaryLabel)}</span>`;
    $("wbRunPrimary").setAttribute("aria-label", `${model.primaryLabel} · ${model.activeStageSubtitle || model.workflowTitle}`);
    $("wbPageTitleIcon").innerHTML = icon(model.pageIcon, { className: "wb-icon-accent" });
    queryAll(".wb-side-item").forEach((node) => {
      const benchmarkId = String(node.dataset.benchmark || "").trim();
      const bench = BENCHMARKS[benchmarkId];
      const iconName = bench?.shellMeta?.sidebarIcon || bench?.shellMeta?.pageIcon || "bookOpen";
      const iconNode = node.querySelector(".wb-side-item-icon");
      if (iconNode) iconNode.innerHTML = icon(iconName);
    });
    $("wbImportPanelIcon").innerHTML = icon("settings2");
    $("wbProgressPanelIcon").innerHTML = icon("activity");
    $("wbLogPanelIcon").innerHTML = icon("terminal");
    const panelIcons = {
      wbQaConfigPanelIcon: "settings2",
      wbQaTaskPanelIcon: "activity",
      wbQaGatePanelIcon: "shieldCheck",
      wbQaPreviewPanelIcon: "clipboardList",
      wbQaRunsPanelIcon: "clock3",
      wbQaDiagnosticsPanelIcon: "barChart3",
      wbQaRecallPanelIcon: "database",
      wbJudgeCurrentPanelIcon: "barChart3",
      wbJudgeActionPanelIcon: "clipboardList",
      wbJudgePendingPanelIcon: "clock3",
      wbReportCurrentPanelIcon: "barChart3",
      wbReportActionsPanelIcon: "folderArchive",
      wbReportRunsPanelIcon: "clock3",
    };
    Object.entries(panelIcons).forEach(([id, iconName]) => {
      const node = $(id);
      if (node) node.innerHTML = icon(iconName);
    });
    queryAll(".wb-flow-step").forEach((node) => {
      node.classList.add("wb-flow-step-shell", `wb-flow-step--${node.dataset.stage || "unknown"}`);
      const label = model.stageNavLabels[node.dataset.stage];
      if (!label) return;
      const textNode = node.querySelector(".wb-flow-copy strong");
      if (textNode) textNode.textContent = label;
      const presentation = model.stagePresentation?.[node.dataset.stage];
      if (presentation?.tone) node.dataset.stageTone = presentation.tone;
      const subtitleNode = node.querySelector(".wb-flow-copy small");
      if (subtitleNode && presentation?.subtitle) subtitleNode.textContent = presentation.subtitle;
      const iconNode = node.querySelector(".wb-flow-icon");
      if (iconNode && presentation?.icon) iconNode.innerHTML = icon(presentation.icon);
    });
  }

  function renderTopbar() {
    const benchmarkId = currentBenchmark().id;
    const model = buildTopbarModel({
      backendName: backendName(),
      compactPath,
      formatInt,
      hasRunningTasks: hasRunningTasks(),
      readiness: state.readiness,
      selectedAccount: state.selectedAccount,
      standaloneApiBase,
    });
    $("wbAccountIcon").innerHTML = icon("user");
    $("wbBackendIcon").innerHTML = icon("database");
    $("wbStatusIcon").innerHTML = icon("activity");
    $("wbRefreshIcon").innerHTML = icon("refreshCw");
    $("wbOpenLegacyIcon").innerHTML = icon("bookOpen");
    if ($("wbAccountLabel")) $("wbAccountLabel").textContent = benchmarkId === "locomo" ? "工作空间" : "账户";
    if ($("wbBackendLabel")) $("wbBackendLabel").textContent = "记忆后端";
    if ($("wbStatusLabel")) $("wbStatusLabel").textContent = "运行状态";
    $("wbAccountName").textContent = model.accountName;
    $("wbAccountHint").textContent = model.accountHint;
    $("wbAccountHint").hidden = !String(model.accountHint || "").trim();
    const accountSelect = $("wbAccountSelect");
    if (accountSelect) {
      const accounts = Array.isArray(state.accounts) ? state.accounts : [];
      const seen = new Set();
      const accountOptions = [];
      const pushAccount = (id) => {
        const text = String(id || "").trim();
        if (!text || seen.has(text)) return;
        seen.add(text);
        accountOptions.push(text);
      };
      accounts.forEach((item) => pushAccount(item?.id));
      pushAccount(state.selectedAccount);
      accountSelect.innerHTML = accountOptions.map((id) => `
        <option value="${escapeHtml(id)}"${id === state.selectedAccount ? " selected" : ""}>${escapeHtml(id)}</option>
      `).join("");
      accountSelect.value = state.selectedAccount || "default";
    }
    $("wbBackendName").textContent = model.backendName;
    $("wbSidebarStatus").textContent = model.sidebarStatus;
    $("wbBackendHint").textContent = model.backendHint;
    $("wbTopbarStatus").textContent = model.topbarStatusLabel || model.topbarStatus;
    $("wbTopbarStatusHint").textContent = model.topbarStatusHint;
    $("wbAccountName").hidden = benchmarkId === "locomo";
    $("wbAccountHint").hidden = benchmarkId === "locomo" || !String(model.accountHint || "").trim();
    $("wbBackendHint").hidden = benchmarkId === "locomo";
    $("wbTopbarStatusHint").hidden = benchmarkId === "locomo";
    $("wbTopbarStatus").dataset.status = model.topbarStatus;
    $("wbSidebarStatus").dataset.status = model.sidebarStatusTone || model.topbarStatus;
  }

  function renderOverview() {
    const benchmark = currentBenchmark();
    const runs = visibleRunsForBenchmark(state.activeBenchmark, {
      ...benchmarkDefaultVisibleRunOptions(getBenchmark(BENCHMARKS, state.activeBenchmark)),
    });
    const tasks = state.tasks || [];
    const liveDataPath = activeTaskDataPath(tasksForBenchmark(state.activeBenchmark));
    const recentRun = currentRun();
    const recentDetail = recentRun ? state.runDetails[recentRun.run_dir] || null : null;
    const recentResult = recentRun?.output_file ? state.resultSummaries[recentRun.output_file] || null : null;
    const recentMetrics = summarizeBenchmarkRun(state.activeBenchmark, recentRun, recentDetail, recentResult);
    const recentRunStatusLabel = getRunStatusLabel(state.activeBenchmark, recentRun, recentMetrics, tasksForBenchmark(state.activeBenchmark));
    const model = buildOverviewModel({
      benchmark,
      compactPath,
      backendName: backendName(),
      currentDataset: currentDatasetRecord(),
      runs,
      tasks,
      liveDataPath,
      readiness: state.readiness,
      recentRunStatusLabel,
    });
    $("wbOverviewStrip").style.setProperty("--wb-overview-columns", String(model.stripItems.length || 5));
    $("wbOverviewStrip").dataset.overviewLabel = model.overviewLabel;
    $("wbOverviewStrip").dataset.family = model.benchmarkFamily;
    $("wbOverviewStrip").innerHTML = model.stripItems.map((item) => `
      <article class="wb-overview-chip wb-overview-chip--${escapeHtml(item.id)} wb-overview-chip-tone--${escapeHtml(item.tone || "default")}" data-chip="${escapeHtml(item.id)}" data-tone="${escapeHtml(item.tone || "default")}" data-show-hint="${item.showHint ? "true" : "false"}">
        <span class="wb-overview-chip-icon wb-overview-chip-icon--${escapeHtml(item.id)}" aria-hidden="true">${icon(item.icon)}</span>
        <div class="wb-overview-chip-copy" data-slot="copy">
          <span class="wb-overview-chip-label">${escapeHtml(item.label)}</span>
          <strong class="wb-overview-chip-value" title="${escapeHtml(item.value)}">${escapeHtml(item.value)}</strong>
          <small class="wb-overview-chip-hint" title="${escapeHtml(item.hint || "")}">${escapeHtml(item.hint || "")}</small>
        </div>
      </article>
    `).join("");
  }

  return {
    renderShellText,
    renderTopbar,
    renderOverview,
  };
}

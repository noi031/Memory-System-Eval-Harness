import { actionButtons } from "./shared.js";
import { icon } from "../icons.js";

function setupField({ label, iconName, controlHtml, escapeHtml, hint = "" }) {
  return `
    <label class="wb-setup-field">
      <span class="wb-setup-field-label">
        <span class="wb-setup-field-label-icon" aria-hidden="true">${icon(iconName)}</span>
        <span>${escapeHtml(label)}</span>
      </span>
      ${hint ? `<small class="wb-setup-field-hint">${escapeHtml(hint)}</small>` : ""}
      <span class="wb-setup-field-control">${controlHtml}</span>
    </label>
  `;
}

function progressMetric(label, value, tone = "default", escapeHtml) {
  return `
    <article class="wb-progress-metric wb-progress-metric--${escapeHtml(tone)}">
      <span>${escapeHtml(label)}</span>
      <strong title="${escapeHtml(String(value || "-"))}">${escapeHtml(String(value || "-"))}</strong>
    </article>
  `;
}

function progressMetaRow(label, value, title, escapeHtml) {
  return `
    <div class="wb-progress-detail-row">
      <dt>${escapeHtml(label)}</dt>
      <dd title="${escapeHtml(String(title || value || "-"))}">${escapeHtml(String(value || "-"))}</dd>
    </div>
  `;
}

function compactPathInline(compactPath, value) {
  return typeof compactPath === "function" ? compactPath(value || "-") : String(value || "-");
}

function localizeImportState(value) {
  const text = String(value || "").trim().toLowerCase();
  if (!text) return "-";
  if (["ready", "success", "completed", "complete"].includes(text)) return "就绪";
  if (["waiting"].includes(text)) return "等待中";
  if (["failed", "fail", "error", "interrupted"].includes(text)) return "失败";
  if (["running", "queued", "pending", "live"].includes(text)) return "运行中";
  if (["stopping"].includes(text)) return "停止中";
  return String(value || "-");
}

function buildLocomoImportProgressViewModel({ importTask, flowStatus, model }) {
  const imported = flowStatus?.artifacts?.imported || {};
  const sessionCount = Number(imported?.session_count || 0);
  const summaryCount = Number(imported?.summary_count || 0);
  const completeCount = Number(imported?.complete_count || 0);
  const fallbackRun = model?.sourceRun || null;
  const importedSummaryPath = (Array.isArray(imported?.summaries) && imported.summaries[0]?.summary_path) || "";
  const importedAccountPath = String(imported?.account_path || "").trim();
  const fallbackSampleLabel = String(fallbackRun?.sample || "").trim();
  const sampleLabel = String(imported?.sample || "").trim() || fallbackSampleLabel || "当前范围";
  const stageText = String(model.stage || "等待任务").trim() || "等待任务";
  const detailText = String(model.detail || model.log || "").trim();
  const statusText = String(importTask?.status || "").trim().toLowerCase();
  const statusSource = `${stageText} ${detailText} ${statusText}`.toLowerCase();
  const running = Boolean(importTask);
  const hasRecoveredImportRun = Boolean(!running && fallbackRun && (fallbackRun.output_file || fallbackRun.run_dir));
  const ready = !running && (summaryCount > 0 || completeCount > 0);
  const failed = !running && (
    statusSource.includes("失败")
    || statusSource.includes("failed")
    || statusSource.includes("error")
    || statusSource.includes("interrupted")
  );
  const completed = !running && !failed && (
    ready
    || statusSource.includes("完成")
    || statusSource.includes("complete")
    || statusSource.includes("ready")
  );
  const stateText = running
    ? String(importTask.status || "running")
    : failed
      ? "failed"
      : completed
        ? "ready"
        : "waiting";
  const stateLabel = localizeImportState(stateText);
  const stateTone = failed ? "failed" : (completed ? "success" : "default");
  const metrics = running
    ? [
        { label: "状态", value: stateLabel, tone: "live" },
        { label: "进度", value: `${model.percent || 0}%`, tone: "accent" },
        { label: "会话", value: model.processedText || "0 / 0", tone: "default" },
        { label: "任务", value: importTask.kind || "import", tone: "default" },
        { label: "阶段", value: stageText, tone: "default" },
        { label: "输出", value: importTask.output_file ? "已生成" : "待生成", tone: importTask.output_file ? "success" : "default" },
      ]
    : [
        { label: "状态", value: stateLabel, tone: stateTone },
        { label: "范围", value: sampleLabel, tone: "default" },
        { label: "会话", value: sessionCount > 0 ? String(sessionCount) : (hasRecoveredImportRun ? "-" : "0"), tone: "default" },
        { label: "摘要", value: summaryCount > 0 ? String(summaryCount) : (hasRecoveredImportRun ? "1" : "0"), tone: "default" },
        { label: "完成", value: completeCount > 0 ? String(completeCount) : (completed ? (hasRecoveredImportRun ? "1" : "0") : "0"), tone: completed ? "success" : "default" },
        { label: "阶段", value: stageText, tone: failed ? "failed" : "default" },
      ];
  const outputDirValue = running
    ? (importTask?.output_dir || importTask?.run_dir || "-")
    : (importedAccountPath || fallbackRun?.run_dir || "-");
  const resultFileValue = running
    ? (importTask?.output_file || "-")
    : (importedSummaryPath || fallbackRun?.output_file || "-");
  const detailRows = [
    { label: "当前阶段", value: stageText, title: stageText },
    { label: "输出目录", value: outputDirValue, title: outputDirValue },
    { label: "结果文件", value: resultFileValue, title: resultFileValue },
  ];
  return {
    stateText,
    stateLabel,
    stageText,
    title: running ? (String(model.title || "").trim() || "当前任务") : (failed ? "最近一次导入" : (completed ? "导入结果" : "导入状态")),
    metrics,
    detailRows,
  };
}

export function renderLocomoImportConfig({
  currentAccountConfig,
  currentBenchmark,
  currentDatasetRecord,
  currentWorkspace,
  escapeHtml,
  firstValue,
  state,
}) {
  const cfg = currentAccountConfig ? currentAccountConfig() : {};
  const activeBackend = String(cfg.memoryBackend || state.config?.memoryBackend || "echomemory").trim() || "echomemory";
  const benchmark = currentBenchmark();
  const currentPath = String(firstValue(
    state?.questionDataPaths?.locomo,
    currentDatasetRecord()?.path,
    benchmark.defaultData
  )).trim();
  const cachedSamples = Array.isArray(state?.questionSampleOptions?.[currentPath]) ? state.questionSampleOptions[currentPath] : [];
  const sampleCandidates = cachedSamples.length
    ? cachedSamples
    : Array.from(new Set((state.questions || []).map((item) => String(item.sample_id || "").trim()).filter(Boolean))).sort((a, b) => a.localeCompare(b));
  const preferredSample = String(
    state?.questionSamples?.locomo
    || state?.locomoQaDraft?.wbQaSample
    || "all"
  ).trim() || "all";
  const samples = Array.from(new Set([
    ...sampleCandidates,
    ...(preferredSample && preferredSample !== "all" ? [preferredSample] : []),
  ])).sort((a, b) => a.localeCompare(b));
  const sharedEchomemRoot = String(
    state?.locomoQaDraft?.wbQaEchomemRoot
    || firstValue(cfg.echomemRoot, state.config?.echomemRoot, state.readiness?.preflight?.runtime?.root, "")
  ).trim();
  const sharedEchomemBaseUrl = String(
    state?.locomoQaDraft?.wbQaEchomemBaseUrl
    || firstValue(cfg.echomemBaseUrl, state.config?.echomemBaseUrl, state.readiness?.preflight?.runtime?.url, "")
  ).trim();
  const preferredWorkspace = String(
    state?.locomoQaDraft?.wbWorkspace
    || currentWorkspace()
    || ""
  ).trim();

  const fields = [
    setupField({
      label: "数据集",
      iconName: "fileJson",
      hint: "选择本次导入使用的 LoCoMo 数据文件。",
      controlHtml: `<input id="wbDataPath" type="text" value="${escapeHtml(currentPath || benchmark.defaultData)}" placeholder="./dataset/locomo10.json">`,
      escapeHtml,
    }),
    setupField({
      label: "导入会话",
      iconName: "messagesSquare",
      hint: "先确定是导入全部对话，还是只导入当前样本。",
      controlHtml: `<select id="wbImportSample"><option value="all"${preferredSample === "all" ? " selected" : ""}>全部对话</option>${samples.map((sample) => `<option value="${escapeHtml(sample)}"${sample === preferredSample ? " selected" : ""}>${escapeHtml(sample)}</option>`).join("")}</select>`,
      escapeHtml,
    }),
    setupField({
      label: "记忆目录",
      iconName: "folderArchive",
      hint: "导入结果会写入当前 workspace 对应的记忆目录。",
      controlHtml: `<input id="wbWorkspace" type="text" value="${escapeHtml(preferredWorkspace)}" placeholder="记忆目录">`,
      escapeHtml,
    }),
  ];
  if (activeBackend === "echomemory") {
    fields.push(setupField({
      label: "EchoMemory 根目录",
      iconName: "database",
      hint: "只有在切换本地 EchoMemory 代码目录时才需要修改。",
      controlHtml: `<input id="wbImportEchomemRoot" type="text" value="${escapeHtml(sharedEchomemRoot)}" placeholder="/path/to/EchoMem">`,
      escapeHtml,
    }));
    fields.push(setupField({
      label: "EchoMemory URL",
      iconName: "activity",
      hint: "黑盒 API 模式下导入和检索会共用这个地址与身份。",
      controlHtml: `<input id="wbQaEchomemBaseUrl" type="text" value="${escapeHtml(sharedEchomemBaseUrl)}" placeholder="http://127.0.0.1:8015">`,
      escapeHtml,
    }));
  }

  const actions = actionButtons([
    { label: "开始导入", tone: "primary", action: "run-primary" },
    { label: "停止任务", tone: "danger-outline", action: "stop-tasks" },
  ], escapeHtml);

  return `
    <div class="wb-setup-flow">
      <div class="wb-setup-flow-copy">
        <strong>导入准备</strong>
        <p>先确定数据文件、会话范围和记忆目录，再启动这一轮记忆注入。</p>
      </div>
      <div class="wb-setup-actions">${actions}</div>
      <div class="wb-setup-field-stack">
        ${fields.join("")}
      </div>
    </div>
  `;
}

export function renderLocomoImportProgress({
  compactPath,
  escapeHtml,
  flowStatus,
  importRun,
  importTask,
  model,
}) {
  const view = buildLocomoImportProgressViewModel({
    importTask,
    flowStatus,
    model: {
      ...model,
      sourceRun: importRun || null,
    },
  });
  const hasRun = Boolean(importRun || importTask || (flowStatus?.artifacts?.imported?.summary_count > 0));
  return `
    <section class="wb-import-dashboard" data-state="${escapeHtml(view.stateText)}">
      <div class="wb-import-dashboard-head">
        <div class="wb-import-dashboard-title">
          <strong>${escapeHtml(view.title)}</strong>
          <p>${escapeHtml(view.stageText)}</p>
        </div>
        <div class="wb-import-dashboard-state wb-import-dashboard-state--${escapeHtml(view.stateText)}">
          <span class="wb-import-dashboard-state-dot" aria-hidden="true"></span>
          <span>${escapeHtml(view.stateLabel)}</span>
        </div>
      </div>
      <div class="wb-progress-track wb-import-dashboard-track">
        <span class="wb-progress-bar" style="width:${escapeHtml(String(model.percent || 0))}%"></span>
      </div>
      <div class="wb-import-dashboard-percent">${escapeHtml(String(model.percent || 0))}%</div>
      <div class="wb-import-dashboard-metrics">
        ${view.metrics.map((item) => progressMetric(item.label, item.value, item.tone, escapeHtml)).join("")}
      </div>
      <dl class="wb-import-dashboard-details">
        ${view.detailRows.map((item) => progressMetaRow(item.label, compactPathInline(compactPath, item.value), item.value, escapeHtml)).join("")}
      </dl>
      ${hasRun ? "" : `<div class="wb-import-dashboard-note">导入完成前，其他阶段的信息会保持折叠，避免首屏被运行细节打散。</div>`}
    </section>
  `;
}

import { summarizeBenchmarkRun } from "../run-metrics.js";
import { buildLongMemEvalConfigState, buildLongMemEvalPreviewModel } from "../benchmark-view-models.js";
import { getRunStatusLabel, isActiveStatus } from "../run-status.js";
import { officialQaFormProfile, renderOfficialQaConfig, renderOfficialQaPreview } from "./qa-generic.js";
import { longMemEvalDatasetOptions, longMemEvalDatasetPath, preferredLongMemEvalDatasetRecord } from "../longmemeval-defaults.js";
import { actionButtons } from "./shared.js";

// Validator contract: LongMemEval QA config still renders id="wbLongMemEvalCount" from the shared QA renderer.

function getLongMemEvalTask(tasksForBenchmark) {
  const tasks = tasksForBenchmark("longmemeval");
  return tasks.find((item) => {
    const cfg = item?.meta?.config || {};
    return isActiveStatus(item?.status) && cfg.import_only !== true;
  }) || tasks.find((item) => {
    const cfg = item?.meta?.config || {};
    return cfg.import_only !== true;
  }) || null;
}

function getLongMemEvalRunConfig(currentRun, state) {
  const run = currentRun();
  if (!run) return null;
  const runSnapshot = state.runConfigSnapshots?.[run.run_dir] || null;
  const runConfig = runSnapshot?.config || runSnapshot || null;
  if (runConfig && typeof runConfig === "object") return runConfig;
  const detail = state.runDetails[run.run_dir] || null;
  const manifestConfig = detail?.manifest?.config || null;
  if (manifestConfig && typeof manifestConfig === "object") return manifestConfig;
  const result = run.output_file ? state.resultSummaries[run.output_file] || null : null;
  const metrics = summarizeBenchmarkRun("longmemeval", run, detail, result);
  return metrics.summary?.summary_json || null;
}

export function getActiveLongMemEvalTaskConfig(tasksForBenchmark, currentRun, state) {
  const task = getLongMemEvalTask(tasksForBenchmark);
  if (task?.meta?.config) {
    const runConfig = getLongMemEvalRunConfig(currentRun, state) || {};
    return {
      ...runConfig,
      ...task.meta.config,
    };
  }
  return getLongMemEvalRunConfig(currentRun, state);
}

function getRunningLongMemEvalTaskConfig(tasksForBenchmark) {
  const tasks = tasksForBenchmark("longmemeval");
  const task = tasks.find((item) => {
    const cfg = item?.meta?.config || {};
    return isActiveStatus(item?.status) && cfg.import_only !== true;
  });
  return task?.meta?.config || {};
}

export function renderLongMemEvalQaConfig({
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
}) {
  const cfg = currentAccountConfig();
  const benchmark = currentBenchmark();
  const taskConfig = getRunningLongMemEvalTaskConfig(tasksForBenchmark);
  const records = datasetRecords("longmemeval");
  const draft = state.officialQaDrafts?.longmemeval || {};
  const draftedDataPath = String(draft.wbDataPath || "").trim();
  const selectedDataset = currentDatasetRecord() || preferredLongMemEvalDatasetRecord(records);
  const dataPath = longMemEvalDatasetPath({
    benchmark,
    currentDatasetRecord: draftedDataPath ? {path: draftedDataPath} : selectedDataset,
    datasetRecords: records,
    firstValue,
    taskConfig: {
      ...taskConfig,
      data: firstValue(draftedDataPath, taskConfig.data),
    },
  });
  const config = buildLongMemEvalConfigState({
    backendId: backendId(),
    benchmark,
    cfg,
    currentDatasetRecord: {path: dataPath},
    firstValue,
    taskConfig,
  });
  const formProfile = officialQaFormProfile("longmemeval", { config, escapeHtml, field, check });
  renderOfficialQaConfig({
    $,
    queryAll,
    check,
    escapeHtml,
    field,
    benchmarkId: "longmemeval",
    state,
    config,
    datasetLabel: formProfile.datasetLabel,
    datasetOptionsHtml: longMemEvalDatasetOptions(records, config.dataPath, escapeHtml),
    dataPlaceholder: formProfile.dataPlaceholder,
    countId: formProfile.countId,
    preTopKFields: formProfile.preTopKFields,
    echoFields: formProfile.echoFields,
    toolEnabledLabel: formProfile.toolEnabledLabel,
    toolLoopLabel: formProfile.toolLoopLabel,
    supportsSelected: formProfile.supportsSelected,
  });
}

export function renderLongMemEvalQaPreview({
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
}) {
  const run = currentRun();
  const detail = run ? state.runDetails[run.run_dir] : null;
  const result = run?.output_file ? state.resultSummaries[run.output_file] : null;
  const metrics = summarizeBenchmarkRun("longmemeval", run, detail, result);
  const taskConfig = getActiveLongMemEvalTaskConfig(tasksForBenchmark, currentRun, state) || {};
  const records = datasetRecords("longmemeval");
  const selectedDataset = currentDatasetRecord() || preferredLongMemEvalDatasetRecord(records);
  const displayStatus = getRunStatusLabel("longmemeval", run, metrics, tasksForBenchmark("longmemeval"));
  const preview = buildLongMemEvalPreviewModel({
    backendId: backendId(),
    cfg: currentAccountConfig(),
    currentBenchmark: currentBenchmark(),
    currentDatasetRecord: currentDatasetRecord(),
    firstValue,
    metrics,
    run,
    statusLabel: displayStatus,
    taskConfig: {
      ...taskConfig,
      data: longMemEvalDatasetPath({
        benchmark: currentBenchmark(),
        currentDatasetRecord: selectedDataset,
        datasetRecords: records,
        firstValue,
        taskConfig: {
          ...taskConfig,
          data: firstValue(taskConfig.data, metrics.dataPath),
        },
      }),
    },
  });
  renderOfficialQaPreview({
    $,
    compactPath,
    escapeHtml,
    formatDurationSeconds,
    formatInt,
    formatPct,
    preview,
  });
  const diagnostics = run?.output_file ? (state.qaDiagnosticsCache?.[run.output_file] || null) : null;
  const retryFailedCount = Number(diagnostics?.retryable_failed_questions || 0);
  const retryMissingCount = Number(diagnostics?.missing_questions_count || 0);
  const hasRun = Boolean(run?.output_file);
  const followupButtons = actionButtons([
    {
      id: "wbRunQaWrongCsv",
      label: "错题 CSV 重跑",
      tone: "secondary",
      disabled: !hasRun,
      title: hasRun ? "默认读取当前结果目录下的 wrong_questions_brief.csv" : "当前没有可用结果",
    },
    {
      id: "wbRunQaRetryFailed",
      label: "重跑失败题",
      tone: "secondary",
      disabled: !hasRun || (Boolean(diagnostics) && retryFailedCount <= 0),
      title: !hasRun
        ? "当前没有可用结果"
        : (Boolean(diagnostics)
          ? (retryFailedCount > 0 ? "" : "当前结果没有可恢复失败题")
          : "点击后从当前结果读取失败题诊断"),
    },
    {
      id: "wbRunQaRetryMissing",
      label: "补跑缺失题",
      tone: "secondary",
      disabled: !hasRun || (Boolean(diagnostics) && retryMissingCount <= 0),
      title: !hasRun
        ? "当前没有可用结果"
        : (Boolean(diagnostics)
          ? (retryMissingCount > 0 ? "" : "当前结果没有缺失题")
          : "点击后从当前结果读取缺失题诊断"),
    },
  ], escapeHtml);
  $("wbQaPreview").innerHTML += `
    <section class="wb-current-workbench wb-current-workbench-followups">
      <article class="wb-current-workbench-main">
        <div class="wb-current-workbench-copy">
          <span class="wb-current-workbench-kicker">恢复入口</span>
          <strong>结果后续操作</strong>
          <small>${escapeHtml(hasRun ? `错题重放基于当前结果；缺失 ${retryMissingCount || 0} · 可恢复失败 ${retryFailedCount || 0}` : "先运行 LongMemEval QA，再进行错题重跑或缺失题补跑。")}</small>
        </div>
      </article>
      <div class="wb-current-workbench-grid wb-current-workbench-grid-followups">
        <section class="wb-current-workbench-side-section">
          <header>可执行操作</header>
          <div class="wb-panel-head-actions wb-current-workbench-followup-actions">${followupButtons}</div>
        </section>
      </div>
    </section>
  `;
}

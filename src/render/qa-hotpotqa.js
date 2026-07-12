import { summarizeBenchmarkRun } from "../run-metrics.js";
import { buildHotpotQaConfigState, buildHotpotQaPreviewModel } from "../benchmark-view-models.js";
import { getRunStatusLabel, isActiveStatus } from "../run-status.js";
import { officialQaFormProfile, renderOfficialQaConfig, renderOfficialQaPreview } from "./qa-generic.js";
import { hotpotQaDatasetOptions, hotpotQaDatasetPath, preferredHotpotQaDatasetRecord } from "../hotpotqa-defaults.js";
import { actionButtons } from "./shared.js";

// Validator contract: HotpotQA QA config still renders id="wbDatasetPreset" from the shared QA renderer.
// Validator contract: HotpotQA QA config keeps wbHotpotQaCorpusMode and wbHotpotQaGlobalImportMode in the shared profile.

function getHotpotQaTask(tasksForBenchmark) {
  const tasks = tasksForBenchmark("hotpotqa");
  return tasks.find((item) => {
    const cfg = item?.meta?.config || {};
    return isActiveStatus(item?.status) && cfg.import_only !== true;
  }) || tasks.find((item) => {
    const cfg = item?.meta?.config || {};
    return cfg.import_only !== true;
  }) || null;
}

function getHotpotQaRunConfig(currentRun, state) {
  const run = currentRun();
  if (!run) return null;
  const runSnapshot = state.runConfigSnapshots?.[run.run_dir] || null;
  const runConfig = runSnapshot?.config || runSnapshot || null;
  if (runConfig && typeof runConfig === "object") return runConfig;
  const detail = state.runDetails[run.run_dir] || null;
  const manifestConfig = detail?.manifest?.config || null;
  if (manifestConfig && typeof manifestConfig === "object") return manifestConfig;
  const result = run.output_file ? state.resultSummaries[run.output_file] || null : null;
  const metrics = summarizeBenchmarkRun("hotpotqa", run, detail, result);
  return metrics.summary?.summary_json || null;
}

export function getActiveHotpotQaTaskConfig(tasksForBenchmark, currentRun, state) {
  const task = getHotpotQaTask(tasksForBenchmark);
  if (task?.meta?.config) {
    const runConfig = getHotpotQaRunConfig(currentRun, state) || {};
    return {
      ...runConfig,
      ...task.meta.config,
    };
  }
  return getHotpotQaRunConfig(currentRun, state);
}

function getRunningHotpotQaTaskConfig(tasksForBenchmark) {
  const tasks = tasksForBenchmark("hotpotqa");
  const task = tasks.find((item) => {
    const cfg = item?.meta?.config || {};
    return isActiveStatus(item?.status) && cfg.import_only !== true;
  });
  return task?.meta?.config || {};
}

export function renderHotpotQaConfig({
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
  const taskConfig = getRunningHotpotQaTaskConfig(tasksForBenchmark);
  const records = datasetRecords("hotpotqa");
  const draft = state.officialQaDrafts?.hotpotqa || {};
  const draftedDataPath = String(draft.wbDataPath || "").trim();
  const selectedDataset = currentDatasetRecord() || preferredHotpotQaDatasetRecord(records);
  const dataPath = hotpotQaDatasetPath({
    benchmark,
    currentDatasetRecord: draftedDataPath ? {path: draftedDataPath} : selectedDataset,
    datasetRecords: records,
    firstValue,
    taskConfig: {
      ...taskConfig,
      data: firstValue(draftedDataPath, taskConfig.data),
    },
  });
  const config = buildHotpotQaConfigState({
    backendId: backendId(),
    benchmark,
    cfg,
    currentDatasetRecord: {path: dataPath},
    firstValue,
    taskConfig,
  });
  const formProfile = officialQaFormProfile("hotpotqa", { config, escapeHtml, field, check });
  renderOfficialQaConfig({
    $,
    queryAll,
    check,
    escapeHtml,
    field,
    benchmarkId: "hotpotqa",
    state,
    config,
    datasetLabel: formProfile.datasetLabel,
    datasetOptionsHtml: hotpotQaDatasetOptions(records, config.dataPath, escapeHtml),
    dataPlaceholder: formProfile.dataPlaceholder,
    countId: formProfile.countId,
    preTopKFields: formProfile.preTopKFields,
    echoFields: formProfile.echoFields,
    toolEnabledLabel: formProfile.toolEnabledLabel,
    toolLoopLabel: formProfile.toolLoopLabel,
    supportsSelected: formProfile.supportsSelected,
  });
}

export function renderHotpotQaPreview({
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
  const metrics = summarizeBenchmarkRun("hotpotqa", run, detail, result);
  const taskConfig = getActiveHotpotQaTaskConfig(tasksForBenchmark, currentRun, state) || {};
  const records = datasetRecords("hotpotqa");
  const selectedDataset = currentDatasetRecord() || preferredHotpotQaDatasetRecord(records);
  const displayStatus = getRunStatusLabel("hotpotqa", run, metrics, tasksForBenchmark("hotpotqa"));
  const preview = buildHotpotQaPreviewModel({
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
      data: hotpotQaDatasetPath({
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
    <article class="wb-card">
      <strong>结果后续操作</strong>
      <small>${escapeHtml(hasRun ? `错题重放基于当前结果；缺失 ${retryMissingCount || 0} · 可恢复失败 ${retryFailedCount || 0}` : "先运行 HotpotQA QA，再进行错题重跑或缺失题补跑。")}</small>
      <div class="wb-panel-head-actions">${followupButtons}</div>
    </article>
  `;
}

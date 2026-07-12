import { BENCHMARKS } from "./config.js";
import { isActiveStatus } from "./run-status.js";

function activeTask(tasks) {
  return (tasks || []).find((item) =>
    isActiveStatus(item.status)
  ) || null;
}

function basename(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  const normalized = text.replace(/\\/g, "/");
  const parts = normalized.split("/");
  return parts[parts.length - 1] || text;
}

function stagePresentation(benchmarkId) {
  return BENCHMARKS[String(benchmarkId || "").toLowerCase()]?.shellMeta?.stagePresentation || {};
}

function primaryActionIcon(shellMeta, activeStage) {
  return shellMeta?.primaryActionIcons?.[activeStage] || "play";
}

function localizeStatus(value) {
  const text = String(value || "").trim().toLowerCase();
  if (!text) return "-";
  if (["idle"].includes(text)) return "空闲";
  if (["ready", "ok", "success", "succeeded"].includes(text)) return "就绪";
  if (["running", "queued", "pending", "live"].includes(text)) return "运行中";
  if (["waiting"].includes(text)) return "等待中";
  if (["failed", "fail", "error", "bad"].includes(text)) return "失败";
  if (["completed", "done", "complete"].includes(text)) return "已完成";
  if (["finalizing"].includes(text)) return "收尾中";
  if (["stopping"].includes(text)) return "停止中";
  return String(value || "-");
}

export function activeTaskDataPath(tasks) {
  return activeTask(tasks)?.meta?.config?.data || "";
}

export function buildShellTextModel({ benchmark, activeStage }) {
  const stageLabels = benchmark.stageLabels || {};
  const shellMeta = benchmark?.shellMeta || {};
  const presentation = stagePresentation(benchmark?.id);
  const activeStagePresentation = presentation?.[activeStage] || {};
  const stageWorkflowTitles = shellMeta.stageWorkflowTitles || {};
  const stageWorkflowSubtitles = shellMeta.stageWorkflowSubtitles || {};
  return {
    pageTitle: benchmark.title,
    pageSubtitle: benchmark.subtitle,
    workflowTitle: stageWorkflowTitles[activeStage] || benchmark.workflowTitle,
    workflowSubtitle: stageWorkflowSubtitles[activeStage] || benchmark.workflowSubtitle,
    primaryLabel: stageLabels[activeStage]
      || (activeStage === "import" ? benchmark.importLabel : benchmark.primaryRunLabel),
    stageNavLabels: benchmark.stageNavLabels || {},
    stagePresentation: presentation,
    activeStageLabel: stageLabels[activeStage]
      || (activeStage === "import" ? benchmark.importLabel : benchmark.primaryRunLabel),
    activeStageIcon: activeStagePresentation.icon || primaryActionIcon(shellMeta, activeStage),
    activeStageTone: activeStagePresentation.tone || activeStage || "default",
    activeStageSubtitle: activeStagePresentation.subtitle || "",
    pageIcon: shellMeta.pageIcon || "bookOpen",
    workflowIcon: shellMeta.workflowIcon || "bookOpen",
    shellLayout: shellMeta.shellLayout || "default-workbench",
    benchmarkFamily: shellMeta.benchmarkFamily || "general",
    heroKicker: shellMeta.heroKicker || "当前页面",
    overviewLabel: shellMeta.overviewLabel || "概览",
    primaryActionIcon: primaryActionIcon(shellMeta, activeStage),
  };
}

export function buildTopbarModel({
  backendName,
  compactPath,
  formatInt,
  hasRunningTasks,
  readiness,
  selectedAccount,
  standaloneApiBase,
}) {
  const readinessStatus = String(readiness?.status || "").trim();
  const normalizedReadiness = readinessStatus.toLowerCase();
  const hasReadinessScore = Number.isFinite(Number(readiness?.score));
  const idleStatus = hasRunningTasks ? "running" : "ready";
  const resolvedStatus = ["fail", "failed", "error", "bad"].includes(normalizedReadiness)
    ? "failed"
    : (["running", "queued", "pending"].includes(normalizedReadiness) ? "running" : idleStatus);
  const readinessScoreSuffix = hasReadinessScore ? ` ${formatInt(readiness.score)}/100` : "";
  return {
    accountName: selectedAccount,
    accountHint: "",
    backendName,
    sidebarStatus: readinessStatus
      ? `${localizeStatus(readinessStatus)}${readinessScoreSuffix}`
      : localizeStatus(idleStatus),
    sidebarStatusTone: resolvedStatus,
    backendHint: standaloneApiBase ? compactPath(standaloneApiBase, 22, 14) : "同源 API",
    topbarStatus: resolvedStatus,
    topbarStatusLabel: localizeStatus(resolvedStatus),
    topbarStatusHint: standaloneApiBase ? `代理到 ${compactPath(standaloneApiBase, 24, 10)}` : "当前页面与 API 同源",
  };
}

export function buildOverviewModel({
  benchmark,
  compactPath,
  backendName,
  currentDataset,
  runs,
  tasks,
  liveDataPath,
  recentRunStatusLabel,
}) {
  const recentRun = runs[0] || null;
  const liveTask = activeTask(tasks);
  const liveProgress = liveTask?.progress || null;
  const datasetPath = liveDataPath || currentDataset?.path || benchmark.defaultData;
  const idleText = liveTask ? String(liveTask.status || "-") : "idle";
  const statusValue = localizeStatus(idleText);
  const recentHint = recentRun ? `${recentRunStatusLabel || recentRun.status || "-"} · ${benchmark.title}` : "暂无结果";
  return {
    shellLayout: benchmark?.shellMeta?.shellLayout || "default-workbench",
    benchmarkFamily: benchmark?.shellMeta?.benchmarkFamily || "general",
    overviewLabel: benchmark?.shellMeta?.overviewLabel || "Overview",
    activeTaskCount: String(tasks.length),
    activeTaskHint: liveTask
      ? (liveProgress?.detail || `${liveTask.kind || "-"} · ${liveTask.status || "-"}`)
      : "当前无活跃任务",
    recentRunCount: String(runs.length),
    recentRunHint: recentHint,
    datasetLabel: basename(datasetPath),
    datasetHint: compactPath(datasetPath),
    backendValue: backendName || "-",
    backendHint: "记忆后端",
    statusValue,
    statusHint: liveTask ? (liveProgress?.detail || "运行中") : "空闲",
    stripItems: [
      { id: "active", label: "活跃任务", value: String(tasks.length), hint: liveTask ? (liveProgress?.detail || `${liveTask.kind || "-"} · ${localizeStatus(liveTask.status || "-")}`) : "当前无活跃任务", icon: "activity", tone: liveTask ? "live" : "idle" },
      { id: "recent", label: "最近结果", value: String(runs.length), hint: recentHint, icon: "clock3", tone: recentRun ? "recent" : "empty" },
      { id: "dataset", label: "数据集", value: basename(datasetPath), hint: compactPath(datasetPath), icon: "fileJson", tone: "source" },
      { id: "backend", label: "后端", value: backendName || "-", hint: "记忆后端", icon: "database", tone: "system" },
      { id: "status", label: "状态", value: statusValue, hint: liveTask ? (liveProgress?.detail || "运行中") : "空闲", icon: "circleDot", tone: liveTask ? "live" : "idle" },
    ],
  };
}

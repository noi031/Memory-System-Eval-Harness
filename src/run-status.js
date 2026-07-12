import { benchmarkHasOfficialEval, getBenchmark } from "./benchmark-registry.js";
import { BENCHMARKS } from "./config.js";
import { officialSummaryReadyForMetrics } from "./run-metrics.js";

export const ACTIVE_TASK_STATUSES = ["running", "queued", "pending", "stopping"];

export function isActiveStatus(status) {
  return ACTIVE_TASK_STATUSES.includes(String(status || "").toLowerCase());
}

export function findTaskForRun(run, tasks) {
  if (!run) return null;
  return (tasks || []).find((task) =>
    String(task?.id || "") === String(run.id || "")
    || String(task?.run_dir || "") === String(run.run_dir || "")
  ) || null;
}

function findLocomoJudgeTaskForRun(run, tasks) {
  const outputFile = String(run?.output_file || "").trim();
  if (!run || !outputFile) return null;
  return (tasks || []).find((task) => {
    if (String(task?.kind || "").trim().toLowerCase() !== "judge") return false;
    if (!isActiveStatus(task?.status)) return false;
    const inputFile = String(task?.meta?.config?.input || "").trim();
    const taskOutput = String(task?.output_file || "").trim();
    return inputFile === outputFile || taskOutput === outputFile;
  }) || null;
}

function normalizedRunStatus(status) {
  const text = String(status || "").toLowerCase();
  if (["ok", "ready", "success", "succeeded", "completed", "done"].includes(text)) return "completed";
  if (["fail", "failed", "error", "bad"].includes(text)) return "failed";
  return text || "unknown";
}

function officialSummaryReady(benchmarkId, metrics) {
  return officialSummaryReadyForMetrics(benchmarkId, metrics);
}

function officialRunPhase(benchmarkId, run, metrics, tasks) {
  const status = normalizedRunStatus(run?.status);
  const summaryReady = officialSummaryReady(benchmarkId, metrics);
  const liveTask = findTaskForRun(run, tasks);
  const progress = liveTask?.progress || null;
  if (!isActiveStatus(status)) {
    return summaryReady ? "completed" : (status === "failed" ? "failed" : "waiting_summary");
  }
  if (summaryReady) return "completed";
  const total = Number(progress?.total || 0);
  const current = Number(progress?.current || 0);
  if (total > 0 && current >= total) return "finalizing";
  return "running";
}

function genericRunPhase(run, tasks) {
  const status = normalizedRunStatus(run?.status);
  if (isActiveStatus(status)) return "running";
  if (status === "completed") return "completed";
  if (status === "failed") return "failed";
  if (findTaskForRun(run, tasks)) return "running";
  return status || "unknown";
}

function locomoRunPhase(run, metrics, tasks) {
  const status = normalizedRunStatus(run?.status);
  const rows = Number(metrics?.rows || 0);
  const pending = Number(metrics?.pending || 0);
  const activeJudge = findLocomoJudgeTaskForRun(run, tasks);
  if (isActiveStatus(status)) return "running";
  if (status === "failed") return "failed";
  if (activeJudge) return "finalizing";
  if (rows > 0 && pending > 0) return "waiting_judge";
  if (status === "completed") return "completed";
  if (findTaskForRun(run, tasks)) return "running";
  return status || "unknown";
}

export function getRunPhase(benchmarkId, run, metrics, tasks) {
  if (!run) return "empty";
  if (String(benchmarkId || "").toLowerCase() === "locomo") {
    return locomoRunPhase(run, metrics, tasks);
  }
  if (benchmarkHasOfficialEval(getBenchmark(BENCHMARKS, benchmarkId))) {
    return officialRunPhase(benchmarkId, run, metrics, tasks);
  }
  return genericRunPhase(run, tasks);
}

export function getRunStatusLabel(benchmarkId, run, metrics, tasks) {
  const phase = getRunPhase(benchmarkId, run, metrics, tasks);
  if (phase === "completed") return "已完成";
  if (phase === "finalizing") return "收尾中";
  if (phase === "running") return "运行中";
  if (phase === "waiting_judge") return "等待判分";
  if (phase === "waiting_summary") return "等待 summary";
  if (phase === "failed") return "失败";
  return run?.status || "-";
}

export function getRunStatusTone(benchmarkId, run, metrics, tasks) {
  const phase = getRunPhase(benchmarkId, run, metrics, tasks);
  if (phase === "completed") return "success";
  if (phase === "failed") return "failed";
  return "running";
}

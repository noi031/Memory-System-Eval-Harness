import { isImportOnlySummary, officialSummaryReadyForMetrics } from "../run-metrics.js";
import { isActiveStatus } from "../run-status.js";

function asNumber(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function firstNumber() {
  for (let i = 0; i < arguments.length; i += 1) {
    const num = asNumber(arguments[i]);
    if (num !== null) return num;
  }
  return null;
}

function artifactEntry(detail, key) {
  const value = detail?.artifact_status?.[key];
  return value && typeof value === "object" ? value : null;
}

function artifactPath(detail, key, fallback = "") {
  const entry = artifactEntry(detail, key);
  return String(entry?.path || fallback || "").trim();
}

function artifactExists(detail, key, fallbackPath = "") {
  const entry = artifactEntry(detail, key);
  if (entry && entry.exists !== undefined) return entry.exists === true;
  return Boolean(String(fallbackPath || "").trim());
}

function isTerminalSuccessStatus(status) {
  return ["ok", "ready", "success", "succeeded", "completed", "done"].includes(String(status || "").trim().toLowerCase());
}

function summarizeProbeError(error) {
  const text = String(error || "").trim();
  if (!text) return "";
  if (/Arrearage|overdue-payment/i.test(text)) return "provider 账号欠费或当前不可用";
  if (/无效的令牌|invalid token/i.test(text)) return "provider token 无效";
  if (/timed out|timeout/i.test(text)) return "provider 请求超时";
  return text.slice(0, 220);
}

function modelProbeCheck(result, label) {
  const ok = result?.ok === true;
  const model = String(result?.model || "").trim();
  const status = String(result?.status || "").trim();
  const error = summarizeProbeError(result?.error);
  return {
    name: label,
    ok,
    message: ok
      ? `${model || "model"} @ ${result?.base_url || "-"}`
      : `${model || "model"} ${status ? `status=${status}` : ""}${error ? ` · ${error}` : ""}`.trim(),
  };
}

function officialMetrics(benchmarkId, summary, officialSummary) {
  if (benchmarkId === "hotpotqa") {
    return {
      official: officialSummary || null,
      answerF1: firstNumber(summary?.official_answer_f1, officialSummary?.answer_f1),
      answerEm: firstNumber(summary?.official_answer_em, officialSummary?.answer_em),
    };
  }
  return {
    official: officialSummary || null,
    officialOverallAccuracy: firstNumber(summary?.official_overall_accuracy, officialSummary?.overall_accuracy),
    officialTaskAveragedAccuracy: firstNumber(summary?.official_task_averaged_accuracy, officialSummary?.task_averaged_accuracy),
  };
}

export function buildOfficialJudgePreflight({
  benchmarkId,
  benchmarkLabel,
  run,
  detail,
  result,
  formDataPath = "",
  officialSummaryArtifactKey = "",
  officialSummaryFallbackKey = "",
}) {
  const summary = result?.summary || detail?.record?.summary || run?.summary || {};
  const summaryJson = summary?.summary_json || {};
  const officialSummary = summaryJson?.official_eval?.summary || null;
  const officialSummaryPath = artifactPath(
    detail,
    officialSummaryArtifactKey,
    String(summaryJson?.official_eval?.summary_path || summary?.[officialSummaryFallbackKey] || "").trim()
  );
  const officialSummaryExists = artifactExists(detail, officialSummaryArtifactKey, officialSummaryPath);
  const dataPath = String(detail?.record?.dataset_path || run?.dataset_path || formDataPath || "").trim();
  const status = String(run?.status || "").trim().toLowerCase();
  const runActive = ["running", "queued", "pending", "stopping"].includes(status);
  const runComplete = Boolean(run?.run_dir) && !runActive && isTerminalSuccessStatus(status);
  const outputPath = artifactPath(detail, "output_file", String(run?.output_file || "").trim());
  const outputExists = artifactExists(detail, "output_file", outputPath);
  const rows = firstNumber(summary?.rows, summaryJson?.rows, summaryJson?.count) || 0;
  const runOfficialEvalAfter = Boolean(
    summary?.official_eval_after
    ?? summaryJson?.official_eval_after
  );
  const importOnly = isImportOnlySummary(benchmarkId, summary, run?.name);
  const metrics = officialMetrics(benchmarkId, summary, officialSummary);
  const hasOfficialMetrics = officialSummaryReadyForMetrics(benchmarkId, metrics);
  const officialGraded = firstNumber(
    officialSummary?.graded,
    summary?.official_graded,
    summaryJson?.official_graded,
    summary?.graded
  ) || 0;
  const officialSummaryReady = Boolean(
    hasOfficialMetrics
    && officialSummaryPath
    && officialSummaryExists
    && officialGraded > 0
  );
  const checks = [
    {
      name: "current_result",
      ok: Boolean(run?.run_dir),
      message: run?.name || `当前没有 ${benchmarkLabel} 结果`,
    },
    {
      name: "run_complete",
      ok: runComplete,
      message: !run?.run_dir
        ? "当前没有可检查的运行目录"
        : runActive
          ? `当前 ${benchmarkLabel} 任务仍在运行，完成后再进入报告阶段`
          : (isTerminalSuccessStatus(status) ? "当前任务已结束" : `当前任务状态为 ${status || "-"}`),
    },
    {
      name: "output_file",
      ok: Boolean(outputPath) && outputExists,
      message: outputExists
        ? outputPath
        : (outputPath || "当前结果还没有稳定输出文件"),
    },
    {
      name: "dataset",
      ok: Boolean(dataPath),
      message: dataPath || "当前结果缺少稳定的数据集路径",
    },
    {
      name: "import_only",
      ok: !importOnly,
      message: importOnly
        ? "当前 run 是 import_only，只完成导入，没有可进入报告阶段的 QA 结果"
        : "当前 run 包含 QA 结果",
    },
    {
      name: "rows",
      ok: rows > 0,
      message: rows > 0 ? `${rows} rows` : "当前结果还没有有效 QA 行",
    },
    {
      name: "official_eval_after",
      ok: Boolean(hasOfficialMetrics || runOfficialEvalAfter),
      message: hasOfficialMetrics
        ? `当前结果已包含 ${benchmarkLabel} 官方评测指标`
        : (runOfficialEvalAfter
          ? "当前配置开启了 official eval；若摘要尚未出现，通常还在收尾"
          : `当前结果未开启 official eval，报告页不会自动生成官方 ${benchmarkLabel} 结果`),
    },
    {
      name: "official_summary_status",
      ok: officialSummaryReady,
      message: officialSummaryReady
        ? `official summary 已就绪：${officialSummaryPath} · graded=${officialGraded}`
        : (runActive
          ? "任务仍在运行，官方评测结果会在完成后落盘"
          : (officialSummaryPath && !officialSummaryExists
            ? `official summary 路径存在但文件缺失：${officialSummaryPath}`
            : (hasOfficialMetrics
              ? `official summary 还不完整，graded=${officialGraded}`
              : "当前结果尚未看到完整 official summary"))),
    },
  ];
  const ok = checks.every((item) => item.ok !== false);
  return {
    ok,
    title: "官方评测预检查",
    subtitle: ok
      ? `${benchmarkLabel} 当前结果已经具备官方评测视图，可直接进入报告阶段。`
      : `${benchmarkLabel} 报告预检查未通过：需要任务完成并生成完整官方评测摘要。`,
    checks,
    dataPath,
    officialSummaryReady,
  };
}

export async function buildOfficialQaLaunchGate({
  benchmarkId,
  benchmarkLabel,
  form,
  payload,
  state,
  tasks = [],
  validatePayload,
  probeModel,
  probePayload = null,
  extraChecks = [],
}) {
  const { check: activeTaskCheck } = activeOfficialTaskCheck(tasks, benchmarkLabel);
  const localChecks = [
    activeTaskCheck,
    {
      name: "dataset",
      ok: Boolean(String(form?.data || "").trim()),
      message: String(form?.data || "").trim() || `未填写 ${benchmarkLabel} 数据集路径`,
    },
    {
      name: "workspace",
      ok: Boolean(String(form?.workspace || "").trim()),
      message: String(form?.workspace || "").trim() || "未填写记忆目录",
    },
    {
      name: "count",
      ok: Number(form?.count || 0) > 0,
      message: `${Number(form?.count || 0)} 题`,
    },
    {
      name: "top_k",
      ok: Number(form?.top_k || 0) > 0,
      message: `Top K ${Number(form?.top_k || 0)}`,
    },
    {
      name: "answer_model",
      ok: Boolean(String(payload?.answer_model || "").trim()),
      message: String(payload?.answer_model || "").trim() || "未配置回答模型",
    },
    {
      name: "answer_base_url",
      ok: Boolean(String(payload?.answer_base_url || "").trim()),
      message: String(payload?.answer_base_url || "").trim() || "未配置回答模型地址",
    },
    ...((extraChecks || []).map((item) => ({
      name: item?.name,
      ok: item?.ok !== false,
      message: item?.message || "",
    }))),
  ];
  const validateResult = typeof validatePayload === "function"
    ? await validatePayload(payload).catch((error) => ({
      ok: false,
      checks: [{ name: "validate", ok: false, message: error.message || "QA 校验失败" }],
    }))
    : { ok: true, checks: [] };
  const modelProbe = typeof probeModel === "function"
    ? await probeModel(probePayload || payload, "agent")
    : null;
  const checks = [
    ...localChecks,
    ...((validateResult?.checks || []).map((item) => ({
      name: item.name,
      ok: item.ok !== false,
      message: item.message || "",
    }))),
    ...(modelProbe ? [modelProbeCheck(modelProbe, "answer_model_probe")] : []),
  ];
  const gate = {
    ok: checks.every((item) => item.ok !== false),
    title: "QA 启动检查",
    subtitle: checks.every((item) => item.ok !== false)
      ? `${benchmarkLabel} QA 启动检查通过，可以开始运行。`
      : `${benchmarkLabel} QA 启动检查未通过，请先修正参数或运行环境。`,
    checks,
    validateResult,
    modelProbe,
  };
  if (state) {
    state.officialQaGates = state.officialQaGates || {};
    state.officialQaGates[benchmarkId] = gate;
  }
  return gate;
}

export function activeOfficialTaskCheck(tasks = [], benchmarkLabel = "当前 benchmark") {
  const activeTask = (tasks || []).find((task) => isActiveStatus(task?.status));
  return {
    activeTask,
    check: {
      name: "active_task",
      ok: !activeTask,
      message: activeTask
        ? `${benchmarkLabel} 当前已有活跃任务：${activeTask.name || activeTask.id || activeTask.kind || "-"}`
        : `${benchmarkLabel} 当前没有活跃任务`,
    },
  };
}

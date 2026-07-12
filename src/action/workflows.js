import { createHotpotQaActions } from "./hotpotqa.js";
import { createLongMemEvalActions } from "./longmemeval.js";
import { createLocomoActions } from "./locomo.js";
import { createReportActions } from "./report.js";
import { createSystemActions } from "./system.js";
import { summarizeBenchmarkRun } from "../run-metrics.js";

export function createWorkflowActions(deps) {
  const {
    api,
    currentAccountConfig,
    currentBenchmark,
    currentRun,
    ensureRunDetail,
    exportPendingCsv,
    firstValue,
    formReaders,
    loadCsvPreview,
    loadQaDiagnostics,
    loadQuestionDetail,
    loadPendingPreview,
    state,
    tasksForBenchmark,
    validatePayload,
  } = deps;

  const locomo = createLocomoActions(deps);
  const hotpotqa = createHotpotQaActions(deps);
  const longmemeval = createLongMemEvalActions(deps);
  const report = createReportActions(deps);
  const system = createSystemActions(deps);
  const benchmarkWorkflows = {
    locomo: {
      startImport: locomo.startImport,
      startQa: locomo.startQa,
      preflightQa: locomo.preflightQa,
      preflightJudge: () => locomo.preflightJudge({ currentRun }),
      startSelectedQa: locomo.startSelectedQa,
      startWrongCsvQa: locomo.startWrongCsvQa,
      retryFailedQa: locomo.retryFailedQa,
      retryMissingQa: locomo.retryMissingQa,
      runJudge: () => runJudge(false),
    },
    hotpotqa: {
      startImport: hotpotqa.startImport,
      startQa: hotpotqa.startQa,
      preflightQa: hotpotqa.preflightQa,
      preflightJudge: hotpotqa.preflightJudge,
      startSelectedQa: hotpotqa.startSelectedQa,
      startWrongCsvQa: hotpotqa.startWrongCsvQa,
      retryFailedQa: hotpotqa.retryFailedQa,
      retryMissingQa: hotpotqa.retryMissingQa,
      runJudge: async () => {
        const preflight = await hotpotqa.preflightJudge();
        if (!preflight?.ok) throw new Error("当前没有可查看的 HotpotQA 结果");
        return {stage: "report"};
      },
    },
    longmemeval: {
      startImport: longmemeval.startImport,
      startQa: longmemeval.startQa,
      preflightQa: longmemeval.preflightQa,
      preflightJudge: longmemeval.preflightJudge,
      startSelectedQa: longmemeval.startSelectedQa,
      startWrongCsvQa: longmemeval.startWrongCsvQa,
      retryFailedQa: longmemeval.retryFailedQa,
      retryMissingQa: longmemeval.retryMissingQa,
      runJudge: async () => {
        const preflight = await longmemeval.preflightJudge();
        if (!preflight?.ok) throw new Error("当前没有可查看的 LongMemEval 结果");
        return {stage: "report"};
      },
    },
  };

  function locomoTasks() {
    return typeof tasksForBenchmark === "function" ? tasksForBenchmark("locomo") : (state.tasks || []);
  }

  function locomoQaRunKinds() {
    return new Set([
      "echomemory_qa",
      "openviking_qa",
      "echomemory_qa_retry_failed",
      "echomemory_qa_retry_missing",
      "openviking_qa_retry_failed",
      "openviking_qa_retry_missing",
    ]);
  }

  function activeLocomoJudgeTaskForRun(run) {
    const outputFile = String(run?.output_file || "").trim();
    if (!outputFile) return null;
    return locomoTasks().find((task) => {
      if (String(task?.kind || "").trim().toLowerCase() !== "judge") return false;
      const status = String(task?.status || "").trim().toLowerCase();
      if (!["running", "queued", "pending", "stopping"].includes(status)) return false;
      const inputFile = String(task?.meta?.config?.input || "").trim();
      const taskOutput = String(task?.output_file || "").trim();
      return inputFile === outputFile || taskOutput === outputFile;
    }) || null;
  }

  function locomoRunProgress(run, detail, result) {
    const metrics = summarizeBenchmarkRun("locomo", run, detail, result);
    return {
      summary: metrics.summary || {},
      rows: Number(metrics.rows || 0),
      graded: Number(metrics.graded || 0),
      pending: Number(metrics.pending || 0),
    };
  }

  async function maybeCompleteLocomoQaRun(run, { detail = null, result = null } = {}) {
    const kind = String(run?.kind || "").trim().toLowerCase();
    const status = String(run?.status || "").trim().toLowerCase();
    if (!locomoQaRunKinds().has(kind) || !["ok", "ready", "success", "succeeded", "completed", "done"].includes(status)) {
      return null;
    }
    const { rows, pending } = locomoRunProgress(run, detail, result);
    if (rows <= 0) return null;
    if (pending > 0 && activeLocomoJudgeTaskForRun(run)) return null;
    return api("/api/locomo-qa-completion", {
      method: "POST",
      body: JSON.stringify({
        run_dir: run.run_dir,
        export_report: pending <= 0,
      }),
    }).catch(() => null);
  }

  function mergeRecallRows(traceRows = [], previewRows = []) {
    const merged = [];
    const seen = new Set();
    for (const row of [...traceRows, ...previewRows]) {
      const questionId = String(row?.question_id || "").trim();
      const rowIndex = String(row?._row_index ?? "").trim();
      const key = questionId || rowIndex;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      merged.push(row);
    }
    return merged;
  }

  function currentBenchmarkWorkflow() {
    return benchmarkWorkflows[state.activeBenchmark] || benchmarkWorkflows.locomo;
  }

  async function startImport() {
    state.userSelectedRunDirs[state.activeBenchmark] = false;
    return currentBenchmarkWorkflow().startImport();
  }

  async function startQa() {
    state.userSelectedRunDirs[state.activeBenchmark] = false;
    let gate = null;
    if (typeof currentBenchmarkWorkflow().preflightQa === "function") {
      gate = await currentBenchmarkWorkflow().preflightQa();
      if (!gate?.ok) {
        const failedCheck = (gate?.checks || []).find((item) => item.ok === false);
        throw new Error(failedCheck?.message || "QA 启动检查未通过");
      }
    }
    const result = await currentBenchmarkWorkflow().startQa({ preflightGate: gate });
    return {...result, stage: "qa"};
  }

  async function runJudge(smoke) {
    const run = currentRun();
    if (!run?.output_file) throw new Error("当前没有可评分结果");
    let locomoPreflight = null;
    if (state.activeBenchmark === "locomo") {
      locomoPreflight = await locomo.preflightJudge({ currentRun });
      if (!locomoPreflight?.ok) {
        const failedCheck = (locomoPreflight?.checks || []).find((item) => item.ok === false);
        throw new Error(failedCheck?.message || "Judge 预检查未通过");
      }
    }
    let filterPayload = {};
    let name = "judge";
    if (smoke) {
      const preview = await api(`/api/pending-preview?path=${encodeURIComponent(run.output_file)}&limit=3`);
      const indexes = (preview.rows || []).map((row) => row._row_index).filter((value) => value !== undefined && value !== "");
      if (!indexes.length) throw new Error("当前结果没有待评分样本可抽查");
      filterPayload = {only_pending: true, row_indexes: indexes.join(",")};
      name = `judge validation ${indexes.length} pending`;
    }
    const form = formReaders.readJudgeForm();
    const dataPath = state.activeBenchmark === "locomo"
      ? String(locomoPreflight?.dataPath || run?.dataset_path || form.data || "").trim()
      : form.data;
    const payload = {
      kind: "judge",
      input: run.output_file,
      data: dataPath,
      output_dir: firstValue(state.config?.output_dir, ""),
      name,
      judge_base_url: firstValue(currentAccountConfig().judgeBaseUrl, state.config?.judge_base_url, ""),
      judge_model: firstValue(currentAccountConfig().judgeModel, state.config?.judge_model, ""),
      judge_token: firstValue(currentAccountConfig().judgeToken, state.config?.judge_token, ""),
      ...filterPayload,
    };
    const task = await api("/api/tasks", {method: "POST", body: JSON.stringify(payload)});
    return {
      refresh: true,
      createdTask: task,
      stage: "report",
      followupRefreshMs: 2000,
    };
  }

  async function runJudgePending() {
    const run = currentRun();
    if (!run?.output_file) throw new Error("当前没有可评分结果");
    let locomoPreflight = null;
    if (state.activeBenchmark === "locomo") {
      locomoPreflight = await locomo.preflightJudge({ currentRun });
      if (!locomoPreflight?.ok) {
        const failedCheck = (locomoPreflight?.checks || []).find((item) => item.ok === false);
        throw new Error(failedCheck?.message || "Judge 预检查未通过");
      }
      if (Number(locomoPreflight?.pendingPreview?.total_pending || 0) <= 0) {
        throw new Error("当前结果没有待判分样本");
      }
    }
    const form = formReaders.readJudgeForm();
    const dataPath = state.activeBenchmark === "locomo"
      ? String(locomoPreflight?.dataPath || run?.dataset_path || form.data || "").trim()
      : form.data;
    const payload = {
      kind: "judge",
      input: run.output_file,
      data: dataPath,
      output_dir: firstValue(state.config?.output_dir, ""),
      name: "judge all pending",
      judge_base_url: firstValue(currentAccountConfig().judgeBaseUrl, state.config?.judge_base_url, ""),
      judge_model: firstValue(currentAccountConfig().judgeModel, state.config?.judge_model, ""),
      judge_token: firstValue(currentAccountConfig().judgeToken, state.config?.judge_token, ""),
      only_pending: true,
    };
    const task = await api("/api/tasks", {method: "POST", body: JSON.stringify(payload)});
    return {
      refresh: true,
      createdTask: task,
      stage: "report",
      followupRefreshMs: 2000,
    };
  }

  async function preflightLocomoQa() {
    if (state.activeBenchmark !== "locomo") return null;
    return locomo.preflightQa();
  }

  async function preflightLocomoJudge() {
    if (state.activeBenchmark !== "locomo") return null;
    return locomo.preflightJudge({ currentRun });
  }

  async function preflightQaGate() {
    const workflow = currentBenchmarkWorkflow();
    if (typeof workflow.preflightQa !== "function") return null;
    return workflow.preflightQa();
  }

  async function preflightJudgeStage() {
    const workflow = currentBenchmarkWorkflow();
    if (typeof workflow.preflightJudge !== "function") return null;
    return workflow.preflightJudge();
  }

  async function startLocomoSelectedQa() {
    if (state.activeBenchmark !== "locomo") return null;
    state.userSelectedRunDirs[state.activeBenchmark] = false;
    return locomo.startSelectedQa();
  }

  async function startOfficialSelectedQa() {
    if (!["hotpotqa", "longmemeval"].includes(String(state.activeBenchmark || "").trim())) return null;
    state.userSelectedRunDirs[state.activeBenchmark] = false;
    return currentBenchmarkWorkflow().startSelectedQa();
  }

  async function startLocomoWrongCsvQa() {
    if (state.activeBenchmark !== "locomo") return null;
    state.userSelectedRunDirs[state.activeBenchmark] = false;
    return locomo.startWrongCsvQa();
  }

  async function startHotpotWrongCsvQa() {
    if (state.activeBenchmark !== "hotpotqa") return null;
    state.userSelectedRunDirs[state.activeBenchmark] = false;
    return hotpotqa.startWrongCsvQa();
  }

  async function startLongMemEvalWrongCsvQa() {
    if (state.activeBenchmark !== "longmemeval") return null;
    state.userSelectedRunDirs[state.activeBenchmark] = false;
    return longmemeval.startWrongCsvQa();
  }

  async function retryLocomoFailedQa() {
    if (state.activeBenchmark !== "locomo") return null;
    state.userSelectedRunDirs[state.activeBenchmark] = false;
    return locomo.retryFailedQa();
  }

  async function retryHotpotFailedQa() {
    if (state.activeBenchmark !== "hotpotqa") return null;
    state.userSelectedRunDirs[state.activeBenchmark] = false;
    return hotpotqa.retryFailedQa();
  }

  async function retryLongMemEvalFailedQa() {
    if (state.activeBenchmark !== "longmemeval") return null;
    state.userSelectedRunDirs[state.activeBenchmark] = false;
    return longmemeval.retryFailedQa();
  }

  async function retryLocomoMissingQa() {
    if (state.activeBenchmark !== "locomo") return null;
    state.userSelectedRunDirs[state.activeBenchmark] = false;
    return locomo.retryMissingQa();
  }

  async function retryHotpotMissingQa() {
    if (state.activeBenchmark !== "hotpotqa") return null;
    state.userSelectedRunDirs[state.activeBenchmark] = false;
    return hotpotqa.retryMissingQa();
  }

  async function retryLongMemEvalMissingQa() {
    if (state.activeBenchmark !== "longmemeval") return null;
    state.userSelectedRunDirs[state.activeBenchmark] = false;
    return longmemeval.retryMissingQa();
  }

  async function refreshLocomoDiagnostics({ sample = "", datasetPath = "", force = false } = {}) {
    if (state.activeBenchmark !== "locomo") return null;
    const run = currentRun();
    if (!run?.output_file) {
      state.locomoRecallPreview = null;
      state.locomoRecallDetail = null;
      state.locomoRecallSelection = { path: "", questionId: "", index: "" };
      return null;
    }
    const detail = state.runDetails?.[run.run_dir] || null;
    const snapshot = state.runConfigSnapshots?.[run.run_dir] || null;
    const runConfig = snapshot?.config || snapshot || null;
    const diagnostics = await loadQaDiagnostics({
      path: run.output_file,
      datasetPath: datasetPath || detail?.record?.dataset_path || runConfig?.data || run?.dataset_path || "",
      sample: sample || detail?.record?.sample || runConfig?.sample || "all",
    });
    const traceRows = Array.isArray(diagnostics?.retrieval_trace_preview) ? diagnostics.retrieval_trace_preview : [];
    const csvPreview = await loadCsvPreview({ path: run.output_file, limit: 2000, force }).catch(() => null);
    const previewRows = Array.isArray(csvPreview?.rows) ? csvPreview.rows : [];
    const mergedRows = mergeRecallRows(traceRows, previewRows);
    const recallPreview = mergedRows.length
      ? { path: run.output_file, fieldnames: csvPreview?.fieldnames || [], rows: mergedRows }
      : null;
    state.locomoRecallPreview = recallPreview || null;
    const rows = Array.isArray(recallPreview?.rows) ? recallPreview.rows : [];
    const currentSelection = state.locomoRecallSelection || {};
    let questionId = currentSelection.path === run.output_file ? String(currentSelection.questionId || "").trim() : "";
    let index = currentSelection.path === run.output_file ? String(currentSelection.index ?? "").trim() : "";
    if (!questionId && index === "" && rows.length) {
      questionId = String(rows[0]?.question_id || "").trim();
      index = String(rows[0]?._row_index ?? "");
    }
    if (questionId || index !== "") {
      await loadQuestionDetail({
        path: run.output_file,
        questionId,
        index,
        force,
      }).catch(() => {
        state.locomoRecallDetail = null;
      });
    } else {
      state.locomoRecallDetail = null;
      state.locomoRecallSelection = { path: run.output_file, questionId: "", index: "" };
    }
    return { refresh: false };
  }

  async function refreshLocomoRecallDetail({ force = false } = {}) {
    if (state.activeBenchmark !== "locomo") return null;
    const run = currentRun();
    if (!run?.output_file) {
      state.locomoRecallDetail = null;
      return null;
    }
    const diagnostics = state.qaDiagnosticsCache?.[run.output_file] || null;
    const rows = Array.isArray(state.locomoRecallPreview?.rows) ? state.locomoRecallPreview.rows : [];
    const currentSelection = state.locomoRecallSelection || {};
    let questionId = currentSelection.path === run.output_file ? String(currentSelection.questionId || "").trim() : "";
    let index = currentSelection.path === run.output_file ? String(currentSelection.index ?? "").trim() : "";
    if (!questionId && index === "" && rows.length) {
      questionId = String(rows[0]?.question_id || "").trim();
      index = String(rows[0]?._row_index ?? "");
    }
    if (!questionId && index === "") {
      state.locomoRecallDetail = null;
      state.locomoRecallSelection = { path: run.output_file, questionId: "", index: "" };
      return null;
    }
    await loadQuestionDetail({
      path: run.output_file,
      questionId,
      index,
      force,
    });
    return { refresh: false };
  }

  async function refreshLocomoCurrentResult() {
    if (state.activeBenchmark !== "locomo") return null;
    const run = currentRun();
    if (!run?.run_dir) throw new Error("当前没有可刷新的 LoCoMo 结果");
    let loaded = await ensureRunDetail(run, { force: true });
    let detail = loaded?.detail || state.runDetails?.[run.run_dir] || null;
    let result = loaded?.result || state.resultSummaries?.[run.output_file] || null;
    const completion = await maybeCompleteLocomoQaRun(run, { detail, result });
    if (completion?.status === "judge_started" || completion?.status === "report_exported") {
      loaded = await ensureRunDetail(run, { force: true });
    }
    detail = loaded?.detail || state.runDetails?.[run.run_dir] || null;
    const snapshot = state.runConfigSnapshots?.[run.run_dir] || null;
    const runConfig = snapshot?.config || snapshot || null;
    await refreshLocomoDiagnostics({
      sample: detail?.record?.sample || runConfig?.sample || "all",
      datasetPath: detail?.record?.dataset_path || runConfig?.data || run?.dataset_path || "",
      force: true,
    });
    await refreshLocomoPendingPreview();
    if (state.activeStage === "judge") {
      await preflightLocomoJudge();
    }
    return { refresh: false };
  }

  async function refreshLocomoPendingPreview() {
    if (state.activeBenchmark !== "locomo") return null;
    const run = currentRun();
    if (!run?.output_file) {
      state.locomoPendingPreview = null;
      return null;
    }
    await loadPendingPreview({
      path: run.output_file,
      filters: state.locomoPendingFilters || {},
      limit: 3,
    });
    return { refresh: false };
  }

  async function exportLocomoPendingCsv() {
    if (state.activeBenchmark !== "locomo") return null;
    const run = currentRun();
    if (!run?.output_file) throw new Error("当前没有结果文件可导出");
    return exportPendingCsv({
      path: run.output_file,
      filters: state.locomoPendingFilters || {},
    });
  }

  async function runPrimary() {
    if (state.activeStage === "import") {
      return startImport();
    }
    if (state.activeStage === "qa") {
      return startQa();
    }
    if (state.activeStage === "judge") {
      return currentBenchmarkWorkflow().runJudge();
    }
    return {
      kind: "report-export",
      model: await report.exportReport(),
    };
  }

  return {
    ensureRunDetail,
    exportLocomoPendingCsv,
    exportReport: report.exportReport,
    loadQaDiagnostics,
    openPath: system.openPath,
    preflightJudgeStage,
    preflightQaGate,
    preflightLocomoJudge,
    preflightLocomoQa,
    refreshLocomoCurrentResult,
    refreshLocomoDiagnostics,
    refreshLocomoRecallDetail,
    refreshLocomoPendingPreview,
    retryHotpotFailedQa,
    retryHotpotMissingQa,
    retryLongMemEvalFailedQa,
    retryLongMemEvalMissingQa,
    retryLocomoFailedQa,
    retryLocomoMissingQa,
    runJudge,
    runJudgePending,
    runPrimary,
    saveLocomoQaConfig: system.saveLocomoQaConfig,
    startHotpotWrongCsvQa,
    startLongMemEvalWrongCsvQa,
    startImport,
    startLocomoSelectedQa,
    startOfficialSelectedQa,
    startLocomoWrongCsvQa,
    startQa,
    stopAllTasks: system.stopAllTasks,
    validatePayload,
  };
}

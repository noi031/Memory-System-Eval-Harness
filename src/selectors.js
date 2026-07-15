import { benchmarkDefaultVisibleRunOptions, benchmarkHasOfficialEval, benchmarkPrefetchLimit, getBenchmark, matchesRunForBenchmark, matchesTaskForBenchmark } from "./benchmark-registry.js";
import { isImportOnlySummary, officialSummaryReadyForMetrics } from "./run-metrics.js";
import { isActiveStatus } from "./run-status.js";

export function createSelectors({ state, BENCHMARKS, $, queryAll, firstValue }) {
  function isSuccessfulStatus(status) {
    return ["ok", "ready", "success", "succeeded", "completed", "done"].includes(String(status || "").trim().toLowerCase());
  }

  function isLocomoQaKind(kind) {
    return [
      "echomemory_qa",
      "openviking_qa",
      "echomemory_qa_retry_failed",
      "echomemory_qa_retry_missing",
      "openviking_qa_retry_failed",
      "openviking_qa_retry_missing",
    ].includes(String(kind || "").trim().toLowerCase());
  }

  function selectedAccount() {
    return String(state.selectedAccount || "").trim();
  }

  function runAccount(run) {
    const detail = run?.run_dir ? state.runDetails?.[run.run_dir] || null : null;
    return String(run?.account || detail?.record?.account || "").trim();
  }

  function runMatchesSelectedAccount(run) {
    const selected = selectedAccount();
    const account = runAccount(run);
    return !selected || !account || account === selected;
  }

  function exactAccountRuns(runs = []) {
    const selected = selectedAccount();
    if (!selected) return [];
    return (runs || []).filter((run) => runAccount(run) === selected);
  }

  function unscopedAccountRuns(runs = []) {
    return (runs || []).filter((run) => !runAccount(run));
  }

  function accountScopedRuns(runs = []) {
    const selected = selectedAccount();
    if (!selected) return runs || [];
    const exactRuns = exactAccountRuns(runs);
    if (exactRuns.length) return exactRuns;
    return unscopedAccountRuns(runs);
  }

  function isImportOnlyRunForBenchmark(benchmarkId, run) {
    if (!run) return false;
    const detail = state.runDetails?.[run.run_dir] || null;
    const summaryJson = detail?.record?.summary?.summary_json || run.summary?.summary_json || {};
    return isImportOnlySummary(benchmarkId, { summary_json: summaryJson }, run.name);
  }

  function isOfficialEvalUsableRun(benchmarkId, run) {
    if (!run || isImportOnlyRunForBenchmark(benchmarkId, run)) return false;
    if (isActiveStatus(run.status)) return true;
    if (String(run.output_file || "").trim()) return true;
    const detail = state.runDetails?.[run.run_dir] || null;
    return Boolean(String(detail?.record?.output_file || "").trim());
  }

  function hasOfficialEvalReadyMetrics(benchmarkId, run) {
    if (!run) return false;
    const detail = state.runDetails?.[run.run_dir] || null;
    const result = run?.output_file ? state.resultSummaries?.[run.output_file] || null : null;
    const summary = result?.summary || detail?.record?.summary || run.summary || {};
    const summaryJson = summary?.summary_json || {};
    return officialSummaryReadyForMetrics(benchmarkId, {
      official: summaryJson?.official_eval?.summary || null,
      answerF1: summary?.official_answer_f1 ?? null,
      jointF1: summary?.official_joint_f1 ?? null,
      supportF1: summary?.official_supporting_facts_f1 ?? null,
      officialOverallAccuracy: summary?.official_overall_accuracy ?? null,
      officialTaskAveragedAccuracy: summary?.official_task_averaged_accuracy ?? null,
    });
  }

  function preferredRunForBenchmark(benchmarkId, runs) {
    const benchmark = getBenchmark(BENCHMARKS, benchmarkId);
    const accountBound = Boolean(selectedAccount());
    const exactRuns = exactAccountRuns(runs);
    const scopedRuns = accountScopedRuns(runs);
    const scopedCandidates = accountBound ? scopedRuns : (scopedRuns.length ? scopedRuns : runs);
    if (!benchmarkHasOfficialEval(benchmark)) {
      if (String(benchmarkId || "").trim().toLowerCase() === "locomo") {
        return scopedCandidates.find((run) => isLocomoQaKind(run?.kind) && isSuccessfulStatus(run?.status))
          || scopedCandidates.find((run) => isLocomoQaKind(run?.kind) && isActiveStatus(run?.status))
          || scopedCandidates.find((run) => isLocomoQaKind(run?.kind))
          || null;
      }
      return scopedCandidates[0] || null;
    }
    const officialCandidates = accountBound && exactRuns.length
      ? exactRuns
      : (exactRuns.length ? exactRuns : runs);
    return officialCandidates.find((run) => hasOfficialEvalReadyMetrics(benchmarkId, run))
      || scopedCandidates.find((run) => isActiveStatus(run.status))
      || scopedCandidates.find((run) => isOfficialEvalUsableRun(benchmarkId, run))
      || scopedCandidates.find((run) => !isImportOnlyRunForBenchmark(benchmarkId, run))
      || officialCandidates[0]
      || scopedCandidates[0]
      || null;
  }

  function visibleRunsForBenchmark(benchmarkId, options = {}) {
    const {
      limit = null,
      includeImportOnly = false,
      includeIncomplete = false,
    } = options;
    const benchmark = getBenchmark(BENCHMARKS, benchmarkId);
    const allRuns = runsForBenchmark(benchmarkId);
    const accountBound = Boolean(selectedAccount());
    const exactRuns = exactAccountRuns(allRuns);
    const runs = accountBound
      ? (benchmarkHasOfficialEval(benchmark) && !exactRuns.length ? allRuns : accountScopedRuns(allRuns))
      : allRuns;
    if (!benchmarkHasOfficialEval(benchmark)) {
      const nonOfficialRuns = String(benchmarkId || "").trim().toLowerCase() === "locomo"
        ? runs.filter((run) => isLocomoQaKind(run?.kind))
        : runs;
      return limit ? nonOfficialRuns.slice(0, Math.max(1, limit)) : nonOfficialRuns;
    }
    const selectedRunDir = state.currentRunDirs[benchmarkId] || "";
    const selectedRun = runs.find((run) => run.run_dir === selectedRunDir) || null;
    const preferredRun = preferredRunForBenchmark(benchmarkId, runs);
    const ordered = [];
    const seen = new Set();
    function pushRun(run) {
      if (!run?.run_dir || seen.has(run.run_dir)) return;
      seen.add(run.run_dir);
      ordered.push(run);
    }
    pushRun(selectedRun);
    pushRun(preferredRun);
    runs.forEach((run) => {
      if (hasOfficialEvalReadyMetrics(benchmarkId, run)) {
        pushRun(run);
      }
    });
    runs.forEach((run) => {
      if (isOfficialEvalUsableRun(benchmarkId, run)) {
        pushRun(run);
      }
    });
    if (includeImportOnly) {
      runs.forEach((run) => {
        if (isImportOnlyRunForBenchmark(benchmarkId, run)) {
          pushRun(run);
        }
      });
    }
    if (includeIncomplete) {
      runs.forEach((run) => {
        pushRun(run);
      });
    }
    return limit ? ordered.slice(0, Math.max(1, limit)) : ordered;
  }

  function currentBenchmark() {
    return getBenchmark(BENCHMARKS, state.activeBenchmark);
  }

  function backendId() {
    return String(
      state.accountDetails?.config?.memoryBackend
      || state.config?.memoryBackend
      || "echomemory"
    ).trim() || "echomemory";
  }

  function backendName() {
    const descriptor = (state.backends || []).find((item) => item.id === backendId());
    return descriptor?.name || (backendId() === "openviking" ? "OpenViking" : "EchoMemory");
  }

  function currentAccountConfig() {
    return state.accountDetails?.config || state.config?.active_account_config || {};
  }

  function currentWorkspace() {
    const activeBenchmarkId = String(state.activeBenchmark || "").trim().toLowerCase();
    const draftWorkspace = activeBenchmarkId === "locomo"
      ? String(state.locomoQaDraft?.wbWorkspace || "").trim()
      : String(state.officialQaDrafts?.[activeBenchmarkId]?.wbWorkspace || "").trim();
    return firstValue(
      draftWorkspace,
      currentAccountConfig().workspace,
      currentAccountConfig().ovWorkspace,
      currentAccountConfig().memoryWorkspace,
      state.config?.workspace,
      state.config?.openviking_workspace
    );
  }

  function datasetRecords(format) {
    return (state.datasets || []).filter((item) => String(item.format || item.dataset_format || "").toLowerCase() === format);
  }

  function currentDatasetRecord() {
    const scopedInputs = queryAll(`.wb-stage.active #wbDataPath`);
    const input = scopedInputs.length === 1 ? scopedInputs[0] : $("wbDataPath");
    const path = input ? input.value.trim() : "";
    return datasetRecords(currentBenchmark().datasetFormat).find((item) => String(item.path || "") === path) || null;
  }

  function runsForBenchmark(benchmarkId) {
    const benchmark = getBenchmark(BENCHMARKS, benchmarkId);
    return (state.runs || []).filter((run) => matchesRunForBenchmark(benchmark, run));
  }

  function tasksForBenchmark(benchmarkId) {
    const benchmark = getBenchmark(BENCHMARKS, benchmarkId);
    return (state.tasks || []).filter((task) => matchesTaskForBenchmark(benchmark, task));
  }

  function currentRun() {
    const benchmarkId = state.activeBenchmark;
    const runs = runsForBenchmark(benchmarkId);
    const benchmark = getBenchmark(BENCHMARKS, benchmarkId);
    const stored = runs.find((run) => run.run_dir === state.currentRunDirs[benchmarkId]) || null;
    const accountBound = Boolean(selectedAccount());
    const storedMatchesAccount = !stored || runMatchesSelectedAccount(stored);
    const preferred = preferredRunForBenchmark(benchmarkId, runs);
    if (!benchmarkHasOfficialEval(benchmark)) {
      const locomo = String(benchmarkId || "").trim().toLowerCase() === "locomo";
      if (
        stored
        && state.userSelectedRunDirs[benchmarkId]
        && (!accountBound || storedMatchesAccount)
        && (!locomo || isLocomoQaKind(stored?.kind))
      ) {
        return stored;
      }
      if (locomo) {
        return preferred || (stored && isLocomoQaKind(stored?.kind) ? stored : null);
      }
      return preferred || stored;
    }
    if (stored && (!accountBound || storedMatchesAccount || state.userSelectedRunDirs[benchmarkId]) && !isImportOnlyRunForBenchmark(benchmarkId, stored)) {
      if (state.userSelectedRunDirs[benchmarkId]) return stored;
      if (
        preferred?.run_dir
        && preferred.run_dir !== stored.run_dir
        && hasOfficialEvalReadyMetrics(benchmarkId, preferred)
        && !hasOfficialEvalReadyMetrics(benchmarkId, stored)
      ) {
        return preferred;
      }
      if (isOfficialEvalUsableRun(benchmarkId, stored)) return stored;
    }
    return preferred;
  }

  function qaKind() {
    return backendId() === "openviking" ? "openviking_qa" : "echomemory_qa";
  }

  function genericQaKind() {
    return backendId() === "openviking" ? "openviking_generic_qa" : "echomemory_generic_qa";
  }

  function hasRunningTasks() {
    return (state.tasks || []).some((task) => isActiveStatus(task.status));
  }

  function prefetchLimitForBenchmark(benchmarkId) {
    return benchmarkPrefetchLimit(getBenchmark(BENCHMARKS, benchmarkId));
  }

  return {
    backendId,
    backendName,
    currentAccountConfig,
    currentBenchmark,
    currentDatasetRecord,
    currentRun,
    currentWorkspace,
    datasetRecords,
    genericQaKind,
    hasRunningTasks,
    prefetchLimitForBenchmark,
    preferredRunForBenchmark,
    qaKind,
    runsForBenchmark,
    tasksForBenchmark,
    visibleRunsForBenchmark,
  };
}

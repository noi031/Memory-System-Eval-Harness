import { benchmarkHasOfficialEval, benchmarkSupportsQuestionPreview, getBenchmark } from "../benchmark-registry.js";
import { officialSummaryReadyForMetrics, isImportOnlySummary } from "../run-metrics.js";
import { isActiveStatus } from "../run-status.js";
import { BENCHMARKS } from "../config.js";

const LOCOMO_SAMPLE_DRAFT_KEY = "wbQa" + "Sample";

export function createRuntimeActions(deps) {
  const {
    api,
    currentBenchmark,
    currentRun,
    firstValue,
    formReaders,
    onBootstrapState,
    prefetchLimitForBenchmark,
    preferredRunForBenchmark,
    runsForBenchmark,
    state,
  } = deps;

  function normalizeAccount(value, fallback = "default") {
    return String(value || "").trim() || fallback;
  }

  function isLocomoRun(run) {
    return String(run?.dataset_format || "").trim().toLowerCase() === "locomo";
  }

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

  function isLocomoImportKind(kind) {
    return /import/i.test(String(kind || ""));
  }

  function hasUsableLocomoRun(account, runs = []) {
    const normalizedAccount = normalizeAccount(account, "");
    if (!normalizedAccount) return false;
    return (runs || []).some((run) => {
      if (!isLocomoRun(run)) return false;
      if (normalizeAccount(run?.account, "") !== normalizedAccount) return false;
      const kind = String(run?.kind || "").trim().toLowerCase();
      if (isLocomoQaKind(kind)) return isSuccessfulStatus(run?.status);
      if (isLocomoImportKind(kind)) return isSuccessfulStatus(run?.status);
      return false;
    });
  }

  function preferredLocomoAccountFromRuns(runs = []) {
    const locomoRuns = (runs || []).filter((run) => isLocomoRun(run) && normalizeAccount(run?.account, ""));
    const latestSuccessfulQa = locomoRuns.find((run) => isLocomoQaKind(run?.kind) && isSuccessfulStatus(run?.status));
    if (latestSuccessfulQa?.account) return normalizeAccount(latestSuccessfulQa.account, "");
    const latestSuccessfulImport = locomoRuns.find((run) => isLocomoImportKind(run?.kind) && isSuccessfulStatus(run?.status));
    if (latestSuccessfulImport?.account) return normalizeAccount(latestSuccessfulImport.account, "");
    const latestAnyImport = locomoRuns.find((run) => isLocomoImportKind(run?.kind));
    return normalizeAccount(latestAnyImport?.account, "");
  }

  function questionDetailCacheKey(path, questionId = "", index = "") {
    return [String(path || "").trim(), String(questionId || "").trim(), String(index ?? "").trim()].join("::");
  }

  async function validatePayload(payload) {
    const cacheKey = JSON.stringify(payload || {});
    const result = await api("/api/validate", {method: "POST", body: JSON.stringify(payload || {})});
    state.validationCache[cacheKey] = result || null;
    return result;
  }

  function qaDiagnosticsCacheKey(path, datasetPath = "", sample = "all") {
    return [String(path || "").trim(), String(datasetPath || "").trim(), String(sample || "all").trim() || "all"].join("::");
  }

  function csvPreviewCacheKey(path, limit = 20) {
    return [String(path || "").trim(), String(limit || 20)].join("::");
  }

  async function loadRunConfigSnapshot(run, options = {}) {
    const runDir = String(run?.run_dir || "").trim();
    if (!runDir) return null;
    state.runConfigSnapshots = state.runConfigSnapshots || {};
    const force = options.force === true;
    if (!force && Object.prototype.hasOwnProperty.call(state.runConfigSnapshots, runDir)) {
      return state.runConfigSnapshots[runDir];
    }
    const result = await api(`/api/config-snapshot?run_dir=${encodeURIComponent(runDir)}`).catch(() => null);
    state.runConfigSnapshots[runDir] = result?.config || null;
    return state.runConfigSnapshots[runDir];
  }

  async function loadQaDiagnostics({ path, datasetPath = "", sample = "all" }) {
    const safePath = String(path || "").trim();
    const safeDatasetPath = String(datasetPath || "").trim();
    const safeSample = String(sample || "all").trim() || "all";
    const qs = new URLSearchParams({ path: String(path || "").trim() });
    if (datasetPath) qs.set("dataset", String(datasetPath || "").trim());
    if (sample) qs.set("sample", String(sample || "").trim() || "all");
    const result = await api(`/api/qa-diagnostics?${qs.toString()}`);
    state.qaDiagnosticsCache[safePath] = result || null;
    state.qaDiagnosticsCache[qaDiagnosticsCacheKey(safePath, safeDatasetPath, safeSample)] = result || null;
    return result;
  }

  async function loadCsvPreview({ path, limit = 20, force = false }) {
    const safePath = String(path || "").trim();
    const safeLimit = Math.max(1, Number(limit || 20));
    if (!safePath) {
      state.locomoRecallPreview = null;
      return null;
    }
    const cacheKey = csvPreviewCacheKey(safePath, safeLimit);
    if (!force && Object.prototype.hasOwnProperty.call(state.csvPreviewCache || {}, cacheKey)) {
      const cached = state.csvPreviewCache[cacheKey] || null;
      state.locomoRecallPreview = cached;
      return cached;
    }
    const qs = new URLSearchParams({
      path: safePath,
      limit: String(safeLimit),
    });
    const result = await api(`/api/csv-preview?${qs.toString()}`);
    state.csvPreviewCache[cacheKey] = result || null;
    state.locomoRecallPreview = result || null;
    return result;
  }

  async function loadQuestionDetail({ path, questionId = "", index = "", force = false }) {
    const safePath = String(path || "").trim();
    const safeQuestionId = String(questionId || "").trim();
    const safeIndex = String(index ?? "").trim();
    if (!safePath || (!safeQuestionId && safeIndex === "")) {
      state.locomoRecallDetail = null;
      return null;
    }
    const cacheKey = questionDetailCacheKey(safePath, safeQuestionId, safeIndex);
    if (!force && state.questionDetailCache?.[cacheKey]) {
      state.locomoRecallDetail = state.questionDetailCache[cacheKey];
      state.locomoRecallSelection = {
        path: safePath,
        questionId: safeQuestionId,
        index: safeIndex,
      };
      return state.questionDetailCache[cacheKey];
    }
    const qs = new URLSearchParams({ path: safePath });
    if (safeQuestionId) qs.set("question_id", safeQuestionId);
    if (safeIndex !== "") qs.set("index", safeIndex);
    const result = await api(`/api/question-detail?${qs.toString()}`);
    state.questionDetailCache[cacheKey] = result || null;
    state.locomoRecallDetail = result || null;
    state.locomoRecallSelection = {
      path: safePath,
      questionId: safeQuestionId || String(result?.row?.question_id || "").trim(),
      index: safeIndex !== "" ? safeIndex : String(result?.index ?? ""),
    };
    return result;
  }

  async function loadPendingPreview({ path, filters = {}, limit = 3 }) {
    const qs = new URLSearchParams({
      path: String(path || "").trim(),
      limit: String(limit || 3),
    });
    if (filters.category) qs.set("category", String(filters.category).trim());
    if (filters.query) qs.set("q", String(filters.query).trim());
    if (filters.min_tokens !== "" && filters.min_tokens !== null && filters.min_tokens !== undefined) qs.set("min_tokens", String(filters.min_tokens).trim());
    if (filters.max_tokens !== "" && filters.max_tokens !== null && filters.max_tokens !== undefined) qs.set("max_tokens", String(filters.max_tokens).trim());
    const result = await api(`/api/pending-preview?${qs.toString()}`);
    state.locomoPendingPreview = {
      ...(result || {}),
      path: String(path || "").trim(),
      filters: {
        category: String(filters.category || ""),
        query: String(filters.query || ""),
        min_tokens: String(filters.min_tokens ?? ""),
        max_tokens: String(filters.max_tokens ?? ""),
      },
      limit,
    };
    return state.locomoPendingPreview;
  }

  async function exportPendingCsv({ path, filters = {} }) {
    const qs = new URLSearchParams({ path: String(path || "").trim() });
    if (filters.category) qs.set("category", String(filters.category).trim());
    if (filters.query) qs.set("q", String(filters.query).trim());
    if (filters.min_tokens !== "" && filters.min_tokens !== null && filters.min_tokens !== undefined) qs.set("min_tokens", String(filters.min_tokens).trim());
    if (filters.max_tokens !== "" && filters.max_tokens !== null && filters.max_tokens !== undefined) qs.set("max_tokens", String(filters.max_tokens).trim());
    return api(`/api/export-pending-csv?${qs.toString()}`);
  }

  function needsOfficialEvalFinalRefresh(run, cachedResult) {
    const benchmarkId = String(run?.dataset_format || "").toLowerCase();
    if (!run || !benchmarkHasOfficialEval(getBenchmark(BENCHMARKS, benchmarkId))) return false;
    if (isActiveStatus(run.status)) return true;
    const summary = cachedResult?.summary || null;
    if (!summary) return true;
    if (isImportOnlySummary(benchmarkId, summary, run.name)) return false;
    const summaryJson = summary.summary_json;
    if (summaryJson === null || summaryJson === undefined) return true;
    const hasOfficialEval = Boolean(summaryJson?.official_eval?.summary);
    return !(hasOfficialEval || officialSummaryReadyForMetrics(benchmarkId, {
      official: summaryJson?.official_eval?.summary || null,
      answerF1: summary?.official_answer_f1 ?? null,
      answerEm: summary?.official_answer_em ?? null,
      officialOverallAccuracy: summary?.official_overall_accuracy ?? null,
      officialTaskAveragedAccuracy: summary?.official_task_averaged_accuracy ?? null,
    }));
  }

  function needsLocomoJudgeFinalRefresh(run, cachedDetail, cachedResult) {
    const kind = String(run?.kind || "").trim().toLowerCase();
    if (![
      "echomemory_qa",
      "openviking_qa",
      "echomemory_qa_retry_failed",
      "echomemory_qa_retry_missing",
      "openviking_qa_retry_failed",
      "openviking_qa_retry_missing",
    ].includes(kind)) {
      return false;
    }
    const status = String(run?.status || "").trim().toLowerCase();
    const refreshEligibleStatuses = [
      "ok",
      "ready",
      "success",
      "succeeded",
      "completed",
      "done",
      "interrupted",
      "stopped",
      "cancelled",
      "canceled",
    ];
    if (!refreshEligibleStatuses.includes(status)) {
      return false;
    }
    const summary = cachedResult?.summary || cachedDetail?.record?.summary || null;
    if (!summary) return true;
    const rows = Number(summary?.rows || 0);
    const graded = Number(summary?.graded || 0);
    const pending = Number(summary?.result_counts?.UNSCORED ?? Math.max(0, rows - graded));
    return rows <= 0 || pending > 0;
  }

  async function ensureQuestions() {
    if (!benchmarkSupportsQuestionPreview(currentBenchmark())) {
      state.questions = [];
      state.questionScope = "";
      return;
    }
    const benchmark = currentBenchmark();
    const {path, sample} = formReaders.readQuestionPreviewScope({
      path: state.questionDataPaths?.[benchmark.id] || "",
      sample: state.questionSamples?.[benchmark.id] || "all",
    });
    const scopeKey = `${path}::${sample}`;
    if (state.questions.length && state.questionScope === scopeKey) return;
    const data = await api(`/api/questions?path=${encodeURIComponent(path)}&sample=${encodeURIComponent(sample)}`).catch(() => ({questions: []}));
    state.questions = Array.isArray(data.questions) ? data.questions : [];
    state.questionScope = scopeKey;
    if (benchmark.id === "locomo" && path) {
      state.questionSampleOptions = state.questionSampleOptions || {};
      if (!Array.isArray(state.questionSampleOptions[path]) || !state.questionSampleOptions[path].length) {
        const allData = await api(`/api/questions?path=${encodeURIComponent(path)}&sample=all`).catch(() => ({questions: []}));
        const allQuestions = Array.isArray(allData.questions) ? allData.questions : [];
        state.questionSampleOptions[path] = Array.from(new Set(
          allQuestions.map((item) => String(item.sample_id || "").trim()).filter(Boolean)
        )).sort((a, b) => a.localeCompare(b));
      }
    }
  }

  async function ensureRunDetail(run, options = {}) {
    if (!run) return {detail: null, result: null};
    const force = options.force === true;
    const cachedDetail = state.runDetails[run.run_dir] || null;
    const cachedResult = run.output_file ? state.resultSummaries[run.output_file] || null : null;
    const shouldRefresh = force
      || isActiveStatus(run.status)
      || needsOfficialEvalFinalRefresh(run, cachedResult)
      || needsLocomoJudgeFinalRefresh(run, cachedDetail, cachedResult);
    if (!cachedDetail || shouldRefresh) {
      state.runDetails[run.run_dir] = await api(`/api/run-detail?run_dir=${encodeURIComponent(run.run_dir)}`).catch(() => null);
    }
    if (run.output_file && (!cachedResult || shouldRefresh)) {
      state.resultSummaries[run.output_file] = await api(`/api/results?path=${encodeURIComponent(run.output_file)}`).catch(() => null);
    }
    await loadRunConfigSnapshot(run, { force: shouldRefresh }).catch(() => null);
    return {
      detail: state.runDetails[run.run_dir],
      result: state.resultSummaries[run.output_file] || null,
    };
  }

  async function ensureBenchmarkRunDetails(benchmarkId, limit = 2) {
    const benchmark = getBenchmark(BENCHMARKS, benchmarkId);
    const allRuns = runsForBenchmark(benchmarkId);
    if (!allRuns.length) return;
    const ordered = [];
    const seen = new Set();
    const pushRun = (run) => {
      if (!run?.run_dir || seen.has(run.run_dir)) return;
      seen.add(run.run_dir);
      ordered.push(run);
    };
    const selectedRun = allRuns.find((run) => run.run_dir === state.currentRunDirs?.[benchmarkId]) || null;
    const preferredRun = preferredRunForBenchmark(benchmarkId, allRuns);
    pushRun(selectedRun);
    pushRun(preferredRun);
    allRuns.forEach((run) => {
      if (String(run?.account || "").trim()) pushRun(run);
    });
    allRuns.forEach(pushRun);
    const effectiveLimit = benchmarkHasOfficialEval(benchmark)
      ? Math.max(8, Number(limit || 2))
      : Math.max(1, Number(limit || 2));
    await Promise.all(ordered.slice(0, effectiveLimit).map((run) => ensureRunDetail(run).catch(() => null)));
  }

  function resetAccountScopedSelection() {
    Object.keys(state.currentRunDirs || {}).forEach((benchmarkId) => {
      state.currentRunDirs[benchmarkId] = "";
      state.userSelectedRunDirs[benchmarkId] = false;
    });
    state.officialQaDrafts = {};
    state.locomoQaGate = null;
    state.locomoJudgePreflight = null;
    state.officialQaGates = {};
    state.officialJudgePreflights = {};
    state.locomoQaDraft = {};
    state.locomoSelectedQuestions = new Set();
    state.locomoWrongCsv = "";
    state.locomoPendingPreview = null;
    state.locomoRecallDetail = null;
    state.locomoRecallPreview = null;
    state.locomoRecallSelection = { path: "", questionId: "", index: "" };
  }

  function syncPreferredRunDirs() {
    const selectedAccount = normalizeAccount(state.selectedAccount, "");
    Object.keys(state.currentRunDirs).forEach((benchmarkId) => {
      const runsForId = runsForBenchmark(benchmarkId);
      const scopedRunsForId = selectedAccount
        ? runsForId.filter((run) => {
            const account = normalizeAccount(run?.account || state.runDetails?.[run?.run_dir]?.record?.account, "");
            return !account || account === selectedAccount;
          })
        : runsForId;
      const preferred = preferredRunForBenchmark(benchmarkId, runsForId);
      const storedRunExists = scopedRunsForId.some((run) => run.run_dir === state.currentRunDirs[benchmarkId]);
      if (!storedRunExists) {
        state.userSelectedRunDirs[benchmarkId] = false;
      }
      if ((!state.userSelectedRunDirs[benchmarkId] || !storedRunExists) && preferred) {
        state.currentRunDirs[benchmarkId] = preferred.run_dir || "";
      } else if (!preferred && selectedAccount) {
        state.currentRunDirs[benchmarkId] = "";
      }
    });
  }

  function syncLocomoPreferredSample() {
    const locomoRuns = runsForBenchmark("locomo");
    if (!locomoRuns.length) return;
    const preferredRun = preferredRunForBenchmark("locomo", locomoRuns);
    const preferredSample = String(preferredRun?.sample || "").trim();
    if (!preferredSample || preferredSample === "all") return;
    state.questionSamples = state.questionSamples || {};
    const scopedSample = String(state.questionSamples.locomo || "").trim();
    if (!scopedSample || scopedSample === "all") {
      state.questionSamples.locomo = preferredSample;
    }
    if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") {
      state.locomoQaDraft = {};
    }
    const draftSample = String(state.locomoQaDraft[LOCOMO_SAMPLE_DRAFT_KEY] || "").trim();
    if (!draftSample || draftSample === "all") {
      state.locomoQaDraft[LOCOMO_SAMPLE_DRAFT_KEY] = preferredSample;
    }
  }

  async function loadBootstrap(options = {}) {
    const [config, backends, datasets, tasks, runs, accountsState] = await Promise.all([
      api("/api/config"),
      api("/api/backends"),
      api("/api/datasets"),
      api("/api/tasks"),
      api(`/api/runs?include_history=1&limit=60&dataset_format=${currentBenchmark().datasetFormat}`),
      api("/api/accounts").catch(() => ({ active_account: "default", accounts: [] })),
    ]);
    const accountMap = new Map();
    const pushAccount = (item, { real = false } = {}) => {
      const accountId = normalizeAccount(item?.id || item);
      if (!accountId) return;
      const existing = accountMap.get(accountId) || null;
      const nextRecord = (
        item && typeof item === "object"
          ? {...(existing || {}), ...item, id: accountId}
          : {id: accountId}
      );
      nextRecord.isRealAccount = Boolean(existing?.isRealAccount || real || nextRecord.config);
      accountMap.set(accountId, nextRecord);
    };
    (Array.isArray(accountsState?.accounts) ? accountsState.accounts : []).forEach((item) => pushAccount(item, { real: true }));
    (Array.isArray(config?.accounts) ? config.accounts : []).forEach((item) => pushAccount(item, { real: true }));
    const accountList = Array.from(accountMap.values());
    const realAccountIds = new Set(
      accountList
        .filter((item) => item?.isRealAccount)
        .map((item) => normalizeAccount(item?.id, ""))
        .filter(Boolean)
    );
    const selectedAccountId = normalizeAccount(state.selectedAccount, "");
    const detailedAccountId = normalizeAccount(state.accountDetails?.id, "");
    const stickySelectedAccount = (
      selectedAccountId
      && detailedAccountId
      && selectedAccountId === detailedAccountId
      && realAccountIds.has(selectedAccountId)
    )
      ? selectedAccountId
      : "";
    const requestedAccount = normalizeAccount(
      options.account,
      normalizeAccount(
        stickySelectedAccount,
        normalizeAccount(config?.active_account, normalizeAccount(accountsState?.active_account))
      )
    );
    const fallbackAccount = normalizeAccount(accountsState?.active_account, normalizeAccount(config?.active_account));
    const allRuns = runs.runs || [];
    let resolvedAccount = realAccountIds.has(requestedAccount)
      ? requestedAccount
      : (realAccountIds.has(fallbackAccount) ? fallbackAccount : normalizeAccount(accountList[0]?.id, requestedAccount));
    const shouldPreferLocomoAccount = !options.account && !stickySelectedAccount && state.activeBenchmark === "locomo";
    if (shouldPreferLocomoAccount && !hasUsableLocomoRun(resolvedAccount, allRuns)) {
      const preferredLocomoAccount = preferredLocomoAccountFromRuns(allRuns);
      if (preferredLocomoAccount && realAccountIds.has(preferredLocomoAccount)) {
        resolvedAccount = preferredLocomoAccount;
      }
    }
    state.selectedAccount = resolvedAccount;
    state.readiness = null;
    const readinessRequestToken = `${resolvedAccount}:${Date.now()}:${Math.random().toString(36).slice(2, 8)}`;
    state.readinessRequestToken = readinessRequestToken;
    api("/api/readiness", {method: "POST", body: JSON.stringify({account: resolvedAccount}), timeoutMs: 10000})
      .then((readiness) => {
        if (state.selectedAccount !== resolvedAccount) return;
        if (state.readinessRequestToken !== readinessRequestToken) return;
        state.readiness = readiness || null;
        if (typeof onBootstrapState === "function") {
          onBootstrapState({
            phase: "readiness",
            account: resolvedAccount,
          });
        }
      })
      .catch(() => {
        if (state.selectedAccount !== resolvedAccount) return;
        if (state.readinessRequestToken !== readinessRequestToken) return;
        state.readiness = null;
        if (typeof onBootstrapState === "function") {
          onBootstrapState({
            phase: "readiness",
            account: resolvedAccount,
          });
        }
      });
    const accountDetails = await api(`/api/account-config?account=${encodeURIComponent(resolvedAccount)}`).catch(() => null);
    state.config = config || {};
    state.backends = backends.backends || [];
    state.datasets = datasets.datasets || [];
    state.tasks = tasks.tasks || [];
    state.runs = allRuns;
    state.accounts = accountList;
    state.accountDetails = accountDetails || null;
    state.questionDataPaths = state.questionDataPaths || {};
    state.questionSamples = state.questionSamples || {};
    if (typeof onBootstrapState === "function") {
      onBootstrapState({
        phase: "base",
        account: resolvedAccount,
      });
    }
    for (const item of state.datasets || []) {
      const format = String(item.format || item.dataset_format || "").toLowerCase();
      if (!format || state.questionDataPaths[format]) continue;
      if (item.exists === false) continue;
      if (item.path) state.questionDataPaths[format] = item.path;
    }
    syncLocomoPreferredSample();
    syncPreferredRunDirs();
    await ensureQuestions().catch(() => {});
    if (state.activeBenchmark === "locomo") {
      const locomoWorkspace = normalizeAccount(state.selectedAccount, "")
        ? firstValue(
            state.accountDetails?.config?.workspace,
            state.accountDetails?.config?.ovWorkspace,
            state.accountDetails?.config?.memoryWorkspace,
            state.config?.workspace,
            state.config?.openviking_workspace,
            ""
          )
        : "";
      const locomoDatasetPath = String(state.questionDataPaths?.locomo || BENCHMARKS.locomo.defaultData || "").trim();
      const locomoSample = String(
        state.locomoQaDraft?.[LOCOMO_SAMPLE_DRAFT_KEY]
        || state.questionSamples?.locomo
        || "all"
      ).trim() || "all";
      console.log("[MemoryBenchV2] locomo-flow bootstrap", {
        account: state.selectedAccount,
        dataset: locomoDatasetPath,
        sample: locomoSample,
        workspace: locomoWorkspace,
      });
      state.locomoFlowStatusMeta = { timedOut: false, error: "" };
      state.locomoFlowStatus = await api("/api/locomo-flow-status", {
        method: "POST",
        body: JSON.stringify({
          account: state.selectedAccount,
          dataset: locomoDatasetPath,
          sample: locomoSample,
          fast_ui_probe: true,
          config: {
            memoryBackend: state.accountDetails?.config?.memoryBackend || state.config?.memoryBackend || "echomemory",
            ovWorkspace: locomoWorkspace,
            memoryWorkspace: locomoWorkspace,
            ovHost: firstValue(state.accountDetails?.config?.ovHost, state.config?.ov_host, ""),
            ovPort: firstValue(state.accountDetails?.config?.ovPort, state.config?.ov_port, ""),
            judgeBaseUrl: firstValue(state.accountDetails?.config?.judgeBaseUrl, state.config?.judge_base_url, ""),
            judgeModel: firstValue(state.accountDetails?.config?.judgeModel, state.config?.judge_model, ""),
          },
        }),
        timeoutMs: 5000,
      }).catch((error) => {
        const message = String(error?.message || "").trim();
        state.locomoFlowStatusMeta = {
          timedOut: /aborted|timed out|timeout/i.test(message),
          error: message,
        };
        console.warn("[MemoryBenchV2] locomo-flow bootstrap failed", {
          account: state.selectedAccount,
          dataset: locomoDatasetPath,
          sample: locomoSample,
          workspace: locomoWorkspace,
          message,
        });
        return null;
      });
      console.log("[MemoryBenchV2] locomo-flow bootstrap result", {
        account: state.selectedAccount,
        status: state.locomoFlowStatus?.status || null,
        importedSessions: state.locomoFlowStatus?.artifacts?.imported?.session_count || 0,
        importedSummaries: state.locomoFlowStatus?.artifacts?.imported?.summary_count || 0,
        meta: state.locomoFlowStatusMeta,
      });
      if (typeof onBootstrapState === "function") {
        onBootstrapState({
          phase: "locomo_flow",
          account: resolvedAccount,
        });
      }
    }
    await Promise.all(
      Object.keys(state.currentRunDirs).map((benchmarkId) =>
        ensureBenchmarkRunDetails(benchmarkId, prefetchLimitForBenchmark(benchmarkId)).catch(() => null)
      )
    ).catch(() => {});
    syncPreferredRunDirs();
    const run = currentRun();
    const loaded = await ensureRunDetail(run).catch(() => ({detail: null, result: null}));
    if (state.activeBenchmark === "locomo" && run?.output_file) {
      const detail = loaded?.detail || state.runDetails?.[run.run_dir] || null;
      const snapshot = state.runConfigSnapshots?.[run.run_dir] || null;
      const runConfig = snapshot?.config || snapshot || null;
      const diagnostics = await loadQaDiagnostics({
        path: run.output_file,
        datasetPath: detail?.record?.dataset_path || runConfig?.data || run?.dataset_path || state.questionDataPaths?.locomo || "",
        sample: detail?.record?.sample || runConfig?.sample || "all",
      }).catch(() => {});
      const traceRows = Array.isArray(diagnostics?.retrieval_trace_preview) ? diagnostics.retrieval_trace_preview : [];
      const recallPreview = traceRows.length
        ? { path: run.output_file, fieldnames: [], rows: traceRows }
        : await loadCsvPreview({ path: run.output_file, limit: 12 }).catch(() => null);
      state.locomoRecallPreview = recallPreview || null;
      const recallRows = Array.isArray(recallPreview?.rows) ? recallPreview.rows : [];
      const currentSelection = state.locomoRecallSelection || {};
      const nextSelection = currentSelection.path === run.output_file
        ? currentSelection
        : {
          path: run.output_file,
          questionId: String(recallRows[0]?.question_id || "").trim(),
          index: String(recallRows[0]?._row_index ?? ""),
        };
      if (nextSelection.questionId || nextSelection.index !== "") {
        await loadQuestionDetail({
          path: run.output_file,
          questionId: nextSelection.questionId,
          index: nextSelection.index,
        }).catch(() => {});
      } else {
        state.locomoRecallDetail = null;
        state.locomoRecallSelection = { path: run.output_file, questionId: "", index: "" };
      }
      await loadPendingPreview({
        path: run.output_file,
        filters: state.locomoPendingFilters || {},
        limit: 3,
      }).catch(() => {});
    }
  }

  async function refreshAll(loadBootstrapRunner) {
    await loadBootstrapRunner();
  }

  async function switchAccount(account) {
    const nextAccount = normalizeAccount(account);
    if (nextAccount === state.selectedAccount) return { refresh: true };
    state.selectedAccount = nextAccount;
    state.accountDetails = null;
    state.readiness = null;
    resetAccountScopedSelection();
    return { refresh: true };
  }

  async function pollLog(task) {
    if (!task?.id) return {text: "", active: false};
    try {
      const data = await api(`/api/tasks/${encodeURIComponent(task.id)}/log?offset=0`);
      return {
        text: data.text || "日志为空",
        active: isActiveStatus(task.status),
      };
    } catch (error) {
      return {
        text: error.message || "读取日志失败",
        active: isActiveStatus(task.status),
      };
    }
  }

  return {
    ensureBenchmarkRunDetails,
    ensureQuestions,
    ensureRunDetail,
    exportPendingCsv,
    loadQaDiagnostics,
    loadCsvPreview,
    loadRunConfigSnapshot,
    loadQuestionDetail,
    loadPendingPreview,
    loadBootstrap,
    pollLog,
    refreshAll,
    switchAccount,
    validatePayload,
  };
}

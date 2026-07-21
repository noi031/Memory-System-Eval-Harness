import { applyProviderIdentity, buildEchoMemoryIdentityFields, buildModelEndpointFields, buildWorkspaceTaskContext } from "./payload-common.js";
import { buildLocomoJudgePreflightModel, buildLocomoQaGateModel } from "./locomo-helpers.js";
import { normalizeLocomoAccountConfig, normalizeLocomoQaForm } from "../locomo-qa-defaults.js";
import { resolveWrongCsvQuestionSet } from "./wrong-csv.js";

export function createLocomoActions(deps) {
  const {
    api,
    backendId,
    currentAccountConfig,
    currentRun,
    currentWorkspace,
    ensureRunDetail,
    firstValue,
    formReaders,
    loadQaDiagnostics,
    loadRunConfigSnapshot,
    qaKind,
    tasksForBenchmark,
    state,
    validatePayload,
  } = deps;
  const locomoTasks = () => (typeof tasksForBenchmark === "function" ? tasksForBenchmark("locomo") : (state.tasks || []));
  const validateTaskPayload = async (payload) => (
    typeof validatePayload === "function"
      ? validatePayload(payload)
      : { ok: true, checks: [] }
  );

  async function probeModel(payload, role = "agent") {
    try {
      return await api("/api/model-preflight", {
        method: "POST",
        body: JSON.stringify({
          ...payload,
          role,
        }),
      });
    } catch (error) {
      return {
        ok: false,
        status: "request_failed",
        role,
        error: error.message || "model preflight failed",
      };
    }
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

  function qaModelProbePayload(payload) {
    const cfg = currentAccountConfig();
    return {
      ...payload,
      answer_base_url: payload.answer_base_url || firstValue(cfg.answerBaseUrl, state.config?.answer_base_url, ""),
      answer_model: payload.answer_model || firstValue(cfg.answerModel, state.config?.answer_model, ""),
      answer_token: firstValue(cfg.answerToken, cfg.judgeToken, state.config?.answer_token, state.config?.judge_token, ""),
      judge_token: firstValue(cfg.judgeToken, cfg.answerToken, state.config?.judge_token, state.config?.answer_token, ""),
    };
  }

  function judgeModelProbePayload() {
    const cfg = currentAccountConfig();
    return {
      kind: "judge",
      judge_base_url: firstValue(cfg.judgeBaseUrl, state.config?.judge_base_url, ""),
      judge_model: firstValue(cfg.judgeModel, state.config?.judge_model, ""),
      judge_token: firstValue(cfg.judgeToken, state.config?.judge_token, ""),
    };
  }

  function parseQuestionIds(rawValue) {
    return String(rawValue || "")
      .split(/[,\n]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function defaultWrongCsvPath(run = currentRun()) {
    const output = String(run?.output_file || "").trim();
    if (!output) return "";
    const slash = Math.max(output.lastIndexOf("/"), output.lastIndexOf("\\"));
    if (slash < 0) return "wrong_questions_brief.csv";
    return `${output.slice(0, slash + 1)}wrong_questions_brief.csv`;
  }

  function syncDerivedQuestionIds(questionIds) {
    const ids = Array.from(new Set((questionIds || []).map((item) => String(item || "").trim()).filter(Boolean)));
    state.locomoSelectedQuestions = new Set(ids);
    if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") {
      state.locomoQaDraft = {};
    }
    state.locomoQaDraft.wbQaQuestionIds = ids.join(",");
    return ids;
  }

  function validQuestionIdsForForm(form) {
    const sample = String(form?.sample || "all").trim() || "all";
    return new Set((state.questions || [])
      .filter((row) => sample === "all" || String(row.sample_id || "").trim() === sample)
      .map((row) => String(row.question_id || "").trim())
      .filter(Boolean));
  }

  function ensureScopedQuestionIds(questionIds, form, sourceLabel = "question ids") {
    const ids = Array.from(new Set((questionIds || []).map((item) => String(item || "").trim()).filter(Boolean)));
    if (!ids.length) return ids;
    const validIds = validQuestionIdsForForm(form);
    if (!validIds.size) return ids;
    const invalidIds = ids.filter((id) => !validIds.has(id));
    if (invalidIds.length) {
      const preview = invalidIds.slice(0, 6).join(", ");
      throw new Error(`${sourceLabel} 中有 ${invalidIds.length} 个题号不属于当前 LoCoMo JSON / sample：${preview}`);
    }
    return ids;
  }

  function extractRunTaskConfig(snapshot) {
    let value = snapshot;
    if (value && typeof value === "object" && value.config && typeof value.config === "object") {
      value = value.config;
    }
    if (value && typeof value === "object" && value.run_id && value.config && typeof value.config === "object") {
      value = value.config;
    }
    return value && typeof value === "object" ? value : null;
  }

  async function loadRunSourceConfig(run) {
    const snapshot = typeof loadRunConfigSnapshot === "function"
      ? await loadRunConfigSnapshot(run).catch(() => null)
      : await api(`/api/config-snapshot?run_dir=${encodeURIComponent(String(run?.run_dir || "").trim())}`).catch(() => null);
    return extractRunTaskConfig(snapshot);
  }

  async function loadLocomoFlowStatus(form) {
    const cfg = currentAccountConfig();
    const workspace = String(form?.workspace || currentWorkspace() || "").trim();
    const payload = {
      account: String(state.selectedAccount || cfg.account || "default").trim() || "default",
      dataset: String(form?.data || "").trim(),
      sample: String(form?.sample || "all").trim() || "all",
      fast_ui_probe: true,
      config: {
        memoryBackend: backendId(),
        ovWorkspace: workspace,
        memoryWorkspace: workspace,
        ovHost: firstValue(cfg.ovHost, state.config?.ov_host, ""),
        ovPort: firstValue(cfg.ovPort, state.config?.ov_port, ""),
        judgeBaseUrl: firstValue(cfg.judgeBaseUrl, state.config?.judge_base_url, ""),
        judgeModel: firstValue(cfg.judgeModel, state.config?.judge_model, ""),
      },
    };
    state.locomoFlowStatusMeta = { timedOut: false, error: "" };
    const result = await api("/api/locomo-flow-status", {
      method: "POST",
      body: JSON.stringify(payload),
      timeoutMs: 5000,
    }).catch((error) => {
      const message = String(error?.message || "").trim();
      state.locomoFlowStatusMeta = {
        timedOut: /aborted|timed out|timeout/i.test(message),
        error: message,
      };
      return null;
    });
    state.locomoFlowStatus = result || null;
    return state.locomoFlowStatus;
  }

  async function buildQaGate(form) {
    const flowStatus = await loadLocomoFlowStatus(form).catch(() => null);
    const gate = buildLocomoQaGateModel({
      backendId,
      currentWorkspace,
      currentAccountConfig,
      form,
      flowStatus,
      readiness: state.readiness,
      state,
      tasks: locomoTasks(),
    });
    state.locomoQaGate = gate;
    return gate;
  }

  function runDetailRecord(run) {
    return run?.run_dir ? (state.runDetails?.[run.run_dir]?.record || null) : null;
  }

  async function loadRunScope(run) {
    if (!run) return { snapshot: null, record: null };
    const snapshot = await loadRunSourceConfig(run).catch(() => null);
    const record = runDetailRecord(run);
    return { snapshot, record };
  }

  function resolveRunScopeField({
    formValue = "",
    snapshotValue = "",
    recordValue = "",
    runValue = "",
    fallback = "",
    preferScope = false,
  }) {
    const values = preferScope
      ? [snapshotValue, recordValue, runValue, formValue, fallback]
      : [formValue, snapshotValue, recordValue, runValue, fallback];
    return String(firstValue(...values) || "").trim();
  }

  function resolveRunIdentityField(runConfig, runRecord, run, keys = []) {
    for (const key of keys) {
      const snapshotValue = runConfig?.[key];
      if (snapshotValue !== undefined && snapshotValue !== null && String(snapshotValue).trim()) {
        return String(snapshotValue).trim();
      }
      const recordValue = runRecord?.[key];
      if (recordValue !== undefined && recordValue !== null && String(recordValue).trim()) {
        return String(recordValue).trim();
      }
      const runValue = run?.[key];
      if (runValue !== undefined && runValue !== null && String(runValue).trim()) {
        return String(runValue).trim();
      }
    }
    return "";
  }

  function buildValidationPayload(payload, extras = {}) {
    const next = { ...payload };
    Object.entries(extras || {}).forEach(([key, value]) => {
      if (value === undefined || value === null) return;
      if (typeof value === "string" && !value.trim()) return;
      next[key] = value;
    });
    return next;
  }

  function forceAlignedLocomoEchoPayload(payload) {
    if (backendId() !== "echomemory") return payload;
    const sanitized = { ...payload };
    [
      "neo4j_graph_evidence",
      "neo4j_uri",
      "neo4j_username",
      "neo4j_password",
      "neo4j_database",
      "neo4j_graph_tenant_id",
      "neo4j_graph_user_id",
      "neo4j_graph_limit",
      "neo4j_graph_candidate_limit",
      "neo4j_graph_min_score",
      "neo4j_graph_min_selected",
      "neo4j_graph_max_selected",
      "current_session_raw_fallback",
      "segment_readback",
      "precision_session_readback",
      "precision_grounded_projection",
      "longmemeval_current_session_summary_fallback",
      "hotpot_empty_overview_fallback",
      "local_session_summaries",
      "local_segments",
      "local_atoms",
      "local_messages",
      "local_timeline_hints",
      "local_memory_artifacts",
      "compat_allow_local_evidence",
    ].forEach((key) => delete sanitized[key]);
    return {
      ...sanitized,
      identity_mode: "sample_question",
      prompt_mode: "vikingboat_lite",
      retrieval_mode: "search",
      evidence_policy: "blackbox",
      retrieval_source_mode: "echo_http_native",
      qa_memory_injection: true,
      tool_set: "vikingbot_native_safe",
      initial_tool_prefetch: false,
      fallback_to_one_shot: false,
      vikingboat_compat: false,
    };
  }

  function buildRunScopedQaForm(form, runConfig) {
    if (!runConfig) return { ...form };
    const next = { ...form };
    if (runConfig.data) next.data = String(runConfig.data).trim();
    if (runConfig.sample) next.sample = String(runConfig.sample).trim() || next.sample;
    if (runConfig.workspace) next.workspace = String(runConfig.workspace).trim();
    if (runConfig.echomem_root) next.echomem_root = String(runConfig.echomem_root).trim();
    const runUserId = runConfig.em_user_id || runConfig.ov_user_id || runConfig.user_id;
    const runAgentId = runConfig.em_agent_id || runConfig.ov_agent_id || runConfig.agent_id;
    if (runUserId) next.memory_user_id = String(runUserId).trim();
    if (runAgentId) next.memory_agent_id = String(runAgentId).trim();
    return next;
  }

  function applyRunConfigToPayload(payload, runConfig) {
    if (!runConfig) return payload;
    const next = { ...payload };
    const assignIfPresent = (field, value) => {
      if (value === undefined || value === null) return;
      if (typeof value === "string" && !value.trim()) return;
      next[field] = value;
    };
    assignIfPresent("data", runConfig.data);
    assignIfPresent("sample", runConfig.sample);
    assignIfPresent("workspace", runConfig.workspace || runConfig.echomemory_workspace || runConfig.openviking_workspace);
    assignIfPresent("account", runConfig.account);
    assignIfPresent("host", runConfig.host);
    assignIfPresent("port", runConfig.port);
    assignIfPresent("answer_base_url", runConfig.answer_base_url);
    assignIfPresent("answer_model", runConfig.answer_model);
    assignIfPresent("model_retries", runConfig.model_retries);
    assignIfPresent("top_k", runConfig.top_k);
    assignIfPresent("max_iterations", runConfig.max_iterations);
    assignIfPresent("tool_search_limit", runConfig.tool_search_limit);
    assignIfPresent("question_timeout_s", runConfig.question_timeout_s || runConfig.timeout_s);
    assignIfPresent("timeout_s", runConfig.timeout_s || runConfig.question_timeout_s);
    if (backendId() === "echomemory") {
      assignIfPresent("user_id", runConfig.em_user_id || runConfig.user_id);
      assignIfPresent("agent_id", runConfig.em_agent_id || runConfig.agent_id);
      assignIfPresent("em_user_id", runConfig.em_user_id || runConfig.user_id);
      assignIfPresent("em_agent_id", runConfig.em_agent_id || runConfig.agent_id);
      assignIfPresent("echomem_root", runConfig.echomem_root);
      assignIfPresent("echomem_config", runConfig.echomem_config);
      assignIfPresent("tool_set", runConfig.tool_set || runConfig.memory_tool_set);
      assignIfPresent("qa_parallelism", runConfig.qa_parallelism);
      assignIfPresent("judge_every", runConfig.judge_every);
      assignIfPresent("judge_parallel", runConfig.judge_parallel);
      assignIfPresent("judge_timeout_s", runConfig.judge_timeout_s);
      assignIfPresent("judge_retries", runConfig.judge_retries);
      if (typeof runConfig.qa_memory_injection === "boolean") next.qa_memory_injection = runConfig.qa_memory_injection;
      assignIfPresent("memory_budget_chars", runConfig.memory_budget_chars);
      assignIfPresent("user_memory_budget_chars", runConfig.user_memory_budget_chars);
      assignIfPresent("agent_memory_budget_chars", runConfig.agent_memory_budget_chars);
      assignIfPresent("prefetch_read_count", runConfig.prefetch_read_count);
      assignIfPresent("prefetch_context_chars", runConfig.prefetch_context_chars);
      assignIfPresent("tool_log_chars", runConfig.tool_log_chars);
      if (typeof runConfig.vikingboat_tool_loop === "boolean") next.vikingboat_tool_loop = runConfig.vikingboat_tool_loop;
    } else {
      assignIfPresent("ov_user_id", runConfig.ov_user_id || runConfig.user_id);
      assignIfPresent("ov_agent_id", runConfig.ov_agent_id || runConfig.agent_id);
      assignIfPresent("openviking_tool_set", runConfig.openviking_tool_set || runConfig.tool_set);
      if (typeof runConfig.openviking_tool_loop === "boolean") next.openviking_tool_loop = runConfig.openviking_tool_loop;
    }
    return next;
  }

  function backendLabel(id) {
    return id === "openviking" ? "OpenViking" : "EchoMemory";
  }

  function runBackendId(run) {
    const kind = String(run?.kind || "").trim().toLowerCase();
    if (kind.startsWith("openviking_")) return "openviking";
    if (kind.startsWith("echomemory_")) return "echomemory";
    return "";
  }

  function ensureRunMatchesSelectedBackend(run, actionLabel) {
    const selectedBackend = backendId();
    const resultBackend = runBackendId(run);
    if (resultBackend && resultBackend !== selectedBackend) {
      throw new Error(`当前结果来自 ${backendLabel(resultBackend)}，请先切换到 ${backendLabel(selectedBackend)} 结果再执行${actionLabel}`);
    }
  }

  function buildLocomoQaPayload(form) {
    const cfg = normalizeLocomoAccountConfig(currentAccountConfig());
    const normalizedForm = normalizeLocomoQaForm(form);
    const toolEnabled = normalizedForm.use_tools;
    const toolLoop = normalizedForm.tool_loop;
    const memoryUserId = String(normalizedForm.memory_user_id || firstValue(cfg.memoryUserId, "default")).trim() || "default";
    const memoryAgentId = String(normalizedForm.memory_agent_id || firstValue(cfg.memoryAgentId, "default")).trim() || "default";
    const echomemBaseUrl = String(normalizedForm.echomem_base_url || firstValue(cfg.echomemBaseUrl, state.config?.echomemBaseUrl, state.readiness?.preflight?.runtime?.url, "")).trim();
    const echomemTransport = echomemBaseUrl
      ? "http"
      : String(firstValue(cfg.echomemTransport, "")).trim().toLowerCase();
    const payload = {
      kind: qaKind(),
      dataset_format: "locomo",
      data: normalizedForm.data,
      sample: normalizedForm.sample,
      ...buildWorkspaceTaskContext({ backendId, cfg, currentWorkspace, firstValue, state, workspace: normalizedForm.workspace }),
      top_k: normalizedForm.top_k,
      skip_model_preflight: true,
      judge_every: 10,
      judge_parallel: 4,
      judge_timeout_s: 90,
      ...buildModelEndpointFields({ cfg, firstValue, state }),
    };
    if (backendId() === "echomemory") {
      Object.assign(payload, {
        ...buildEchoMemoryIdentityFields({
          cfg,
          firstValue,
          state,
          overrides: {
            echomem_root: normalizedForm.echomem_root,
            echomem_base_url: echomemBaseUrl,
            echomem_transport: echomemTransport,
            user_id: memoryUserId,
            agent_id: memoryAgentId,
            em_user_id: memoryUserId,
            em_agent_id: memoryAgentId,
          },
        }),
        user_id: memoryUserId,
        agent_id: memoryAgentId,
        em_user_id: memoryUserId,
        em_agent_id: memoryAgentId,
        identity_mode: "sample_question",
        prompt_mode: "vikingboat_lite",
        retrieval_mode: "search",
        evidence_policy: "blackbox",
        retrieval_source_mode: "echo_http_native",
        qa_memory_injection: true,
        answer_thinking_mode: "disabled",
        tool_set: "vikingbot_native_safe",
        tool_search_limit: normalizedForm.tool_search_limit,
        max_iterations: normalizedForm.max_iterations,
        model_retries: Number.isFinite(normalizedForm.model_retries) ? normalizedForm.model_retries : Number(firstValue(cfg.echomemQaModelRetries, "5")),
        judge_retries: Number.isFinite(normalizedForm.model_retries) ? normalizedForm.model_retries : Number(firstValue(cfg.echomemQaModelRetries, "5")),
        qa_parallelism: Number.isFinite(normalizedForm.qa_parallelism) ? normalizedForm.qa_parallelism : Number(firstValue(cfg.echomemQaParallelism, "10")),
        question_timeout_s: Number.isFinite(normalizedForm.question_timeout_s) ? normalizedForm.question_timeout_s : Number(firstValue(cfg.echomemQaQuestionTimeout, "600")),
        memory_budget_chars: Number.isFinite(normalizedForm.memory_budget_chars) ? normalizedForm.memory_budget_chars : Number(firstValue(cfg.echomemQaMemoryBudgetChars, "6000")),
        user_memory_budget_chars: Number.isFinite(normalizedForm.user_memory_budget_chars) ? normalizedForm.user_memory_budget_chars : Number(firstValue(cfg.echomemQaUserMemoryBudgetChars, "4000")),
        agent_memory_budget_chars: Number.isFinite(normalizedForm.agent_memory_budget_chars) ? normalizedForm.agent_memory_budget_chars : Number(firstValue(cfg.echomemQaAgentMemoryBudgetChars, "2000")),
        prefetch_read_count: Number.isFinite(normalizedForm.prefetch_read_count) ? normalizedForm.prefetch_read_count : Number(firstValue(cfg.echomemQaPrefetchReadCount, "4")),
        prefetch_context_chars: Number.isFinite(normalizedForm.prefetch_context_chars) ? normalizedForm.prefetch_context_chars : Number(firstValue(cfg.echomemQaPrefetchContextChars, "5000")),
        tool_log_chars: Number.isFinite(normalizedForm.tool_log_chars) ? normalizedForm.tool_log_chars : Number(firstValue(cfg.echomemQaToolLogChars, "1200")),
        initial_tool_prefetch: false,
        fallback_to_one_shot: false,
        vikingboat_compat: false,
        vikingboat_tool_loop: Boolean(toolEnabled && toolLoop),
      });
    } else {
      applyProviderIdentity(payload, { backendId, cfg, firstValue, state });
      Object.assign(payload, {
        ov_user_id: memoryUserId,
        ov_agent_id: memoryAgentId,
        prompt_mode: toolEnabled ? (form.prompt_mode || firstValue(cfg.echomemQaPromptMode, "vikingbot_aligned")) : "one_shot",
        openviking_tool_set: form.tool_set || firstValue(cfg.echomemQaToolSet, "vikingbot_native_safe"),
        tool_search_limit: form.tool_search_limit,
        max_iterations: form.max_iterations,
        model_retries: Number.isFinite(form.model_retries) ? form.model_retries : Number(firstValue(cfg.echomemQaModelRetries, "5")),
        judge_retries: Number.isFinite(form.model_retries) ? form.model_retries : Number(firstValue(cfg.echomemQaModelRetries, "5")),
        timeout_s: Number.isFinite(form.question_timeout_s) ? form.question_timeout_s : Number(firstValue(cfg.echomemQaQuestionTimeout, "600")),
        question_timeout_s: Number.isFinite(form.question_timeout_s) ? form.question_timeout_s : Number(firstValue(cfg.echomemQaQuestionTimeout, "600")),
        openviking_tool_loop: Boolean(toolEnabled && toolLoop),
        read_openviking_content: true,
      });
    }
    return payload;
  }

  async function startImport() {
    const cfg = currentAccountConfig();
    const form = formReaders.readLocomoImportForm();
    const echomemBaseUrl = String(form.echomem_base_url || firstValue(cfg.echomemBaseUrl, state.config?.echomemBaseUrl, state.readiness?.preflight?.runtime?.url, "")).trim();
    const echomemTransport = echomemBaseUrl
      ? "http"
      : String(firstValue(cfg.echomemTransport, "")).trim().toLowerCase();
    const payload = {
      kind: backendId() === "openviking" ? "openviking_import" : "echomemory_import",
      dataset_format: "locomo",
      data: form.data,
      sample: form.sample,
      ...buildWorkspaceTaskContext({ backendId, cfg, currentWorkspace, firstValue, state, workspace: form.workspace }),
      ...buildModelEndpointFields({ cfg, firstValue, state }),
      name: `locomo ${backendId()} import new-ui`,
      workspace_mode: "manual",
      session_mode: "locomo",
      import_wait_mode: firstValue(cfg.echoImportMode, "fast"),
      defer_artifact_wait: firstValue(cfg.echoImportMode, "fast") === "fast",
      ...buildEchoMemoryIdentityFields({
        cfg,
        firstValue,
        state,
        overrides: {
          echomem_root: form.echomem_root,
          echomem_base_url: echomemBaseUrl,
          echomem_transport: echomemTransport,
        },
      }),
    };
    const task = await api("/api/tasks", {method: "POST", body: JSON.stringify(payload)});
    return {
      refresh: true,
      pollLogTarget: "wbImportLogBody",
      createdTask: task,
      followupRefreshMs: 2500,
    };
  }

  async function validateAndSubmitQaPayload(payload, gate) {
    const validateResult = await validateTaskPayload(payload);
    const modelProbe = await probeModel(qaModelProbePayload(payload), "agent");
    const probeCheck = modelProbeCheck(modelProbe, "answer_model_probe");
    state.locomoQaGate = {
      ...gate,
      ok: gate.ok && validateResult?.ok !== false && probeCheck.ok !== false,
      subtitle: validateResult?.ok === false ? "后端校验未通过，已阻止 LoCoMo QA 启动。" : gate.subtitle,
      validateResult,
      modelProbe,
      checks: [
        ...gate.checks,
        ...((validateResult?.checks || []).map((item) => ({
          name: item.name,
          ok: item.ok !== false,
          message: item.message || "",
        }))),
        probeCheck,
      ],
    };
    if (validateResult?.ok === false || probeCheck.ok === false) {
      const failedCheck = probeCheck.ok === false
        ? probeCheck
        : (validateResult.checks || []).find((item) => item.ok === false);
      throw new Error(failedCheck?.message || "LoCoMo QA 启动校验未通过");
    }
    const task = await api("/api/tasks", {method: "POST", body: JSON.stringify(payload)});
    return {
      refresh: true,
      createdTask: task,
      followupRefreshMs: 2500,
    };
  }

  async function buildQuestionSet(mode, form) {
    if (mode === "wrong_csv" && form.wrong_csv) {
      return resolveWrongCsvQuestionSet(api, {
        datasetPath: form.data,
        sample: form.sample,
        wrongCsv: form.wrong_csv,
        resultPath: currentRun()?.output_file || "",
      });
    }
    const params = new URLSearchParams({
      mode,
      path: form.data,
      sample: form.sample,
    });
    return api(`/api/question-set?${params.toString()}`);
  }

  async function launchSelectedQuestionsQa({ form, questionIds, runName }) {
    const initialGate = await buildQaGate(form);
    if (!initialGate.ok) {
      const failedCheck = (initialGate.checks || []).find((item) => item.ok === false);
      throw new Error(failedCheck?.message || "LoCoMo QA 启动检查未通过");
    }
    const ids = ensureScopedQuestionIds(questionIds, form, "指定题号");
    if (!ids.length) throw new Error("当前没有可运行的题号");
    const payload = buildValidationPayload(buildLocomoQaPayload(form), {
      mode: form.mode,
      wrong_csv: form.wrong_csv,
    });
    Object.assign(payload, {
      sample: form.sample,
      questions: ids.join(","),
      name: runName || `locomo selected ${ids.length}q ${backendId()} QA`,
    });
    return validateAndSubmitQaPayload(forceAlignedLocomoEchoPayload(payload), initialGate);
  }

  async function startQa({ preflightGate = null } = {}) {
    const form = formReaders.readLocomoQaForm();
    const initialGate = preflightGate || await buildQaGate(form);
    if (!initialGate.ok) {
      const failedCheck = (initialGate.checks || []).find((item) => item.ok === false);
      throw new Error(failedCheck?.message || "LoCoMo QA 启动检查未通过");
    }
    if (form.mode === "selected") {
      return launchSelectedQuestionsQa({
        form,
        questionIds: parseQuestionIds(form.question_ids),
        runName: `locomo selected ${backendId()} QA`,
      });
    }
    if (form.mode === "wrong_csv") {
      const questionSet = await buildQuestionSet("wrong_csv", form);
      if (Number(questionSet.invalid_count || 0) > 0) {
        throw new Error(`wrong_csv 中有 ${questionSet.invalid_count} 个题号不属于当前 LoCoMo JSON / sample`);
      }
      syncDerivedQuestionIds(questionSet.question_ids || []);
      return launchSelectedQuestionsQa({
        form,
        questionIds: questionSet.question_ids || [],
        runName: `locomo 上轮错题重跑 ${backendId()} QA`,
      });
    }
    const payload = buildValidationPayload(buildLocomoQaPayload(form), {
      mode: form.mode,
      wrong_csv: form.wrong_csv,
    });
    if (form.mode === "time") {
      const data = await api(`/api/question-set?mode=time&path=${encodeURIComponent(form.data)}&sample=${encodeURIComponent(form.sample)}`);
      const allQuestionIds = Array.isArray(data.question_ids) ? data.question_ids : [];
      const questionIds = form.question_limit > 0 ? allQuestionIds.slice(0, form.question_limit) : allQuestionIds;
      if (!questionIds.length) throw new Error("当前范围没有可测试的时间题");
      Object.assign(payload, {
        sample: form.sample,
        questions: questionIds.join(","),
        name: `locomo 时间题 QA ${questionIds.length} 题`,
      });
    } else {
      Object.assign(payload, {
        sample: form.sample,
        questions: "",
        ...(form.sample === "all" ? {full_locomo_run: true} : {}),
        name: `locomo ${form.sample === "all" ? "full" : form.sample} ${backendId()} QA`,
      });
    }
    return validateAndSubmitQaPayload(forceAlignedLocomoEchoPayload(payload), initialGate);
  }

  async function preflightQa() {
    const form = formReaders.readLocomoQaForm();
    const gate = await buildQaGate(form);
    let checks = [...gate.checks];
    let validateResult = null;
    if (gate.ok) {
      const payload = buildValidationPayload(buildLocomoQaPayload(form), {
        mode: form.mode,
        wrong_csv: form.wrong_csv,
      });
      if (form.mode === "time") {
        const data = await buildQuestionSet("time", form);
        const allQuestionIds = Array.isArray(data.question_ids) ? data.question_ids : [];
        const questionIds = form.question_limit > 0 ? allQuestionIds.slice(0, form.question_limit) : allQuestionIds;
        if (!questionIds.length) {
          checks = checks.concat([{ name: "question_set", ok: false, message: "当前范围没有可测试的时间题" }]);
          state.locomoQaGate = {
            ...gate,
            ok: false,
            subtitle: "当前范围没有可测试的时间题。",
            checks,
          };
          return state.locomoQaGate;
        }
        payload.questions = questionIds.join(",");
      }
      if (form.mode === "selected") {
        const selectedIds = parseQuestionIds(form.question_ids);
        if (!selectedIds.length) {
          checks = checks.concat([{ name: "question_set", ok: false, message: "请先填写要运行的 question ids" }]);
        } else {
          const scopedIds = ensureScopedQuestionIds(selectedIds, form, "指定题号");
          payload.questions = scopedIds.join(",");
        }
      }
      if (form.mode === "wrong_csv") {
        const data = await buildQuestionSet("wrong_csv", form);
        const wrongIds = Array.isArray(data.question_ids) ? data.question_ids : [];
        if (!wrongIds.length) {
          checks = checks.concat([{ name: "question_set", ok: false, message: "当前错题 CSV 没有可重跑题目" }]);
        } else if (Number(data.invalid_count || 0) > 0) {
          checks = checks.concat([{ name: "question_scope", ok: false, message: `wrong_csv 中有 ${data.invalid_count} 个题号不属于当前 LoCoMo JSON / sample` }]);
        } else {
          payload.questions = wrongIds.join(",");
        }
      }
      if (checks.some((item) => item.ok === false)) {
        state.locomoQaGate = {
          ...gate,
          ok: false,
          subtitle: "LoCoMo QA 启动检查未通过，请先修正输入参数。",
          checks,
        };
        return state.locomoQaGate;
      }
      validateResult = await validateTaskPayload(payload);
      const modelProbe = await probeModel(qaModelProbePayload(payload), "agent");
      checks = checks.concat((validateResult?.checks || []).map((item) => ({
        name: item.name,
        ok: item.ok !== false,
        message: item.message || "",
      })));
      checks = checks.concat([modelProbeCheck(modelProbe, "answer_model_probe")]);
      if (modelProbe?.ok === false) {
        validateResult = {
          ...(validateResult || {}),
          ok: false,
          checks: [
            ...((validateResult?.checks || []).map((item) => ({
              name: item.name,
              ok: item.ok !== false,
              message: item.message || "",
            }))),
            modelProbeCheck(modelProbe, "answer_model_probe"),
          ],
        };
      }
    }
    state.locomoQaGate = {
      ...gate,
      ok: gate.ok && validateResult?.ok !== false,
      subtitle: gate.ok
        ? (validateResult?.ok === false ? "后端校验未通过，请先修正数据或输出目录。" : "LoCoMo QA 启动检查通过，可以开始运行。")
        : gate.subtitle,
      validateResult,
      checks,
    };
    return state.locomoQaGate;
  }

  async function preflightJudge({ currentRun }) {
    const run = currentRun();
    if (run?.run_dir && typeof ensureRunDetail === "function") {
      await ensureRunDetail(run, { force: true }).catch(() => null);
    }
    const form = formReaders.readJudgeForm();
    const qaForm = formReaders.readLocomoQaForm();
    const localChecks = [];
    try {
      ensureRunMatchesSelectedBackend(run, "Judge");
      localChecks.push({
        name: "backend_match",
        ok: true,
        message: run ? `当前结果属于 ${backendLabel(runBackendId(run) || backendId())}` : "当前未选择结果",
      });
    } catch (error) {
      localChecks.push({
        name: "backend_match",
        ok: false,
        message: error.message || "当前结果后端与当前选择不一致",
      });
    }
    const runScope = await loadRunScope(run);
    const runConfig = runScope.snapshot;
    const runRecord = runScope.record;
    const judgeDataPath = resolveRunScopeField({
      snapshotValue: runConfig?.data,
      recordValue: runRecord?.dataset_path,
      runValue: run?.dataset_path,
      formValue: form.data,
      preferScope: true,
    });
    const judgeWorkspace = resolveRunScopeField({
      snapshotValue: runConfig?.workspace || runConfig?.echomemory_workspace || runConfig?.openviking_workspace,
      recordValue: runRecord?.workspace,
      runValue: run?.workspace,
      preferScope: true,
    });
    const runSample = resolveRunScopeField({
      snapshotValue: runConfig?.sample,
      recordValue: runRecord?.sample,
      runValue: run?.sample,
      preferScope: true,
      fallback: "all",
    }) || "all";
    const currentSample = String(qaForm.sample || "all").trim() || "all";
    const sourceMemoryUserId = resolveRunIdentityField(runConfig, runRecord, run, ["em_user_id", "ov_user_id", "user_id"]);
    const sourceMemoryAgentId = resolveRunIdentityField(runConfig, runRecord, run, ["em_agent_id", "ov_agent_id", "agent_id"]);
    const currentMemoryUserId = String(qaForm.memory_user_id || currentAccountConfig().memoryUserId || "default").trim() || "default";
    const currentMemoryAgentId = String(qaForm.memory_agent_id || currentAccountConfig().memoryAgentId || "default").trim() || "default";
    const selectedAccount = String(state.selectedAccount || "").trim();
    const runAccount = resolveRunScopeField({
      snapshotValue: runConfig?.account,
      recordValue: runRecord?.account,
      runValue: run?.account,
      preferScope: true,
    });
    localChecks.push({
      name: "account_match",
      ok: !selectedAccount || !runAccount || selectedAccount === runAccount,
      message: runAccount
        ? (selectedAccount && selectedAccount !== runAccount
          ? `当前结果来自账户 ${runAccount}，请先切换到账户 ${runAccount}`
          : `当前结果账户 ${runAccount}`)
        : (selectedAccount || "当前未绑定账户来源"),
    });
    localChecks.push({
      name: "data_scope",
      ok: Boolean(judgeDataPath),
      message: judgeDataPath || "当前结果缺少稳定的数据集来源",
    });
    localChecks.push({
      name: "sample_scope",
      ok: !runSample || currentSample === "all" || runSample === currentSample,
      message: `当前结果 sample=${runSample || "-"}；界面 sample=${currentSample}`,
    });
    localChecks.push({
      name: "memory_user_scope",
      ok: !sourceMemoryUserId || sourceMemoryUserId === currentMemoryUserId,
      message: sourceMemoryUserId
        ? `当前结果 Memory User=${sourceMemoryUserId}；界面=${currentMemoryUserId}`
        : `界面 Memory User=${currentMemoryUserId}`,
    });
    localChecks.push({
      name: "memory_agent_scope",
      ok: !sourceMemoryAgentId || sourceMemoryAgentId === currentMemoryAgentId,
      message: sourceMemoryAgentId
        ? `当前结果 Memory Agent=${sourceMemoryAgentId}；界面=${currentMemoryAgentId}`
        : `界面 Memory Agent=${currentMemoryAgentId}`,
    });
    const payload = buildValidationPayload({
      kind: "judge",
      input: run?.output_file || "",
      data: judgeDataPath,
      output_dir: firstValue(state.config?.output_dir, ""),
    }, {
      run_data: run?.dataset_path,
      source_data: runConfig?.data || runRecord?.dataset_path,
      workspace: judgeWorkspace,
      run_workspace: run?.workspace,
      source_workspace: runConfig?.workspace || runConfig?.echomemory_workspace || runConfig?.openviking_workspace || runRecord?.workspace,
      judge_token: firstValue(currentAccountConfig().judgeToken, state.config?.judge_token, ""),
    });
    const modelProbe = await probeModel(judgeModelProbePayload(), "judge").catch(() => null);
    const rawValidateResult = await validateTaskPayload(payload).catch((error) => ({
      ok: false,
      checks: [{ name: "validate", ok: false, message: error.message || "Judge 校验失败" }],
    }));
    const probeCheck = modelProbeCheck(modelProbe, "judge_model_probe");
    const validateChecks = (rawValidateResult?.checks || []).map((item) => {
      if (item?.name === "judge_token" && probeCheck.ok) {
        return {
          ...item,
          ok: true,
          message: item.message || "judge token 由运行时预检确认可用",
        };
      }
      return {
        ...item,
        ok: item?.ok !== false,
        message: item?.message || "",
      };
    });
    const validateResult = {
      ...(rawValidateResult || {}),
      ok: localChecks.every((item) => item.ok !== false) && validateChecks.every((item) => item.ok !== false) && probeCheck.ok !== false,
      checks: [
        ...localChecks,
        ...validateChecks,
        probeCheck,
      ],
    };
    const pendingPreview = run?.output_file
      ? await api(`/api/pending-preview?path=${encodeURIComponent(run.output_file)}&limit=1`).catch(() => ({ total_pending: 0, rows: [] }))
      : { total_pending: 0, rows: [] };
    const model = buildLocomoJudgePreflightModel({
      currentRun,
      currentTasks: locomoTasks(),
      form: { ...form, data: judgeDataPath, workspace: judgeWorkspace },
      currentAccountConfig,
      pendingPreview,
      validateResult,
    });
    state.locomoJudgePreflight = model;
    return model;
  }

  async function startSelectedQa() {
    const form = formReaders.readLocomoQaForm();
    return launchSelectedQuestionsQa({
      form: { ...form, mode: "selected" },
      questionIds: parseQuestionIds(form.question_ids),
      runName: `locomo 指定题重跑 ${backendId()} QA`,
    });
  }

  async function startWrongCsvQa() {
    const form = formReaders.readLocomoQaForm();
    const normalizedForm = {
      ...form,
      wrong_csv: String(form?.wrong_csv || "").trim() || defaultWrongCsvPath(),
    };
    if (!normalizedForm.wrong_csv) {
      throw new Error("当前没有可重跑的错题 CSV");
    }
    const questionSet = await buildQuestionSet("wrong_csv", normalizedForm);
    if (Number(questionSet.invalid_count || 0) > 0) {
      throw new Error(`wrong_csv 中有 ${questionSet.invalid_count} 个题号不属于当前 LoCoMo JSON / sample`);
    }
    syncDerivedQuestionIds(questionSet.question_ids || []);
    return launchSelectedQuestionsQa({
      form: { ...normalizedForm, mode: "wrong_csv" },
      questionIds: questionSet.question_ids || [],
      runName: `locomo 错题 CSV 重跑 ${backendId()} QA`,
    });
  }

  async function retryFailedQa() {
    const run = currentRun();
    if (!run?.output_file) throw new Error("当前没有可重跑失败题的结果文件");
    ensureRunMatchesSelectedBackend(run, "失败题补跑");
    const runConfig = await loadRunSourceConfig(run);
    const form = buildRunScopedQaForm(formReaders.readLocomoQaForm(), runConfig);
    const initialGate = await buildQaGate(form);
    if (!initialGate.ok) {
      const failedCheck = (initialGate.checks || []).find((item) => item.ok === false);
      throw new Error(failedCheck?.message || "LoCoMo QA 启动检查未通过");
    }
    const diagnostics = await loadQaDiagnostics({
      path: run.output_file,
      datasetPath: form.data,
      sample: form.sample,
    });
    const failedRows = Number(diagnostics.retryable_failed_rows || 0);
    const failedQuestions = Number(diagnostics.retryable_failed_questions || 0);
    if (!failedQuestions) throw new Error("当前结果没有模型/API/检索失败题");
    const cfg = currentAccountConfig();
    const payload = {
      ...applyRunConfigToPayload(buildLocomoQaPayload(form), runConfig),
      kind: backendId() === "echomemory" ? "echomemory_qa_retry_failed" : "openviking_qa_retry_failed",
      input: run.output_file,
      timeout_s: form.question_timeout_s,
      read_openviking_content: true,
      name: `重跑失败问答 ${failedQuestions} 题/${failedRows} 行`,
    };
    return validateAndSubmitQaPayload(forceAlignedLocomoEchoPayload(payload), initialGate);
  }

  async function retryMissingQa() {
    const run = currentRun();
    if (!run?.output_file) throw new Error("当前没有可补跑缺失题的结果文件");
    ensureRunMatchesSelectedBackend(run, "缺失题补跑");
    const runConfig = await loadRunSourceConfig(run);
    const form = buildRunScopedQaForm(formReaders.readLocomoQaForm(), runConfig);
    const diagnostics = await loadQaDiagnostics({
      path: run.output_file,
      datasetPath: form.data,
      sample: form.sample,
    });
    const missingIds = Array.isArray(diagnostics.missing_question_ids) ? diagnostics.missing_question_ids : [];
    const failedIds = Array.isArray(diagnostics.retryable_failed_question_ids) ? diagnostics.retryable_failed_question_ids : [];
    if (!missingIds.length) {
      if (backendId() === "openviking" && failedIds.length) {
        return retryFailedQa();
      }
      throw new Error("当前结果没有缺失题");
    }
    syncDerivedQuestionIds(missingIds);
    const initialGate = await buildQaGate(form);
    if (!initialGate.ok) {
      const failedCheck = (initialGate.checks || []).find((item) => item.ok === false);
      throw new Error(failedCheck?.message || "LoCoMo QA 启动检查未通过");
    }
    const payload = {
      ...applyRunConfigToPayload(buildLocomoQaPayload(form), runConfig),
      kind: backendId() === "echomemory" ? "echomemory_qa_retry_missing" : "openviking_qa_retry_missing",
      input: run.output_file,
      question_ids: missingIds.join(","),
      questions: missingIds.join(","),
      name: `locomo 补跑并合并缺失题 ${missingIds.length}`,
    };
    if (backendId() !== "echomemory") {
      payload.sample = "all";
      payload.read_openviking_content = true;
    }
    return validateAndSubmitQaPayload(forceAlignedLocomoEchoPayload(payload), initialGate);
  }

  return {
    buildLocomoQaPayload,
    preflightJudge,
    preflightQa,
    retryFailedQa,
    retryMissingQa,
    startImport,
    startSelectedQa,
    startQa,
    startWrongCsvQa,
  };
}

import { applyProviderIdentity, buildModelEndpointFields, buildWorkspaceTaskContext } from "./payload-common.js";
import { buildOfficialJudgePreflight, buildOfficialQaLaunchGate } from "./official-preflight.js";
import { resolveWrongCsvQuestionSet } from "./wrong-csv.js";

export function createLongMemEvalActions(deps) {
  const {
    api,
    backendId,
    currentAccountConfig,
    currentRun,
    currentWorkspace,
    ensureRunDetail,
    firstValue,
    formReaders,
    genericQaKind,
    loadQaDiagnostics,
    state,
    tasksForBenchmark,
    validatePayload,
  } = deps;

  function parseQuestionIds(rawValue) {
    return String(rawValue || "")
      .split(/[,\n]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

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

  function buildQaPayload(form, cfg) {
    const toolEnabled = form.use_tools;
    const questionCount = form.count;
    const payload = {
      kind: genericQaKind(),
      data: form.data,
      dataset_format: "longmemeval",
      format: "longmemeval",
      count: questionCount,
      sample: "all",
      identity_mode: "isolated_sample",
      auto_judge: false,
      official_eval_after: form.official_eval_after,
      skip_model_preflight: true,
      ...buildWorkspaceTaskContext({ backendId, cfg, currentWorkspace, firstValue, state, workspace: form.workspace }),
      top_k: form.top_k,
      ...buildModelEndpointFields({ cfg, firstValue, state }),
      name: `longmemeval ${backendId()} ${questionCount}q`,
    };
    if (backendId() === "echomemory") {
      Object.assign(payload, {
        prompt_mode: toolEnabled ? firstValue(cfg.longMemEvalPromptMode, cfg.echomemQaPromptMode, "vikingboat_lite") : "one_shot",
        retrieval_mode: form.retrieval_mode || firstValue(cfg.longMemEvalRetrievalMode, cfg.hotpotQaRetrievalMode, "search"),
        retrieval_query_strategy: "direct",
        tool_set: form.tool_set || firstValue(cfg.longMemEvalToolSet, cfg.hotpotQaToolSet, "vikingbot_native_safe"),
        tool_search_limit: form.tool_search_limit,
        tool_min_score: Number.isFinite(form.tool_min_score) ? form.tool_min_score : Number(firstValue(cfg.longMemEvalToolMinScore, cfg.hotpotQaToolMinScore, "0.35")),
        max_iterations: form.max_iterations,
        question_timeout_s: Number.isFinite(form.question_timeout_s) ? form.question_timeout_s : Number(firstValue(cfg.longMemEvalQuestionTimeout, cfg.hotpotQaQuestionTimeout, "180")),
        qa_parallelism: Number.isFinite(form.qa_parallelism) ? form.qa_parallelism : Number(firstValue(cfg.longMemEvalQaParallelism, cfg.echomemQaParallelism, "10")),
        fallback_to_one_shot: !toolEnabled || cfg.echomemQaFallbackToOneShot !== false,
        vikingboat_tool_loop: false,
      });
      applyProviderIdentity(payload, { backendId, cfg, firstValue, state });
    } else {
      applyProviderIdentity(payload, {
        backendId,
        cfg,
        firstValue,
        state,
        openvikingExtras: { read_openviking_content: true },
      });
    }
    return payload;
  }

  function defaultWrongCsvPath(run = currentRun()) {
    const output = String(run?.output_file || "").trim();
    if (!output) return "";
    const slash = Math.max(output.lastIndexOf("/"), output.lastIndexOf("\\"));
    if (slash < 0) return "wrong_questions_brief.csv";
    return `${output.slice(0, slash + 1)}wrong_questions_brief.csv`;
  }

  function runScopedConfig(run) {
    const snapshot = run?.run_dir ? (state.runConfigSnapshots?.[run.run_dir] || null) : null;
    return snapshot?.config || snapshot || null;
  }

  async function loadRunScope() {
    const run = currentRun();
    if (!run?.output_file) throw new Error("当前没有可用的 LongMemEval 结果");
    if (run?.run_dir && typeof ensureRunDetail === "function") {
      await ensureRunDetail(run, { force: true }).catch(() => null);
    }
    const detail = run?.run_dir ? (state.runDetails?.[run.run_dir] || null) : null;
    const config = runScopedConfig(run);
    const dataPath = String(
      detail?.record?.dataset_path
      || run?.dataset_path
      || config?.data
      || formReaders.readLongMemEvalQaForm().data
      || ""
    ).trim();
    const sample = String(
      detail?.record?.sample
      || run?.sample
      || config?.sample
      || "all"
    ).trim() || "all";
    return { run, detail, config, dataPath, sample };
  }

  function applyRunScopedOverrides(payload, scope) {
    const next = { ...payload };
    const { config, dataPath } = scope || {};
    if (dataPath) next.data = dataPath;
    if (config?.workspace) next.workspace = String(config.workspace).trim();
    if (config?.account) next.account = String(config.account).trim();
    if (config?.top_k !== undefined) next.top_k = config.top_k;
    if (config?.tool_search_limit !== undefined) next.tool_search_limit = config.tool_search_limit;
    if (config?.max_iterations !== undefined) next.max_iterations = config.max_iterations;
    if (config?.retrieval_mode) next.retrieval_mode = config.retrieval_mode;
    if (config?.tool_set) next.tool_set = config.tool_set;
    if (config?.tool_min_score !== undefined) next.tool_min_score = config.tool_min_score;
    if (config?.question_timeout_s !== undefined) next.question_timeout_s = config.question_timeout_s;
    if (config?.official_eval_after !== undefined) next.official_eval_after = config.official_eval_after;
    if (config?.qa_parallelism !== undefined) next.qa_parallelism = config.qa_parallelism;
    if (backendId() === "echomemory") {
      if (config?.echomem_root) next.echomem_root = config.echomem_root;
      if (config?.em_user_id || config?.user_id) {
        next.user_id = config.em_user_id || config.user_id;
        next.em_user_id = config.em_user_id || config.user_id;
      }
      if (config?.em_agent_id || config?.agent_id) {
        next.agent_id = config.em_agent_id || config.agent_id;
        next.em_agent_id = config.em_agent_id || config.agent_id;
      }
    } else {
      if (config?.ov_user_id || config?.user_id) next.ov_user_id = config.ov_user_id || config.user_id;
      if (config?.ov_agent_id || config?.agent_id) next.ov_agent_id = config.ov_agent_id || config.agent_id;
      if (config?.read_openviking_content !== undefined) next.read_openviking_content = config.read_openviking_content;
    }
    return next;
  }

  function buildQuestionScopedPayload(questionIds, scope, cfg) {
    const ids = Array.from(new Set((questionIds || []).map((item) => String(item || "").trim()).filter(Boolean)));
    if (!ids.length) throw new Error("当前没有可运行的 LongMemEval 题目");
    const form = formReaders.readLongMemEvalQaForm();
    const payload = buildQaPayload(form, cfg);
    const scopedPayload = applyRunScopedOverrides(payload, scope);
    scopedPayload.questions = ids.join(",");
    scopedPayload.count = 0;
    return scopedPayload;
  }

  async function startImport() {
    const cfg = currentAccountConfig();
    const form = formReaders.readLongMemEvalImportForm();
    const payload = {
      kind: genericQaKind(),
      data: form.data,
      dataset_format: "longmemeval",
      format: "longmemeval",
      count: form.count,
      sample: "all",
      import_only: true,
      auto_judge: false,
      official_eval_after: false,
      ...buildWorkspaceTaskContext({ backendId, cfg, currentWorkspace, firstValue, state, workspace: form.workspace }),
      top_k: Math.max(1, Number(firstValue(cfg.longMemEvalTopK, cfg.hotpotQaTopK, "8"))),
      ...buildModelEndpointFields({ cfg, firstValue, state }),
      name: `longmemeval import ${backendId()} new-ui`,
    };
    applyProviderIdentity(payload, { backendId, cfg, firstValue, state });
    await api("/api/tasks", {method: "POST", body: JSON.stringify(payload)});
    return {refresh: true, pollLogTarget: "wbImportLogBody"};
  }

  async function startQa() {
    const cfg = currentAccountConfig();
    const form = formReaders.readLongMemEvalQaForm();
    if (String(form.mode || "full").trim() === "selected") {
      return startSelectedQa();
    }
    const payload = buildQaPayload(form, cfg);
    await api("/api/tasks", {method: "POST", body: JSON.stringify(payload)});
    return {refresh: true};
  }

  async function startSelectedQa() {
    const cfg = currentAccountConfig();
    const form = formReaders.readLongMemEvalQaForm();
    const ids = parseQuestionIds(form.question_ids);
    if (!ids.length) throw new Error("请先填写要运行的 question ids");
    const payload = buildQuestionScopedPayload(ids, null, cfg);
    payload.name = `longmemeval selected ${ids.length}q`;
    await api("/api/tasks", {method: "POST", body: JSON.stringify(payload)});
    return {refresh: true};
  }

  async function preflightQa() {
    const cfg = currentAccountConfig();
    const form = formReaders.readLongMemEvalQaForm();
    const payload = buildQaPayload(form, cfg);
    const selectedIds = parseQuestionIds(form.question_ids);
    return buildOfficialQaLaunchGate({
      benchmarkId: "longmemeval",
      benchmarkLabel: "LongMemEval",
      form,
      payload,
      state,
      tasks: typeof tasksForBenchmark === "function" ? tasksForBenchmark("longmemeval") : [],
      validatePayload,
      probeModel,
      probePayload: {
        ...payload,
        answer_token: firstValue(cfg.answerToken, cfg.judgeToken, state.config?.answer_token, state.config?.judge_token, ""),
        judge_token: firstValue(cfg.judgeToken, cfg.answerToken, state.config?.judge_token, state.config?.answer_token, ""),
      },
      extraChecks: String(form.mode || "full").trim() === "selected"
        ? [{
            name: "question_set",
            ok: selectedIds.length > 0,
            message: selectedIds.length > 0 ? `${selectedIds.length} 个 question ids` : "selected 模式需要 question ids",
          }]
        : [],
    });
  }

  async function preflightJudge() {
    const run = currentRun();
    if (run?.run_dir && typeof ensureRunDetail === "function") {
      await ensureRunDetail(run, { force: true }).catch(() => null);
    }
    const detail = run?.run_dir ? state.runDetails?.[run.run_dir] || null : null;
    const result = run?.output_file ? state.resultSummaries?.[run.output_file] || null : null;
    const form = formReaders.readLongMemEvalQaForm();
    state.officialJudgePreflights = state.officialJudgePreflights || {};
    state.officialJudgePreflights.longmemeval = buildOfficialJudgePreflight({
      benchmarkId: "longmemeval",
      benchmarkLabel: "LongMemEval",
      run,
      detail,
      result,
      formDataPath: form.data,
      officialSummaryArtifactKey: "longmemeval_official_summary",
      officialSummaryFallbackKey: "longmemeval_official_summary_path",
    });
    return state.officialJudgePreflights.longmemeval;
  }

  async function retryFailedQa() {
    const cfg = currentAccountConfig();
    const scope = await loadRunScope();
    const diagnostics = await loadQaDiagnostics({
      path: scope.run.output_file,
      datasetPath: scope.dataPath,
      sample: scope.sample,
    });
    const ids = Array.from(new Set((diagnostics?.retryable_failed_question_ids || []).map((item) => String(item || "").trim()).filter(Boolean)));
    if (!ids.length) throw new Error("当前结果没有可恢复失败题");
    const payload = buildQuestionScopedPayload(ids, scope, cfg);
    payload.name = `longmemeval retry failed ${ids.length}q`;
    await api("/api/tasks", {method: "POST", body: JSON.stringify(payload)});
    return {refresh: true};
  }

  async function retryMissingQa() {
    const cfg = currentAccountConfig();
    const scope = await loadRunScope();
    const diagnostics = await loadQaDiagnostics({
      path: scope.run.output_file,
      datasetPath: scope.dataPath,
      sample: scope.sample,
    });
    const ids = Array.from(new Set((diagnostics?.missing_question_ids || []).map((item) => String(item || "").trim()).filter(Boolean)));
    if (!ids.length) throw new Error("当前结果没有缺失题");
    const payload = buildQuestionScopedPayload(ids, scope, cfg);
    payload.name = `longmemeval retry missing ${ids.length}q`;
    await api("/api/tasks", {method: "POST", body: JSON.stringify(payload)});
    return {refresh: true};
  }

  async function startWrongCsvQa() {
    const cfg = currentAccountConfig();
    const scope = await loadRunScope();
    const wrongCsv = defaultWrongCsvPath(scope.run);
    if (!wrongCsv) throw new Error("当前没有可用结果，无法定位错题 CSV");
    const result = await resolveWrongCsvQuestionSet(api, {
      datasetPath: scope.dataPath,
      sample: scope.sample,
      wrongCsv,
      resultPath: scope.run.output_file,
    });
    const ids = Array.from(new Set((result?.question_ids || []).map((item) => String(item || "").trim()).filter(Boolean)));
    if (!ids.length) {
      throw new Error(result?.invalid_count ? "错题 CSV 没有命中当前 LongMemEval 数据集题号" : "错题 CSV 当前没有可重跑题目");
    }
    const payload = buildQuestionScopedPayload(ids, scope, cfg);
    payload.name = `longmemeval wrong csv ${ids.length}q`;
    await api("/api/tasks", {method: "POST", body: JSON.stringify(payload)});
    return {refresh: true};
  }

  return {
    preflightJudge,
    preflightQa,
    retryFailedQa,
    retryMissingQa,
    startImport,
    startQa,
    startSelectedQa,
    startWrongCsvQa,
  };
}

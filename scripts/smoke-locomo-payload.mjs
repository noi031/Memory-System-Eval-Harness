#!/usr/bin/env node
import { createLocomoActions } from "../src/action/locomo.js";
import { createWorkbenchController } from "../src/controller.js";
import { createRuntimeActions } from "../src/action/runtime.js";
import { createWorkflowActions } from "../src/action/workflows.js";
import { createFormReaders } from "../src/form-readers.js";
import { normalizeLocomoAccountConfig } from "../src/locomo-qa-defaults.js";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function firstValue(...values) {
  return values.find((value) => value !== undefined && value !== null && String(value).trim() !== "") || "";
}

function makeDeps({ backend = "echomemory", activeStage = "import", diagnostics = null } = {}) {
  const calls = [];
  const runOutput = backend === "openviking"
    ? "/tmp/locomo-run/openviking_qa/openviking_memory_qa_results.csv"
    : "/tmp/locomo-run/echomemory_qa/echomemory_memory_qa_results.csv";
  const state = {
    activeBenchmark: "locomo",
    activeStage,
    selectedAccount: "payload-smoke",
    currentRunDirs: { locomo: "", hotpotqa: "" },
    userSelectedRunDirs: { locomo: false, hotpotqa: false },
    config: {
      judge_base_url: "https://judge.example/v1",
      judge_model: "judge-model",
      output_dir: "/tmp/locomo-output",
    },
    accountDetails: { config: {} },
  };
  const cfg = {
    memoryWorkspace: "/tmp/locomo-payload-smoke",
    echomemRoot: "/tmp/echomem-root",
    memoryUserId: "mem-user",
    memoryAgentId: "mem-agent",
    agentBaseUrl: "https://agent.example/v1",
    agentModel: "answer-model",
    echoImportMode: "fast",
    echomemQaPromptMode: "vikingboat_lite",
    echomemQaRetrievalMode: "search",
    echomemQaToolSet: "vikingbot_native_safe",
    echomemQaToolMinScore: "0.35",
    echomemQaModelRetries: "5",
    echomemQaQuestionTimeout: "600",
    judgeBaseUrl: "https://judge.example/v1",
    judgeModel: "judge-model",
    judgeToken: "judge-test",
    answerToken: "answer-test",
    agentToken: "agent-test",
    memoryInjectBaseUrl: "https://memory.example/v1",
    memoryInjectModel: "memory-model",
    memoryInjectToken: "memory-test",
  };
  const api = async (path, options = {}) => {
    if (path.startsWith("/api/question-set")) {
      return { question_ids: ["conv-30_q1", "conv-30_q2"] };
    }
    if (path.startsWith("/api/pending-preview")) {
      return { rows: [{ _row_index: 2 }, { _row_index: 7 }] };
    }
    if (path === "/api/model-preflight") {
      const payload = options.body ? JSON.parse(options.body) : {};
      return {
        ok: true,
        status: "ok",
        role: payload.role || "agent",
        base_url: payload.answer_base_url || payload.judge_base_url || "https://probe.example/v1",
        model: payload.answer_model || payload.judge_model || "probe-model",
      };
    }
    if (path.startsWith("/api/report")) {
      return { generated_at: "now", report_html_file: "/tmp/report.html", report_public_url: "/generated/report.html" };
    }
    if (path === "/api/tasks" || path === "/api/tasks/stop-all" || path === "/api/open-path") {
      calls.push({
        path,
        payload: options.body ? JSON.parse(options.body) : null,
      });
      return { ok: true };
    }
    throw new Error(`unexpected API path: ${path}`);
  };
  const currentRun = () => ({
    run_dir: "/tmp/locomo-run",
    output_file: runOutput,
    kind: backend === "openviking" ? "openviking_qa" : "echomemory_qa",
  });
  const runConfig = {
    data: "/tmp/locomo.json",
    sample: "conv-30",
    workspace: "/tmp/locomo-payload-smoke",
    account: "payload-smoke",
    em_user_id: "mem-user",
    em_agent_id: "mem-agent",
    ov_user_id: "mem-user",
    ov_agent_id: "mem-agent",
    echomem_config: "/tmp/echomem.config.json",
    top_k: 30,
    prompt_mode: "vikingboat_lite",
    retrieval_mode: "search",
    tool_set: "vikingbot_native_safe",
    tool_search_limit: 20,
    tool_min_score: 0.35,
    max_iterations: 50,
    model_retries: 7,
    qa_memory_injection: false,
    question_timeout_s: 600,
    timeout_s: 600,
    initial_tool_prefetch: true,
  };
  const deps = {
    api,
    backendId: () => backend,
    currentAccountConfig: () => cfg,
    currentBenchmark: () => ({ id: "locomo" }),
    currentRun,
    currentWorkspace: () => "/tmp/locomo-payload-smoke",
    ensureRunDetail: async () => ({}),
    firstValue,
    formReaders: {
      readLocomoImportForm: () => ({
        data: "/tmp/locomo.json",
        sample: "conv-30",
        workspace: "/tmp/locomo-payload-smoke",
      }),
      readLocomoQaForm: () => ({
        data: "/tmp/locomo.json",
        sample: "conv-30",
        mode: "full",
        top_k: 30,
        use_tools: true,
        tool_loop: true,
        tool_search_limit: 20,
        max_iterations: 50,
        model_retries: 5,
        initial_tool_prefetch: true,
        workspace: "/tmp/locomo-payload-smoke",
      }),
      readJudgeForm: () => ({ data: "/tmp/locomo.json" }),
    },
    genericQaKind: () => (backend === "openviking" ? "openviking_generic_qa" : "echomemory_generic_qa"),
    loadQaDiagnostics: async () => diagnostics || {
      retryable_failed_rows: 2,
      retryable_failed_questions: 2,
      retryable_failed_question_ids: ["conv-30_q1", "conv-30_q2"],
      missing_question_ids: ["conv-30_q3", "conv-30_q4"],
      missing_questions_count: 2,
    },
    loadRunConfigSnapshot: async () => runConfig,
    qaKind: () => (backend === "openviking" ? "openviking_qa" : "echomemory_qa"),
    state,
    tasksForBenchmark: () => [],
    validatePayload: async () => ({ ok: true, checks: [] }),
  };
  return { calls, deps, state };
}

const echoSetup = makeDeps({ backend: "echomemory" });
const originalEchoAccountConfig = echoSetup.deps.currentAccountConfig;
echoSetup.deps.currentAccountConfig = () => ({
  ...originalEchoAccountConfig(),
  echomemBaseUrl: "http://127.0.0.1:19080",
  echomemTransport: "http",
});
const locomo = createLocomoActions(echoSetup.deps);

const normalizedLegacyAccountCfg = normalizeLocomoAccountConfig({
  echomemQaRetrievalMode: "search",
  echomemQaMaxIterations: "8",
  echomemQaFallbackToOneShot: true,
  echomemQaVikingboatCompat: false,
  echomemQaLocalSessionSummaries: true,
  echomemQaLocalAtoms: true,
  echomemQaLocalMessages: false,
  echomemQaLocalTimelineHints: true,
  echomemQaLocalMemoryArtifacts: true,
});
assert(
  normalizedLegacyAccountCfg.echomemQaRetrievalMode === "search",
  "LoCoMo EchoMemory account defaults must preserve explicit search instead of rewriting it to local",
);

const normalizedHttpAccountCfg = normalizeLocomoAccountConfig({
  echomemBaseUrl: "http://127.0.0.1:19080",
  echomemTransport: "http",
  echomemQaRetrievalMode: "search",
  echomemQaMaxIterations: "50",
  echomemQaFallbackToOneShot: false,
});
assert(
  normalizedHttpAccountCfg.echomemQaRetrievalMode === "search",
  "LoCoMo EchoMemory HTTP mode must preserve search retrieval instead of rewriting it back to local",
);

const qaGate = await locomo.preflightQa();
assert(qaGate.ok === true, "LoCoMo QA preflight must pass for the default smoke form");
assert(qaGate.checks.some((item) => item.name === "answer_model_probe" && item.ok === true), "LoCoMo QA preflight must include a live answer-model probe check");

await locomo.startImport();
const importPayload = echoSetup.calls.at(-1).payload;
assert(importPayload.kind === "echomemory_import", "LoCoMo EchoMemory import must use echomemory_import");
assert(importPayload.data === "/tmp/locomo.json", "LoCoMo import must preserve data path");
assert(importPayload.sample === "conv-30", "LoCoMo import must preserve sample");
assert(importPayload.workspace === "/tmp/locomo-payload-smoke", "LoCoMo import must preserve workspace");
assert(importPayload.session_mode === "locomo", "LoCoMo import must set session_mode");
assert(importPayload.import_wait_mode === "fast", "LoCoMo import must preserve import wait mode");
assert(importPayload.defer_artifact_wait === true, "LoCoMo import fast mode must defer artifact wait");
assert(importPayload.user_id === "mem-user", "LoCoMo import must include EchoMemory user id");
assert(importPayload.agent_id === "mem-agent", "LoCoMo import must include EchoMemory agent id");
assert(importPayload.answer_token === "answer-test", "LoCoMo import must include account-scoped answer token");
assert(importPayload.judge_token === "judge-test", "LoCoMo import must include account-scoped judge token");
assert(importPayload.vlm_api_key === "memory-test", "LoCoMo import must include account-scoped memory inject token");
assert(importPayload.memory_base_url === "https://memory.example/v1", "LoCoMo import must include memory base url");
assert(importPayload.memory_inject_model === "memory-model", "LoCoMo import must include memory inject model");

await locomo.startQa();
const qaPayload = echoSetup.calls.at(-1).payload;
assert(qaPayload.kind === "echomemory_qa", "LoCoMo EchoMemory QA must use echomemory_qa");
assert(qaPayload.sample === "conv-30", "LoCoMo QA must preserve sample");
assert(qaPayload.top_k === 30, "LoCoMo QA must preserve top_k");
assert(qaPayload.prompt_mode === "vikingboat_lite", "LoCoMo QA must use the fixed VikingBoat-aligned prompt mode");
assert(qaPayload.echomem_base_url === "http://127.0.0.1:19080", "LoCoMo QA must include EchoMemory HTTP base url when API mode is configured");
assert(qaPayload.echomem_transport === "http", "LoCoMo QA must force EchoMemory transport to http when base url is configured");
assert(qaPayload.identity_mode === "sample_question", "LoCoMo QA must scope identity by sample/question to match VikingBoat");
assert(qaPayload.retrieval_mode === "search", "LoCoMo QA must keep retrieval mode fixed on search");
assert(qaPayload.evidence_policy === "blackbox", "LoCoMo QA must use the strict EchoMemory HTTP black-box evidence policy");
assert(qaPayload.retrieval_source_mode === "echo_http_native", "LoCoMo QA must leave source selection to the EchoMemory HTTP API");
assert(qaPayload.neo4j_graph_evidence === undefined, "LoCoMo QA must not expose a platform Neo4j evidence switch");
assert(qaPayload.local_session_summaries === undefined, "LoCoMo QA must not expose local session-summary injection");
assert(qaPayload.local_atoms === undefined, "LoCoMo QA must not expose local atom injection");
assert(qaPayload.local_messages === undefined, "LoCoMo QA must not expose local message injection");
assert(qaPayload.local_timeline_hints === undefined, "LoCoMo QA must not expose local timeline hints");
assert(qaPayload.local_memory_artifacts === undefined, "LoCoMo QA must not expose local artifact injection");
assert(qaPayload.search_overview_enrichment === undefined, "LoCoMo QA must not expose platform overview enrichment");
assert(qaPayload.current_session_raw_fallback === undefined, "LoCoMo QA must not expose platform current-session raw fallback");
assert(qaPayload.precision_session_readback === undefined, "LoCoMo QA must not expose platform precision session readback");
assert(qaPayload.precision_grounded_projection === undefined, "LoCoMo QA must not expose platform precision grounded projection");
assert(
  qaPayload.longmemeval_current_session_summary_fallback === undefined,
  "LoCoMo QA must not expose LongMemEval summary fallback",
);
assert(qaPayload.hotpot_empty_overview_fallback === undefined, "LoCoMo QA must not expose Hotpot empty-overview fallback");
assert(qaPayload.vikingboat_tool_loop === true, "LoCoMo QA must preserve tool-loop switch");
assert(qaPayload.tool_search_limit === 20, "LoCoMo QA must preserve tool search limit");
assert(qaPayload.max_iterations === 50, "LoCoMo QA must preserve max iterations");
assert(qaPayload.model_retries === 5, "LoCoMo QA must preserve model retry count on first launch");
assert(qaPayload.qa_memory_injection === true, "LoCoMo QA must keep QA memory injection enabled");
assert(qaPayload.judge_every === 10, "LoCoMo QA must default to incremental judge every 10 questions");
assert(qaPayload.judge_parallel === 4, "LoCoMo QA must preserve incremental judge parallelism");
assert(qaPayload.judge_timeout_s === 90, "LoCoMo QA must preserve incremental judge timeout");
assert(qaPayload.judge_retries === 5, "LoCoMo QA must keep judge retries aligned with model retries");
assert(qaPayload.initial_tool_prefetch === false, "LoCoMo QA must keep initial tool prefetch fixed off");
assert(qaPayload.fallback_to_one_shot === false, "LoCoMo QA must keep one-shot fallback disabled");
assert(qaPayload.vikingboat_compat === false, "LoCoMo QA must keep compat mode disabled");
assert(qaPayload.answer_token === "answer-test", "LoCoMo QA must include account-scoped answer token");
assert(qaPayload.judge_token === "judge-test", "LoCoMo QA must include account-scoped judge token");
assert(qaPayload.vlm_api_key === "memory-test", "LoCoMo QA must include account-scoped memory inject token");
assert(qaPayload.memory_base_url === "https://memory.example/v1", "LoCoMo QA must include memory base url");
assert(qaPayload.memory_inject_model === "memory-model", "LoCoMo QA must include memory inject model");

const invalidSelectedSetup = makeDeps({ backend: "echomemory" });
const invalidSelectedLocomo = createLocomoActions({
  ...invalidSelectedSetup.deps,
  formReaders: {
    ...invalidSelectedSetup.deps.formReaders,
    readLocomoQaForm: () => ({
      ...invalidSelectedSetup.deps.formReaders.readLocomoQaForm(),
      mode: "selected",
      question_ids: "",
    }),
  },
});
const invalidSelectedGate = await invalidSelectedLocomo.preflightQa();
assert(invalidSelectedGate.ok === false, "LoCoMo QA preflight must fail when selected mode has no question ids");
assert(invalidSelectedGate.checks.some((item) => item.name === "question_set" && item.ok === false), "LoCoMo QA preflight must report missing selected question ids");

const slowFlowSetup = makeDeps({ backend: "echomemory" });
slowFlowSetup.deps.state.runs = [{ kind: "judge", status: "succeeded", run_dir: "/tmp/other-run" }];
const slowFlowLocomo = createLocomoActions({
  ...slowFlowSetup.deps,
  api: async (path, options = {}) => {
    if (path === "/api/locomo-flow-status") {
      throw new Error("request timed out");
    }
    return slowFlowSetup.deps.api(path, options);
  },
});
const slowFlowGate = await slowFlowLocomo.preflightQa();
assert(slowFlowGate.ok === true, "LoCoMo QA preflight must degrade gracefully when locomo-flow-status times out");
assert(
  slowFlowGate.checks.some((item) => item.name === "import_ready" && String(item.message || "").includes("超时")),
  "LoCoMo QA preflight must explain when import scope probing timed out",
);

const draftFallbackReaders = createFormReaders({
  $: () => null,
  queryAll: () => [],
  currentBenchmark: () => ({ defaultData: "/tmp/locomo.json" }),
  currentWorkspace: () => "/tmp/locomo-payload-smoke",
  state: {
    activeStage: "qa",
    locomoQaDraft: {
      wbQaMode: "selected",
      wbQaQuestionIds: "conv-30_q7,conv-30_q8",
      wbQaWrongCsv: "/tmp/locomo-run/wrong_questions_brief.csv",
    },
    locomoSelectedQuestions: new Set(["conv-30_q9"]),
    locomoWrongCsv: "/tmp/ignored-wrong.csv",
  },
});
const draftFallbackForm = draftFallbackReaders.readLocomoQaForm();
assert(draftFallbackForm.mode === "selected", "LoCoMo form reader must fall back to draft mode when the DOM field is unmounted");
assert(draftFallbackForm.question_ids === "conv-30_q7,conv-30_q8", "LoCoMo form reader must fall back to draft selected ids when the DOM field is unmounted");
assert(draftFallbackForm.wrong_csv === "/tmp/locomo-run/wrong_questions_brief.csv", "LoCoMo form reader must fall back to draft wrong_csv when the DOM field is unmounted");

const stateFallbackReaders = createFormReaders({
  $: () => null,
  queryAll: () => [],
  currentBenchmark: () => ({ defaultData: "/tmp/locomo.json" }),
  currentWorkspace: () => "/tmp/locomo-payload-smoke",
  state: {
    activeStage: "qa",
    locomoQaDraft: {},
    locomoSelectedQuestions: new Set(["conv-30_q11", "conv-30_q12"]),
    locomoWrongCsv: "/tmp/locomo-run/from-state.csv",
  },
});
const stateFallbackForm = stateFallbackReaders.readLocomoQaForm();
assert(stateFallbackForm.question_ids === "conv-30_q11,conv-30_q12", "LoCoMo form reader must fall back to selected-question state when draft ids are empty");
assert(stateFallbackForm.wrong_csv === "/tmp/locomo-run/from-state.csv", "LoCoMo form reader must fall back to state wrong_csv when the DOM field is unmounted");

const selectedSetup = makeDeps({ backend: "echomemory" });
await createLocomoActions({
  ...selectedSetup.deps,
  formReaders: {
    ...selectedSetup.deps.formReaders,
    readLocomoQaForm: () => ({
      ...selectedSetup.deps.formReaders.readLocomoQaForm(),
      mode: "selected",
      question_ids: "conv-30_q9,conv-30_q10",
    }),
  },
}).startSelectedQa();
const selectedPayload = selectedSetup.calls.at(-1).payload;
assert(selectedPayload.questions === "conv-30_q9,conv-30_q10", "LoCoMo selected QA must preserve explicit question ids");
assert(selectedPayload.name.includes("指定题重跑"), "LoCoMo selected QA must use selected-run naming");

const wrongCsvSetup = makeDeps({ backend: "echomemory" });
await createLocomoActions({
  ...wrongCsvSetup.deps,
  formReaders: {
    ...wrongCsvSetup.deps.formReaders,
    readLocomoQaForm: () => ({
      ...wrongCsvSetup.deps.formReaders.readLocomoQaForm(),
      mode: "wrong_csv",
      wrong_csv: "/tmp/locomo-run/wrong_questions_brief.csv",
    }),
  },
}).startWrongCsvQa();
const wrongCsvPayload = wrongCsvSetup.calls.at(-1).payload;
assert(wrongCsvPayload.questions === "conv-30_q1,conv-30_q2", "LoCoMo wrong_csv QA must use /api/question-set ids");
assert(wrongCsvPayload.name.includes("错题 CSV 重跑"), "LoCoMo wrong_csv QA must use wrong-csv naming");

const wrongCsvFallbackSetup = makeDeps({ backend: "echomemory" });
await createLocomoActions({
  ...wrongCsvFallbackSetup.deps,
  formReaders: {
    ...wrongCsvFallbackSetup.deps.formReaders,
    readLocomoQaForm: () => ({
      ...wrongCsvFallbackSetup.deps.formReaders.readLocomoQaForm(),
      mode: "wrong_csv",
      wrong_csv: "",
    }),
  },
}).startWrongCsvQa();
const wrongCsvFallbackPayload = wrongCsvFallbackSetup.calls.at(-1).payload;
assert(
  wrongCsvFallbackPayload.wrong_csv === "/tmp/locomo-run/echomemory_qa/wrong_questions_brief.csv",
  "LoCoMo wrong_csv QA must fall back to the current run's wrong_questions_brief.csv when the form field is empty",
);

const retryFailedEchoSetup = makeDeps({ backend: "echomemory" });
await createLocomoActions(retryFailedEchoSetup.deps).retryFailedQa();
const retryFailedEchoPayload = retryFailedEchoSetup.calls.at(-1).payload;
assert(retryFailedEchoPayload.kind === "echomemory_qa_retry_failed", "EchoMemory retry_failed must use echomemory retry task kind");
assert(retryFailedEchoPayload.input.endsWith("echomemory_memory_qa_results.csv"), "EchoMemory retry_failed must target the current output file");
assert(retryFailedEchoPayload.echomem_config === "/tmp/echomem.config.json", "EchoMemory retry_failed must preserve run-scoped echomem config");
assert(retryFailedEchoPayload.qa_memory_injection === true, "EchoMemory retry_failed must keep LoCoMo QA memory injection fixed on");
assert(retryFailedEchoPayload.evidence_policy === "blackbox", "EchoMemory retry_failed must preserve HTTP black-box evidence policy");
assert(retryFailedEchoPayload.retrieval_source_mode === "echo_http_native", "EchoMemory retry_failed must preserve native HTTP source selection");
assert(retryFailedEchoPayload.search_overview_enrichment === undefined, "EchoMemory retry_failed must not expose overview enrichment");
assert(retryFailedEchoPayload.current_session_raw_fallback === undefined, "EchoMemory retry_failed must not expose raw fallback");
assert(retryFailedEchoPayload.precision_session_readback === undefined, "EchoMemory retry_failed must not expose precision readback");
assert(retryFailedEchoPayload.precision_grounded_projection === undefined, "EchoMemory retry_failed must not expose grounded projection");
assert(retryFailedEchoPayload.initial_tool_prefetch === false, "EchoMemory retry_failed must keep initial tool prefetch fixed off");
assert(retryFailedEchoPayload.model_retries === 7, "EchoMemory retry_failed must preserve run-scoped model retries");

const retryFailedOvSetup = makeDeps({ backend: "openviking" });
await createLocomoActions(retryFailedOvSetup.deps).retryFailedQa();
const retryFailedOvPayload = retryFailedOvSetup.calls.at(-1).payload;
assert(retryFailedOvPayload.kind === "openviking_qa_retry_failed", "OpenViking retry_failed must use openviking retry task kind");
assert(retryFailedOvPayload.input.endsWith("openviking_memory_qa_results.csv"), "OpenViking retry_failed must target the current output file");

const retryMissingEchoSetup = makeDeps({ backend: "echomemory" });
await createLocomoActions(retryMissingEchoSetup.deps).retryMissingQa();
const retryMissingEchoPayload = retryMissingEchoSetup.calls.at(-1).payload;
assert(retryMissingEchoPayload.kind === "echomemory_qa_retry_missing", "EchoMemory retry_missing must use dedicated retry_missing task kind");
assert(retryMissingEchoPayload.questions === "conv-30_q3,conv-30_q4", "EchoMemory retry_missing must use missing question ids");
assert(retryMissingEchoPayload.question_ids === "conv-30_q3,conv-30_q4", "EchoMemory retry_missing must preserve missing question ids");
assert(retryMissingEchoPayload.echomem_config === "/tmp/echomem.config.json", "EchoMemory retry_missing must preserve run-scoped echomem config");
assert(retryMissingEchoPayload.qa_memory_injection === true, "EchoMemory retry_missing must keep LoCoMo QA memory injection fixed on");
assert(retryMissingEchoPayload.evidence_policy === "blackbox", "EchoMemory retry_missing must preserve HTTP black-box evidence policy");
assert(retryMissingEchoPayload.retrieval_source_mode === "echo_http_native", "EchoMemory retry_missing must preserve native HTTP source selection");
assert(retryMissingEchoPayload.search_overview_enrichment === undefined, "EchoMemory retry_missing must not expose overview enrichment");
assert(retryMissingEchoPayload.current_session_raw_fallback === undefined, "EchoMemory retry_missing must not expose raw fallback");
assert(retryMissingEchoPayload.precision_session_readback === undefined, "EchoMemory retry_missing must not expose precision readback");
assert(retryMissingEchoPayload.precision_grounded_projection === undefined, "EchoMemory retry_missing must not expose grounded projection");

const retryMissingOvSetup = makeDeps({ backend: "openviking" });
await createLocomoActions(retryMissingOvSetup.deps).retryMissingQa();
const retryMissingOvPayload = retryMissingOvSetup.calls.at(-1).payload;
assert(retryMissingOvPayload.kind === "openviking_qa_retry_missing", "OpenViking retry_missing must use dedicated retry_missing task kind");
assert(retryMissingOvPayload.question_ids === "conv-30_q3,conv-30_q4", "OpenViking retry_missing must preserve missing question ids");

const timeSetup = makeDeps({ backend: "echomemory" });
timeSetup.deps.formReaders.readLocomoQaForm = () => ({
  data: "/tmp/locomo.json",
  sample: "conv-30",
  mode: "time",
  top_k: 30,
  use_tools: true,
  tool_loop: false,
  tool_search_limit: 20,
  max_iterations: 50,
  workspace: "/tmp/locomo-payload-smoke",
});
await createLocomoActions(timeSetup.deps).startQa();
const timePayload = timeSetup.calls.at(-1).payload;
assert(timePayload.questions === "conv-30_q1,conv-30_q2", "LoCoMo time-mode QA must use /api/question-set ids");
assert(timePayload.name.includes("时间题 QA 2 题"), "LoCoMo time-mode QA must name the selected question count");

const ovSetup = makeDeps({ backend: "openviking" });
await createLocomoActions(ovSetup.deps).startImport();
assert(ovSetup.calls.at(-1).payload.kind === "openviking_import", "LoCoMo OpenViking import must use openviking_import");
await createLocomoActions(ovSetup.deps).startQa();
const ovQaPayload = ovSetup.calls.at(-1).payload;
assert(ovQaPayload.kind === "openviking_qa", "LoCoMo OpenViking QA must use openviking_qa");
assert(ovQaPayload.ov_user_id === "mem-user", "LoCoMo OpenViking QA must include provider user id");
assert(ovQaPayload.ov_agent_id === "mem-agent", "LoCoMo OpenViking QA must include provider agent id");

const workflowSetup = makeDeps({ backend: "echomemory", activeStage: "judge" });
workflowSetup.deps.formReaders.readJudgeForm = () => ({ data: "/tmp/wrong-from-dom.json" });
const workflow = createWorkflowActions(workflowSetup.deps);
const judgePreflight = await createLocomoActions(workflowSetup.deps).preflightJudge({ currentRun: workflowSetup.deps.currentRun });
assert(judgePreflight.ok === true, "LoCoMo judge preflight must pass for the default smoke run");
assert(judgePreflight.checks.some((item) => item.name === "judge_model_probe" && item.ok === true), "LoCoMo judge preflight must include a live judge-model probe check");
await workflow.runJudge(true);
const judgePayload = workflowSetup.calls.at(-1).payload;
assert(judgePayload.kind === "judge", "LoCoMo judge must submit judge task");
assert(judgePayload.input.endsWith("echomemory_memory_qa_results.csv"), "LoCoMo judge must score current run output");
assert(judgePayload.data === "/tmp/locomo.json", "LoCoMo judge must use the selected run dataset path as source of truth");
assert(judgePayload.only_pending === true, "LoCoMo smoke judge must only score pending rows");
assert(judgePayload.row_indexes === "2,7", "LoCoMo smoke judge must preserve pending row indexes");
assert(judgePayload.judge_base_url === "https://judge.example/v1", "LoCoMo judge must preserve judge base URL");
assert(judgePayload.judge_model === "judge-model", "LoCoMo judge must preserve judge model");

const reportModel = await workflow.exportReport();
assert(reportModel.title === "报告已生成", "LoCoMo report export must use /api/report result");
assert(reportModel.path === "/tmp/report.html", "LoCoMo report export must expose generated HTML path");

const diagnosticsRun = {
  run_dir: "/tmp/locomo-run",
  output_file: "/tmp/locomo-run/echomemory_qa/echomemory_memory_qa_results.csv",
  dataset_path: "/tmp/locomo.json",
  sample: "conv-30",
};
const keyedDiagnosticsState = {
  activeBenchmark: "locomo",
  activeStage: "qa",
  selectedAccount: "payload-smoke",
  locomoQaDraft: {},
  locomoSelectedQuestions: new Set(),
  locomoWrongCsv: "/tmp/legacy.csv",
  officialQaGates: {},
  officialJudgePreflights: {},
  qaDiagnosticsCache: {
    [`${diagnosticsRun.output_file}::/tmp/locomo.json::conv-30`]: {
      retryable_failed_question_ids: ["conv-30_q21", "conv-30_q22"],
      missing_question_ids: ["conv-30_q31"],
    },
  },
  runDetails: {
    [diagnosticsRun.run_dir]: {
      record: {
        dataset_path: "/tmp/locomo.json",
        sample: "conv-30",
      },
    },
  },
  runConfigSnapshots: {
    [diagnosticsRun.run_dir]: {
      data: "/tmp/locomo.json",
      sample: "conv-30",
    },
  },
};
const eventHandlers = {};
class FakeElement {
  constructor(id = "") {
    this.id = id;
  }

  closest() {
    return this;
  }

  matches(selector) {
    return selector === `#${this.id}` || selector.includes(this.id);
  }
}
globalThis.Element = FakeElement;
const noopNode = () => ({
  addEventListener: () => {},
  classList: { toggle: () => {} },
  dataset: {},
  children: [],
  textContent: "",
});
const domNodes = {
  wbQaQuestionIds: { value: "", addEventListener: () => {} },
  wbQaWrongCsv: { value: "", addEventListener: () => {} },
  wbQaMode: { value: "full", addEventListener: () => {} },
  wbRunQaSelected: { disabled: true, title: "", removeAttribute(name) { if (name === "title") this.title = ""; } },
  wbRunQaWrongCsv: { disabled: true, title: "", removeAttribute(name) { if (name === "title") this.title = ""; } },
  wbRunQaCurrentScope: { disabled: false, title: "", removeAttribute(name) { if (name === "title") this.title = ""; } },
  wbRefreshAll: noopNode(),
  wbOpenLegacy: noopNode(),
  wbRunPrimary: { ...noopNode(), children: [], dataset: {} },
  wbStopTasks: noopNode(),
  wbShell: { dataset: {} },
};
let renderQaConfigCount = 0;
let renderQaPreviewCount = 0;
const controller = createWorkbenchController({
  $: (id) => domNodes[id] || noopNode(),
  alertUser: (message) => { throw new Error(`unexpected alert: ${message}`); },
  actions: {
    ensureQuestions: async () => null,
  },
  copyText: async () => {},
  currentBenchmark: () => ({ id: "locomo", stageLabels: {}, importLabel: "导入", primaryRunLabel: "运行" }),
  currentRun: () => diagnosticsRun,
  defaultBenchmarkId: "locomo",
  legacyReferenceUrl: "",
  onDocument: (type, handler) => { eventHandlers[type] = handler; },
  openReferenceUrl: () => {},
  prefetchLimitForBenchmark: () => 0,
  queryAll: () => [],
  renderQaConfig: () => { renderQaConfigCount += 1; },
  renderQaPreview: () => { renderQaPreviewCount += 1; },
  renderAll: () => {},
  renderReportExportResult: () => {},
  state: keyedDiagnosticsState,
  tasksForBenchmark: () => [],
});
controller.bindEvents({
  refreshAllRunner: async () => {},
  pollLogRunner: async () => {},
});
await eventHandlers.click({
  target: new FakeElement("wbLoadFailedToSelected"),
});
assert(
  [...keyedDiagnosticsState.locomoSelectedQuestions].join(",") === "conv-30_q21,conv-30_q22",
  "LoCoMo diagnostics->selected must read retryable failed ids from the scoped diagnostics cache key",
);
assert(keyedDiagnosticsState.locomoQaDraft.wbQaMode === "selected", "LoCoMo diagnostics->selected must switch QA mode to selected");
assert(keyedDiagnosticsState.locomoQaDraft.wbQaQuestionIds === "conv-30_q21,conv-30_q22", "LoCoMo diagnostics->selected must sync selected ids into the QA draft");
assert(keyedDiagnosticsState.locomoWrongCsv === "", "LoCoMo diagnostics->selected must clear stale wrong_csv state");
assert(renderQaConfigCount > 0 && renderQaPreviewCount > 0, "LoCoMo diagnostics->selected must rerender QA config and preview");

let recallRefreshCount = 0;
keyedDiagnosticsState.locomoRecallPreview = null;
keyedDiagnosticsState.locomoRecallSelection = {};
domNodes.wbRecallQuestion = {
  id: "wbRecallQuestion",
  value: "7",
  selectedOptions: [{ dataset: { questionId: "conv-30_q31" } }],
  matches(selector) {
    return selector === "#wbRecallQuestion" || String(selector || "").includes("wbRecallQuestion");
  },
  closest() {
    return this;
  },
};
const recallController = createWorkbenchController({
  $: (id) => domNodes[id] || noopNode(),
  alertUser: (message) => { throw new Error(`unexpected alert: ${message}`); },
  actions: {
    ensureQuestions: async () => null,
    refreshLocomoRecallDetail: async () => { recallRefreshCount += 1; },
  },
  copyText: async () => {},
  currentBenchmark: () => ({ id: "locomo", stageLabels: {}, importLabel: "导入", primaryRunLabel: "运行" }),
  currentRun: () => diagnosticsRun,
  defaultBenchmarkId: "locomo",
  legacyReferenceUrl: "",
  onDocument: (type, handler) => { eventHandlers[`recall-${type}`] = handler; },
  openReferenceUrl: () => {},
  prefetchLimitForBenchmark: () => 0,
  queryAll: () => [],
  renderQaConfig: () => {},
  renderQaPreview: () => {},
  renderAll: () => {},
  renderReportExportResult: () => {},
  state: keyedDiagnosticsState,
  tasksForBenchmark: () => [],
});
recallController.bindEvents({
  refreshAllRunner: async () => {},
  pollLogRunner: async () => {},
});
await eventHandlers["recall-change"]({
  target: domNodes.wbRecallQuestion,
});
assert(recallRefreshCount === 1, "LoCoMo recall question change must trigger recall detail refresh");
assert(
  keyedDiagnosticsState.locomoRecallSelection.questionId === "conv-30_q31",
  "LoCoMo recall question change must preserve selected scoped question id",
);
assert(
  keyedDiagnosticsState.locomoRecallSelection.index === "7",
  "LoCoMo recall question change must read row index from the scoped diagnostics trace preview",
);

const runtimeRunOutput = "/tmp/locomo-run/echomemory_qa/echomemory_memory_qa_results.csv";
const runtimeState = {
  runDetails: {
    "/tmp/locomo-run": {
      record: {
        summary: {
          rows: 1,
          graded: 0,
          result_counts: { UNSCORED: 1 },
        },
      },
    },
  },
  resultSummaries: {
    [runtimeRunOutput]: {
      summary: {
        rows: 1,
        graded: 0,
        result_counts: { UNSCORED: 1 },
      },
    },
  },
  runConfigSnapshots: {},
};
const runtimeCalls = [];
const runtimeRun = {
  run_dir: "/tmp/locomo-run",
  output_file: runtimeRunOutput,
  kind: "echomemory_qa",
  status: "succeeded",
};
const runtimeActions = createRuntimeActions({
  api: async (path) => {
    runtimeCalls.push(path);
    if (path.startsWith("/api/run-detail")) {
      return {
        record: {
          summary: {
            rows: 1,
            graded: 1,
            correct: 0,
            wrong: 1,
            result_counts: { WRONG: 1, UNSCORED: 0 },
          },
        },
      };
    }
    if (path.startsWith("/api/results")) {
      return {
        summary: {
          rows: 1,
          graded: 1,
          correct: 0,
          wrong: 1,
          result_counts: { WRONG: 1, UNSCORED: 0 },
        },
      };
    }
    if (path.startsWith("/api/config-snapshot")) {
      return { config: {} };
    }
    throw new Error(`unexpected runtime API path: ${path}`);
  },
  currentBenchmark: () => ({ id: "locomo" }),
  currentRun: () => runtimeRun,
  formReaders: {
    readQuestionPreviewScope: () => ({ path: "/tmp/locomo.json", sample: "conv-30" }),
  },
  onBootstrapState: () => {},
  prefetchLimitForBenchmark: () => 1,
  preferredRunForBenchmark: () => runtimeRun,
  runsForBenchmark: () => [runtimeRun],
  state: runtimeState,
});
await runtimeActions.ensureRunDetail(runtimeRun);
assert(runtimeCalls.some((path) => path.startsWith("/api/run-detail")), "LoCoMo runtime must refetch run detail when cached summary is still pending after terminal success");
assert(runtimeCalls.some((path) => path.startsWith("/api/results")), "LoCoMo runtime must refetch results when cached summary is still pending after terminal success");
runtimeCalls.length = 0;
await runtimeActions.ensureRunDetail(runtimeRun);
assert(!runtimeCalls.some((path) => path.startsWith("/api/run-detail")), "LoCoMo runtime must stop refetching once judge-enriched summary is current");
assert(!runtimeCalls.some((path) => path.startsWith("/api/results")), "LoCoMo runtime must stop refetching results once pending is cleared");

const interruptedRuntimeCalls = [];
const interruptedRuntimeRun = {
  run_dir: "/tmp/locomo-run-interrupted",
  output_file: "/tmp/locomo-run-interrupted/echomemory_qa/echomemory_memory_qa_results.csv",
  kind: "echomemory_qa",
  status: "interrupted",
  dataset_format: "locomo",
  sample: "conv-30",
};
const interruptedRuntimeState = {
  resultSummaries: {
    [interruptedRuntimeRun.output_file]: {
      summary: {
        rows: 16,
        graded: 0,
        correct: 0,
        wrong: 0,
        result_counts: { UNSCORED: 16 },
        samples: {
          "conv-30": { CORRECT: 0, WRONG: 0, UNSCORED: 16 },
        },
      },
    },
  },
  runDetails: {
    [interruptedRuntimeRun.run_dir]: {
      record: {
        sample: "conv-30",
        summary: {
          rows: 16,
          graded: 0,
          correct: 0,
          wrong: 0,
          result_counts: { UNSCORED: 16 },
          samples: {
            "conv-30": { CORRECT: 0, WRONG: 0, UNSCORED: 16 },
          },
        },
      },
    },
  },
  runConfigSnapshots: {},
};
const interruptedRuntimeActions = createRuntimeActions({
  api: async (path) => {
    interruptedRuntimeCalls.push(path);
    if (path.startsWith("/api/run-detail")) {
      return {
        record: {
          sample: "conv-30",
          summary: {
            rows: 16,
            graded: 16,
            correct: 6,
            wrong: 10,
            result_counts: { CORRECT: 6, WRONG: 10, UNSCORED: 0 },
            samples: {
              "conv-30": { CORRECT: 6, WRONG: 10, UNSCORED: 0 },
            },
          },
        },
      };
    }
    if (path.startsWith("/api/results")) {
      return {
        summary: {
          rows: 16,
          graded: 16,
          correct: 6,
          wrong: 10,
          result_counts: { CORRECT: 6, WRONG: 10, UNSCORED: 0 },
          samples: {
            "conv-30": { CORRECT: 6, WRONG: 10, UNSCORED: 0 },
          },
        },
      };
    }
    if (path.startsWith("/api/config-snapshot")) {
      return { config: {} };
    }
    throw new Error(`unexpected interrupted runtime API path: ${path}`);
  },
  currentBenchmark: () => ({ id: "locomo" }),
  currentRun: () => interruptedRuntimeRun,
  formReaders: {
    readQuestionPreviewScope: () => ({ path: "/tmp/locomo.json", sample: "conv-30" }),
  },
  onBootstrapState: () => {},
  prefetchLimitForBenchmark: () => 1,
  preferredRunForBenchmark: () => interruptedRuntimeRun,
  runsForBenchmark: () => [interruptedRuntimeRun],
  state: interruptedRuntimeState,
});
await interruptedRuntimeActions.ensureRunDetail(interruptedRuntimeRun);
assert(interruptedRuntimeCalls.some((path) => path.startsWith("/api/results")), "Interrupted LoCoMo run must refetch results when cached summary still has pending rows");
assert(interruptedRuntimeState.resultSummaries[interruptedRuntimeRun.output_file]?.summary?.result_counts?.UNSCORED === 0, "Interrupted LoCoMo run must refresh judged result summary");

await workflow.stopAllTasks();
const stopCall = workflowSetup.calls.at(-1);
assert(stopCall.path === "/api/tasks/stop-all", "stop action must call stop-all endpoint");
assert(stopCall.payload.scope === "all", "stop action must stop all active tasks");

console.log("locomo payload smoke passed");

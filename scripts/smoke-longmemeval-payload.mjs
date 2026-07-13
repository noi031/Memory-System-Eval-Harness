#!/usr/bin/env node
import { createLongMemEvalActions } from "../src/action/longmemeval.js";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function firstValue(...values) {
  return values.find((value) => value !== undefined && value !== null && String(value).trim() !== "") || "";
}

async function captureLongMemEvalPayload({ backend = "echomemory" } = {}) {
  let captured = null;
  const actions = createLongMemEvalActions({
    api: async (path, options) => {
      if (path.startsWith("/api/question-set")) {
        return { question_ids: ["lme_wrong_1", "lme_wrong_2"] };
      }
      assert(path === "/api/tasks", `unexpected API path: ${path}`);
      captured = JSON.parse(options.body);
      return { ok: true };
    },
    backendId: () => backend,
    currentAccountConfig: () => ({
      account: "longmem-payload-smoke",
      memoryWorkspace: "/tmp/longmem-payload-smoke",
      longMemEvalPromptMode: "vikingboat_lite",
      longMemEvalRetrievalMode: "search",
      longMemEvalToolSet: "vikingbot_native_safe",
      longMemEvalToolMinScore: "0.35",
      longMemEvalQuestionTimeout: "180",
      answerToken: "answer-test",
      judgeToken: "judge-test",
      agentToken: "agent-test",
      memoryInjectBaseUrl: "https://memory.example/v1",
      memoryInjectModel: "memory-model",
      memoryInjectToken: "memory-test",
    }),
    currentWorkspace: () => "/tmp/longmem-payload-smoke",
    firstValue,
    formReaders: {
      readLongMemEvalImportForm: () => ({
        data: "/tmp/longmemeval.json",
        count: 10,
        workspace: "/tmp/longmem-payload-smoke",
      }),
      readLongMemEvalQaForm: () => ({
        data: "/tmp/longmemeval.json",
        count: 10,
        top_k: 20,
        use_tools: true,
        official_eval_after: true,
        tool_search_limit: 12,
        max_iterations: 9,
        retrieval_mode: "search",
        tool_set: "vikingbot_native_safe",
        question_timeout_s: 180,
        workspace: "/tmp/longmem-payload-smoke",
      }),
    },
    genericQaKind: () => (backend === "openviking" ? "openviking_generic_qa" : "echomemory_generic_qa"),
    loadQaDiagnostics: async () => ({
      retryable_failed_question_ids: ["lme_retry_1", "lme_retry_2"],
      missing_question_ids: ["lme_missing_1", "lme_missing_2"],
      retryable_failed_questions: 2,
      missing_questions_count: 2,
    }),
    state: {
      selectedAccount: "longmem-payload-smoke",
      accountDetails: { config: {} },
      runDetails: {
        "/tmp/longmemeval-run": {
          record: {
            dataset_path: "/tmp/longmemeval.json",
            sample: "all",
          },
        },
      },
      runConfigSnapshots: {
        "/tmp/longmemeval-run": {
          config: {
            data: "/tmp/longmemeval.json",
            sample: "all",
            workspace: "/tmp/longmem-payload-smoke",
            account: "longmem-payload-smoke",
            top_k: 20,
            tool_search_limit: 12,
            max_iterations: 9,
            retrieval_mode: "search",
            tool_set: "vikingbot_native_safe",
            question_timeout_s: 180,
            official_eval_after: true,
            qa_parallelism: 10,
            em_user_id: "longmem-payload-smoke-user",
            em_agent_id: "longmem-payload-smoke-agent",
          },
        },
      },
    },
  });
  await actions.startQa();
  assert(captured, "LongMemEval QA action did not submit a payload");
  return captured;
}

async function captureLongMemEvalSelectedPayload({ backend = "echomemory", questionIds = "lme_selected_1,lme_selected_2" } = {}) {
  let captured = null;
  const actions = createLongMemEvalActions({
    api: async (path, options) => {
      assert(path === "/api/tasks", `unexpected API path: ${path}`);
      captured = JSON.parse(options.body);
      return { ok: true };
    },
    backendId: () => backend,
    currentAccountConfig: () => ({
      account: "longmem-payload-smoke",
      memoryWorkspace: "/tmp/longmem-payload-smoke",
      longMemEvalPromptMode: "vikingboat_lite",
      longMemEvalRetrievalMode: "search",
      longMemEvalToolSet: "vikingbot_native_safe",
      longMemEvalToolMinScore: "0.35",
      longMemEvalQuestionTimeout: "180",
    }),
    currentWorkspace: () => "/tmp/longmem-payload-smoke",
    firstValue,
    formReaders: {
      readLongMemEvalImportForm: () => ({
        data: "/tmp/longmemeval.json",
        count: 10,
        workspace: "/tmp/longmem-payload-smoke",
      }),
      readLongMemEvalQaForm: () => ({
        data: "/tmp/longmemeval.json",
        count: 10,
        mode: "selected",
        question_ids: questionIds,
        top_k: 20,
        use_tools: true,
        official_eval_after: true,
        tool_search_limit: 12,
        max_iterations: 9,
        retrieval_mode: "search",
        tool_set: "vikingbot_native_safe",
        question_timeout_s: 180,
        qa_parallelism: 10,
        workspace: "/tmp/longmem-payload-smoke",
      }),
    },
    genericQaKind: () => (backend === "openviking" ? "openviking_generic_qa" : "echomemory_generic_qa"),
    loadQaDiagnostics: async () => ({
      retryable_failed_question_ids: ["lme_retry_1", "lme_retry_2"],
      missing_question_ids: ["lme_missing_1", "lme_missing_2"],
      retryable_failed_questions: 2,
      missing_questions_count: 2,
    }),
    state: {
      selectedAccount: "longmem-payload-smoke",
      accountDetails: { config: {} },
    },
  });
  await actions.startQa();
  assert(captured, "LongMemEval selected QA action did not submit a payload");
  return captured;
}

function makeLongMemEvalPreflightDeps({ run = {}, summary = {}, formOverrides = {} } = {}) {
  const runDir = run.run_dir || "/tmp/longmemeval-run";
  const outputFile = run.output_file || "/tmp/longmemeval-run/echomemory_generic_qa_results.csv";
  return {
    api: async (path) => {
      throw new Error(`unexpected API path: ${path}`);
    },
    backendId: () => "echomemory",
    currentAccountConfig: () => ({
      account: "longmem-payload-smoke",
      memoryWorkspace: "/tmp/longmem-payload-smoke",
      longMemEvalPromptMode: "vikingboat_lite",
      answerToken: "answer-test",
      judgeToken: "judge-test",
    }),
    currentRun: () => ({
      run_dir: runDir,
      output_file: outputFile,
      kind: "echomemory_generic_qa",
      status: "succeeded",
      name: "longmemeval preflight smoke",
      ...run,
    }),
    currentWorkspace: () => "/tmp/longmem-payload-smoke",
    ensureRunDetail: async () => ({}),
    firstValue,
    formReaders: {
      readLongMemEvalImportForm: () => ({
        data: "/tmp/longmemeval.json",
        count: 10,
        workspace: "/tmp/longmem-payload-smoke",
      }),
      readLongMemEvalQaForm: () => ({
        data: "/tmp/longmemeval.json",
        count: 10,
        top_k: 20,
        use_tools: true,
        official_eval_after: true,
        tool_search_limit: 12,
        max_iterations: 9,
        retrieval_mode: "search",
        tool_set: "vikingbot_native_safe",
        question_timeout_s: 180,
        workspace: "/tmp/longmem-payload-smoke",
        ...formOverrides,
      }),
    },
    genericQaKind: () => "echomemory_generic_qa",
    state: {
      selectedAccount: "longmem-payload-smoke",
      accountDetails: { config: {} },
      runDetails: {
        [runDir]: {
          record: {
            dataset_path: "/tmp/longmemeval.json",
            summary,
          },
          artifact_status: {
            output_file: { path: outputFile, exists: true },
            longmemeval_official_summary: {
              path: "/tmp/longmemeval_official_summary.json",
              exists: Boolean(summary?.summary_json?.official_eval?.summary_path || summary?.longmemeval_official_summary_path),
            },
          },
        },
      },
      resultSummaries: {
        [outputFile]: { summary },
      },
    },
    validatePayload: async () => ({ ok: true, checks: [] }),
  };
}

async function captureLongMemEvalImportPayload({ backend = "echomemory" } = {}) {
  let captured = null;
  const actions = createLongMemEvalActions({
    api: async (path, options) => {
      assert(path === "/api/tasks", `unexpected API path: ${path}`);
      captured = JSON.parse(options.body);
      return { ok: true };
    },
    backendId: () => backend,
    currentAccountConfig: () => ({
      account: "longmem-payload-smoke",
      memoryWorkspace: "/tmp/longmem-payload-smoke",
      longMemEvalTopK: "8",
    }),
    currentWorkspace: () => "/tmp/longmem-payload-smoke",
    firstValue,
    formReaders: {
      readLongMemEvalImportForm: () => ({
        data: "/tmp/longmemeval.json",
        count: 10,
        workspace: "/tmp/longmem-payload-smoke",
      }),
      readLongMemEvalQaForm: () => {
        throw new Error("QA form should not be read during import payload smoke");
      },
    },
    genericQaKind: () => (backend === "openviking" ? "openviking_generic_qa" : "echomemory_generic_qa"),
    state: {
      selectedAccount: "longmem-payload-smoke",
      accountDetails: { config: {} },
    },
  });
  await actions.startImport();
  assert(captured, "LongMemEval import action did not submit a payload");
  return captured;
}

const echoPayload = await captureLongMemEvalPayload({ backend: "echomemory" });
assert(echoPayload.kind === "echomemory_generic_qa", "EchoMemory LongMemEval must use generic QA task kind");
assert(echoPayload.dataset_format === "longmemeval", "LongMemEval payload must set dataset_format");
assert(echoPayload.format === "longmemeval", "LongMemEval payload must set format");
assert(echoPayload.count === 10, "LongMemEval payload must preserve question count");
assert(echoPayload.top_k === 20, "LongMemEval top_k must reach action payload");
assert(echoPayload.tool_search_limit === 12, "LongMemEval tool search limit must reach action payload");
assert(echoPayload.max_iterations === 9, "LongMemEval max iterations must reach action payload");
assert(echoPayload.official_eval_after === true, "LongMemEval official eval switch must reach action payload");
assert(echoPayload.prompt_mode === "vikingboat_lite", "EchoMemory LongMemEval retrieval mode must use configured prompt mode");
assert(echoPayload.vikingboat_tool_loop === false, "LongMemEval must keep model tool loop disabled");
assert(echoPayload.answer_token === "answer-test", "LongMemEval EchoMemory payload must include account-scoped answer token");
assert(echoPayload.judge_token === "judge-test", "LongMemEval EchoMemory payload must include account-scoped judge token");
assert(echoPayload.vlm_api_key === "memory-test", "LongMemEval EchoMemory payload must include account-scoped memory inject token");
assert(echoPayload.memory_base_url === "https://memory.example/v1", "LongMemEval EchoMemory payload must include memory base url");
assert(echoPayload.memory_inject_model === "memory-model", "LongMemEval EchoMemory payload must include memory inject model");

const echoImportPayload = await captureLongMemEvalImportPayload({ backend: "echomemory" });
assert(echoImportPayload.kind === "echomemory_generic_qa", "EchoMemory LongMemEval import must use generic QA task kind");
assert(echoImportPayload.import_only === true, "LongMemEval import payload must set import_only");
assert(echoImportPayload.dataset_format === "longmemeval", "LongMemEval import payload must set dataset_format");
assert(echoImportPayload.official_eval_after === false, "LongMemEval import must not run official eval");

const openVikingPayload = await captureLongMemEvalPayload({ backend: "openviking" });
assert(openVikingPayload.kind === "openviking_generic_qa", "OpenViking LongMemEval must use generic QA task kind");
assert(openVikingPayload.dataset_format === "longmemeval", "OpenViking LongMemEval payload must set dataset_format");
assert(openVikingPayload.read_openviking_content === true, "OpenViking LongMemEval should request content reads");

const selectedPayload = await captureLongMemEvalSelectedPayload({ backend: "echomemory" });
assert(selectedPayload.questions === "lme_selected_1,lme_selected_2", "LongMemEval selected QA must forward question ids");
assert(selectedPayload.count === 0, "LongMemEval selected QA must clear count for question-scoped execution");
assert(selectedPayload.name === "longmemeval selected 2q", "LongMemEval selected QA must set selected run name");

const gateWithActiveTask = await createLongMemEvalActions({
  ...makeLongMemEvalPreflightDeps(),
  tasksForBenchmark: () => [{ id: "long-active", name: "long active task", status: "running" }],
}).preflightQa();
assert(gateWithActiveTask.ok === false, "LongMemEval launch gate must fail when an active LongMemEval task exists");
assert(gateWithActiveTask.checks.some((item) => item.name === "active_task" && item.ok === false), "LongMemEval launch gate must surface active task conflicts");

const selectedGateMissingIds = await createLongMemEvalActions(makeLongMemEvalPreflightDeps({
  formOverrides: {
    mode: "selected",
    question_ids: "",
  },
})).preflightQa();
assert(selectedGateMissingIds.ok === false, "LongMemEval selected launch gate must fail when question ids are missing");
assert(selectedGateMissingIds.checks.some((item) => item.name === "question_set" && item.ok === false), "LongMemEval selected launch gate must flag missing question ids");

const judgeReady = await createLongMemEvalActions(makeLongMemEvalPreflightDeps({
  summary: {
    rows: 4,
    summary_json: {
      official_eval_after: true,
      official_eval: {
        summary: { graded: 4, overall_accuracy: 0.5, task_averaged_accuracy: 0.25 },
        summary_path: "/tmp/longmemeval_official_summary.json",
      },
    },
  },
})).preflightJudge();
assert(judgeReady.ok === true, "LongMemEval judge preflight must pass when official summary is ready");
assert(judgeReady.officialSummaryReady === true, "LongMemEval judge preflight must expose official summary readiness");

const judgeMissingOfficial = await createLongMemEvalActions(makeLongMemEvalPreflightDeps()).preflightJudge();
assert(judgeMissingOfficial.ok === false, "LongMemEval judge preflight must fail when official summary is missing");
assert(judgeMissingOfficial.checks.some((item) => item.name === "official_summary_status" && item.ok === false), "LongMemEval judge preflight must flag missing official summary");

const judgeRunning = await createLongMemEvalActions(makeLongMemEvalPreflightDeps({
  run: { status: "running" },
  summary: {
    rows: 4,
    summary_json: {
      official_eval_after: true,
      official_eval: {
        summary: { graded: 4, overall_accuracy: 0.5, task_averaged_accuracy: 0.25 },
        summary_path: "/tmp/longmemeval_official_summary.json",
      },
    },
  },
})).preflightJudge();
assert(judgeRunning.ok === false, "LongMemEval judge preflight must fail while the task is still running");
assert(judgeRunning.checks.some((item) => item.name === "run_complete" && item.ok === false), "LongMemEval judge preflight must flag running tasks");

let followupPayload = await captureLongMemEvalPayload({ backend: "echomemory" });
assert(followupPayload.kind === "echomemory_generic_qa", "LongMemEval QA payload smoke must stay on generic QA");

const followupActions = createLongMemEvalActions({
  api: async (path, options) => {
    if (path.startsWith("/api/question-set")) {
      return { question_ids: ["lme_wrong_1", "lme_wrong_2"] };
    }
    assert(path === "/api/tasks", `unexpected API path: ${path}`);
    followupPayload = JSON.parse(options.body);
    return { ok: true };
  },
  backendId: () => "echomemory",
  currentAccountConfig: () => ({
    account: "longmem-payload-smoke",
    memoryWorkspace: "/tmp/longmem-payload-smoke",
    longMemEvalPromptMode: "vikingboat_lite",
    longMemEvalRetrievalMode: "search",
    longMemEvalToolSet: "vikingbot_native_safe",
    longMemEvalToolMinScore: "0.35",
    longMemEvalQuestionTimeout: "180",
  }),
  currentRun: () => ({
    run_dir: "/tmp/longmemeval-run",
    output_file: "/tmp/longmemeval-run/echomemory_generic_qa_results.csv",
    kind: "echomemory_generic_qa",
    status: "succeeded",
    dataset_path: "/tmp/longmemeval.json",
  }),
  currentWorkspace: () => "/tmp/longmem-payload-smoke",
  ensureRunDetail: async () => ({}),
  firstValue,
  formReaders: {
    readLongMemEvalImportForm: () => ({
      data: "/tmp/longmemeval.json",
      count: 10,
      workspace: "/tmp/longmem-payload-smoke",
    }),
    readLongMemEvalQaForm: () => ({
      data: "/tmp/longmemeval.json",
      count: 10,
      top_k: 20,
      use_tools: true,
      official_eval_after: true,
      tool_search_limit: 12,
      max_iterations: 9,
      retrieval_mode: "search",
      tool_set: "vikingbot_native_safe",
      question_timeout_s: 180,
      qa_parallelism: 10,
      workspace: "/tmp/longmem-payload-smoke",
    }),
  },
  genericQaKind: () => "echomemory_generic_qa",
  loadQaDiagnostics: async () => ({
    retryable_failed_question_ids: ["lme_retry_1", "lme_retry_2"],
    missing_question_ids: ["lme_missing_1", "lme_missing_2"],
    retryable_failed_questions: 2,
    missing_questions_count: 2,
  }),
  state: {
    selectedAccount: "longmem-payload-smoke",
    accountDetails: { config: {} },
    runDetails: {
      "/tmp/longmemeval-run": {
        record: {
          dataset_path: "/tmp/longmemeval.json",
          sample: "all",
        },
      },
    },
    runConfigSnapshots: {
      "/tmp/longmemeval-run": {
        config: {
          data: "/tmp/longmemeval.json",
          sample: "all",
          workspace: "/tmp/longmem-payload-smoke",
          account: "longmem-payload-smoke",
          top_k: 20,
          tool_search_limit: 12,
          max_iterations: 9,
          retrieval_mode: "search",
          tool_set: "vikingbot_native_safe",
          question_timeout_s: 180,
          official_eval_after: true,
          qa_parallelism: 10,
          em_user_id: "longmem-payload-smoke-user",
          em_agent_id: "longmem-payload-smoke-agent",
        },
      },
    },
  },
  validatePayload: async () => ({ ok: true, checks: [] }),
});

await followupActions.retryFailedQa();
assert(followupPayload.questions === "lme_retry_1,lme_retry_2", "LongMemEval retry-failed must submit scoped question ids");
assert(followupPayload.count === 0, "LongMemEval retry-failed must switch to question-scoped execution");
assert(followupPayload.name === "longmemeval retry failed 2q", "LongMemEval retry-failed must set the expected run name");

await followupActions.retryMissingQa();
assert(followupPayload.questions === "lme_missing_1,lme_missing_2", "LongMemEval retry-missing must submit scoped question ids");
assert(followupPayload.name === "longmemeval retry missing 2q", "LongMemEval retry-missing must set the expected run name");

await followupActions.startWrongCsvQa();
assert(followupPayload.questions === "lme_wrong_1,lme_wrong_2", "LongMemEval wrong-csv must submit ids returned by /api/question-set");
assert(followupPayload.name === "longmemeval wrong csv 2q", "LongMemEval wrong-csv must set the expected run name");

console.log("longmemeval payload smoke passed");

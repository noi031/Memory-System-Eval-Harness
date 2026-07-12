#!/usr/bin/env node
import { createHotpotQaActions } from "../src/action/hotpotqa.js";
import { summarizeBenchmarkRun } from "../src/run-metrics.js";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function firstValue(...values) {
  return values.find((value) => value !== undefined && value !== null && String(value).trim() !== "") || "";
}

async function captureHotpotQaPayload({ backend = "echomemory" } = {}) {
  let captured = null;
  const actions = createHotpotQaActions({
    api: async (path, options) => {
      assert(path === "/api/tasks", `unexpected API path: ${path}`);
      captured = JSON.parse(options.body);
      return { ok: true };
    },
    backendId: () => backend,
    currentAccountConfig: () => ({
      account: "payload-smoke",
      memoryWorkspace: "/tmp/payload-smoke",
      echomemQaPromptMode: "vikingboat_lite",
      echomemQaRetrievalMode: "search",
      echomemQaToolSet: "vikingbot_native_safe",
      echomemQaToolMinScore: "0.35",
      echomemQaQuestionTimeout: "600",
      answerToken: "answer-test",
      judgeToken: "judge-test",
      agentToken: "agent-test",
      memoryInjectBaseUrl: "https://memory.example/v1",
      memoryInjectModel: "memory-model",
      memoryInjectToken: "memory-test",
    }),
    currentWorkspace: () => "/tmp/payload-smoke",
    firstValue,
    formReaders: {
      readHotpotQaImportForm: () => ({
        data: "/tmp/hotpotqa.json",
        count: 10,
        workspace: "/tmp/payload-smoke",
      }),
      readHotpotQaQaForm: () => ({
        data: "/tmp/hotpotqa.json",
        count: 10,
        hotpotqa_corpus_mode: "global_sentence_corpus",
        hotpotqa_global_import_mode: "projection",
        checkpoint_interval: 5,
        top_k: 20,
        use_tools: true,
        official_eval_after: true,
        tool_search_limit: 12,
        max_iterations: 9,
        retrieval_mode: "search",
        tool_set: "vikingbot_native_safe",
        tool_min_score: 0.35,
        question_timeout_s: 180,
        workspace: "/tmp/payload-smoke",
      }),
    },
    genericQaKind: () => (backend === "openviking" ? "openviking_generic_qa" : "echomemory_generic_qa"),
    state: {
      selectedAccount: "payload-smoke",
      accountDetails: { config: {} },
    },
  });
  await actions.startQa();
  assert(captured, "HotpotQA QA action did not submit a payload");
  return captured;
}

async function captureHotpotQaSelectedPayload({ backend = "echomemory", questionIds = "hotpotqa_7,hotpotqa_8" } = {}) {
  let captured = null;
  const actions = createHotpotQaActions({
    api: async (path, options) => {
      assert(path === "/api/tasks", `unexpected API path: ${path}`);
      captured = JSON.parse(options.body);
      return { ok: true };
    },
    backendId: () => backend,
    currentAccountConfig: () => ({
      account: "payload-smoke",
      memoryWorkspace: "/tmp/payload-smoke",
      echomemQaPromptMode: "vikingboat_lite",
      echomemQaRetrievalMode: "search",
      echomemQaToolSet: "vikingbot_native_safe",
      echomemQaToolMinScore: "0.35",
      echomemQaQuestionTimeout: "600",
    }),
    currentWorkspace: () => "/tmp/payload-smoke",
    firstValue,
    formReaders: {
      readHotpotQaImportForm: () => ({
        data: "/tmp/hotpotqa.json",
        count: 10,
        workspace: "/tmp/payload-smoke",
      }),
      readHotpotQaQaForm: () => ({
        data: "/tmp/hotpotqa.json",
        count: 10,
        mode: "selected",
        question_ids: questionIds,
        hotpotqa_corpus_mode: "global_sentence_corpus",
        hotpotqa_global_import_mode: "projection",
        checkpoint_interval: 5,
        top_k: 20,
        use_tools: true,
        official_eval_after: true,
        tool_search_limit: 12,
        max_iterations: 9,
        retrieval_mode: "search",
        tool_set: "vikingbot_native_safe",
        tool_min_score: 0.35,
        question_timeout_s: 180,
        workspace: "/tmp/payload-smoke",
      }),
    },
    genericQaKind: () => (backend === "openviking" ? "openviking_generic_qa" : "echomemory_generic_qa"),
    state: {
      selectedAccount: "payload-smoke",
      accountDetails: { config: {} },
    },
  });
  await actions.startQa();
  assert(captured, "HotpotQA selected QA action did not submit a payload");
  return captured;
}

async function captureHotpotQaFollowupPayload({ action, diagnostics = {}, questionSet = null } = {}) {
  let captured = null;
  const run = {
    run_dir: "/tmp/hotpotqa-run",
    output_file: "/tmp/hotpotqa-run/echomemory_generic_qa_results.csv",
    kind: "echomemory_generic_qa",
    status: "succeeded",
    dataset_path: "/tmp/hotpotqa.json",
  };
  const actions = createHotpotQaActions({
    api: async (path, options = {}) => {
      if (path.startsWith("/api/qa-diagnostics?")) return diagnostics;
      if (path.startsWith("/api/question-set?")) return questionSet || { question_ids: [] };
      if (path === "/api/tasks") {
        captured = JSON.parse(options.body);
        return { ok: true };
      }
      throw new Error(`unexpected API path: ${path}`);
    },
    backendId: () => "echomemory",
    currentAccountConfig: () => ({
      account: "payload-smoke",
      memoryWorkspace: "/tmp/payload-smoke",
      echomemQaPromptMode: "vikingboat_lite",
      echomemQaRetrievalMode: "search",
      echomemQaToolSet: "vikingbot_native_safe",
      echomemQaToolMinScore: "0.35",
      echomemQaQuestionTimeout: "600",
    }),
    currentRun: () => run,
    currentWorkspace: () => "/tmp/payload-smoke",
    ensureRunDetail: async () => ({}),
    firstValue,
    formReaders: {
      readHotpotQaImportForm: () => ({
        data: "/tmp/hotpotqa.json",
        count: 10,
        workspace: "/tmp/payload-smoke",
      }),
      readHotpotQaQaForm: () => ({
        data: "/tmp/hotpotqa.json",
        count: 10,
        hotpotqa_corpus_mode: "global_sentence_corpus",
        hotpotqa_global_import_mode: "projection",
        checkpoint_interval: 5,
        top_k: 20,
        use_tools: true,
        official_eval_after: true,
        tool_search_limit: 12,
        max_iterations: 9,
        retrieval_mode: "search",
        tool_set: "vikingbot_native_safe",
        tool_min_score: 0.35,
        question_timeout_s: 180,
        workspace: "/tmp/payload-smoke",
      }),
    },
    genericQaKind: () => "echomemory_generic_qa",
    loadQaDiagnostics: async () => diagnostics,
    state: {
      selectedAccount: "payload-smoke",
      accountDetails: { config: {} },
      runDetails: {
        [run.run_dir]: {
          record: {
            dataset_path: "/tmp/hotpotqa.json",
            sample: "all",
          },
        },
      },
      runConfigSnapshots: {
        [run.run_dir]: {
          data: "/tmp/hotpotqa.json",
          workspace: "/tmp/payload-smoke",
          sample: "all",
        },
      },
    },
  });
  await actions[action]();
  assert(captured, `HotpotQA ${action} did not submit a payload`);
  return captured;
}

function makeHotpotQaPreflightDeps({ run = {}, summary = {}, formOverrides = {} } = {}) {
  const runDir = run.run_dir || "/tmp/hotpotqa-run";
  const outputFile = run.output_file || "/tmp/hotpotqa-run/echomemory_generic_qa_results.csv";
  return {
    api: async (path) => {
      throw new Error(`unexpected API path: ${path}`);
    },
    backendId: () => "echomemory",
    currentAccountConfig: () => ({
      account: "payload-smoke",
      memoryWorkspace: "/tmp/payload-smoke",
      echomemQaPromptMode: "vikingboat_lite",
      answerToken: "answer-test",
      judgeToken: "judge-test",
    }),
    currentRun: () => ({
      run_dir: runDir,
      output_file: outputFile,
      kind: "echomemory_generic_qa",
      status: "succeeded",
      name: "hotpotqa preflight smoke",
      ...run,
    }),
    currentWorkspace: () => "/tmp/payload-smoke",
    ensureRunDetail: async () => ({}),
    firstValue,
    formReaders: {
      readHotpotQaImportForm: () => ({
        data: "/tmp/hotpotqa.json",
        count: 10,
        workspace: "/tmp/payload-smoke",
      }),
      readHotpotQaQaForm: () => ({
        data: "/tmp/hotpotqa.json",
        count: 10,
        hotpotqa_corpus_mode: "global_sentence_corpus",
        hotpotqa_global_import_mode: "projection",
        checkpoint_interval: 5,
        top_k: 20,
        use_tools: true,
        official_eval_after: true,
        tool_search_limit: 12,
        max_iterations: 9,
        retrieval_mode: "search",
        tool_set: "vikingbot_native_safe",
        tool_min_score: 0.35,
        question_timeout_s: 180,
        workspace: "/tmp/payload-smoke",
        ...formOverrides,
      }),
    },
    genericQaKind: () => "echomemory_generic_qa",
    state: {
      selectedAccount: "payload-smoke",
      accountDetails: { config: {} },
      runDetails: {
        [runDir]: {
          record: {
            dataset_path: "/tmp/hotpotqa.json",
            summary,
          },
          artifact_status: {
            output_file: { path: outputFile, exists: true },
            hotpotqa_answer_summary: {
              path: "/tmp/hotpotqa_answer_summary.json",
              exists: Boolean(summary?.summary_json?.official_eval?.summary_path || summary?.hotpotqa_answer_summary_path),
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

async function captureHotpotQaImportPayload({ backend = "echomemory" } = {}) {
  let captured = null;
  const actions = createHotpotQaActions({
    api: async (path, options) => {
      assert(path === "/api/tasks", `unexpected API path: ${path}`);
      captured = JSON.parse(options.body);
      return { ok: true };
    },
    backendId: () => backend,
    currentAccountConfig: () => ({
      account: "payload-smoke",
      memoryWorkspace: "/tmp/payload-smoke",
      hotpotQaTopK: "8",
    }),
    currentWorkspace: () => "/tmp/payload-smoke",
    firstValue,
    formReaders: {
      readHotpotQaImportForm: () => ({
        data: "/tmp/hotpotqa.json",
        count: 10,
        hotpotqa_corpus_mode: "global_sentence_corpus",
        hotpotqa_global_import_mode: "projection",
        workspace: "/tmp/payload-smoke",
      }),
      readHotpotQaQaForm: () => {
        throw new Error("QA form should not be read during import payload smoke");
      },
    },
    genericQaKind: () => (backend === "openviking" ? "openviking_generic_qa" : "echomemory_generic_qa"),
    state: {
      selectedAccount: "payload-smoke",
      accountDetails: { config: {} },
    },
  });
  await actions.startImport();
  assert(captured, "HotpotQA import action did not submit a payload");
  return captured;
}

const echoPayload = await captureHotpotQaPayload({ backend: "echomemory" });
assert(echoPayload.kind === "echomemory_generic_qa", "EchoMemory HotpotQA must use generic QA task kind");
assert(echoPayload.dataset_format === "hotpotqa", "HotpotQA payload must set dataset_format");
assert(echoPayload.format === "hotpotqa", "HotpotQA payload must set format");
assert(echoPayload.count === 10, "HotpotQA payload must preserve question count");
assert(echoPayload.hotpotqa_corpus_mode === "global_sentence_corpus", "HotpotQA corpus mode must reach action payload");
assert(echoPayload.hotpotqa_global_import_mode === "projection", "HotpotQA global import mode must reach action payload");
assert(echoPayload.checkpoint_interval === 5, "HotpotQA checkpoint interval must reach action payload");
assert(echoPayload.top_k === 20, "HotpotQA top_k must reach action payload");
assert(echoPayload.tool_search_limit === 12, "HotpotQA tool search limit must reach action payload");
assert(echoPayload.max_iterations === 9, "HotpotQA max iterations must reach action payload");
assert(echoPayload.official_eval_after === true, "HotpotQA official eval switch must reach action payload");
assert(echoPayload.prompt_mode === "vikingboat_lite", "EchoMemory retrieval-enhanced mode must use configured prompt mode");
assert(echoPayload.vikingboat_tool_loop === false, "HotpotQA must keep model tool loop disabled");
assert(echoPayload.answer_token === "answer-test", "HotpotQA EchoMemory payload must include account-scoped answer token");
assert(echoPayload.judge_token === "judge-test", "HotpotQA EchoMemory payload must include account-scoped judge token");
assert(echoPayload.vlm_api_key === "memory-test", "HotpotQA EchoMemory payload must include account-scoped memory inject token");
assert(echoPayload.memory_base_url === "https://memory.example/v1", "HotpotQA EchoMemory payload must include memory base url");
assert(echoPayload.memory_inject_model === "memory-model", "HotpotQA EchoMemory payload must include memory inject model");

const echoImportPayload = await captureHotpotQaImportPayload({ backend: "echomemory" });
assert(echoImportPayload.kind === "echomemory_generic_qa", "EchoMemory HotpotQA import must use generic QA task kind");
assert(echoImportPayload.import_only === true, "HotpotQA import payload must set import_only");
assert(echoImportPayload.dataset_format === "hotpotqa", "HotpotQA import payload must set dataset_format");
assert(echoImportPayload.hotpotqa_corpus_mode === "global_sentence_corpus", "HotpotQA import corpus mode must reach action payload");
assert(echoImportPayload.hotpotqa_global_import_mode === "projection", "HotpotQA import mode must reach action payload");
assert(echoImportPayload.official_eval_after === false, "HotpotQA import must not run official eval");

const openVikingPayload = await captureHotpotQaPayload({ backend: "openviking" });
assert(openVikingPayload.kind === "openviking_generic_qa", "OpenViking HotpotQA must use generic QA task kind");
assert(openVikingPayload.dataset_format === "hotpotqa", "OpenViking HotpotQA payload must set dataset_format");
assert(openVikingPayload.read_openviking_content === true, "OpenViking HotpotQA should request content reads");
assert(!("hotpotqa_corpus_mode" in openVikingPayload), "OpenViking payload must not receive EchoMemory-only corpus mode");

const selectedPayload = await captureHotpotQaSelectedPayload({ backend: "echomemory" });
assert(selectedPayload.questions === "hotpotqa_7,hotpotqa_8", "HotpotQA selected QA must forward question ids");
assert(selectedPayload.count === 0, "HotpotQA selected QA must clear count for question-scoped execution");
assert(selectedPayload.name === "hotpotqa selected 2q", "HotpotQA selected QA must set selected run name");

const retryFailedPayload = await captureHotpotQaFollowupPayload({
  action: "retryFailedQa",
  diagnostics: {
    retryable_failed_question_ids: ["hotpotqa_1", "hotpotqa_2"],
  },
});
assert(retryFailedPayload.questions === "hotpotqa_1,hotpotqa_2", "HotpotQA retryFailedQa must forward question ids through generic QA payload");
assert(retryFailedPayload.count === 0, "HotpotQA retryFailedQa must clear count for question-scoped reruns");

const retryMissingPayload = await captureHotpotQaFollowupPayload({
  action: "retryMissingQa",
  diagnostics: {
    missing_question_ids: ["hotpotqa_3"],
  },
});
assert(retryMissingPayload.questions === "hotpotqa_3", "HotpotQA retryMissingQa must forward missing question ids through generic QA payload");

const wrongCsvPayload = await captureHotpotQaFollowupPayload({
  action: "startWrongCsvQa",
  questionSet: {
    question_ids: ["hotpotqa_4", "hotpotqa_5"],
  },
});
assert(wrongCsvPayload.questions === "hotpotqa_4,hotpotqa_5", "HotpotQA wrong_csv rerun must convert CSV ids into question-scoped generic QA payload");

const gateWithActiveTask = await createHotpotQaActions({
  ...makeHotpotQaPreflightDeps(),
  tasksForBenchmark: () => [{ id: "hotpot-active", name: "hotpot active task", status: "running" }],
}).preflightQa();
assert(gateWithActiveTask.ok === false, "HotpotQA launch gate must fail when an active HotpotQA task exists");
assert(gateWithActiveTask.checks.some((item) => item.name === "active_task" && item.ok === false), "HotpotQA launch gate must surface active task conflicts");

const selectedGateMissingIds = await createHotpotQaActions(makeHotpotQaPreflightDeps({
  formOverrides: {
    mode: "selected",
    question_ids: "",
  },
})).preflightQa();
assert(selectedGateMissingIds.ok === false, "HotpotQA selected launch gate must fail when question ids are missing");
assert(selectedGateMissingIds.checks.some((item) => item.name === "question_set" && item.ok === false), "HotpotQA selected launch gate must flag missing question ids");

const judgeReady = await createHotpotQaActions(makeHotpotQaPreflightDeps({
  summary: {
    rows: 5,
    summary_json: {
      official_eval_after: true,
      official_eval: {
        summary: { graded: 5, answer_f1: 0.6, joint_f1: 0.51 },
        summary_path: "/tmp/hotpotqa_answer_summary.json",
      },
    },
  },
})).preflightJudge();
assert(judgeReady.ok === true, "HotpotQA judge preflight must pass when official summary is ready");
assert(judgeReady.officialSummaryReady === true, "HotpotQA judge preflight must expose official summary readiness");

const judgeMissingOfficial = await createHotpotQaActions(makeHotpotQaPreflightDeps()).preflightJudge();
assert(judgeMissingOfficial.ok === false, "HotpotQA judge preflight must fail when official summary is missing");
assert(judgeMissingOfficial.checks.some((item) => item.name === "official_summary_status" && item.ok === false), "HotpotQA judge preflight must flag missing official summary");

const judgeRunning = await createHotpotQaActions(makeHotpotQaPreflightDeps({
  run: { status: "running" },
  summary: {
    rows: 5,
    summary_json: {
      official_eval_after: true,
      official_eval: {
        summary: { graded: 5, answer_f1: 0.6, joint_f1: 0.51 },
        summary_path: "/tmp/hotpotqa_answer_summary.json",
      },
    },
  },
})).preflightJudge();
assert(judgeRunning.ok === false, "HotpotQA judge preflight must fail while the task is still running");
assert(judgeRunning.checks.some((item) => item.name === "run_complete" && item.ok === false), "HotpotQA judge preflight must flag running tasks");

const summarizedHotpot = summarizeBenchmarkRun(
  "hotpotqa",
  {
    run_dir: "/tmp/hotpot-run",
    output_file: "/tmp/hotpot-run/results.csv",
    dataset_format: "hotpotqa",
    status: "succeeded",
    name: "hotpot rows smoke",
  },
  {
    record: {
      summary: {
        rows: 11,
        graded: 0,
        exact_match_reference: 0,
        result_counts: { UNSCORED: 11 },
      },
    },
  },
  {
    summary: {
      rows: 11,
      graded: 0,
      exact_match_reference: 0,
      result_counts: { UNSCORED: 11 },
    },
  }
);
assert(summarizedHotpot.rows === 11, "HotpotQA metrics must not coerce null precedence to 0 rows");
assert(summarizedHotpot.pending === 11, "HotpotQA pending count must preserve UNSCORED rows");

console.log("hotpotqa payload smoke passed");

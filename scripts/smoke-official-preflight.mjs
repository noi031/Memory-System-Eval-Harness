#!/usr/bin/env node
import { createHotpotQaActions } from "../src/action/hotpotqa.js";
import { createLongMemEvalActions } from "../src/action/longmemeval.js";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function baseDeps({ run, detail, result, form }) {
  return {
    api: async () => ({ ok: true }),
    backendId: () => "echomemory",
    currentAccountConfig: () => ({}),
    currentRun: () => run,
    currentWorkspace: () => "/tmp/workspace",
    ensureRunDetail: async () => ({ detail, result }),
    firstValue: (...values) => values.find((value) => value !== undefined && value !== null && String(value).trim() !== "") || "",
    formReaders: {
      readHotpotQaQaForm: () => form,
      readLongMemEvalQaForm: () => form,
    },
    genericQaKind: () => "echomemory_generic_qa",
    state: {
      runDetails: run?.run_dir ? { [run.run_dir]: detail } : {},
      resultSummaries: run?.output_file ? { [run.output_file]: result } : {},
      officialJudgePreflights: {},
    },
    validatePayload: async () => ({ ok: true, checks: [] }),
  };
}

const hotpotRun = {
  run_dir: "/tmp/hotpot-run",
  output_file: "/tmp/hotpot-run/echomemory_generic_qa_results.csv",
  status: "succeeded",
  name: "hotpot ready",
};
const hotpotDetail = {
  record: {
    dataset_path: "/tmp/hotpotqa.json",
  },
  artifact_status: {
    output_file: { path: hotpotRun.output_file, exists: true },
    hotpotqa_answer_summary: { path: "/tmp/hotpot-run/hotpotqa_answer_summary.json", exists: true },
  },
};
const hotpotResult = {
  summary: {
    rows: 20,
    graded: 0,
    official_answer_em: 0,
    official_answer_f1: 0,
    summary_json: {
      official_eval_after: true,
      official_eval: {
        summary_path: "/tmp/hotpot-run/hotpotqa_answer_summary.json",
        summary: {
          graded: 20,
          answer_em: 0,
          answer_f1: 0,
          joint_f1: 0,
        },
      },
    },
  },
};
const hotpotActions = createHotpotQaActions(baseDeps({
  run: hotpotRun,
  detail: hotpotDetail,
  result: hotpotResult,
  form: { data: "/tmp/hotpotqa.json", official_eval_after: true },
}));
const hotpotPreflight = await hotpotActions.preflightJudge();
assert(hotpotPreflight.ok === true, "HotpotQA preflight should pass when official summary exists and graded > 0 even if score is 0");
assert(hotpotPreflight.checks.find((item) => item.name === "official_summary_status")?.ok === true, "HotpotQA official summary check should pass for graded all-zero-score runs");

const longImportOnlyRun = {
  run_dir: "/tmp/long-run",
  output_file: "/tmp/long-run/merged/echomemory_generic_qa_results.csv",
  status: "succeeded",
  name: "longmemeval import test",
};
const longImportOnlyDetail = {
  record: {
    dataset_path: "/tmp/longmemeval.json",
  },
  artifact_status: {
    output_file: { path: longImportOnlyRun.output_file, exists: false },
    longmemeval_official_summary: { path: "/tmp/long-run/official_eval/longmemeval_official_summary.json", exists: false },
  },
};
const longImportOnlyResult = {
  summary: {
    rows: 0,
    summary_json: {
      import_only: true,
      official_eval_after: false,
      rows: 0,
    },
  },
};
const longImportOnlyActions = createLongMemEvalActions(baseDeps({
  run: longImportOnlyRun,
  detail: longImportOnlyDetail,
  result: longImportOnlyResult,
  form: { data: "/tmp/longmemeval.json", official_eval_after: true },
}));
const longImportOnlyPreflight = await longImportOnlyActions.preflightJudge();
assert(longImportOnlyPreflight.ok === false, "LongMemEval import_only run must not pass judge preflight");
assert(longImportOnlyPreflight.checks.find((item) => item.name === "import_only")?.ok === false, "LongMemEval import_only check should fail");
assert(longImportOnlyPreflight.checks.find((item) => item.name === "output_file")?.ok === false, "LongMemEval import_only run should fail missing output file existence check");

const longReadyRun = {
  run_dir: "/tmp/long-ready",
  output_file: "/tmp/long-ready/merged/echomemory_generic_qa_results.csv",
  status: "succeeded",
  name: "longmemeval ready",
};
const longReadyDetail = {
  record: {
    dataset_path: "/tmp/longmemeval.json",
  },
  artifact_status: {
    output_file: { path: longReadyRun.output_file, exists: true },
    longmemeval_official_summary: { path: "/tmp/long-ready/official_eval/longmemeval_official_summary.json", exists: true },
  },
};
const longReadyResult = {
  summary: {
    rows: 19,
    official_overall_accuracy: 0.0,
    official_task_averaged_accuracy: 0.0,
    summary_json: {
      official_eval_after: true,
      official_eval: {
        summary_path: "/tmp/long-ready/official_eval/longmemeval_official_summary.json",
        summary: {
          graded: 19,
          overall_accuracy: 0.0,
          task_averaged_accuracy: 0.0,
        },
      },
    },
  },
};
const longReadyActions = createLongMemEvalActions(baseDeps({
  run: longReadyRun,
  detail: longReadyDetail,
  result: longReadyResult,
  form: { data: "/tmp/longmemeval.json", official_eval_after: true },
}));
const longReadyPreflight = await longReadyActions.preflightJudge();
assert(longReadyPreflight.ok === true, "LongMemEval preflight should pass when official summary exists and graded > 0 even if scores are 0");
assert(longReadyPreflight.checks.find((item) => item.name === "official_summary_status")?.ok === true, "LongMemEval official summary check should pass for graded all-zero-score runs");

console.log("official preflight smoke passed");

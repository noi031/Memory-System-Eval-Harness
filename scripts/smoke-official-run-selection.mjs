#!/usr/bin/env node
import { BENCHMARKS } from "../src/config.js";
import { createSelectors } from "../src/selectors.js";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function firstValue(...values) {
  return values.find((value) => value !== undefined && value !== null && String(value).trim() !== "") || "";
}

function makeState() {
  return {
    activeBenchmark: "hotpotqa",
    selectedAccount: "locomo-conv30-20260703_014852",
    runs: [],
    tasks: [],
    backends: [],
    datasets: [],
    config: {},
    accountDetails: { config: {} },
    runDetails: {},
    resultSummaries: {},
    currentRunDirs: {
      locomo: "",
      hotpotqa: "/tmp/hotpot-blank-incomplete",
      longmemeval: "/tmp/longmem-blank-incomplete",
    },
    userSelectedRunDirs: {
      locomo: false,
      hotpotqa: false,
      longmemeval: false,
    },
  };
}

const state = makeState();

const hotpotBlankIncomplete = {
  run_dir: "/tmp/hotpot-blank-incomplete",
  output_file: "/tmp/hotpot-blank-incomplete/results.csv",
  dataset_format: "hotpotqa",
  status: "succeeded",
  account: "",
  name: "hotpot blank incomplete",
  summary: {
    rows: 11,
    graded: 0,
    summary_json: {
      dataset_format: "hotpotqa",
    },
  },
};
const hotpotOfficialReady = {
  run_dir: "/tmp/hotpot-official-ready",
  output_file: "/tmp/hotpot-official-ready/results.csv",
  dataset_format: "hotpotqa",
  status: "succeeded",
  account: "hotpotqa-live1-20260704",
  name: "hotpot official ready",
  summary: {
    rows: 1,
    graded: 0,
    summary_json: {
      dataset_format: "hotpotqa",
    },
  },
};
const hotpotExactAccount = {
  run_dir: "/tmp/hotpot-exact-account",
  output_file: "/tmp/hotpot-exact-account/results.csv",
  dataset_format: "hotpotqa",
  status: "succeeded",
  account: "locomo-conv30-20260703_014852",
  name: "hotpot exact account run",
  summary: {
    rows: 3,
    graded: 0,
    summary_json: {
      dataset_format: "hotpotqa",
    },
  },
};

const longmemBlankIncomplete = {
  run_dir: "/tmp/longmem-blank-incomplete",
  output_file: "/tmp/longmem-blank-incomplete/results.csv",
  dataset_format: "longmemeval",
  status: "succeeded",
  account: "",
  name: "longmemeval blank incomplete",
  summary: {
    rows: 6,
    graded: 0,
    summary_json: {
      dataset_format: "longmemeval",
    },
  },
};
const longmemOfficialReady = {
  run_dir: "/tmp/longmem-official-ready",
  output_file: "/tmp/longmem-official-ready/results.csv",
  dataset_format: "longmemeval",
  status: "succeeded",
  account: "default",
  name: "longmemeval official ready",
  summary: {
    rows: 1,
    graded: 0,
    summary_json: {
      dataset_format: "longmemeval",
    },
  },
};

state.runs = [
  hotpotBlankIncomplete,
  hotpotOfficialReady,
  hotpotExactAccount,
  longmemBlankIncomplete,
  longmemOfficialReady,
];

state.runDetails[hotpotBlankIncomplete.run_dir] = {
  record: {
    account: hotpotBlankIncomplete.account,
    summary: hotpotBlankIncomplete.summary,
  },
};
state.runDetails[hotpotOfficialReady.run_dir] = {
  record: {
    account: hotpotOfficialReady.account,
    summary: hotpotOfficialReady.summary,
  },
};
state.runDetails[hotpotExactAccount.run_dir] = {
  record: {
    account: hotpotExactAccount.account,
    summary: hotpotExactAccount.summary,
  },
};
state.runDetails[longmemBlankIncomplete.run_dir] = {
  record: {
    account: longmemBlankIncomplete.account,
    summary: longmemBlankIncomplete.summary,
  },
};
state.runDetails[longmemOfficialReady.run_dir] = {
  record: {
    account: longmemOfficialReady.account,
    summary: longmemOfficialReady.summary,
  },
};

state.resultSummaries[hotpotBlankIncomplete.output_file] = {
  summary: hotpotBlankIncomplete.summary,
};
state.resultSummaries[hotpotOfficialReady.output_file] = {
  summary: {
    ...hotpotOfficialReady.summary,
    official_answer_f1: 0.0,
    official_joint_f1: 0.0,
    summary_json: {
      dataset_format: "hotpotqa",
      official_eval: {
        summary: {
          graded: 1,
          answer_f1: 0.0,
          joint_f1: 0.0,
        },
      },
    },
  },
};
state.resultSummaries[hotpotExactAccount.output_file] = {
  summary: hotpotExactAccount.summary,
};
state.resultSummaries[longmemBlankIncomplete.output_file] = {
  summary: longmemBlankIncomplete.summary,
};
state.resultSummaries[longmemOfficialReady.output_file] = {
  summary: {
    ...longmemOfficialReady.summary,
    official_overall_accuracy: 0.0,
    official_task_averaged_accuracy: 0.0,
    summary_json: {
      dataset_format: "longmemeval",
      official_eval: {
        summary: {
          graded: 1,
          overall_accuracy: 0.0,
          task_averaged_accuracy: 0.0,
        },
      },
    },
  },
};

const selectors = createSelectors({
  state,
  BENCHMARKS,
  $: () => null,
  queryAll: () => [],
  firstValue,
});

const hotpotRuns = selectors.runsForBenchmark("hotpotqa");
const hotpotPreferredNoExact = selectors.preferredRunForBenchmark("hotpotqa", hotpotRuns.filter((run) => run.run_dir !== hotpotExactAccount.run_dir));
assert(hotpotPreferredNoExact?.run_dir === hotpotOfficialReady.run_dir, "HotpotQA should fall back to official-ready cross-account run when no exact-account run exists");

const hotpotPreferredWithExact = selectors.preferredRunForBenchmark("hotpotqa", hotpotRuns);
assert(hotpotPreferredWithExact?.run_dir === hotpotExactAccount.run_dir, "HotpotQA should keep exact-account scope when exact-account runs exist");

const noExactState = {
  ...state,
  activeBenchmark: "hotpotqa",
  runs: state.runs.filter((run) => run.run_dir !== hotpotExactAccount.run_dir),
  currentRunDirs: {
    ...state.currentRunDirs,
    hotpotqa: hotpotBlankIncomplete.run_dir,
  },
  userSelectedRunDirs: {
    ...state.userSelectedRunDirs,
    hotpotqa: false,
  },
};
const noExactSelectors = createSelectors({
  state: noExactState,
  BENCHMARKS,
  $: () => null,
  queryAll: () => [],
  firstValue,
});
const hotpotCurrent = noExactSelectors.currentRun();
assert(hotpotCurrent?.run_dir === hotpotOfficialReady.run_dir, "HotpotQA currentRun should upgrade stale stored incomplete run to official-ready run");

state.activeBenchmark = "longmemeval";
state.currentRunDirs.longmemeval = longmemBlankIncomplete.run_dir;
state.userSelectedRunDirs.longmemeval = false;
const longmemCurrent = selectors.currentRun();
assert(longmemCurrent?.run_dir === longmemOfficialReady.run_dir, "LongMemEval currentRun should upgrade stale stored incomplete run to official-ready run");

const visibleLongmem = selectors.visibleRunsForBenchmark("longmemeval", { limit: 3 });
assert(visibleLongmem[0]?.run_dir === longmemOfficialReady.run_dir || visibleLongmem[1]?.run_dir === longmemOfficialReady.run_dir, "LongMemEval visible runs should prioritize official-ready runs near the front");

console.log("official run selection smoke passed");

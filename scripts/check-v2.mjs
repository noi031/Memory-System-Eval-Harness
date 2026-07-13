#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));

function walk(dir, predicate, files = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const stat = statSync(path);
    if (stat.isDirectory()) {
      walk(path, predicate, files);
    } else if (predicate(path)) {
      files.push(path);
    }
  }
  return files;
}

function run(label, command, args) {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: "utf8",
    stdio: "pipe",
  });
  if (result.status !== 0) {
    const detail = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
    throw new Error(`${label} failed${detail ? `\n${detail}` : ""}`);
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function checkJsSyntax() {
  for (const file of walk(root, (path) => [".js", ".mjs"].includes(extname(path)))) {
    run(`node --check ${relative(root, file)}`, "node", ["--check", file]);
  }
}

function checkPythonEntrypointsCompile() {
  for (const file of ["dev_server.py", "server.py"]) {
    run(`python3 -m py_compile ${file}`, "python3", ["-m", "py_compile", join(root, file)]);
  }
}

function checkEntryAssets() {
  const html = readFileSync(join(root, "index.html"), "utf8");
  assert(html.includes('href="./styles.css'), "index.html must load local styles.css");
  assert(html.includes('src="./app.js'), "index.html must load local app.js");
  assert(!html.includes("/static/"), "index.html must not load compatibility /static assets");
  assert(!html.includes("web/static"), "index.html must not load compatibility web/static assets");
}

function checkSingleEntryAssets() {
  const html = readFileSync(join(root, "index.html"), "utf8");
  const stylesheetTags = [...html.matchAll(/<link\b[^>]*rel=["']stylesheet["'][^>]*>/gi)].map((match) => match[0]);
  const scriptTags = [...html.matchAll(/<script\b[^>]*\bsrc=["'][^"']+["'][^>]*>/gi)].map((match) => match[0]);
  assert(stylesheetTags.length === 1, `index.html must have exactly one stylesheet entry, found ${stylesheetTags.length}`);
  assert(scriptTags.length === 1, `index.html must have exactly one script entry, found ${scriptTags.length}`);
  assert(/href=["']\.\/styles\.css(?:\?[^"']*)?["']/.test(stylesheetTags[0]), "the stylesheet entry must be ./styles.css");
  assert(/src=["']\.\/app\.js(?:\?[^"']*)?["']/.test(scriptTags[0]), "the script entry must be ./app.js");
  assert(/\btype=["']module["']/.test(scriptTags[0]), "the script entry must be type=module");
}

function checkFrontendDoesNotReferenceCompatibilityAssets() {
  const frontendFiles = [
    join(root, "index.html"),
    join(root, "app.js"),
    join(root, "styles.css"),
    ...walk(join(root, "src"), (path) => extname(path) === ".js"),
  ];
  const violations = [];
  for (const file of frontendFiles) {
    const text = readFileSync(file, "utf8");
    for (const pattern of ["/static/", "web/static/", "../static/", "../web/static/"]) {
      if (text.includes(pattern)) {
        violations.push(`${relative(root, file)} references ${pattern}`);
      }
    }
  }
  assert(!violations.length, `frontend must not reference compatibility assets:\n${violations.join("\n")}`);
}

function checkBundledBackendLayout() {
  const required = [
    "server.py",
    "memory",
    "benchmark/locomo/echomemory/run_eval.py",
    "benchmark/locomo/openviking/run_eval.py",
    "openviking_custom_memory_templates/locomo_evidence/events.yaml",
    "scripts/start-api-server.sh",
    "scripts/status-api-server.sh",
    "scripts/stop-api-server.sh",
    "scripts/start-workbench-stack.sh",
    "scripts/echomemory_memory_qa.py",
    "scripts/echomemory_locomo_import.py",
    "scripts/openviking_memory_qa.py",
    "scripts/openviking_locomo_import.py",
    "web/api/tasks.py",
    "web/package.py",
    "web/ui_contract.json",
    "web/static/index.html",
  ];
  const missing = required.filter((path) => !existsSync(join(root, path)));
  assert(!missing.length, `bundled backend files are missing:\n${missing.join("\n")}`);
}

function checkReadmeDocumentsBundledBackend() {
  const readme = readFileSync(join(root, "README.md"), "utf8");
  const required = [
    "start-workbench-stack.sh",
    "start-api-server.sh",
    "server.py",
    "memory/",
    "benchmark/locomo/",
    "compatibility assets",
  ];
  const missing = required.filter((item) => !readme.includes(item));
  assert(!missing.length, `README.md must document the bundled backend layout:\n${missing.join("\n")}`);
}

function checkApiContractDocumentsBundledBackend() {
  const doc = readFileSync(join(root, "docs", "api-contract.md"), "utf8");
  const required = ["server.py", "memory/", "benchmark/locomo/", "scripts/"];
  const missing = required.filter((item) => !doc.includes(item));
  assert(!missing.length, `docs/api-contract.md must document bundled runtime pieces:\n${missing.join("\n")}`);
}

function checkRuntimeSmokeScriptExists() {
  const script = join(root, "scripts", "check-v2-runtime.mjs");
  assert(statSync(script).isFile(), "scripts/check-v2-runtime.mjs must exist for live runtime smoke checks");
  const text = readFileSync(script, "utf8");
  assert(text.includes("--start-server"), "scripts/check-v2-runtime.mjs must support --start-server");
}

function checkHotpotQaPayloadSmoke() {
  run("HotpotQA payload smoke", "node", [join(root, "scripts", "smoke-hotpotqa-payload.mjs")]);
}

function checkLongMemEvalPayloadSmoke() {
  run("LongMemEval payload smoke", "node", [join(root, "scripts", "smoke-longmemeval-payload.mjs")]);
}

function checkLocomoPayloadSmoke() {
  run("LoCoMo payload smoke", "node", [join(root, "scripts", "smoke-locomo-payload.mjs")]);
}

function checkStrictBlackboxReportSmoke() {
  run("strict black-box report smoke", "python3", [join(root, "scripts", "smoke_blackbox_report.py")]);
}

function checkLocomoControlsStillExist() {
  const renderer = readFileSync(join(root, "src", "render", "qa-locomo.js"), "utf8");
  const formReader = readFileSync(join(root, "src", "form-readers.js"), "utf8");
  const action = readFileSync(join(root, "src", "action", "locomo.js"), "utf8");
  assert(renderer.includes("wbQaQuestionLimit"), "LoCoMo QA renderer must expose the quick-test question limit");
  assert(renderer.includes("wbRunQaRetryFailed"), "LoCoMo QA renderer must expose retry-failed control");
  assert(renderer.includes("wbRunQaRetryMissing"), "LoCoMo QA renderer must expose retry-missing control");
  assert(formReader.includes("question_limit"), "LoCoMo form reader must read question_limit");
  assert(action.includes("retryFailedQa"), "LoCoMo action layer must still export retryFailedQa");
  assert(action.includes("retryMissingQa"), "LoCoMo action layer must still export retryMissingQa");
}

const checks = [
  checkJsSyntax,
  checkPythonEntrypointsCompile,
  checkEntryAssets,
  checkSingleEntryAssets,
  checkFrontendDoesNotReferenceCompatibilityAssets,
  checkBundledBackendLayout,
  checkReadmeDocumentsBundledBackend,
  checkApiContractDocumentsBundledBackend,
  checkRuntimeSmokeScriptExists,
  checkHotpotQaPayloadSmoke,
  checkLongMemEvalPayloadSmoke,
  checkLocomoPayloadSmoke,
  checkStrictBlackboxReportSmoke,
  checkLocomoControlsStillExist,
];

try {
  for (const check of checks) check();
  console.log("workbench checks passed");
} catch (error) {
  console.error(error.message || String(error));
  process.exit(1);
}

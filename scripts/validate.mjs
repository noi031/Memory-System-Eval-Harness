#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const ignoredDirectories = new Set([
  ".git",
  ".runtime",
  "node_modules",
  "coverage",
  "dist",
  "__pycache__",
  "runs",
  "outputs",
  "artifacts",
  "generated-reports",
  "external",
  "tmp",
]);
const textExtensions = new Set([".css", ".html", ".js", ".json", ".md", ".mjs", ".py", ".sh", ".yaml"]);

function run(label, command, args) {
  const result = spawnSync(command, args, { cwd: root, encoding: "utf8", stdio: "pipe" });
  if (result.status !== 0) {
    const detail = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
    throw new Error(`${label} failed${detail ? `\n${detail}` : ""}`);
  }
}

function walk(directory, files = []) {
  for (const name of readdirSync(directory)) {
    if (ignoredDirectories.has(name)) continue;
    const path = join(directory, name);
    const stat = statSync(path);
    if (stat.isDirectory()) walk(path, files);
    else if (!(directory === join(root, "docs") && name.endsWith(".html"))) files.push(path);
  }
  return files;
}

function checkRepositoryBoundary() {
  const forbiddenTopLevel = ["deliverables", "dataset", "datasets", "tmp"];
  const violations = forbiddenTopLevel.filter((name) => {
    try { return statSync(join(root, name)).isDirectory(); } catch { return false; }
  });
  if (violations.length) throw new Error(`forbidden repository directories: ${violations.join(", ")}`);

  const findings = [];
  const secretPatterns = [
    /\bsk-[A-Za-z0-9_-]{16,}\b/g,
    /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g,
    /(?:api[_-]?key|password|token)\s*[=:]\s*["'][^"']{12,}["']/gi,
  ];

  for (const file of walk(root)) {
    if (!textExtensions.has(extname(file)) && !file.endsWith(".env.example")) continue;
    const text = readFileSync(file, "utf8");
    for (const pattern of secretPatterns) {
      pattern.lastIndex = 0;
      if (pattern.test(text)) {
        const match = text.match(pattern)?.[0] || "";
        if (match.includes("${{")) continue;
        findings.push(`${relative(root, file)} matches ${pattern}`);
      }
    }
  }

  const boundaryFiles = [
    "README.md",
    "app.js",
    "index.html",
    "styles.css",
    "dev_server.py",
    "server.py",
    "docs/api-contract.md",
    "docs/acceptance-checklist.md",
  ];
  for (const file of boundaryFiles) {
    const path = join(root, file);
    const text = readFileSync(path, "utf8");
    if (/\/Users\/[A-Za-z0-9._-]+\//.test(text) || /[A-Za-z]:\\Users\\[A-Za-z0-9._-]+\\/.test(text)) {
      findings.push(`${file} contains an absolute user path`);
    }
  }

  if (findings.length) throw new Error(`repository boundary check failed:\n${findings.join("\n")}`);
}

function main() {
  const checks = [];
  run("source checks", "node", [join(root, "scripts", "check-v2.mjs")]);
  checks.push("source_checks");
  run("Python compile dev_server.py", "python3", ["-m", "py_compile", join(root, "dev_server.py")]);
  run("Python compile server.py", "python3", ["-m", "py_compile", join(root, "server.py")]);
  checks.push("python_compile");
  checkRepositoryBoundary();
  checks.push("repository_boundary");
  console.log(JSON.stringify({ ok: true, root, checks }, null, 2));
}

try {
  main();
} catch (error) {
  console.error(error.message || String(error));
  process.exit(1);
}

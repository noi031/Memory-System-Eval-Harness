#!/usr/bin/env node
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const origin = process.env.BENCHMARK_CONSOLE_V2_ORIGIN || "http://127.0.0.1:4173";
const apiBase = process.env.BENCHMARK_CONSOLE_API_BASE || "http://127.0.0.1:19181";
const shouldStartServer = process.argv.includes("--start-server");
const root = dirname(dirname(fileURLToPath(import.meta.url)));

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function fetchText(url) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith("https:") ? httpsRequest : httpRequest;
    const req = client(url, { method: "GET" }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => {
        const body = Buffer.concat(chunks).toString("utf8");
        resolve({
          status: res.statusCode || 0,
          headers: res.headers,
          body,
        });
      });
    });
    req.setTimeout(5000, () => {
      req.destroy(new Error(`request timed out: ${url}`));
    });
    req.on("error", reject);
    req.end();
  });
}

function countMatches(text, pattern) {
  return [...text.matchAll(pattern)].length;
}

async function checkRootHtml() {
  const response = await fetchText(`${origin}/`);
  assert(response.status === 200, `GET / must return 200, got ${response.status}`);
  const html = response.body;
  assert(html.includes("<title>LoCoMo 评测台 | MemoryBench</title>"), "root HTML must serve the workbench title");
  assert(html.includes('name="benchmark-console-reference-url"'), "dev server must inject benchmark-console-reference-url meta");
  assert(countMatches(html, /<link\b[^>]*rel=["']stylesheet["'][^>]*>/gi) === 1, "root HTML must include exactly one stylesheet link");
  assert(countMatches(html, /<script\b[^>]*src=["'][^"']+["'][^>]*>/gi) === 1, "root HTML must include exactly one script tag");
  assert(html.includes('href="./styles.css'), "root HTML must load local styles.css");
  assert(html.includes('src="./app.js'), "root HTML must load local app.js");
  assert(!html.includes("/static/"), "root HTML must not reference legacy /static assets");
  assert(!html.includes("web/static"), "root HTML must not reference legacy web/static assets");
  for (const stage of ["记忆注入", "问答测试", "评分", "测试报告"]) {
    assert(html.includes(stage), `root HTML must include stage label: ${stage}`);
  }
  for (const section of ["运行日志", "历史结果", "QA 启动检查", "报告操作"]) {
    assert(html.includes(section), `root HTML must include collapsible section label: ${section}`);
  }
  assert(countMatches(html, /<details\b/gi) >= 4, "root HTML must include the expected collapsed secondary sections");
  assert(!/<details[^>]*\sopen(?:\s|>|=)/i.test(html), "secondary <details> sections must be closed by default");
}

async function checkApiProxy() {
  const response = await fetchText(`${origin}/api/tasks?include_inactive=0`);
  assert(response.status === 200, `GET /api/tasks?include_inactive=0 must return 200 via proxy, got ${response.status}`);
  let data;
  try {
    data = JSON.parse(response.body);
  } catch (error) {
    throw new Error(`API proxy must return JSON for /api/tasks: ${error.message}`);
  }
  assert(Array.isArray(data.tasks), "API proxy /api/tasks response must include a tasks array");
}

async function waitForV2Server() {
  let lastError = null;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetchText(`${origin}/`);
      if (response.status === 200) return;
      lastError = new Error(`GET / returned ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`V2 dev server did not become ready: ${lastError?.message || "unknown error"}`);
}

function startV2Server() {
  const parsed = new URL(origin);
  const child = spawn("python3", [
    join(root, "dev_server.py"),
    "--host",
    parsed.hostname,
    "--port",
    parsed.port || "4173",
    "--api-base",
    apiBase,
  ], {
    cwd: dirname(root),
    stdio: ["ignore", "pipe", "pipe"],
  });
  let logs = "";
  child.stdout.on("data", (chunk) => { logs += chunk.toString("utf8"); });
  child.stderr.on("data", (chunk) => { logs += chunk.toString("utf8"); });
  return {
    child,
    logs: () => logs.trim(),
  };
}

try {
  const server = shouldStartServer ? startV2Server() : null;
  try {
    if (server) await waitForV2Server();
    await checkRootHtml();
    await checkApiProxy();
    console.log(JSON.stringify({
      origin,
      apiBase,
      ok: true,
      startedServer: Boolean(server),
      checks: [
        "root_html",
        "api_proxy",
      ],
    }, null, 2));
  } finally {
    if (server) server.child.kill();
  }
} catch (error) {
  console.error(error.message || String(error));
  process.exit(1);
}

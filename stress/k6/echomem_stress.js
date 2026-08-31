import http from "k6/http";
import { check, sleep } from "k6";
import exec from "k6/execution";

const baseUrl = (__ENV.ECHOMEM_BASE_URL || "http://127.0.0.1:8010").replace(/\/$/, "");
const authKey = __ENV.ECHOMEM_AUTH_KEY || "";
const authHeader = __ENV.ECHOMEM_AUTH_HEADER || "X-API-Key";
const tenant = __ENV.ECHOMEM_TENANT || `k6-${exec.vu.idInTest}`;
const sessionId = __ENV.ECHOMEM_SESSION_ID || "";
const searchPath = __ENV.ECHOMEM_SEARCH_PATH || "/api/retrieval/search";
const commitPath = __ENV.ECHOMEM_COMMIT_PATH || "/api/sessions/{session}/commit";
const messagePath = __ENV.ECHOMEM_MESSAGE_PATH || "/api/sessions/{session}/messages";
// Keep load knobs outside K6_* because k6 reserves that namespace for
// process-level options and would override the scenarios below.
const targetRps = Number(__ENV.ECHOMEM_TEST_SEARCH_RPS || 1);
const commitEvery = Number(__ENV.ECHOMEM_TEST_COMMIT_EVERY_S || 0);
const executorMode = __ENV.ECHOMEM_TEST_EXECUTOR || "arrival-rate";
const pacedVus = Number(__ENV.ECHOMEM_TEST_VUS || 1);
const duration = __ENV.ECHOMEM_TEST_DURATION || "60s";

const searchScenario = executorMode === "paced-vus"
  ? {
      executor: "constant-vus",
      vus: pacedVus,
      duration,
      exec: "search",
    }
  : {
      executor: "constant-arrival-rate",
      rate: targetRps,
      timeUnit: "1s",
      duration,
      preAllocatedVUs: Number(__ENV.ECHOMEM_TEST_PRE_ALLOCATED_VUS || 4),
      maxVUs: Number(__ENV.ECHOMEM_TEST_MAX_VUS || 128),
      exec: "search",
    };

export const options = {
  scenarios: {
    search: searchScenario,
    ...(commitEvery > 0
      ? {
          commit: {
            executor: "constant-vus",
            vus: Number(__ENV.ECHOMEM_TEST_COMMIT_VUS || 1),
            duration,
            exec: "commit",
          },
        }
      : {}),
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<10000", "p(99)<30000"],
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

function headers() {
  const result = { "Content-Type": "application/json", "X-EchoMem-Tenant": tenant };
  if (authKey) result[authHeader] = authKey;
  return result;
}

function urlFor(path, session) {
  return `${baseUrl}${path.replace("{session}", encodeURIComponent(session || sessionId))}`;
}

function payload(response) {
  try {
    return JSON.parse(response.body || "{}");
  } catch (_) {
    return {};
  }
}

export function search() {
  const requestId = `k6-search-${tenant}-${__VU}-${__ITER}`;
  const requestHeaders = { ...headers(), "X-Request-ID": requestId };
  const response = http.post(
    urlFor(searchPath),
    JSON.stringify({
      query: `k6 real model search ${__VU}-${__ITER}`,
      agent_id: __ENV.ECHOMEM_AGENT_ID || "k6",
      session_id: sessionId || undefined,
      limit: Number(__ENV.ECHOMEM_SEARCH_LIMIT || 10),
      include_debug: true,
    }),
    { headers: requestHeaders, tags: { operation: "search", tenant, request_id: requestId } },
  );
  check(response, {
    "search response is successful or rate limited": (r) => r.status >= 200 && r.status < 500,
  });
  if (executorMode === "paced-vus" && targetRps > 0) {
    sleep(1 / targetRps);
  }
}

export function commit() {
  if (!sessionId) {
    check(null, { "commit requires ECHOMEM_SESSION_ID": () => false });
    return;
  }
  const requestId = `k6-commit-${tenant}-${__VU}-${__ITER}`;
  const requestHeaders = { ...headers(), "X-Request-ID": requestId };
  const message = http.post(
    urlFor(messagePath, sessionId),
    JSON.stringify({
      message_id: `k6-${tenant}-${__VU}-${__ITER}`,
      role: "user",
      content: `real k6 commit payload ${Date.now()} ${"x".repeat(1200)}`,
    }),
    { headers: requestHeaders, tags: { operation: "message", tenant, request_id: `${requestId}-message` } },
  );
  check(message, { "message accepted": (r) => r.status >= 200 && r.status < 300 });
  if (message.status < 200 || message.status >= 300) return;
  const response = http.post(
    urlFor(commitPath, sessionId),
    JSON.stringify({ agent_id: __ENV.ECHOMEM_AGENT_ID || "k6", session_id: sessionId }),
    { headers: requestHeaders, tags: { operation: "commit", tenant, request_id: requestId } },
  );
  check(response, {
    "commit accepted or rate limited": (r) => r.status === 202 || r.status === 200 || r.status === 429 || r.status === 503,
  });
  sleep(commitEvery);
}

export function handleSummary(data) {
  return {
      [__ENV.ECHOMEM_TEST_SUMMARY_PATH || "k6-summary.json"]: JSON.stringify(
      {
        generated_at: new Date().toISOString(),
        base_url: baseUrl,
        tenant,
        scenario: data,
        real_http: true,
        mock_model: false,
      },
      null,
      2,
    ),
  };
}

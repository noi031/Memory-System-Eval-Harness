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
const targetRps = Number(__ENV.K6_SEARCH_RPS || 1);
const commitEvery = Number(__ENV.K6_COMMIT_EVERY_S || 0);

export const options = {
  scenarios: {
    search: {
      executor: "constant-arrival-rate",
      rate: targetRps,
      timeUnit: "1s",
      duration: __ENV.K6_DURATION || "60s",
      preAllocatedVUs: Number(__ENV.K6_PRE_ALLOCATED_VUS || 4),
      maxVUs: Number(__ENV.K6_MAX_VUS || 128),
      exec: "search",
    },
    ...(commitEvery > 0
      ? {
          commit: {
            executor: "constant-vus",
            vus: Number(__ENV.K6_COMMIT_VUS || 1),
            duration: __ENV.K6_DURATION || "60s",
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
  const response = http.post(
    urlFor(searchPath),
    JSON.stringify({
      query: `k6 real model search ${__VU}-${__ITER}`,
      agent_id: __ENV.ECHOMEM_AGENT_ID || "k6",
      session_id: sessionId || undefined,
      limit: Number(__ENV.ECHOMEM_SEARCH_LIMIT || 10),
      include_debug: true,
    }),
    { headers: headers(), tags: { operation: "search", tenant } },
  );
  check(response, {
    "search response is successful or rate limited": (r) => r.status >= 200 && r.status < 500,
  });
}

export function commit() {
  if (!sessionId) {
    check(null, { "commit requires ECHOMEM_SESSION_ID": () => false });
    return;
  }
  const message = http.post(
    urlFor(messagePath, sessionId),
    JSON.stringify({
      message_id: `k6-${tenant}-${__VU}-${__ITER}`,
      content: `real k6 commit payload ${Date.now()} ${"x".repeat(1200)}`,
    }),
    { headers: headers(), tags: { operation: "message", tenant } },
  );
  check(message, { "message accepted": (r) => r.status >= 200 && r.status < 300 });
  if (message.status < 200 || message.status >= 300) return;
  const response = http.post(
    urlFor(commitPath, sessionId),
    JSON.stringify({ agent_id: __ENV.ECHOMEM_AGENT_ID || "k6", session_id: sessionId }),
    { headers: headers(), tags: { operation: "commit", tenant } },
  );
  check(response, {
    "commit accepted or rate limited": (r) => r.status === 202 || r.status === 200 || r.status === 429 || r.status === 503,
  });
  sleep(commitEvery);
}

export function handleSummary(data) {
  return {
    [__ENV.K6_SUMMARY_PATH || "k6-summary.json"]: JSON.stringify(
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

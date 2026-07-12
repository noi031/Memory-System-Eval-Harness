import { clearTimer, delay } from "./dom.js";

export function createApiClient(standaloneApiBase) {
  function apiUrl(path) {
    const normalized = String(path || "");
    if (!standaloneApiBase || /^https?:\/\//i.test(normalized)) return normalized;
    return `${standaloneApiBase}${normalized.startsWith("/") ? normalized : `/${normalized}`}`;
  }

  async function api(path, options = {}) {
    const {timeoutMs, ...fetchOptions} = options;
    const controller = timeoutMs && typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = controller
      ? delay(() => controller.abort(), timeoutMs)
      : null;
    const res = await fetch(apiUrl(path), {
      ...fetchOptions,
      signal: controller ? controller.signal : fetchOptions.signal,
      headers: {"Content-Type": "application/json", ...(fetchOptions.headers || {})},
    }).finally(() => {
      if (timer) clearTimer(timer);
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const error = new Error(data.error || `${res.status} ${res.statusText}`);
      error.data = data;
      error.status = res.status;
      throw error;
    }
    return data;
  }

  return {
    api,
    apiUrl,
  };
}

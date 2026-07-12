export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function firstValue() {
  for (let i = 0; i < arguments.length; i += 1) {
    const value = arguments[i];
    if (value !== undefined && value !== null && String(value).trim() !== "") return value;
  }
  return "";
}

export function toneClass(status) {
  const text = String(status || "").toLowerCase();
  if (["ok", "ready", "success", "succeeded"].includes(text)) return "ok";
  if (["fail", "failed", "error", "bad"].includes(text)) return "fail";
  return "warn";
}

export function tonePill(text, status) {
  return `<span class="wb-pill ${escapeHtml(toneClass(status))}">${escapeHtml(text || "-")}</span>`;
}

export function formatPct(value) {
  if (value === null || value === undefined || value === "") return "-";
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return `${Math.round(n * 100)}%`;
}

export function formatInt(value) {
  const n = Number(value);
  return Number.isFinite(n) ? String(n) : "-";
}

export function formatDurationSeconds(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  if (n < 60) return `${n.toFixed(n >= 10 ? 0 : 1)}s`;
  if (n < 3600) return `${(n / 60).toFixed(1)}m`;
  return `${(n / 3600).toFixed(1)}h`;
}

export function compactPath(value, head = 36, tail = 26) {
  const text = String(value || "");
  if (!text) return "-";
  if (text.length <= head + tail + 3) return text;
  return `${text.slice(0, head)}...${text.slice(-tail)}`;
}

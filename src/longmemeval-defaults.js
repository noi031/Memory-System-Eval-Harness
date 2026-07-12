export function preferredLongMemEvalDatasetRecord(records = []) {
  const candidates = Array.isArray(records) ? records : [];
  return candidates.find((item) => item?.id === "longmemeval-s-cleaned-full" && item.exists !== false)
    || candidates.find((item) => item?.id === "longmemeval-oracle-full" && item.exists !== false)
    || candidates.find((item) => String(item?.scope || "").toLowerCase() === "full" && item.exists !== false)
    || candidates.find((item) => item?.exists !== false)
    || candidates[0]
    || null;
}

function normalizedSegments(value = "") {
  return String(value || "")
    .trim()
    .replace(/\\/g, "/")
    .split("/")
    .filter(Boolean);
}

function lastSegment(value = "") {
  const segments = normalizedSegments(value);
  return segments.length ? segments[segments.length - 1] : "";
}

function scoreLongMemEvalRecord(item) {
  const id = String(item?.id || "").trim();
  const path = String(item?.path || "").trim();
  const scope = String(item?.scope || "").trim().toLowerCase();
  let score = 0;
  if (item?.exists !== false) score += 100;
  if (scope === "full") score += 20;
  if (id === "longmemeval-s-cleaned-full") score += 12;
  if (path.includes("/dataset/full/")) score += 8;
  return score;
}

export function normalizeLongMemEvalDatasetPath(rawValue = "", records = [], fallback = "") {
  const safeValue = String(rawValue || "").trim();
  if (!safeValue) return String(fallback || "").trim();
  const candidates = Array.isArray(records) ? records : [];
  const exact = candidates.find((item) => String(item?.path || "").trim() === safeValue && item?.exists !== false);
  if (exact) return String(exact.path || "").trim();
  const base = lastSegment(safeValue);
  if (!base) return String(fallback || safeValue).trim();
  const matches = candidates
    .filter((item) => lastSegment(item?.path || "") === base)
    .sort((left, right) => scoreLongMemEvalRecord(right) - scoreLongMemEvalRecord(left));
  if (matches.length) return String(matches[0]?.path || "").trim() || safeValue;
  return safeValue;
}

export function longMemEvalDatasetPath({
  benchmark,
  currentDatasetRecord,
  datasetRecords,
  firstValue,
  taskConfig = {},
}) {
  const preferred = preferredLongMemEvalDatasetRecord(datasetRecords);
  const rawValue = firstValue(
    taskConfig.data,
    currentDatasetRecord?.path,
    preferred?.path,
    benchmark?.defaultData,
  );
  return normalizeLongMemEvalDatasetPath(rawValue, datasetRecords, preferred?.path || benchmark?.defaultData || rawValue);
}

export function longMemEvalDatasetOptions(records = [], selectedPath = "", escapeHtml) {
  const candidates = Array.isArray(records) ? records : [];
  return candidates.map((item) => {
    const value = String(item?.path || "");
    const label = String(item?.id || item?.name || value || "-");
    const selected = String(value) === String(selectedPath) ? "selected" : "";
    return `<option value="${escapeHtml(value)}" ${selected}>${escapeHtml(label)}</option>`;
  }).join("");
}

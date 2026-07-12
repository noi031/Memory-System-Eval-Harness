export function preferredHotpotQaDatasetRecord(records = []) {
  const candidates = Array.isArray(records) ? records : [];
  return candidates.find((item) => item?.id === "hotpotqa-dev-distractor" && item.exists !== false)
    || candidates.find((item) => String(item?.scope || "").toLowerCase() === "full" && item.exists !== false)
    || candidates.find((item) => item?.exists !== false)
    || candidates[0]
    || null;
}

export function hotpotQaDatasetPath({
  benchmark,
  currentDatasetRecord,
  datasetRecords,
  firstValue,
  taskConfig = {},
}) {
  const preferred = preferredHotpotQaDatasetRecord(datasetRecords);
  return firstValue(
    taskConfig.data,
    currentDatasetRecord?.path,
    preferred?.path,
    benchmark?.defaultData,
  );
}

export function hotpotQaDatasetOptions(records = [], selectedPath = "", escapeHtml) {
  const candidates = Array.isArray(records) ? records : [];
  return candidates.map((item) => {
    const value = String(item?.path || "");
    const label = String(item?.id || item?.name || value || "-");
    const selected = String(value) === String(selectedPath) ? "selected" : "";
    return `<option value="${escapeHtml(value)}" ${selected}>${escapeHtml(label)}</option>`;
  }).join("");
}

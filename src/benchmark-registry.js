export function getBenchmark(BENCHMARKS, benchmarkId) {
  return BENCHMARKS[benchmarkId] || BENCHMARKS.locomo;
}

export function getBenchmarkIds(BENCHMARKS) {
  return Object.keys(BENCHMARKS || {});
}

export function defaultBenchmarkId(BENCHMARKS) {
  return getBenchmarkIds(BENCHMARKS)[0] || "locomo";
}

function normalizeFormat(value) {
  return String(value || "").toLowerCase();
}

export function matchesRunForBenchmark(benchmark, run) {
  if (benchmark?.id === "locomo") {
    const kind = String(run?.kind || "").trim().toLowerCase();
    if (![
      "echomemory_import",
      "openviking_import",
      "echomemory_qa",
      "openviking_qa",
      "echomemory_qa_retry_failed",
      "echomemory_qa_retry_missing",
      "openviking_qa_retry_failed",
      "openviking_qa_retry_missing",
    ].includes(kind)) {
      return false;
    }
  }
  return normalizeFormat(run?.dataset_format) === normalizeFormat(benchmark.datasetFormat);
}

export function matchesTaskForBenchmark(benchmark, task) {
  if (benchmark.id === "locomo") {
    const kind = String(task?.kind || "");
    return [
      "echomemory_import",
      "openviking_import",
      "echomemory_qa",
      "openviking_qa",
      "echomemory_qa_retry_failed",
      "echomemory_qa_retry_missing",
      "openviking_qa_retry_failed",
      "openviking_qa_retry_missing",
      "judge",
    ].includes(kind)
      || normalizeFormat(task?.dataset_format) === "locomo";
  }

  const kind = String(task?.kind || "").toLowerCase();
  const datasetFormat = normalizeFormat(task?.dataset_format || task?.meta?.config?.dataset_format);
  const name = String(task?.name || "").toLowerCase();
  return datasetFormat === normalizeFormat(benchmark.datasetFormat)
    || ((kind === "openviking_generic_qa" || kind === "echomemory_generic_qa") && name.includes(normalizeFormat(benchmark.id)));
}

export function benchmarkPrefetchLimit(benchmark) {
  return Math.max(1, Number(benchmark?.runDetailPrefetchLimit || 2));
}

export function benchmarkSupportsQuestionPreview(benchmark) {
  return benchmark?.questionPreviewMode === "questions_api";
}

export function benchmarkHasOfficialEval(benchmark) {
  return Boolean(benchmark?.officialEvalMeta);
}

export function benchmarkDefaultVisibleRunOptions(benchmark, overrides = {}) {
  const officialEval = benchmarkHasOfficialEval(benchmark);
  return {
    includeImportOnly: !officialEval,
    includeIncomplete: !officialEval,
    ...overrides,
  };
}

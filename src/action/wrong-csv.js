function buildWrongCsvQuestionSetPath({ datasetPath = "", sample = "all", wrongCsv = "" } = {}) {
  const qs = new URLSearchParams({
    path: String(datasetPath || "").trim(),
    mode: "wrong_csv",
    sample: String(sample || "all").trim() || "all",
    csv: String(wrongCsv || "").trim(),
  });
  return `/api/question-set?${qs.toString()}`;
}

function pendingJudgeCount(analysis) {
  const modeCounts = analysis?.failure_attribution?.mode_counts || {};
  const explicit = Number(modeCounts?.pending_judge || 0);
  if (Number.isFinite(explicit) && explicit > 0) return explicit;
  const unresolved = Number(analysis?.unresolved || 0);
  return Number.isFinite(unresolved) ? unresolved : 0;
}

function isMissingWrongCsvError(error, wrongCsv) {
  const target = String(wrongCsv || "").trim();
  if (!target) return false;
  const status = Number(error?.status || 0);
  if (status !== 400 && status !== 404) return false;
  const message = String(error?.data?.error || error?.message || "").trim();
  return Boolean(message) && message.includes(target);
}

export async function resolveWrongCsvQuestionSet(api, {
  datasetPath = "",
  sample = "all",
  wrongCsv = "",
  resultPath = "",
} = {}) {
  const safeWrongCsv = String(wrongCsv || "").trim();
  const safeResultPath = String(resultPath || "").trim();
  const requestPath = buildWrongCsvQuestionSetPath({ datasetPath, sample, wrongCsv: safeWrongCsv });
  try {
    return await api(requestPath);
  } catch (error) {
    if (!isMissingWrongCsvError(error, safeWrongCsv) || !safeResultPath) {
      throw error;
    }
    const clusterResult = await api(`/api/wrong-clusters?path=${encodeURIComponent(safeResultPath)}`).catch(() => null);
    const analysis = clusterResult?.analysis || {};
    const generatedWrongCsv = String(analysis?.wrong_questions_brief || "").trim();
    if (generatedWrongCsv) {
      return api(buildWrongCsvQuestionSetPath({
        datasetPath,
        sample,
        wrongCsv: generatedWrongCsv,
      }));
    }
    const pendingJudge = pendingJudgeCount(analysis);
    if (pendingJudge > 0) {
      throw new Error(`当前结果还有 ${pendingJudge} 题待 Judge，先完成判分再运行 wrong_csv`);
    }
    const wrongCount = Number(analysis?.wrong || 0);
    if (Number.isFinite(wrongCount) && wrongCount <= 0) {
      throw new Error("当前结果没有已判错题，无法生成 wrong_csv");
    }
    return api(requestPath);
  }
}

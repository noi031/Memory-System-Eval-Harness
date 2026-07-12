import { BENCHMARKS } from "../config.js";

export function createReportActions(deps) {
  const {
    api,
    currentBenchmark,
    currentRun,
  } = deps;

  function deriveOfficialSummaryPath(run, benchmarkId) {
    const outputFile = String(run?.output_file || "").trim();
    const filename = BENCHMARKS[String(benchmarkId || "").toLowerCase()]?.officialEvalMeta?.reportSummaryFilename
      || `${String(benchmarkId || "").toLowerCase()}_summary.json`;
    if (outputFile) {
      const slash = outputFile.lastIndexOf("/");
      const dir = slash >= 0 ? outputFile.slice(0, slash) : "";
      if (dir) return `${dir}/${filename}`;
    }
    return `${run.run_dir}/${filename}`;
  }

  function generatedReportModel(report, fallbackRun, fallbackActions = []) {
    const reportHtmlFile = report.report_html_file || `${fallbackRun.run_dir}/report.html`;
    const actions = report.report_public_url
      ? [{label: "打开 HTML 报告", tone: "primary", href: report.report_public_url}]
      : [{label: "打开 HTML 报告", tone: "primary", action: "open-path", path: reportHtmlFile}];
    return {
      selected: true,
      title: "报告已生成",
      subtitle: report.generated_at || "just now",
      path: reportHtmlFile || "-",
      actions: [
        ...actions,
        ...fallbackActions,
      ].filter(Boolean),
    };
  }

  async function exportOfficialEvalReport(run, benchmarkId) {
    const benchmarkName = BENCHMARKS[String(benchmarkId || "").toLowerCase()]?.officialEvalMeta?.benchmarkName || benchmarkId;
    const reportTitle = BENCHMARKS[String(benchmarkId || "").toLowerCase()]?.officialEvalMeta?.reportTitle || `${benchmarkName} 报告产物`;
    const summaryPath = deriveOfficialSummaryPath(run, benchmarkId);
    const outputFile = run.output_file || "";
    try {
      const report = await api(`/api/report?run_dir=${encodeURIComponent(run.run_dir)}`);
      return generatedReportModel(report, run, [
        {label: "打开 summary", tone: "secondary", action: "open-path", path: summaryPath},
        outputFile ? {label: "打开结果 CSV", tone: "ghost", action: "open-path", path: outputFile} : null,
      ]);
    } catch (error) {
      const reportHtmlFile = `${run.run_dir}/report.html`;
      return {
        selected: true,
        title: reportTitle,
        subtitle: `报告生成接口暂不可用：${error.message || "unknown error"}。可先打开当前 run 已落盘的产物。`,
        path: reportHtmlFile,
        actions: [
          {label: "打开 HTML 报告", tone: "primary", action: "open-path", path: reportHtmlFile},
          {label: "打开 summary", tone: "secondary", action: "open-path", path: summaryPath},
          outputFile ? {label: "打开结果 CSV", tone: "ghost", action: "open-path", path: outputFile} : null,
        ].filter(Boolean),
      };
    }
  }

  async function exportGeneratedReport(run) {
    try {
      const report = await api(`/api/report?run_dir=${encodeURIComponent(run.run_dir)}`);
      return generatedReportModel(report, run, [
        run.output_file ? {label: "打开结果 CSV", tone: "secondary", action: "open-path", path: run.output_file} : null,
        run.run_dir ? {label: "打开当前目录", tone: "ghost", action: "open-path", path: run.run_dir} : null,
      ]);
    } catch (error) {
      return {
        title: "报告导出失败",
        subtitle: error.message || "unknown error",
        body: "先打开当前结果目录确认产物，再决定是否补跑。",
        actions: [
          run.run_dir ? {label: "打开当前目录", tone: "secondary", action: "open-path", path: run.run_dir} : null,
          run.output_file ? {label: "打开结果 CSV", tone: "ghost", action: "open-path", path: run.output_file} : null,
        ].filter(Boolean),
      };
    }
  }

  const reportExporters = {
    hotpotqa: (run) => exportOfficialEvalReport(run, "hotpotqa"),
    longmemeval: (run) => exportOfficialEvalReport(run, "longmemeval"),
    locomo: exportGeneratedReport,
  };

  async function exportReport() {
    const run = currentRun();
    if (!run?.run_dir) throw new Error("当前没有可导出的结果");
    const exporter = reportExporters[currentBenchmark()?.id] || exportGeneratedReport;
    return exporter(run);
  }

  return {
    exportReport,
  };
}

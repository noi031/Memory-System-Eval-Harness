import { createShellRenderers } from "./render/shell.js";
import { createImportRenderers } from "./render/import.js";
import { createQaRenderers } from "./render/qa.js";
import { createJudgeRenderers } from "./render/judge.js";
import { createReportRenderers } from "./render/report.js";

export function createRenderers(deps) {
  const shell = createShellRenderers(deps);
  const imports = createImportRenderers(deps);
  const qa = createQaRenderers(deps);
  const judge = createJudgeRenderers(deps);
  const report = createReportRenderers(deps);

  function renderAll() {
    shell.renderShellText();
    shell.renderTopbar();
    shell.renderOverview();
    imports.renderImportConfig();
    imports.renderImportProgress(deps.state.activeBenchmark);
    qa.renderQaConfig();
    qa.renderQaTasks(deps.state.activeBenchmark);
    qa.renderQaPreview(deps.state.activeBenchmark);
    qa.renderQaRuns(deps.state.activeBenchmark);
    judge.renderJudgeCurrent();
    judge.renderJudgeActions(deps.state.activeBenchmark);
    judge.renderPendingWorkbench();
    report.renderReportCurrent();
    report.renderReportActions();
    report.renderReportRuns(deps.state.activeBenchmark);
  }

  return {
    ...shell,
    ...imports,
    ...qa,
    ...judge,
    ...report,
    renderAll,
  };
}

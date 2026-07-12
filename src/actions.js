import { createRuntimeActions } from "./action/runtime.js";
import { createWorkflowActions } from "./action/workflows.js";

export function createActions(deps) {
  const runtime = createRuntimeActions(deps);
  const workflows = createWorkflowActions({
    ...deps,
    ensureRunDetail: runtime.ensureRunDetail,
    exportPendingCsv: runtime.exportPendingCsv,
    loadCsvPreview: runtime.loadCsvPreview,
    loadQaDiagnostics: runtime.loadQaDiagnostics,
    loadRunConfigSnapshot: runtime.loadRunConfigSnapshot,
    loadQuestionDetail: runtime.loadQuestionDetail,
    loadPendingPreview: runtime.loadPendingPreview,
    validatePayload: runtime.validatePayload,
  });

  return {
    ...runtime,
    ...workflows,
  };
}

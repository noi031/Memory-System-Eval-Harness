import { isActiveStatus } from "../run-status.js";

function normalizeText(value) {
  return String(value || "").trim();
}

function normalizePath(value) {
  return normalizeText(value);
}

function normalizeSample(value) {
  return normalizeText(value) || "all";
}

function normalizeMode(value) {
  return normalizeText(value) || "full";
}

function normalizePathForCompare(value) {
  return normalizePath(value).replace(/\\/g, "/").replace(/\/+$/, "");
}

function normalizeStatus(value) {
  return normalizeText(value).toLowerCase();
}

function tokenConfigured() {
  for (let i = 0; i < arguments.length; i += 1) {
    if (normalizeText(arguments[i])) return true;
  }
  return false;
}

function latestMatchingImportRun(state, backendId, {
  account = "",
  workspace = "",
  dataPath = "",
} = {}) {
  const expectedKind = backendId === "echomemory" ? "echomemory_import" : "openviking_import";
  return (state?.runs || []).find((run) => {
    if (normalizeText(run?.kind) !== expectedKind) return false;
    const runAccount = normalizeText(run?.account);
    const runWorkspace = normalizePath(run?.workspace);
    const runDatasetPath = normalizePath(run?.dataset_path);
    if (account && runAccount && runAccount !== account) return false;
    if (workspace && runWorkspace && runWorkspace !== workspace) return false;
    if (dataPath && runDatasetPath && runDatasetPath !== dataPath) return false;
    return true;
  }) || null;
}

function importSampleMatches(formSample, importedSample) {
  const wanted = normalizeSample(formSample);
  const imported = normalizeSample(importedSample);
  if (!wanted || wanted === "all") return true;
  if (!imported || imported === "all") return true;
  return wanted === imported;
}

function sameValue(left, right) {
  return normalizeText(left) === normalizeText(right);
}

function samePath(left, right) {
  const normalizedLeft = normalizePathForCompare(left);
  const normalizedRight = normalizePathForCompare(right);
  if (!normalizedLeft || !normalizedRight) return normalizedLeft === normalizedRight;
  return normalizedLeft === normalizedRight;
}

function normalizeBackend(kind = "", backend = "") {
  const backendText = normalizeText(backend);
  if (backendText) return backendText;
  return normalizeText(kind).startsWith("openviking_") ? "openviking" : "echomemory";
}

function taskSingleFlightScope(task = {}) {
  const cfg = task?.meta?.config || {};
  return {
    backend: normalizeBackend(task?.kind, cfg.backend || cfg.memoryBackend),
    account: normalizeText(cfg.account || "default") || "default",
    workspace: normalizePath(cfg.workspace || cfg.echomemory_workspace || cfg.openviking_workspace),
    data: normalizePath(cfg.data),
    datasetFormat: normalizeText(cfg.dataset_format || "locomo").toLowerCase() || "locomo",
  };
}

function formSingleFlightScope(form = {}, { backend = "", account = "" } = {}) {
  return {
    backend: normalizeBackend("", backend),
    account: normalizeText(account || "default") || "default",
    workspace: normalizePath(form.workspace),
    data: normalizePath(form.data),
    datasetFormat: "locomo",
  };
}

function sameQaScope(form, task = {}, options = {}) {
  const taskScope = taskSingleFlightScope(task);
  const formScope = formSingleFlightScope(form, options);
  return (
    sameValue(taskScope.backend, formScope.backend)
    && sameValue(taskScope.account, formScope.account)
    && sameValue(taskScope.workspace, formScope.workspace)
    && sameValue(taskScope.data, formScope.data)
    && sameValue(taskScope.datasetFormat, formScope.datasetFormat)
  );
}

function summarizeFlowImport(flowStatus) {
  const imported = flowStatus?.artifacts?.imported || {};
  const summaries = Array.isArray(imported?.summaries) ? imported.summaries : [];
  const sessions = Array.isArray(imported?.sessions) ? imported.sessions : [];
  const completeCount = Number(imported?.complete_count || 0);
  const summaryCount = Number(imported?.summary_count || summaries.length || 0);
  const sessionCount = Number(imported?.session_count || sessions.length || 0);
  return {
    imported,
    summaries,
    completeCount,
    summaryCount,
    sessionCount,
    hasImportArtifacts: completeCount > 0 || summaryCount > 0 || sessionCount > 0,
  };
}

export function findConflictingLocomoQaTask(tasks = [], form, options = {}) {
  return (tasks || []).find((task) => {
    if (!isActiveStatus(task?.status)) return false;
    const kind = normalizeText(task?.kind);
    if (![
      "echomemory_qa",
      "openviking_qa",
      "echomemory_qa_retry_failed",
      "echomemory_qa_retry_missing",
      "openviking_qa_retry_failed",
      "openviking_qa_retry_missing",
    ].includes(kind)) return false;
    return sameQaScope(form, task, options);
  }) || null;
}

export function buildLocomoQaGateModel({
  backendId,
  currentWorkspace,
  currentAccountConfig,
  form,
  flowStatus = null,
  readiness,
  state,
  tasks = [],
}) {
  const cfg = currentAccountConfig();
  const account = normalizeText(state.selectedAccount || cfg.account || "");
  const workspace = normalizePath(form.workspace || currentWorkspace());
  const dataPath = normalizePath(form.data);
  const runtimeRoot = normalizePath(state.readiness?.preflight?.runtime?.root || "");
  const runtimeUrl = normalizeText(state.readiness?.preflight?.runtime?.url || "");
  const echomemRoot = normalizePath(form.echomem_root || cfg.echomemRoot || state.config?.echomemRoot || runtimeRoot || "");
  const echomemBaseUrl = normalizeText(form.echomem_base_url || cfg.echomemBaseUrl || state.config?.echomemBaseUrl || runtimeUrl || "");
  const memoryUserId = normalizeText(form.memory_user_id || cfg.memoryUserId || "default") || "default";
  const memoryAgentId = normalizeText(form.memory_agent_id || cfg.memoryAgentId || "default") || "default";
  const answerModel = normalizeText(cfg.agentModel || state.config?.answer_model || "");
  const answerBaseUrl = normalizeText(cfg.agentBaseUrl || "");
  const currentReadiness = readiness || state.readiness || {};
  const preflight = currentReadiness?.preflight || {};
  const hasPreflight = Object.keys(preflight || {}).length > 0;
  const readyBackend = normalizeText(currentReadiness?.backend || backendId());
  const backendStatus = normalizeStatus(preflight?.backend_adapter?.status);
  const datasetStatus = normalizeStatus(preflight?.dataset?.status);
  const datasetFormat = normalizeText(preflight?.dataset?.format).toLowerCase();
  const workspaceStatus = normalizeStatus(preflight?.workspace?.status);
  const answerPreflight = preflight?.models?.answer || {};
  const echomemoryModelPreflight = preflight?.models?.echomemory || {};
  const embeddingTokenReady = Boolean(
    echomemoryModelPreflight?.embedding_token_set
    || cfg.echomemEmbeddingTokenSet
    || cfg.echomemTokenSet
    || tokenConfigured(cfg.memoryInjectToken, cfg.judgeToken, cfg.answerToken, cfg.agentToken)
  );
  const chatTokenReady = Boolean(
    answerPreflight?.token_set
    || echomemoryModelPreflight?.chat_token_set
    || cfg.answerTokenSet
    || cfg.judgeTokenSet
    || cfg.echomemChatTokenSet
    || cfg.echomemTokenSet
    || tokenConfigured(cfg.answerToken, cfg.agentToken, cfg.judgeToken)
  );
  const answerTokenReady = !hasPreflight
    ? true
    : (backendId() === "echomemory"
      ? Boolean(chatTokenReady && embeddingTokenReady)
      : Boolean(answerPreflight?.token_set));
  const answerBaseUrlReady = backendId() === "echomemory"
    ? Boolean(answerPreflight?.base_url_set || cfg.agentBaseUrl || state.config?.answer_base_url)
    : Boolean(answerPreflight?.base_url_set || cfg.agentBaseUrl || state.config?.answer_base_url);
  const runtime = preflight?.runtime || {};
  const latestImport = latestMatchingImportRun(state, backendId(), {
    account,
    workspace,
    dataPath,
  });
  const flowProbeTimedOut = Boolean(state?.locomoFlowStatusMeta?.timedOut);
  const flowProbeError = String(state?.locomoFlowStatusMeta?.error || "").trim();
  const hasRunHistory = Array.isArray(state?.runs) && state.runs.length > 0;
  const blockingTask = findConflictingLocomoQaTask(tasks, form, {
    backend: backendId(),
    account,
  });
  const flowImport = summarizeFlowImport(flowStatus);
  const flowImportReady = flowImport.hasImportArtifacts;
  const flowScopeReady = flowImport.summaryCount > 0 || flowImport.completeCount > 0;
  const flowProbeUnavailable = !flowStatus && flowProbeTimedOut;
  const importReady = !hasRunHistory
    || (Boolean(latestImport) && ["succeeded", "running", "finalizing"].includes(normalizeStatus(latestImport?.status)))
    || flowImportReady
    || flowProbeUnavailable;
  const memoryScopeOk = !latestImport
    ? (!hasRunHistory || flowScopeReady || flowProbeUnavailable)
    : importSampleMatches(form.sample, latestImport.sample);
  const checks = [
    {
      name: "dataset",
      ok: Boolean(dataPath) && (!hasPreflight || ((!datasetStatus || datasetStatus === "ok") && (!datasetFormat || datasetFormat === "locomo"))),
      message: datasetStatus === "ok"
        ? `${preflight?.dataset?.questions || "-"} 题 · ${preflight?.dataset?.path || dataPath}`
        : (dataPath || "未填写数据集路径"),
    },
    {
      name: "workspace",
      ok: Boolean(workspace) && (!hasPreflight || !workspaceStatus || workspaceStatus === "ok"),
      message: workspaceStatus === "ok"
        ? `${preflight?.workspace?.storage_root || preflight?.workspace?.workspace || workspace}`
        : (workspace || "未填写记忆目录"),
    },
    {
      name: "memory_identity",
      ok: Boolean(memoryUserId) && Boolean(memoryAgentId),
      message: `${memoryUserId || "default"} / ${memoryAgentId || "default"}`,
    },
    {
      name: "echomem_root",
      ok: backendId() !== "echomemory" || Boolean(echomemBaseUrl || echomemRoot || runtime?.root),
      message: backendId() === "echomemory"
        ? (echomemBaseUrl || echomemRoot || runtime?.root || "未填写 EchoMemory 地址或 Root")
        : "OpenViking 不需要 EchoMemory Root",
    },
    {
      name: "backend",
      ok: Boolean(readyBackend) && (!hasPreflight || backendStatus !== "fail") && sameValue(readyBackend, backendId()),
      message: readyBackend || backendId(),
    },
    { name: "answer_model", ok: Boolean(answerModel), message: answerModel || "未配置回答模型" },
    {
      name: "answer_base_url",
      ok: answerBaseUrlReady,
      message: answerBaseUrl || preflight?.models?.answer?.base_url || "未配置回答模型地址",
    },
    {
      name: "answer_token",
      ok: answerTokenReady,
      message: answerTokenReady
        ? (backendId() === "echomemory" ? "EchoMemory embedding/chat token 已就绪" : "回答模型 token 已就绪")
        : (backendId() === "echomemory" ? "EchoMemory embedding/chat token 未就绪" : "回答模型 token 未就绪"),
    },
    {
      name: "runtime",
      ok: backendId() !== "echomemory" || !hasPreflight || runtime?.status !== "fail",
      message: backendId() === "echomemory"
        ? (runtime?.message || runtime?.label || "EchoMemory runtime")
        : "OpenViking runtime 由后端服务负责",
    },
    {
      name: "import_ready",
      ok: importReady,
      message: latestImport
        ? `${latestImport?.sample || "all"} · ${latestImport?.status || "-"}`
        : flowImportReady
          ? `workspace 已检测到 ${flowImport.completeCount || flowImport.summaryCount || flowImport.sessionCount} 条导入摘要/会话`
          : flowProbeUnavailable
            ? `导入范围探测超时，先按当前 workspace 继续（${flowProbeError || "locomo-flow-status timeout"}）`
          : (hasRunHistory ? "当前账户 / workspace / 数据集范围还没有匹配的 LoCoMo 导入记录" : "当前还没有导入历史记录"),
    },
    {
      name: "memory_scope",
      ok: memoryScopeOk,
      message: latestImport?.sample
        ? `最近导入 ${latestImport.sample}；当前测试 ${normalizeSample(form.sample)}`
        : flowScopeReady
          ? `workspace 已发现当前 sample=${normalizeSample(form.sample)} 的导入摘要`
          : flowProbeUnavailable
            ? `导入 sample 范围探测超时，暂按当前 sample=${normalizeSample(form.sample)} 继续`
          : "当前没有导入范围记录",
    },
    {
      name: "single_flight",
      ok: !blockingTask,
      message: blockingTask
        ? `${blockingTask.name || blockingTask.id || blockingTask.kind} 正在运行相同 workspace / dataset 的 LoCoMo QA`
        : "当前 workspace / dataset 没有冲突任务",
    },
  ];
  return {
    ok: checks.every((item) => item.ok),
    title: "QA 启动检查",
    subtitle: blockingTask
      ? "检测到相同范围的活跃任务，已阻止重复启动。"
      : "启动前检查当前 LoCoMo QA 范围、记忆导入范围和运行依赖。",
    checks,
    blockingTask,
    latestImport,
  };
}

export function buildLocomoJudgePreflightModel({
  currentRun,
  currentTasks,
  form,
  validateResult,
  currentAccountConfig,
  pendingPreview,
}) {
  const run = currentRun();
  const outputFile = normalizePath(run?.output_file);
  const cfg = typeof currentAccountConfig === "function" ? currentAccountConfig() : (currentAccountConfig || {});
  const activeWriter = (currentTasks || []).find((task) => {
    if (!isActiveStatus(task?.status)) return false;
    const taskOutput = normalizePath(task?.output_file);
    const taskInput = normalizePath(task?.meta?.config?.input);
    return (taskOutput && samePath(taskOutput, outputFile)) || (taskInput && samePath(taskInput, outputFile));
  }) || null;
  const validateChecks = Array.isArray(validateResult?.checks) ? validateResult.checks : [];
  const totalPending = Number(pendingPreview?.total_pending || 0);
  const checks = [
    {
      name: "result_csv",
      ok: Boolean(outputFile),
      message: outputFile || "当前没有可评分结果文件",
    },
    {
      name: "writer_idle",
      ok: !activeWriter,
      message: activeWriter
        ? `${activeWriter.name || activeWriter.id || activeWriter.kind} 仍在写结果文件`
        : "当前结果文件未被其他活跃任务写入",
    },
    {
      name: "judge_base_url",
      ok: Boolean(normalizeText(cfg?.judgeBaseUrl)),
      message: normalizeText(cfg?.judgeBaseUrl) || "未配置 judge base url",
    },
    {
      name: "judge_model",
      ok: Boolean(normalizeText(cfg?.judgeModel)),
      message: normalizeText(cfg?.judgeModel) || "未配置 judge model",
    },
    {
      name: "judge_token",
      ok: Boolean(normalizeText(cfg?.judgeToken) || validateChecks.find((item) => item.name === "judge_token")?.ok),
      message: normalizeText(cfg?.judgeToken) ? "judge token 已显式配置" : "未显式配置 judge token",
    },
    {
      name: "pending_rows",
      ok: true,
      message: totalPending > 0 ? `${totalPending} 行待判分` : "当前结果没有待判分行",
    },
    ...validateChecks.map((item) => ({
      name: item.name,
      ok: item.ok !== false,
      message: item.message || "",
    })),
  ];
  return {
    ok: checks.every((item) => item.ok),
    title: "Judge 预检查",
    subtitle: activeWriter
      ? "结果文件仍在写入，暂不允许判分。"
      : (totalPending > 0 ? "判分前确认 CSV、字段、judge 配置和待判分范围。" : "当前结果已无待判分样本；可以刷新结果或继续查看报告。"),
    checks,
    validateResult,
    outputFile,
    dataPath: normalizePath(form?.data),
    pendingPreview,
  };
}

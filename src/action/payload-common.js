export function buildWorkspaceTaskContext({
  backendId,
  cfg,
  currentWorkspace,
  firstValue,
  state,
  workspace,
}) {
  return {
    workspace: workspace || currentWorkspace(),
    backend: backendId(),
    memoryBackend: backendId(),
    account: state.selectedAccount,
    host: firstValue(cfg.ovHost, state.config?.server_host, "127.0.0.1"),
    port: String(firstValue(cfg.ovPort, state.config?.server_port, "19080")),
  };
}

export function buildModelEndpointFields({ cfg, firstValue, state }) {
  return {
    answer_base_url: firstValue(cfg.agentBaseUrl, ""),
    answer_model: firstValue(cfg.agentModel, state.config?.answer_model, ""),
    answer_token: firstValue(cfg.answerToken, cfg.agentToken, cfg.judgeToken, ""),
    judge_base_url: firstValue(cfg.judgeBaseUrl, state.config?.judge_base_url, ""),
    judge_model: firstValue(cfg.judgeModel, state.config?.judge_model, ""),
    judge_token: firstValue(cfg.judgeToken, cfg.answerToken, cfg.agentToken, ""),
    memory_base_url: firstValue(cfg.memoryInjectBaseUrl, ""),
    embedding_base_url: firstValue(cfg.memoryInjectBaseUrl, ""),
    memory_inject_model: firstValue(cfg.memoryInjectModel, ""),
    vlm_api_key: firstValue(cfg.memoryInjectToken, cfg.answerToken, cfg.agentToken, cfg.judgeToken, ""),
    echomem_chat_api_key: firstValue(cfg.agentToken, cfg.answerToken, cfg.judgeToken, ""),
    echomem_chat_base_url: firstValue(cfg.agentBaseUrl, ""),
    echomem_chat_model: firstValue(cfg.agentModel, state.config?.answer_model, ""),
  };
}

export function buildEchoMemoryIdentityFields({ cfg, firstValue, state, overrides = {} }) {
  const echomemRoot = String(
    overrides.echomem_root
    ?? firstValue(
      cfg.echomemRoot,
      state.config?.echomemRoot,
      state.readiness?.preflight?.runtime?.root,
      "",
    )
  ).trim();
  const userId = String(
    overrides.user_id
    ?? overrides.em_user_id
    ?? firstValue(cfg.memoryUserId, "default")
  ).trim() || "default";
  const agentId = String(
    overrides.agent_id
    ?? overrides.em_agent_id
    ?? firstValue(cfg.memoryAgentId, "default")
  ).trim() || "default";
  const echomemBaseUrl = String(
    overrides.echomem_base_url
    ?? firstValue(cfg.echomemBaseUrl, state.config?.echomemBaseUrl, state.readiness?.preflight?.runtime?.url, "")
  ).trim();
  const rawTransport = String(
    overrides.echomem_transport
    ?? firstValue(cfg.echomemTransport, "")
  ).trim().toLowerCase();
  const echomemTransport = echomemBaseUrl ? "http" : rawTransport;
  const echomemAuthKey = String(
    overrides.echomem_auth_key
    ?? firstValue(cfg.echomemAuthKey, "")
  ).trim();
  return {
    user_id: userId,
    agent_id: agentId,
    em_user_id: userId,
    em_agent_id: agentId,
    echomem_root: echomemRoot,
    echomem_base_url: echomemBaseUrl,
    echomem_transport: echomemTransport,
    echomem_auth_key: echomemAuthKey,
  };
}

export function buildOpenVikingIdentityFields({ cfg, firstValue, extras = {} }) {
  return {
    ov_user_id: firstValue(cfg.memoryUserId, "default"),
    ov_agent_id: firstValue(cfg.memoryAgentId, "default"),
    ...extras,
  };
}

export function applyProviderIdentity(payload, {
  backendId,
  cfg,
  firstValue,
  state,
  openvikingExtras,
}) {
  if (backendId() === "echomemory") {
    Object.assign(payload, buildEchoMemoryIdentityFields({ cfg, firstValue, state }));
    return payload;
  }
  Object.assign(payload, buildOpenVikingIdentityFields({ cfg, firstValue, extras: openvikingExtras }));
  return payload;
}

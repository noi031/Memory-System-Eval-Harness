import { compactImportRow, renderCompactImportConfig } from "./import-generic.js";

export function renderEchoAgentLiveImportConfig({
  currentAccountConfig,
  escapeHtml,
  firstValue,
}) {
  const config = currentAccountConfig() || {};
  const echoagentUrl = firstValue(config.echoagent_url, "http://127.0.0.1:31020");
  const echomemUrl = firstValue(config.echomem_url, "http://127.0.0.1:8010");
  const username = firstValue(config.echoagent_username, "test_user");
  const password = firstValue(config.echoagent_password, "test_password");
  const numBatches = firstValue(config.echoagent_num_batches, "3");
  const queriesPerBatch = firstValue(config.echoagent_queries_per_batch, "5");
  const scenarioModel = firstValue(config.echoagent_scenario_model, "deepseek-v4-flash");

  return renderCompactImportConfig({
    escapeHtml,
    rows: [
      // EchoAgent 连接配置
      { label: "EchoAgent 连接配置", isHeader: true },
      compactImportRow("EchoAgent URL", `<input id="wbEchoAgentUrl" type="text" value="${escapeHtml(echoagentUrl)}" placeholder="http://127.0.0.1:31020">`, escapeHtml),
      compactImportRow("EchoMem URL", `<input id="wbEchoMemUrl" type="text" value="${escapeHtml(echomemUrl)}" placeholder="http://127.0.0.1:8010">`, escapeHtml),
      compactImportRow("用户名", `<input id="wbEchoAgentUsername" type="text" value="${escapeHtml(username)}" placeholder="test_user">`, escapeHtml),
      compactImportRow("密码", `<input id="wbEchoAgentPassword" type="password" value="${escapeHtml(password)}" placeholder="test_password">`, escapeHtml),
      // 测试参数
      { label: "测试参数", isHeader: true },
      compactImportRow("测试批次", `<input id="wbNumBatches" type="number" min="1" max="20" value="${escapeHtml(numBatches)}">`, escapeHtml),
      compactImportRow("每批查询数", `<input id="wbQueriesPerBatch" type="number" min="1" max="20" value="${escapeHtml(queriesPerBatch)}">`, escapeHtml),
      // 场景设置
      { label: "场景设置", isHeader: true },
      compactImportRow("自定义场景", `<textarea id="wbCustomScenario" rows="4" style="width:100%;resize:vertical;" placeholder="输入自定义场景描述，如：用户在电商平台上购买了一台笔记本电脑，询问关于保修、退换货政策等问题。留空则由 LLM 自动生成场景。">${escapeHtml(config.echoagent_custom_scenario || "")}</textarea>`, escapeHtml),
      compactImportRow("场景生成模型", `<input id="wbScenarioModel" type="text" value="${escapeHtml(scenarioModel)}" placeholder="deepseek-v4-flash">`, escapeHtml),
      compactImportRow("API Base URL", `<input id="wbScenarioBaseUrl" type="text" value="${escapeHtml(config.echoagent_scenario_base_url || "")}" placeholder="留空使用环境变量">`, escapeHtml),
      compactImportRow("API Key", `<input id="wbScenarioApiKey" type="password" value="${escapeHtml(config.echoagent_scenario_api_key || "")}" placeholder="留空使用环境变量">`, escapeHtml),
    ],
    showActions: true,
    primaryLabel: "开始测试",
    stopLabel: "停止任务",
  });
}

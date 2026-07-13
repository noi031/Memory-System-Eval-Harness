function finiteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatNumber(value, digits = 1) {
  const number = finiteNumber(value);
  if (number === null) return "N/A";
  return number.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatInteger(value) {
  const number = finiteNumber(value);
  if (number === null) return "N/A";
  return Math.round(number).toLocaleString("zh-CN");
}

function formatPercent(value) {
  const number = finiteNumber(value);
  if (number === null) return "N/A";
  return `${(number * 100).toFixed(2)}%`;
}

function formatFraction(numerator, denominator, suffix = "条观测") {
  const left = finiteNumber(numerator);
  const right = finiteNumber(denominator);
  if (left === null || right === null || right <= 0) return "无完整观测";
  return `${formatInteger(left)} / ${formatInteger(right)} ${suffix}`;
}

function headlineMetric(label, value, note, escapeHtml, tone = "") {
  return `
    <article class="wb-strict-metric ${escapeHtml(tone)}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(note)}</small>
    </article>
  `;
}

function statsTable(title, rows, escapeHtml, { token = false } = {}) {
  const cells = token
    ? ["平均", "P50", "P95", "P99", "合计"]
    : ["平均", "P50", "P95", "P99", "最大"];
  return `
    <details class="wb-strict-fold">
      <summary>
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(`${rows.length} 项严格观测`)}</span>
      </summary>
      <div class="wb-strict-table-wrap">
        <table class="wb-strict-table">
          <thead>
            <tr><th>指标</th>${cells.map((cell) => `<th>${escapeHtml(cell)}</th>`).join("")}</tr>
          </thead>
          <tbody>
            ${rows.map((row) => {
              const stats = row.stats || {};
              const values = token
                ? [stats.avg, stats.p50, stats.p95, stats.p99, stats.sum]
                : [stats.avg, stats.p50, stats.p95, stats.p99, stats.max];
              return `
                <tr>
                  <th>${escapeHtml(row.label)}</th>
                  ${values.map((value) => `<td>${escapeHtml(formatNumber(value, token ? 0 : 1))}${token || finiteNumber(value) === null ? "" : " ms"}</td>`).join("")}
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
      </div>
    </details>
  `;
}

function categoryTable(categories, escapeHtml) {
  const rows = Object.entries(categories || {});
  if (!rows.length) return "";
  return `
    <details class="wb-strict-fold">
      <summary>
        <strong>分类准确率</strong>
        <span>${escapeHtml(`${rows.length} 个类别`)}</span>
      </summary>
      <div class="wb-strict-table-wrap">
        <table class="wb-strict-table">
          <thead><tr><th>类别</th><th>已判分</th><th>正确</th><th>错误</th><th>准确率</th></tr></thead>
          <tbody>
            ${rows.map(([name, item]) => {
              const correct = finiteNumber(item?.correct) || 0;
              const wrong = finiteNumber(item?.wrong) || 0;
              const graded = correct + wrong;
              return `
                <tr>
                  <th>${escapeHtml(name)}</th>
                  <td>${escapeHtml(formatInteger(graded))}</td>
                  <td>${escapeHtml(formatInteger(correct))}</td>
                  <td>${escapeHtml(formatInteger(wrong))}</td>
                  <td>${escapeHtml(graded > 0 ? formatPercent(correct / graded) : "N/A")}</td>
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
      </div>
    </details>
  `;
}

function definitions(definitionItems, escapeHtml) {
  if (!Array.isArray(definitionItems) || !definitionItems.length) return "";
  return `
    <details class="wb-strict-fold wb-strict-definitions">
      <summary>
        <strong>指标定义与黑盒边界</strong>
        <span>${escapeHtml(`${definitionItems.length} 项`)}</span>
      </summary>
      <div class="wb-strict-definition-list">
        ${definitionItems.map((item) => `
          <details class="wb-strict-definition">
            <summary>
              <strong>${escapeHtml(item?.name || "-")}</strong>
              <span>${escapeHtml(item?.kind || "-")}</span>
            </summary>
            <div>
              <p><b>计算</b><code>${escapeHtml(item?.formula || "N/A")}</code></p>
              <p><b>来源</b>${escapeHtml(item?.source || "N/A")}</p>
              <p><b>含义</b>${escapeHtml(item?.meaning || "N/A")}</p>
              <p><b>边界</b>${escapeHtml(item?.boundary || "N/A")}</p>
            </div>
          </details>
        `).join("")}
      </div>
    </details>
  `;
}

export function renderStrictBlackboxMetrics(strictBlackbox, {
  escapeHtml,
  compact = false,
} = {}) {
  const metrics = strictBlackbox?.metrics;
  if (!metrics || typeof metrics !== "object") return "";

  const headline = [
    headlineMetric(
      "QA 请求成功率",
      formatPercent(metrics.request_success_rate),
      formatFraction(metrics.request_success_count, metrics.request_status_count),
      escapeHtml,
      "success",
    ),
    headlineMetric(
      "空召回率",
      formatPercent(metrics.empty_retrieval_rate),
      formatFraction(metrics.empty_retrieval_count, metrics.retrieval_observed_count),
      escapeHtml,
      finiteNumber(metrics.empty_retrieval_count) > 0 ? "warn" : "",
    ),
    headlineMetric(
      "最终失败率",
      formatPercent(metrics.failure_rate),
      formatFraction(metrics.failure_count, metrics.request_status_count),
      escapeHtml,
      finiteNumber(metrics.failure_count) > 0 ? "danger" : "",
    ),
    headlineMetric(
      "外部可见模型重试率",
      formatPercent(metrics.retry_rate),
      formatFraction(metrics.retried_count, metrics.retry_observed_count),
      escapeHtml,
    ),
    headlineMetric(
      "每个正确答案 Token",
      formatNumber(metrics.tokens_per_correct, 1),
      "回答模型总 Token / 正确题数",
      escapeHtml,
    ),
    headlineMetric(
      "消息提交率",
      formatPercent(metrics.submission_rate),
      formatFraction(metrics.submitted_messages, metrics.expected_messages, "条消息"),
      escapeHtml,
    ),
    headlineMetric(
      "记忆导入状态",
      String(metrics.import_status || "N/A"),
      "直接读取导入摘要，不推断后台完成",
      escapeHtml,
    ),
    headlineMetric(
      "内部记忆注入 Token",
      "N/A",
      "黑盒 API 未返回权威 usage",
      escapeHtml,
    ),
  ].join("");

  return `
    <section class="wb-strict-panel ${compact ? "compact" : ""}">
      <header class="wb-strict-head">
        <div>
          <span>Observed at API boundary</span>
          <h3>严格黑盒指标</h3>
          <p>只使用结果 CSV 和导入摘要中的实际观测字段；缺失值显示 N/A，不做 Token 或耗时推算。</p>
        </div>
        <strong>${escapeHtml(formatInteger(strictBlackbox.row_count))} 题</strong>
      </header>
      <div class="wb-strict-grid">${headline}</div>
      ${compact ? "" : `
        <div class="wb-strict-folds">
          ${categoryTable(metrics.categories, escapeHtml)}
          ${statsTable("时延分布", [
            {label: "端到端 QA", stats: metrics.end_to_end_ms},
            {label: "记忆检索", stats: metrics.retrieval_latency_ms},
            {label: "QA 侧编排注入", stats: metrics.injection_total_ms},
            {label: "回答模型", stats: metrics.llm_total_ms},
          ], escapeHtml)}
          ${statsTable("回答模型 Token（API usage）", [
            {label: "Prompt Token", stats: metrics.answer_prompt_tokens},
            {label: "Completion Token", stats: metrics.answer_completion_tokens},
            {label: "回答总 Token", stats: metrics.answer_total_tokens},
          ], escapeHtml, {token: true})}
          ${definitions(strictBlackbox.definitions, escapeHtml)}
        </div>
      `}
    </section>
  `;
}

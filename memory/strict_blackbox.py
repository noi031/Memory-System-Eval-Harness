from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.generate_html_report import (
    load_results,
    observed_blackbox_metrics,
    strict_metric_definitions,
)


STRICT_BLACKBOX_METRICS_FILENAME = "strict_blackbox_metrics.json"
STRICT_BLACKBOX_REPORT_FILENAME = "strict_blackbox_report.html"
STRICT_BLACKBOX_SCHEMA_VERSION = 2


def _import_summary(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(summary, dict):
        return None
    import_summary = summary.get("import_summary")
    if isinstance(import_summary, dict):
        return import_summary
    summary_json = summary.get("summary_json")
    if isinstance(summary_json, dict) and isinstance(summary_json.get("import_summary"), dict):
        return summary_json["import_summary"]
    return None


def strict_blackbox_metrics_path(csv_path: Path) -> Path:
    return csv_path.parent / STRICT_BLACKBOX_METRICS_FILENAME


def strict_blackbox_report_path(csv_path: Path) -> Path:
    return csv_path.parent / STRICT_BLACKBOX_REPORT_FILENAME


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _format_number(value: Any, digits: int = 1) -> str:
    number = _number(value)
    return "N/A" if number is None else f"{number:,.{digits}f}"


def _format_integer(value: Any) -> str:
    number = _number(value)
    return "N/A" if number is None else f"{round(number):,}"


def _format_percent(value: Any) -> str:
    number = _number(value)
    return "N/A" if number is None else f"{number * 100:.2f}%"


def _fraction(numerator: Any, denominator: Any, suffix: str = "条观测") -> str:
    left = _number(numerator)
    right = _number(denominator)
    if left is None or right is None or right <= 0:
        return "无完整观测"
    return f"{round(left):,} / {round(right):,} {suffix}"


def _metric_card(label: str, value: str, detail: str, tone: str = "") -> str:
    return (
        f'<article class="metric-card {html.escape(tone)}">'
        f"<span>{html.escape(label)}</span>"
        f"<strong>{html.escape(value)}</strong>"
        f"<small>{html.escape(detail)}</small>"
        "</article>"
    )


def _rate_bar(label: str, value: Any, tone: str = "") -> str:
    number = _number(value)
    width = 0 if number is None else min(max(number * 100, 0), 100)
    return (
        '<div class="rate-row">'
        f"<div><span>{html.escape(label)}</span><strong>{html.escape(_format_percent(value))}</strong></div>"
        f'<div class="track"><i class="{html.escape(tone)}" style="width:{width:.2f}%"></i></div>'
        "</div>"
    )


def _stats_table(title: str, rows: list[tuple[str, dict[str, Any] | None]], token: bool = False) -> str:
    final_label = "合计" if token else "最大"
    body = []
    for label, stats in rows:
        values = stats or {}
        keys = ("avg", "p50", "p95", "p99", "sum" if token else "max")
        cells = []
        for key in keys:
            value = _format_number(values.get(key), 0 if token else 1)
            suffix = "" if token or value == "N/A" else " ms"
            cells.append(f"<td>{html.escape(value + suffix)}</td>")
        body.append(f"<tr><th>{html.escape(label)}</th>{''.join(cells)}</tr>")
    return (
        '<section class="data-section">'
        f"<h2>{html.escape(title)}</h2>"
        '<div class="table-wrap"><table><thead><tr><th>指标</th><th>平均</th><th>P50</th>'
        f"<th>P95</th><th>P99</th><th>{final_label}</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div></section>"
    )


def render_strict_blackbox_report(snapshot: dict[str, Any]) -> str:
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    definitions = snapshot.get("definitions") if isinstance(snapshot.get("definitions"), list) else []
    categories = metrics.get("categories") if isinstance(metrics.get("categories"), dict) else {}
    report_path = str(snapshot.get("report_path") or "")
    source_csv = str(snapshot.get("source_csv") or "")

    cards = [
        _metric_card(
            "准确率",
            _format_percent(metrics.get("accuracy")),
            _fraction(metrics.get("correct_count"), metrics.get("graded_count"), "已判题"),
            "success",
        ),
        _metric_card(
            "QA 请求成功率",
            _format_percent(metrics.get("request_success_rate")),
            _fraction(metrics.get("request_success_count"), metrics.get("request_status_count")),
            "success",
        ),
        _metric_card(
            "空召回率",
            _format_percent(metrics.get("empty_retrieval_rate")),
            _fraction(metrics.get("empty_retrieval_count"), metrics.get("retrieval_observed_count")),
            "warn",
        ),
        _metric_card(
            "最终失败率",
            _format_percent(metrics.get("failure_rate")),
            _fraction(metrics.get("failure_count"), metrics.get("request_status_count")),
            "danger",
        ),
        _metric_card(
            "模型重试率",
            _format_percent(metrics.get("retry_rate")),
            _fraction(metrics.get("retried_count"), metrics.get("retry_observed_count")),
        ),
        _metric_card(
            "每个正确答案 Token",
            _format_number(metrics.get("tokens_per_correct"), 1),
            "回答总 Token / 正确题数",
        ),
        _metric_card(
            "消息提交率",
            _format_percent(metrics.get("submission_rate")),
            _fraction(metrics.get("submitted_messages"), metrics.get("expected_messages"), "条消息"),
        ),
        _metric_card(
            "记忆导入状态",
            str(metrics.get("import_status") or "N/A"),
            "直接读取后端导入摘要",
            "status",
        ),
    ]

    category_rows = []
    for name, raw_item in categories.items():
        item = raw_item if isinstance(raw_item, dict) else {}
        correct = int(_number(item.get("correct")) or 0)
        wrong = int(_number(item.get("wrong")) or 0)
        graded = correct + wrong
        accuracy = correct / graded if graded else None
        width = 0 if accuracy is None else accuracy * 100
        category_rows.append(
            "<tr>"
            f"<th>{html.escape(str(name))}</th><td>{graded:,}</td><td>{correct:,}</td><td>{wrong:,}</td>"
            f"<td><div class=\"category-score\"><i style=\"width:{width:.2f}%\"></i>"
            f"<strong>{html.escape(_format_percent(accuracy))}</strong></div></td></tr>"
        )
    category_html = ""
    if category_rows:
        category_html = (
            '<section class="data-section"><h2>分类准确率</h2><div class="table-wrap"><table>'
            "<thead><tr><th>类别</th><th>已判分</th><th>正确</th><th>错误</th><th>准确率</th></tr></thead>"
            f"<tbody>{''.join(category_rows)}</tbody></table></div></section>"
        )

    definition_html = "".join(
        "<details><summary>"
        f"<strong>{html.escape(str(item.get('name') or '-'))}</strong>"
        f"<span>{html.escape(str(item.get('kind') or '-'))}</span></summary>"
        "<div class=\"definition-body\">"
        f"<p><b>计算</b><code>{html.escape(str(item.get('formula') or 'N/A'))}</code></p>"
        f"<p><b>来源</b>{html.escape(str(item.get('source') or 'N/A'))}</p>"
        f"<p><b>含义</b>{html.escape(str(item.get('meaning') or 'N/A'))}</p>"
        f"<p><b>边界</b>{html.escape(str(item.get('boundary') or 'N/A'))}</p>"
        "</div></details>"
        for item in definitions
        if isinstance(item, dict)
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>严格黑盒指标报告</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #18201d;
      --muted: #66706b;
      --line: #dfe5e1;
      --surface: #ffffff;
      --canvas: #f4f6f4;
      --green: #18794e;
      --green-soft: #e6f4ec;
      --amber: #9a6700;
      --amber-soft: #fff4d6;
      --red: #b42318;
      --red-soft: #feeceb;
      --blue: #2864a8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--canvas);
      color: var(--ink);
      font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
      letter-spacing: 0;
    }}
    main {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 42px 0 64px; }}
    header {{ display: flex; justify-content: space-between; gap: 28px; align-items: end; margin-bottom: 28px; }}
    .eyebrow {{ color: var(--green); font-size: 12px; font-weight: 750; text-transform: uppercase; }}
    h1 {{ margin: 8px 0 10px; font-size: clamp(28px, 4vw, 44px); line-height: 1.08; }}
    header p {{ margin: 0; color: var(--muted); max-width: 700px; line-height: 1.7; }}
    .meta {{ min-width: 230px; padding-left: 18px; border-left: 2px solid var(--green); }}
    .meta span, .meta code {{ display: block; font-size: 12px; color: var(--muted); }}
    .meta strong {{ display: block; margin: 4px 0 10px; font-size: 22px; }}
    .meta code {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 320px; }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .metric-card {{ min-height: 126px; padding: 18px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; }}
    .metric-card span, .metric-card small {{ display: block; color: var(--muted); }}
    .metric-card span {{ font-size: 13px; font-weight: 650; }}
    .metric-card strong {{ display: block; margin: 12px 0 8px; font-size: 25px; line-height: 1.1; overflow-wrap: anywhere; }}
    .metric-card small {{ font-size: 12px; line-height: 1.45; }}
    .metric-card.success {{ border-top: 3px solid var(--green); }}
    .metric-card.warn {{ border-top: 3px solid var(--amber); }}
    .metric-card.danger {{ border-top: 3px solid var(--red); }}
    .metric-card.status {{ border-top: 3px solid var(--blue); }}
    .visuals {{ display: grid; grid-template-columns: 1.25fr .75fr; gap: 16px; margin-top: 16px; }}
    .panel, .data-section {{ margin-top: 16px; padding: 22px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; }}
    .visuals .panel {{ margin-top: 0; }}
    h2 {{ margin: 0 0 18px; font-size: 17px; }}
    .rate-row + .rate-row {{ margin-top: 18px; }}
    .rate-row > div:first-child {{ display: flex; justify-content: space-between; gap: 16px; margin-bottom: 7px; font-size: 13px; }}
    .track {{ height: 9px; overflow: hidden; border-radius: 4px; background: #e9eeeb; }}
    .track i {{ display: block; height: 100%; background: var(--green); }}
    .track i.warn {{ background: var(--amber); }}
    .track i.danger {{ background: var(--red); }}
    .unavailable {{ display: grid; gap: 12px; }}
    .na-item {{ padding: 14px; background: #f7f8f7; border-left: 3px solid #9aa39e; }}
    .na-item strong, .na-item span {{ display: block; }}
    .na-item span {{ margin-top: 6px; color: var(--muted); font-size: 12px; line-height: 1.5; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 12px 10px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }}
    th:first-child, td:first-child {{ text-align: left; }}
    thead th {{ color: var(--muted); font-size: 11px; text-transform: uppercase; }}
    tbody tr:last-child th, tbody tr:last-child td {{ border-bottom: 0; }}
    .category-score {{ display: grid; grid-template-columns: minmax(80px, 1fr) 62px; align-items: center; gap: 10px; }}
    .category-score::before {{ content: ""; grid-column: 1; grid-row: 1; height: 7px; border-radius: 4px; background: #e9eeeb; }}
    .category-score i {{ grid-column: 1; grid-row: 1; height: 7px; border-radius: 4px; background: var(--green); }}
    .category-score strong {{ grid-column: 2; grid-row: 1; }}
    details {{ border-top: 1px solid var(--line); }}
    details:first-child {{ border-top: 0; }}
    summary {{ display: flex; justify-content: space-between; gap: 18px; padding: 15px 0; cursor: pointer; }}
    summary span {{ color: var(--muted); font-size: 12px; }}
    .definition-body {{ padding: 0 0 18px; color: var(--muted); font-size: 13px; line-height: 1.65; }}
    .definition-body p {{ display: grid; grid-template-columns: 48px 1fr; gap: 8px; margin: 7px 0; }}
    .definition-body b {{ color: var(--ink); }}
    code {{ font-family: "SFMono-Regular", Consolas, monospace; overflow-wrap: anywhere; white-space: normal; }}
    footer {{ margin-top: 18px; color: var(--muted); font-size: 12px; line-height: 1.6; }}
    @media (max-width: 820px) {{
      main {{ width: min(100% - 22px, 680px); padding-top: 26px; }}
      header {{ display: block; }}
      .meta {{ margin-top: 20px; }}
      .metric-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .visuals {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 480px) {{
      .metric-grid {{ grid-template-columns: 1fr; }}
      .metric-card {{ min-height: 0; }}
      .panel, .data-section {{ padding: 17px; }}
    }}
    @media print {{
      body {{ background: #fff; }}
      main {{ width: 100%; padding: 0; }}
      .metric-card, .panel, .data-section {{ break-inside: avoid; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <span class="eyebrow">Strict observed metrics</span>
      <h1>严格黑盒指标报告</h1>
      <p>仅使用结果 CSV 与导入摘要中的实际观测字段。缺失值保持 N/A，不以字符数、经验系数或推断时间补齐。</p>
    </div>
    <div class="meta">
      <span>本次运行</span>
      <strong>{html.escape(_format_integer(snapshot.get("row_count")))} 题</strong>
      <span>{html.escape(str(snapshot.get("generated_at") or ""))}</span>
      <code title="{html.escape(source_csv)}">{html.escape(source_csv)}</code>
    </div>
  </header>
  <section class="metric-grid">{''.join(cards)}</section>
  <section class="visuals">
    <div class="panel">
      <h2>核心比例</h2>
      {_rate_bar("准确率", metrics.get("accuracy"))}
      {_rate_bar("QA 请求成功率", metrics.get("request_success_rate"))}
      {_rate_bar("消息提交率", metrics.get("submission_rate"))}
      {_rate_bar("空召回率", metrics.get("empty_retrieval_rate"), "warn")}
      {_rate_bar("最终失败率", metrics.get("failure_rate"), "danger")}
      {_rate_bar("外部可见模型重试率", metrics.get("retry_rate"), "warn")}
    </div>
    <div class="panel">
      <h2>当前不可严格黑盒计算</h2>
      <div class="unavailable">
        <div class="na-item"><strong>内部记忆注入 Token · N/A</strong><span>黑盒 API 未返回权威 LLM / Embedding usage。</span></div>
        <div class="na-item"><strong>初始记忆导入时间 · N/A</strong><span>缺少后台处理完成事件，无法严格确定结束时刻。</span></div>
      </div>
    </div>
  </section>
  {category_html}
  {_stats_table("时延分布", [
      ("端到端 QA", metrics.get("end_to_end_ms")),
      ("记忆检索", metrics.get("retrieval_latency_ms")),
      ("QA 侧编排注入", metrics.get("injection_total_ms")),
      ("回答模型", metrics.get("llm_total_ms")),
  ])}
  {_stats_table("回答模型 Token（API usage）", [
      ("Prompt Token", metrics.get("answer_prompt_tokens")),
      ("Completion Token", metrics.get("answer_completion_tokens")),
      ("回答总 Token", metrics.get("answer_total_tokens")),
  ], token=True)}
  <section class="data-section">
    <h2>指标定义与黑盒边界</h2>
    {definition_html}
  </section>
  <footer>
    <div>报告文件：<code>{html.escape(report_path)}</code></div>
    <div>数据快照：<code>{html.escape(str(snapshot.get("artifact_path") or ""))}</code></div>
  </footer>
</main>
</body>
</html>
"""


def _source_signature(csv_path: Path, import_summary: dict[str, Any] | None) -> str:
    stat = csv_path.stat()
    payload = {
        "schema_version": STRICT_BLACKBOX_SCHEMA_VERSION,
        "csv_path": str(csv_path.resolve()),
        "csv_size": stat.st_size,
        "csv_mtime_ns": stat.st_mtime_ns,
        "import_summary": import_summary or {},
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_existing_snapshot(path: Path, source_signature: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(snapshot, dict) or snapshot.get("source_signature") != source_signature:
        return None
    return snapshot


def build_strict_blackbox_snapshot(
    csv_path: Path,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_csv = csv_path.expanduser().resolve()
    rows = load_results(str(resolved_csv))
    import_summary = _import_summary(summary)
    artifact_path = strict_blackbox_metrics_path(resolved_csv).resolve()
    report_path = strict_blackbox_report_path(resolved_csv).resolve()
    metrics = observed_blackbox_metrics(rows, import_summary)
    metrics["internal_memory_injection_tokens"] = None
    metrics["initial_memory_import_time_ms"] = None
    return {
        "schema_version": STRICT_BLACKBOX_SCHEMA_VERSION,
        "kind": "strict_blackbox_metrics",
        "mode": "strict_observed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "artifact_path": str(artifact_path),
        "report_path": str(report_path),
        "source": str(resolved_csv),
        "source_csv": str(resolved_csv),
        "source_signature": _source_signature(resolved_csv, import_summary),
        "row_count": len(rows),
        "metrics": metrics,
        "definitions": strict_metric_definitions(),
        "unavailable": {
            "internal_memory_injection_tokens": None,
            "initial_memory_import_time_ms": None,
        },
    }


def ensure_strict_blackbox_snapshot(
    csv_path: Path,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    resolved_csv = csv_path.expanduser().resolve()
    if resolved_csv.suffix.lower() != ".csv" or not resolved_csv.exists() or not resolved_csv.is_file():
        return None
    import_summary = _import_summary(summary)
    signature = _source_signature(resolved_csv, import_summary)
    artifact_path = strict_blackbox_metrics_path(resolved_csv)
    existing = _read_existing_snapshot(artifact_path, signature)
    if existing is not None:
        report_path = strict_blackbox_report_path(resolved_csv)
        if not report_path.exists():
            temporary_report_path = report_path.with_suffix(f"{report_path.suffix}.tmp")
            temporary_report_path.write_text(render_strict_blackbox_report(existing), encoding="utf-8")
            temporary_report_path.replace(report_path)
        return existing
    snapshot = build_strict_blackbox_snapshot(resolved_csv, summary)
    temporary_path = artifact_path.with_suffix(f"{artifact_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(artifact_path)
    report_path = strict_blackbox_report_path(resolved_csv)
    temporary_report_path = report_path.with_suffix(f"{report_path.suffix}.tmp")
    temporary_report_path.write_text(render_strict_blackbox_report(snapshot), encoding="utf-8")
    temporary_report_path.replace(report_path)
    return snapshot


def merge_strict_blackbox_snapshot(
    summary: dict[str, Any] | None,
    csv_path: Path,
) -> dict[str, Any]:
    merged = dict(summary or {})
    try:
        snapshot = ensure_strict_blackbox_snapshot(csv_path, merged)
    except Exception:
        return merged
    if snapshot is not None:
        merged["strict_blackbox"] = snapshot
        merged["strict_blackbox_metrics_path"] = snapshot.get("artifact_path") or ""
        merged["strict_blackbox_report_path"] = snapshot.get("report_path") or ""
    return merged

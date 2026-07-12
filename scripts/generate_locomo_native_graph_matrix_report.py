#!/usr/bin/env python3
"""Generate a strict EchoMemory HTTP black-box LoCoMo conv-30 matrix report."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


QUESTION_TYPES = ("时间/日期", "事实/描述", "原因", "地点", "数量")

SCENARIO_LABELS = {
    "mixed": "PR123+125 Membase+Graph Top-25",
    "graph": "PR123+125 Graph-only Top-25",
}

STYLE = """
:root {
  --bg: #f5f7fa;
  --panel: #ffffff;
  --line: #dfe4ea;
  --text: #172033;
  --muted: #667085;
  --blue: #2563eb;
  --green: #15803d;
  --red: #b42318;
  --amber: #b45309;
  --purple: #6941c6;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
main { max-width: 1420px; margin: 0 auto; padding: 28px; }
h1 { margin: 0 0 4px; font-size: 26px; letter-spacing: 0; }
h2 { margin: 0 0 14px; font-size: 18px; letter-spacing: 0; }
h3 { margin: 18px 0 8px; font-size: 15px; letter-spacing: 0; }
p { margin: 8px 0; }
code {
  font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow-wrap: anywhere;
}
.muted { color: var(--muted); }
.panel, .metric {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.panel { margin: 16px 0; padding: 18px; }
.metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin: 20px 0;
}
.metric { min-width: 0; padding: 16px; }
.metric-label { color: var(--muted); font-size: 13px; }
.metric strong { display: block; margin: 3px 0; font-size: 28px; }
.metric-detail { color: var(--muted); }
.callout {
  margin: 12px 0;
  padding: 11px 14px;
  border-left: 3px solid var(--blue);
  background: #f8fafc;
}
.callout.warn { border-left-color: var(--amber); background: #fffbeb; }
.callout.pass { border-left-color: var(--green); background: #f0fdf4; }
.pass { color: var(--green); }
.bad { color: var(--red); }
.warn { color: var(--amber); }
table { width: 100%; border-collapse: collapse; }
th, td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}
th { color: var(--muted); background: #fafbfc; font-size: 12px; font-weight: 600; }
tr:last-child td { border-bottom: 0; }
.table-wrap { overflow: auto; border: 1px solid var(--line); border-radius: 6px; }
.nowrap { white-space: nowrap; }
.stack > div { margin: 2px 0; }
.small { font-size: 12px; }
ul { margin: 8px 0; padding-left: 20px; }
li { margin: 5px 0; }
@media (max-width: 900px) {
  main { padding: 16px; }
  .metrics { grid-template-columns: 1fr; }
}
"""


@dataclass
class RunData:
    key: str
    label: str
    run_dir: Path
    import_path: Path
    commit: str
    summary: dict[str, Any]
    rows: list[dict[str, str]]
    recalls: list[dict[str, Any]]
    correct: int
    wrong: int
    graded: int
    accuracy: float | None
    kind_counts: Counter[str]
    source_counts: Counter[str]
    native_graph_rows: int
    native_graph_total: int
    native_graph_average: float
    overview_enabled: bool | None
    overview_read: int | None
    overview_hit: int | None
    overview_injected: int | None
    audit: dict[str, Any]
    strict_augmentation_rows: int | None


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"无法解析 JSON：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层不是对象：{path}")
    return value


def read_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise OSError(f"无法读取 CSV：{path}: {exc}") from exc
    if not rows:
        raise ValueError(f"CSV 没有数据行：{path}")
    return rows


def parse_json_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    return None


def as_mapping(value: Any) -> dict[str, Any] | None:
    parsed = parse_json_value(value)
    return parsed if isinstance(parsed, dict) else None


def as_list(value: Any) -> list[Any]:
    parsed = parse_json_value(value)
    return parsed if isinstance(parsed, list) else []


def as_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return None


def count_mapping(value: Any) -> Counter[str] | None:
    mapping = as_mapping(value)
    if mapping is None:
        return None
    counts: Counter[str] = Counter()
    for key, raw_count in mapping.items():
        count = as_int(raw_count)
        if count is not None:
            counts[str(key)] += count
    return counts


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def display(value: Any, missing: str = "—") -> str:
    return missing if value is None or value == "" else esc(value)


def pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.2f}%"


def is_correct(row: dict[str, str]) -> bool:
    return str(row.get("result") or "").strip().upper() == "CORRECT"


def question_type(question: str) -> str:
    normalized = str(question or "").lower()
    if re.search(r"\bwhen\b|\bhow long\b|\bwhat date\b|\bwhat year\b|\bwhat time\b", normalized):
        return "时间/日期"
    if re.search(r"\bwhy\b", normalized):
        return "原因"
    if re.search(r"\bwhere\b|\bwhich cit|\bwhich countr|\bwhich loc", normalized):
        return "地点"
    if re.search(r"\bhow many\b|\bnumber\b|\bhow much\b", normalized):
        return "数量"
    return "事实/描述"


def is_graph_kind(kind: str) -> bool:
    normalized = str(kind or "").strip().lower()
    return normalized == "graph" or normalized.startswith("graph_") or normalized.endswith("_graph")


def native_counts_for_recall(
    recall: dict[str, Any],
    preferred_field: str,
    selected_field: str,
) -> Counter[str]:
    preferred = count_mapping(recall.get(preferred_field))
    if preferred is not None:
        return preferred
    counts: Counter[str] = Counter()
    for item in as_list(recall.get("selected")):
        if isinstance(item, dict):
            counts[str(item.get(selected_field) or "unknown")] += 1
    return counts


def fallback_field_mapping(recall: dict[str, Any], *fields: str) -> dict[str, Any] | None:
    for field in fields:
        mapping = as_mapping(recall.get(field))
        if mapping is not None:
            return mapping
    return None


def overview_recall_totals(
    recalls: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, bool]]:
    totals = {"read": 0, "hit": 0, "injected": 0}
    seen = {"read": False, "hit": False, "injected": False}
    for recall in recalls:
        audit = fallback_field_mapping(recall, "overview_http_audit") or {}
        read_value = next(
            (as_int(audit.get(key)) for key in ("http_read_count", "read_count") if audit.get(key) is not None),
            None,
        )
        hit_value = next(
            (as_int(audit.get(key)) for key in ("hit_count", "http_hit_count") if audit.get(key) is not None),
            None,
        )
        injected_value = as_int(recall.get("overview_injected_count"))
        if read_value is not None:
            totals["read"] += read_value
            seen["read"] = True
        if hit_value is not None:
            totals["hit"] += hit_value
            seen["hit"] = True
        if injected_value is not None:
            totals["injected"] += injected_value
            seen["injected"] = True
        elif "overview_hits" in recall:
            totals["injected"] += len(as_list(recall.get("overview_hits")))
            seen["injected"] = True
    return totals, seen


def csv_total(rows: list[dict[str, str]], *fields: str) -> tuple[int, bool]:
    total = 0
    seen = False
    for row in rows:
        for field in fields:
            if field in row and row.get(field) not in (None, ""):
                value = as_int(row.get(field), 0) or 0
                total += value
                seen = True
                break
    return total, seen


def summary_total(
    summary: dict[str, Any],
    fields: tuple[str, ...],
) -> int | None:
    for field in fields:
        if field in summary and summary.get(field) is not None:
            return as_int(summary.get(field))
    return None


def overview_enabled(
    summary: dict[str, Any],
    recalls: list[dict[str, Any]],
    rows: list[dict[str, str]],
) -> bool | None:
    for field in ("search_overview_enrichment_enabled", "http_overview_enrichment_enabled"):
        if field in summary:
            value = as_bool(summary.get(field))
            if value is not None:
                return value
    values = [
        as_bool(recall.get("http_overview_enrichment_enabled"))
        for recall in recalls
        if "http_overview_enrichment_enabled" in recall
    ]
    if values:
        return all(value is True for value in values)
    csv_values = [
        as_bool(row.get("http_overview_enrichment_enabled"))
        for row in rows
        if row.get("http_overview_enrichment_enabled") not in (None, "")
    ]
    if csv_values:
        return all(value is True for value in csv_values)
    return None


def transport_audit(summary: dict[str, Any]) -> dict[str, Any]:
    raw = as_mapping(summary.get("echomemory_transport_audit")) or {}
    request_counts = count_mapping(raw.get("request_counts")) or Counter()
    transport = raw.get("transport") or summary.get("echomem_transport")
    base_url = raw.get("base_url") or summary.get("echomem_base_url")
    local_value = as_int(
        raw.get("local_workspace_evidence_reads")
        if "local_workspace_evidence_reads" in raw
        else summary.get("local_workspace_evidence_reads")
    )
    neo4j_value = as_int(
        raw.get("platform_neo4j_queries")
        if "platform_neo4j_queries" in raw
        else summary.get("platform_neo4j_queries")
    )
    return {
        "transport": transport,
        "base_url": base_url,
        "request_counts": request_counts,
        "search": request_counts.get("POST /api/retrieval/search"),
        "read": request_counts.get("GET /fs/read"),
        "local": local_value,
        "neo4j": neo4j_value,
        "present": bool(raw),
    }


def strict_augmentation_rows(
    summary: dict[str, Any],
    rows: list[dict[str, str]],
) -> int | None:
    value = as_int(summary.get("strict_blackbox_augmentation_rows"))
    if value is not None:
        return value
    if any("strict_blackbox_augmentation_triggered" in row for row in rows):
        return sum(
            1
            for row in rows
            if as_bool(row.get("strict_blackbox_augmentation_triggered")) is True
        )
    return None


def infer_commit(explicit: str | None, *summaries: dict[str, Any]) -> str:
    if explicit:
        return explicit
    keys = (
        "commit",
        "git_commit",
        "commit_sha",
        "git_sha",
        "echomem_commit",
        "echomem_git_commit",
        "code_commit",
    )
    for summary in summaries:
        for key in keys:
            value = summary.get(key)
            if value not in (None, ""):
                return str(value)
    return "未提供"


def load_run(
    key: str,
    run_dir: Path,
    import_path: Path,
    commit: str | None,
) -> RunData:
    run_dir = run_dir.expanduser().resolve()
    summary_path = run_dir / "summary.json"
    csv_path = run_dir / "echomemory_memory_qa_results.csv"
    if not run_dir.is_dir():
        raise FileNotFoundError(f"{key} run 目录不存在：{run_dir}")
    if not summary_path.is_file():
        raise FileNotFoundError(f"{key} 缺少 summary.json：{summary_path}")
    if not csv_path.is_file():
        raise FileNotFoundError(f"{key} 缺少 echomemory_memory_qa_results.csv：{csv_path}")
    recall_paths = sorted(run_dir.glob("q*.recall.json"))
    if not recall_paths:
        raise FileNotFoundError(f"{key} 没有 q*.recall.json：{run_dir}")

    summary = read_json(summary_path)
    rows = read_rows(csv_path)
    recalls = [read_json(path) for path in recall_paths]

    csv_correct = sum(1 for row in rows if is_correct(row))
    csv_wrong = sum(
        1
        for row in rows
        if str(row.get("result") or "").strip().upper() == "WRONG"
    )
    correct = as_int(summary.get("correct"), csv_correct)
    wrong = as_int(summary.get("wrong"), csv_wrong)
    graded = as_int(summary.get("graded"), correct + wrong)
    accuracy = as_float(summary.get("accuracy"))
    if accuracy is None and graded:
        accuracy = correct / graded

    kind_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    native_graph_rows = 0
    native_graph_total = 0
    for recall in recalls:
        kinds = native_counts_for_recall(
            recall,
            "native_http_result_kind_counts",
            "memory_type",
        )
        sources = native_counts_for_recall(
            recall,
            "native_http_result_source_counts",
            "source",
        )
        kind_counts.update(kinds)
        source_counts.update(sources)
        graph_count = sum(count for kind, count in kinds.items() if is_graph_kind(kind))
        if graph_count:
            native_graph_rows += 1
            native_graph_total += graph_count

    recall_overview, recall_seen = overview_recall_totals(recalls)
    csv_overview = {
        "read": csv_total(rows, "overview_http_read_count"),
        "hit": csv_total(rows, "overview_http_hit_count"),
        "injected": csv_total(rows, "overview_injected_count"),
    }
    overview_keys = {
        "read": ("overview_http_read_count_total", "overview_http_read_total"),
        "hit": ("overview_http_hit_count_total", "overview_http_hit_total"),
        "injected": ("overview_injected_count_total", "overview_injected_total"),
    }
    overview_values: dict[str, int | None] = {}
    for name in ("read", "hit", "injected"):
        summary_value = summary_total(summary, overview_keys[name])
        if summary_value is not None:
            overview_values[name] = summary_value
        elif recall_seen[name]:
            overview_values[name] = recall_overview[name]
        elif csv_overview[name][1]:
            overview_values[name] = csv_overview[name][0]
        else:
            overview_values[name] = None

    audit = transport_audit(summary)
    return RunData(
        key=key,
        label=SCENARIO_LABELS[key],
        run_dir=run_dir,
        import_path=import_path.expanduser().resolve(),
        commit=commit or infer_commit(None, summary),
        summary=summary,
        rows=rows,
        recalls=recalls,
        correct=correct or 0,
        wrong=wrong or 0,
        graded=graded or 0,
        accuracy=accuracy,
        kind_counts=kind_counts,
        source_counts=source_counts,
        native_graph_rows=native_graph_rows,
        native_graph_total=native_graph_total,
        native_graph_average=native_graph_total / len(recalls) if recalls else 0.0,
        overview_enabled=overview_enabled(summary, recalls, rows),
        overview_read=overview_values["read"],
        overview_hit=overview_values["hit"],
        overview_injected=overview_values["injected"],
        audit=audit,
        strict_augmentation_rows=strict_augmentation_rows(summary, rows),
    )


def count_html(counter: Counter[str]) -> str:
    if not counter:
        return '<span class="muted">—</span>'
    parts = [
        f"<code>{esc(key)}</code> {count}"
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]
    return "<br>".join(parts)


def request_counts_html(counter: Counter[str]) -> str:
    if not counter:
        return '<span class="muted">未记录</span>'
    return "<br>".join(
        f"<code>{esc(key)}</code> {count}"
        for key, count in sorted(counter.items(), key=lambda item: item[0])
    )


def status_html(value: bool | None, true_text: str = "开启") -> str:
    if value is True:
        return f'<span class="pass">{true_text}</span>'
    if value is False:
        return '<span class="bad">关闭</span>'
    return '<span class="muted">未记录</span>'


def audit_value_html(value: Any) -> str:
    if value is None:
        return '<span class="muted">未记录</span>'
    if value == 0:
        return '<span class="pass">0</span>'
    return f'<span class="bad">{esc(value)}</span>'


def render_metrics(runs: list[RunData]) -> str:
    return "".join(
        f"""
        <article class="metric">
          <div class="metric-label">{esc(run.label)}</div>
          <strong>{pct(run.accuracy)}</strong>
          <div class="metric-detail">{run.correct} 正确 · {run.wrong} 错误 · {run.graded} graded</div>
        </article>
        """
        for run in runs
    )


def render_result_rows(runs: list[RunData]) -> str:
    return "".join(
        f"""
        <tr>
          <td><strong>{esc(run.label)}</strong></td>
          <td class="nowrap"><strong>{pct(run.accuracy)}</strong></td>
          <td>{run.correct}</td>
          <td>{run.wrong}</td>
          <td>{run.graded}</td>
          <td>{len(run.recalls)}</td>
          <td>{display(run.summary.get("sample"))}</td>
          <td>{display(run.summary.get("top_k"))}</td>
          <td>{status_html(run.overview_enabled)}</td>
        </tr>
        """
        for run in runs
    )


def render_native_rows(runs: list[RunData]) -> str:
    return "".join(
        f"""
        <tr>
          <td><strong>{esc(run.label)}</strong></td>
          <td>{count_html(run.kind_counts)}</td>
          <td>{count_html(run.source_counts)}</td>
          <td class="nowrap">{run.native_graph_rows} / {len(run.recalls)}</td>
          <td>{run.native_graph_total}</td>
          <td>{run.native_graph_average:.2f}</td>
        </tr>
        """
        for run in runs
    )


def render_audit_rows(runs: list[RunData]) -> str:
    rendered = []
    for run in runs:
        audit = run.audit
        request_counts = audit["request_counts"]
        rendered.append(
            f"""
            <tr>
              <td><strong>{esc(run.label)}</strong></td>
              <td>{status_html(run.overview_enabled)}</td>
              <td>{display(run.overview_read)}</td>
              <td>{display(run.overview_hit)}</td>
              <td>{display(run.overview_injected)}</td>
              <td>{display(audit.get("transport"))}</td>
              <td>{display(audit.get("base_url"))}</td>
              <td>{display(audit.get("search"))}</td>
              <td>{display(audit.get("read"))}</td>
              <td>{audit_value_html(audit.get("local"))}</td>
              <td>{audit_value_html(audit.get("neo4j"))}</td>
              <td>{display(run.strict_augmentation_rows)}</td>
            </tr>
            <tr class="small">
              <td></td>
              <td colspan="11"><span class="muted">request_counts：</span> {request_counts_html(request_counts)}</td>
            </tr>
            """
        )
    return "".join(rendered)


def wrong_counts_by_type(run: RunData) -> Counter[str]:
    return Counter(
        question_type(row.get("question", ""))
        for row in run.rows
        if not is_correct(row)
        and str(row.get("result") or "").strip().upper() == "WRONG"
    )


def render_wrong_rows(runs: list[RunData]) -> str:
    wrong_by_run = {run.key: wrong_counts_by_type(run) for run in runs}
    rendered = []
    for kind in QUESTION_TYPES:
        rendered.append(
            "<tr>"
            f"<td>{esc(kind)}</td>"
            + "".join(f"<td>{wrong_by_run[run.key][kind]}</td>" for run in runs)
            + "</tr>"
        )
    rendered.append(
        "<tr><td><strong>合计</strong></td>"
        + "".join(f"<td><strong>{run.wrong}</strong></td>" for run in runs)
        + "</tr>"
    )
    return "".join(rendered)


def render_artifact_rows(runs: list[RunData]) -> str:
    return "".join(
        f"""
        <tr>
          <td><strong>{esc(run.label)}</strong></td>
          <td><code>{esc(run.run_dir)}</code></td>
          <td><code>{esc(run.import_path)}</code></td>
          <td><code>{esc(run.commit)}</code></td>
          <td><code>{esc(run.summary.get("echomem_root") or "未记录")}</code></td>
        </tr>
        """
        for run in runs
    )


def strict_boundary_state(runs: list[RunData]) -> tuple[bool, bool, bool, bool, bool]:
    matrix_ok = all(
        str(run.summary.get("sample") or "") == "conv-30"
        and as_int(run.summary.get("top_k")) == 25
        for run in runs
    )
    transport_ok = all(
        str(run.audit.get("transport") or "").strip().lower() == "http"
        for run in runs
    )
    overview_ok = all(run.overview_enabled is True for run in runs)
    local_ok = all(run.audit.get("local") == 0 for run in runs)
    neo4j_ok = all(run.audit.get("neo4j") == 0 for run in runs)
    return matrix_ok, transport_ok, overview_ok, local_ok, neo4j_ok


def render_boundary(runs: list[RunData]) -> str:
    matrix_ok, transport_ok, overview_ok, local_ok, neo4j_ok = strict_boundary_state(runs)
    all_ok = matrix_ok and transport_ok and overview_ok and local_ok and neo4j_ok
    class_name = "pass" if all_ok else "warn"
    matrix_line = (
        "两组输入均记录为 LoCoMo <code>conv-30</code>、<code>top_k=25</code>。"
        if matrix_ok
        else "两组输入没有全部记录为 LoCoMo conv-30 / top_k=25，请检查运行产物。"
    )
    transport_line = (
        "两组 transport audit 均记录为 <code>http</code>。"
        if transport_ok
        else "两组 transport audit 没有全部记录为 HTTP，请检查运行产物。"
    )
    overview_line = (
        "两组 overview 均通过 EchoMemory HTTP <code>GET /fs/read</code> 开启。"
        if overview_ok
        else "overview 开关没有在两组输入中全部记录为开启，请检查运行产物。"
    )
    local_line = (
        "没有本地 workspace 补证据：两组 <code>local_workspace_evidence_reads=0</code>。"
        if local_ok
        else "不能确认没有本地 workspace 补证据：transport audit 中存在缺失或非零值。"
    )
    neo4j_line = (
        "没有平台直连 Neo4j：两组 <code>platform_neo4j_queries=0</code>。"
        if neo4j_ok
        else "不能确认没有平台直连 Neo4j：transport audit 中存在缺失或非零值。"
    )
    return f"""
    <div class="callout {class_name}">
      <strong>严格 EchoMemory HTTP 黑盒边界</strong>
      <ul>
        <li>{matrix_line}</li>
        <li>{transport_line}</li>
        <li>{overview_line}</li>
        <li>{neo4j_line}</li>
        <li>{local_line}</li>
        <li><strong>Graph-only 是 EchoMemory 服务端 source defaults 控制。</strong>评测端不直连图数据库、不在本地筛选 Graph 结果，也不把本地 workspace 内容拼入 QA 上下文；native graph 统计只来自 EchoMemory HTTP search 返回。</li>
        <li>overview 的 HTTP read 是允许的 EchoMemory 服务端文件读取，不是平台补证据；它与 native search 结果分开统计。</li>
      </ul>
    </div>
    """


def generate(args: argparse.Namespace) -> Path:
    mixed_run = load_run(
        "mixed",
        Path(args.mixed_run),
        Path(args.pr_import),
        args.pr_commit,
    )
    graph_run = load_run(
        "graph",
        Path(args.graph_run),
        Path(args.pr_import),
        args.pr_commit,
    )
    runs = [mixed_run, graph_run]

    pr_commit = infer_commit(args.pr_commit, mixed_run.summary, graph_run.summary)
    mixed_run.commit = pr_commit
    graph_run.commit = pr_commit

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    matrix_ok, transport_ok, overview_ok, local_ok, neo4j_ok = strict_boundary_state(runs)
    check_class = (
        "pass"
        if matrix_ok and transport_ok and overview_ok and local_ok and neo4j_ok
        else "warn"
    )

    report = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LoCoMo conv-30 原生 Graph Matrix</title>
<style>{STYLE}</style>
</head>
<body>
<main>
  <h1>LoCoMo conv-30 原生 Graph Matrix</h1>
  <p class="muted">严格 EchoMemory HTTP 黑盒 · 两组 Top-25 · overview 开启 · 生成于 {esc(generated_at)}</p>

  <section class="panel">
    <h2>边界声明</h2>
    {render_boundary(runs)}
  </section>

  <div class="metrics">{render_metrics(runs)}</div>

  <section class="panel">
    <h2>结果总览</h2>
    <div class="table-wrap"><table>
      <thead><tr>
        <th>场景</th><th>accuracy</th><th>correct</th><th>wrong</th>
        <th>graded</th><th>recall 文件</th><th>sample</th><th>top_k</th><th>overview</th>
      </tr></thead>
      <tbody>{render_result_rows(runs)}</tbody>
    </table></div>
  </section>

  <section class="panel">
    <h2>原生 HTTP 返回组成</h2>
    <p class="muted">memory_type/source 统计只统计 native HTTP search 结果；新 recall 优先读取
      <code>native_http_result_kind_counts</code>，旧 recall 没有该字段时回退统计
      <code>selected[].memory_type</code>。source 同理优先读取 native source counts，再回退
      <code>selected[].source</code>。</p>
    <div class="table-wrap"><table>
      <thead><tr>
        <th>场景</th><th>native memory_type</th><th>native source</th>
        <th>原生 graph 返回行数</th><th>原生 graph 返回总数</th><th>原生 graph 每题均值</th>
      </tr></thead>
      <tbody>{render_native_rows(runs)}</tbody>
    </table></div>
    <p class="muted small">Graph 行按 native memory_type 中的 <code>graph</code>/<code>graph_*</code>/
      <code>*_graph</code> 统计，例如 <code>graph_node</code>；行数分母为该 run 的 recall JSON 数量。</p>
  </section>

  <section class="panel">
    <h2>overview HTTP 与 transport audit</h2>
    <div class="callout {check_class}">
      <strong>审计摘要：</strong>
      <code>local_workspace_evidence_reads=0</code> 与
      <code>platform_neo4j_queries=0</code> 只有在下表明确记录为 0 时才视为通过；
      未记录不会被默认为 0。
    </div>
    <div class="table-wrap"><table>
      <thead><tr>
        <th>场景</th><th>overview</th><th>HTTP read</th><th>HTTP hit</th><th>injected</th>
        <th>transport</th><th>base_url</th><th>POST search</th><th>GET /fs/read</th>
        <th>local workspace</th><th>platform Neo4j</th><th>strict augmentation rows</th>
      </tr></thead>
      <tbody>{render_audit_rows(runs)}</tbody>
    </table></div>
  </section>

  <section class="panel">
    <h2>错题类型</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>问题类型</th><th>{esc(runs[0].label)}</th><th>{esc(runs[1].label)}</th></tr></thead>
      <tbody>{render_wrong_rows(runs)}</tbody>
    </table></div>
    <p class="muted small">类型按问题文本中的时间、原因、地点、数量关键词归类，用于定位错题，不替代 LoCoMo 官方 category。</p>
  </section>

  <section class="panel">
    <h2>运行、导入与 commit</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>场景</th><th>运行路径</th><th>导入路径</th><th>commit</th><th>EchoMemory root（summary）</th></tr></thead>
      <tbody>{render_artifact_rows(runs)}</tbody>
    </table></div>
    <p class="muted small">PR123+125 两组共用 <code>--pr-import</code> 与 <code>--pr-commit</code>。
      导入路径仅作为运行 provenance 展示，评测阶段不从本地 workspace 补取证据。</p>
  </section>

  <section class="panel">
    <h2>口径</h2>
    <ul>
      <li>两组都应是 LoCoMo <code>conv-30</code>、<code>top_k=25</code>，回答上下文只接受 EchoMemory HTTP 黑盒返回及显式开启的 overview HTTP read。</li>
      <li>Membase+Graph 与 Graph-only 的差异仅由 EchoMemory 服务端配置产生；Graph-only 的 source 选择由 EchoMemory 服务端 source defaults 控制，不是平台侧 Neo4j 注入实验。</li>
      <li>本页不把本地 import 目录、workspace 文件、平台 Neo4j 查询或评测端后处理计入 native graph 结果。</li>
    </ul>
  </section>
</main>
</body>
</html>
"""
    output.write_text(report, encoding="utf-8")
    print(output)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成 LoCoMo conv-30 两组严格 EchoMemory HTTP 黑盒 HTML 对比报告。"
    )
    parser.add_argument("--mixed-run", required=True, help="PR123+125 Membase+Graph QA run 目录")
    parser.add_argument("--graph-run", required=True, help="PR123+125 Graph-only QA run 目录")
    parser.add_argument("--pr-import", required=True, help="PR123+125 导入路径（仅 provenance）")
    parser.add_argument("--output", required=True, help="输出 HTML 路径")
    parser.add_argument("--pr-commit", help="PR123+125 commit，可选")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        generate(args)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise SystemExit(f"错误：{exc}") from exc


if __name__ == "__main__":
    main()

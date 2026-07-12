#!/usr/bin/env python3
"""Compare two LoCoMo EchoMemory HTTP black-box runs."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["question_id"]: row for row in csv.DictReader(handle)}


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def pct(value: object) -> str:
    return f"{100 * float(value or 0):.2f}%"


def compact(value: object, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def normalized_answer(value: object) -> str:
    return " ".join(str(value or "").lower().split())


def is_correct(row: dict[str, str]) -> bool:
    return str(row.get("result") or "").strip().upper() == "CORRECT"


def question_type(question: str) -> str:
    normalized = question.lower()
    if re.search(r"\bwhen\b|\bhow long\b|\bwhat date\b|\bwhat year\b", normalized):
        return "时间/日期"
    if re.search(r"\bwhy\b", normalized):
        return "原因"
    if re.search(r"\bwhere\b|\bwhich cit", normalized):
        return "地点"
    if re.search(r"\bhow many\b|\bnumber\b", normalized):
        return "数量"
    return "事实/描述"


def audit(summary: dict) -> dict:
    transport = summary.get("echomemory_transport_audit") or {}
    requests = transport.get("request_counts") or {}
    return {
        "search": int(requests.get("POST /api/retrieval/search") or 0),
        "read": int(requests.get("GET /fs/read") or 0),
        "local": int(transport.get("local_workspace_evidence_reads") or 0),
        "neo4j": int(transport.get("platform_neo4j_queries") or 0),
    }


def change_rows(
    question_ids: list[str],
    native_rows: dict[str, dict[str, str]],
    overview_rows: dict[str, dict[str, str]],
    label: str,
) -> str:
    rendered = []
    for question_id in question_ids:
        native = native_rows[question_id]
        overview = overview_rows[question_id]
        rendered.append(
            "<tr>"
            f"<td><code>{esc(question_id)}</code></td>"
            f"<td>{esc(question_type(native.get('question', '')))}</td>"
            f"<td>{esc(native.get('question', ''))}</td>"
            f"<td>{esc(compact(native.get('answer')))}</td>"
            f"<td>{esc(compact(native.get('response')))}</td>"
            f"<td>{esc(compact(overview.get('response')))}</td>"
            f"<td><span class='pill'>{esc(label)}</span></td>"
            "</tr>"
        )
    return "".join(rendered)


def generate(args: argparse.Namespace) -> None:
    native_dir = Path(args.native_run).expanduser().resolve()
    overview_dir = Path(args.overview_run).expanduser().resolve()
    native_summary = read_json(native_dir / "summary.json")
    overview_summary = read_json(overview_dir / "summary.json")
    native_rows = read_rows(native_dir / "echomemory_memory_qa_results.csv")
    overview_rows = read_rows(overview_dir / "echomemory_memory_qa_results.csv")
    question_ids = sorted(set(native_rows) & set(overview_rows))

    native_correct = {qid for qid in question_ids if is_correct(native_rows[qid])}
    overview_correct = {qid for qid in question_ids if is_correct(overview_rows[qid])}
    gains = sorted(overview_correct - native_correct)
    losses = sorted(native_correct - overview_correct)
    same_answer_flips = sorted(
        qid
        for qid in question_ids
        if normalized_answer(native_rows[qid].get("response"))
        == normalized_answer(overview_rows[qid].get("response"))
        and native_rows[qid].get("result") != overview_rows[qid].get("result")
    )
    type_totals = Counter(question_type(native_rows[qid].get("question", "")) for qid in question_ids)
    native_wrong = Counter(
        question_type(native_rows[qid].get("question", ""))
        for qid in question_ids
        if qid not in native_correct
    )
    overview_wrong = Counter(
        question_type(overview_rows[qid].get("question", ""))
        for qid in question_ids
        if qid not in overview_correct
    )
    native_audit = audit(native_summary)
    overview_audit = audit(overview_summary)
    delta_pp = 100 * (
        float(overview_summary.get("accuracy") or 0)
        - float(native_summary.get("accuracy") or 0)
    )

    error_rows = "".join(
        "<tr>"
        f"<td>{esc(kind)}</td>"
        f"<td>{type_totals[kind]}</td>"
        f"<td>{native_wrong[kind]}</td>"
        f"<td>{overview_wrong[kind]}</td>"
        "</tr>"
        for kind in ("时间/日期", "事实/描述", "原因", "地点", "数量")
        if type_totals[kind]
    )
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LoCoMo conv30 EchoMemory HTTP 黑盒对比</title>
<style>
:root{{--bg:#f7f7f5;--panel:#fff;--line:#e5e7eb;--text:#172033;--muted:#667085;--blue:#2563eb;--green:#15803d;--red:#b42318}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1240px;margin:0 auto;padding:28px}}h1{{font-size:26px;margin:0 0 4px}}h2{{font-size:18px;margin:0 0 14px}}p{{margin:8px 0}}code{{font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}}
.muted{{color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:20px 0}}.metric,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:8px}}
.metric{{padding:16px}}.metric b{{display:block;font-size:28px;margin:4px 0}}.panel{{padding:18px;margin:16px 0}}.callout{{border-left:3px solid var(--blue);padding:10px 14px;background:#f8fafc;margin:12px 0}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{font-size:12px;color:var(--muted);background:#fafafa}}tr:last-child td{{border-bottom:0}}
.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:6px}}.pill{{display:inline-block;padding:2px 7px;border-radius:999px;background:#eef2ff;color:#3730a3;font-size:11px;white-space:nowrap}}
.pass{{color:var(--green)}}.bad{{color:var(--red)}}ul{{margin:8px 0;padding-left:20px}}li{{margin:5px 0}}@media(max-width:800px){{main{{padding:16px}}.grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body><main>
<h1>LoCoMo conv30 EchoMemory HTTP 黑盒对比</h1>
<p class="muted">PR #123 + #125 · single-query · top-25 · 4 并发 · deepseek-v4-flash · {esc(generated_at)}</p>

<section class="panel">
<h2>评测边界</h2>
<div class="callout"><strong>两组都没有绕过 EchoMemory HTTP API。</strong>平台不读本地 workspace、不直连 Neo4j、不调用本地 SDK、不做 follow-up search、重排或规则补证据。overview 组的额外内容只通过 EchoMemory HTTP <code>/fs/read</code> 获取。</div>
<ul>
<li>原生组：每题一次 <code>POST /api/retrieval/search</code>，直接使用服务返回的 top-25。</li>
<li>overview 组：同样先做一次 search，再对 search 返回的 session URI 调用 <code>GET /fs/read</code> 读取 <code>overview.md</code>。</li>
<li>两组原生 search 返回类型均为 <code>atom</code>，<code>native_graph_recall_rows=0</code>；因此本报告不能宣称 EchoMemory 原生 graph retrieval 已进入 QA。</li>
</ul>
</section>

<div class="grid">
<div class="metric"><span>原生 HTTP search</span><b>{pct(native_summary.get('accuracy'))}</b><span>{native_summary.get('correct')} / {native_summary.get('graded')}</span></div>
<div class="metric"><span>HTTP search + overview</span><b>{pct(overview_summary.get('accuracy'))}</b><span>{overview_summary.get('correct')} / {overview_summary.get('graded')}</span></div>
<div class="metric"><span>overview 变化</span><b>{delta_pp:+.2f}pp</b><span>{len(gains)} 题改善，{len(losses)} 题退化</span></div>
</div>

<section class="panel"><h2>HTTP 与越界审计</h2><div class="table-wrap"><table>
<thead><tr><th>场景</th><th>/search</th><th>/fs/read</th><th>本地 workspace 读取</th><th>平台 Neo4j 查询</th><th>平台补证据题数</th><th>原生 graph 题数</th></tr></thead>
<tbody>
<tr><td>原生 HTTP search</td><td>{native_audit['search']}</td><td>{native_audit['read']}</td><td class="pass">{native_audit['local']}</td><td class="pass">{native_audit['neo4j']}</td><td>{native_summary.get('strict_blackbox_augmentation_rows')}</td><td>{native_summary.get('native_graph_recall_rows')}</td></tr>
<tr><td>HTTP search + overview</td><td>{overview_audit['search']}</td><td>{overview_audit['read']}</td><td class="pass">{overview_audit['local']}</td><td class="pass">{overview_audit['neo4j']}</td><td>{overview_summary.get('strict_blackbox_augmentation_rows')}</td><td>{overview_summary.get('native_graph_recall_rows')}</td></tr>
</tbody></table></div>
<p class="muted">overview 组：HTTP read 命中 {overview_summary.get('overview_http_hit_count_total')} 次，最终注入 {overview_summary.get('overview_injected_count_total')} 条 overview 片段；这是 EchoMemory API 读取，不计为平台补证据。</p></section>

<section class="panel"><h2>错题分类</h2><div class="table-wrap"><table>
<thead><tr><th>类型</th><th>题数</th><th>原生 search 错题</th><th>search + overview 错题</th></tr></thead>
<tbody>{error_rows}</tbody></table></div></section>

<section class="panel"><h2>逐题变化</h2>
<p>overview 组净增 {len(gains) - len(losses)} 题。另有 {len(same_answer_flips)} 题生成答案相同但 judge verdict 不同，说明结果仍包含 judge 波动。</p>
<div class="table-wrap"><table><thead><tr><th>ID</th><th>类型</th><th>问题</th><th>Gold</th><th>原生 search</th><th>search + overview</th><th>变化</th></tr></thead>
<tbody>{change_rows(gains, native_rows, overview_rows, 'overview 改善')}{change_rows(losses, native_rows, overview_rows, 'overview 退化')}</tbody></table></div>
</section>

<section class="panel"><h2>产物</h2><div class="table-wrap"><table>
<tbody>
<tr><td>EchoMemory 代码</td><td><code>{esc(native_summary.get('echomem_root'))}</code></td></tr>
<tr><td>已注入记忆</td><td><code>{esc(native_summary.get('workspace'))}</code></td></tr>
<tr><td>原生 search 运行</td><td><code>{esc(native_dir)}</code></td></tr>
<tr><td>HTTP overview 运行</td><td><code>{esc(overview_dir)}</code></td></tr>
<tr><td>后端路由</td><td><code>echomemory_http_api_blackbox</code></td></tr>
</tbody></table></div></section>
</main></body></html>"""
    output.write_text(report, encoding="utf-8")
    print(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-run", required=True)
    parser.add_argument("--overview-run", required=True)
    parser.add_argument("--output", required=True)
    generate(parser.parse_args())


if __name__ == "__main__":
    main()

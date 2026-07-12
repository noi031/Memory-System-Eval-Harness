#!/usr/bin/env python3
"""Generate the LoCoMo conv30 retrieval-source ablation report."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path


SCENARIOS = (
    ("membase_graph", "Membase + overview + 平台 Neo4j"),
    ("membase_only", "Membase + overview（无图）"),
    ("graph_only", "平台 Neo4j-only（无 overview）"),
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["question_id"]: row for row in csv.DictReader(handle)}


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


def pct(value: float | None) -> str:
    return f"{100 * float(value or 0):.2f}%"


def compact(value: str, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def normalized_answer(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def recall_stats(run_dir: Path) -> dict[str, object]:
    rows = [read_json(path) for path in sorted(run_dir.glob("q*.recall.json"))]
    graph_counts = [int(row.get("neo4j_graph_selected_count") or 0) for row in rows]
    overview_trigger_rows = sum(
        1
        for row in rows
        if bool(
            (row.get("allowed_http_enrichment_flags") or {}).get("overview_enrichment_triggered")
            or (row.get("strict_blackbox_augmentation_flags") or {}).get("overview_enrichment_triggered")
        )
    )
    selected_backends: Counter[str] = Counter()
    for row in rows:
        for item in row.get("selected") or []:
            selected_backends[str(item.get("backend") or "unknown")] += 1
    graph_total = sum(graph_counts)
    return {
        "rows": len(rows),
        "graph_rows": sum(1 for count in graph_counts if count > 0),
        "graph_total": graph_total,
        "graph_avg": graph_total / len(rows) if rows else 0.0,
        "graph_min": min(graph_counts) if graph_counts else 0,
        "graph_max": max(graph_counts) if graph_counts else 0,
        "overview_trigger_rows": overview_trigger_rows,
        "selected_backends": selected_backends,
    }


def render_delta_rows(
    ids: list[str],
    primary: dict[str, dict[str, str]],
    baseline: dict[str, dict[str, str]],
    label: str,
) -> str:
    rendered = []
    for question_id in ids:
        row = primary[question_id]
        effective_label = (
            "同答案判分翻转"
            if normalized_answer(row["response"]) == normalized_answer(baseline[question_id]["response"])
            else label
        )
        rendered.append(
            "<tr>"
            f"<td><code>{esc(question_id)}</code></td>"
            f"<td>{esc(question_type(row['question']))}</td>"
            f"<td>{esc(row['question'])}</td>"
            f"<td>{esc(compact(row['answer']))}</td>"
            f"<td>{esc(compact(baseline[question_id]['response']))}</td>"
            f"<td>{esc(compact(row['response']))}</td>"
            f"<td><span class='pill'>{esc(effective_label)}</span></td>"
            "</tr>"
        )
    return "".join(rendered)


def render_wrong_examples(rows: dict[str, dict[str, str]], kind: str, limit: int = 8) -> str:
    wrong = [row for row in rows.values() if not is_correct(row) and question_type(row["question"]) == kind]
    rendered = []
    for row in wrong[:limit]:
        rendered.append(
            "<tr>"
            f"<td><code>{esc(row['question_id'])}</code></td>"
            f"<td>{esc(row['question'])}</td>"
            f"<td>{esc(compact(row['answer']))}</td>"
            f"<td>{esc(compact(row['response']))}</td>"
            "</tr>"
        )
    return "".join(rendered)


def generate(args: argparse.Namespace) -> None:
    run_dirs = {
        "membase_graph": Path(args.membase_graph).expanduser().resolve(),
        "membase_only": Path(args.membase_only).expanduser().resolve(),
        "graph_only": Path(args.graph_only).expanduser().resolve(),
    }
    summaries = {name: read_json(path / "summary.json") for name, path in run_dirs.items()}
    recall = {name: recall_stats(path) for name, path in run_dirs.items()}
    rows = {
        name: read_rows(path / "echomemory_memory_qa_results.csv")
        for name, path in run_dirs.items()
    }

    all_ids = sorted(rows["membase_only"])
    correct = {
        name: {question_id for question_id, row in scenario_rows.items() if is_correct(row)}
        for name, scenario_rows in rows.items()
    }
    graph_gains = sorted(correct["membase_graph"] - correct["membase_only"])
    graph_losses = sorted(correct["membase_only"] - correct["membase_graph"])
    graph_only_unique = sorted(correct["graph_only"] - correct["membase_only"])
    all_correct = correct["membase_graph"] & correct["membase_only"] & correct["graph_only"]
    all_wrong = set(all_ids) - set().union(*correct.values())
    accuracy_delta_pp = 100 * (
        float(summaries["membase_graph"].get("accuracy") or 0)
        - float(summaries["membase_only"].get("accuracy") or 0)
    )
    same_answer_verdict_flips = sorted(
        question_id
        for question_id in all_ids
        if normalized_answer(rows["membase_graph"][question_id]["response"])
        == normalized_answer(rows["membase_only"][question_id]["response"])
        and rows["membase_graph"][question_id]["result"]
        != rows["membase_only"][question_id]["result"]
    )
    graph_summary = summaries["membase_graph"]
    graph_preflight = graph_summary.get("neo4j_graph_preflight") or {}
    graph_nodes = int(graph_preflight.get("nodes") or 0)
    graph_relationships = int(graph_preflight.get("relationships") or 0)
    graph_max_selected = graph_summary.get("neo4j_graph_max_selected")
    graph_max_selected_text = "None" if graph_max_selected is None else str(graph_max_selected)
    blackbox_run = Path(args.blackbox_run).expanduser().resolve() if args.blackbox_run else None
    blackbox_summary = read_json(blackbox_run / "summary.json") if blackbox_run else {}
    blackbox_augmentation_counts = (
        blackbox_summary.get("strict_blackbox_augmentation_trigger_rows_by_path") or {}
    )
    blackbox_augmentation_total = sum(
        int(value or 0) for value in blackbox_augmentation_counts.values()
    )
    blackbox_report = (
        Path(args.blackbox_report).expanduser().resolve()
        if args.blackbox_report
        else None
    )
    blackbox_link = (
        f"<a href='{esc(blackbox_report.name)}'><code>{esc(blackbox_report.name)}</code></a>"
        if blackbox_report
        else "<span class='muted'>未提供</span>"
    )

    types = ("时间/日期", "事实/描述", "原因", "地点", "数量")
    type_totals = Counter(question_type(row["question"]) for row in rows["membase_only"].values())
    wrong_counts = {
        name: Counter(
            question_type(row["question"])
            for row in scenario_rows.values()
            if not is_correct(row)
        )
        for name, scenario_rows in rows.items()
    }

    result_rows = []
    for name, label in SCENARIOS:
        summary = summaries[name]
        result_rows.append(
            "<tr>"
            f"<td><strong>{esc(label)}</strong><br><code>{esc(name)}</code></td>"
            f"<td>{summary.get('correct')}/{summary.get('graded')}</td>"
            f"<td><strong>{pct(summary.get('accuracy'))}</strong></td>"
            f"<td>{summary.get('avg_retrieval_count')}</td>"
            f"<td>{recall[name]['overview_trigger_rows']}/81</td>"
            f"<td>{recall[name]['graph_rows']}/81</td>"
            f"<td>{float(recall[name]['graph_avg']):.2f}</td>"
            f"<td>{summary.get('retrieval_empty_count', 0)}</td>"
            f"<td>{summary.get('model_failed_count', 0)}</td>"
            "</tr>"
        )

    error_rows = []
    for kind in types:
        if not type_totals[kind]:
            continue
        error_rows.append(
            "<tr>"
            f"<td>{esc(kind)}</td><td>{type_totals[kind]}</td>"
            f"<td>{wrong_counts['membase_graph'][kind]}</td>"
            f"<td>{wrong_counts['membase_only'][kind]}</td>"
            f"<td>{wrong_counts['graph_only'][kind]}</td>"
            "</tr>"
        )

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LoCoMo conv30 Top-25 Overview / Graph 对比</title>
<style>
:root{{--bg:#f7f7f5;--panel:#fff;--line:#e5e7eb;--text:#172033;--muted:#667085;--blue:#2563eb;--green:#15803d;--red:#b42318;--amber:#b45309}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1280px;margin:0 auto;padding:28px}} h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:18px;margin:0 0 14px}} h3{{font-size:15px;margin:20px 0 10px}} p{{margin:8px 0}} code{{font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}}
.muted{{color:var(--muted)}} .grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:20px 0}} .metric,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:8px}}
.metric{{padding:16px}} .metric b{{display:block;font-size:26px;margin:4px 0}} .panel{{padding:18px;margin:16px 0}} .pass{{color:var(--green)}} .warn{{color:var(--amber)}} .bad{{color:var(--red)}}
table{{width:100%;border-collapse:collapse}} th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{font-size:12px;color:var(--muted);background:#fafafa;position:sticky;top:0}} tr:last-child td{{border-bottom:0}}
.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:6px}} .pill{{display:inline-block;padding:2px 7px;border-radius:999px;background:#eef2ff;color:#3730a3;font-size:11px;white-space:nowrap}}
.callout{{border-left:3px solid var(--blue);padding:10px 14px;background:#f8fafc;margin:12px 0}} ul{{margin:8px 0;padding-left:20px}} li{{margin:5px 0}} details{{border-top:1px solid var(--line);padding-top:12px;margin-top:12px}} summary{{cursor:pointer;font-weight:600}}
@media(max-width:800px){{main{{padding:16px}}.grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body><main>
<h1>LoCoMo conv30 Top-25 Overview / Graph 对比</h1>
<p class="muted">Fresh diagnostic run · EchoMemory PR #123 + #125 · single-query · 4 并发 · deepseek-v4-flash · 生成于 {esc(generated_at)}</p>

<section class="panel">
<h2>重要口径更正</h2>
<div class="callout"><strong>本页三组不是 EchoMemory 原生图检索消融。</strong>评测平台在 EchoMemory HTTP search 之外直接读取 Neo4j 或补读 overview，并把这些证据拼入 QA 上下文；因此只能用于诊断平台侧证据融合，不能据此宣称 EchoMemory 原生 graph search 带来增益。</div>
<p>当前权威口径是 <strong>pr_blackbox</strong>：只使用 EchoMemory <code>/api/retrieval/search</code> 返回的证据，关闭平台 Neo4j 直查、overview/fs_read、本地 summaries/atoms/messages 和全部 readback/fallback。</p>
<p>黑盒结果：<strong>{blackbox_summary.get('correct', '—')}/{blackbox_summary.get('graded', '—')}</strong>，
准确率 <strong>{pct(blackbox_summary.get('accuracy')) if blackbox_summary else '—'}</strong>，
平台补证据路径累计触发 <strong>{blackbox_augmentation_total if blackbox_summary else '—'}</strong> 次。
详细报告：{blackbox_link}</p>
</section>

<div class="grid">
  <div class="metric"><span>Membase + overview + 平台 Neo4j</span><b>{pct(summaries['membase_graph'].get('accuracy'))}</b><span>{summaries['membase_graph'].get('correct')} / {summaries['membase_graph'].get('graded')}，每题平均 {float(recall['membase_graph']['graph_avg']):.2f} 条图证据</span></div>
  <div class="metric"><span>Membase + overview（无图）</span><b>{pct(summaries['membase_only'].get('accuracy'))}</b><span>{summaries['membase_only'].get('correct')} / {summaries['membase_only'].get('graded')}，overview 触发 {recall['membase_only']['overview_trigger_rows']}/81</span></div>
  <div class="metric"><span>平台 Neo4j-only（无 overview）</span><b>{pct(summaries['graph_only'].get('accuracy'))}</b><span>{summaries['graph_only'].get('correct')} / {summaries['graph_only'].get('graded')}，每题平均 {float(recall['graph_only']['graph_avg']):.2f} 条图证据</span></div>
</div>

<section class="panel">
<h2>诊断结论</h2>
<div class="callout"><strong>以下差异只反映平台侧证据拼接实验。</strong>不得把第一组的 {pct(summaries['membase_graph'].get('accuracy'))} 写成 EchoMemory 原生 graph retrieval 准确率。</div>
<ul>
  <li><strong>平台侧 Neo4j 证据已注入：</strong>Neo4j preflight 为 {graph_nodes} nodes / {graph_relationships} relationships，混合模式有 {summaries['membase_graph'].get('neo4j_graph_recall_rows', 0)}/81 题、graph-only 有 {summaries['graph_only'].get('neo4j_graph_recall_rows', 0)}/81 题由评测平台直接加入图证据。</li>
  <li><strong>当前融合明显退化：</strong>相对 Membase + overview，judge 结果有 {len(graph_gains)} 题由错变对、{len(graph_losses)} 题由对变错，净变化 {len(graph_gains) - len(graph_losses)} 题（{accuracy_delta_pp:+.2f}pp）。这不是 EchoMemory 内部图检索增益。</li>
  <li><strong>图证据占位过多：</strong>混合组每题平均选入 {float(recall['membase_graph']['graph_avg']):.2f}/25 条图证据，范围 {recall['membase_graph']['graph_min']}–{recall['membase_graph']['graph_max']}；共挤入 {recall['membase_graph']['graph_total']} 个 top-25 槽位。对应地，选中的 overview 从无图组的 {(recall['membase_only']['selected_backends']).get('echomemory_fs_read', 0)} 条降到 {(recall['membase_graph']['selected_backends']).get('echomemory_fs_read', 0)} 条，原子记忆从 {(recall['membase_only']['selected_backends']).get('echomemory', 0)} 条降到 {(recall['membase_graph']['selected_backends']).get('echomemory', 0)} 条。</li>
  <li><strong>judge 存在可证实波动：</strong>{len(same_answer_verdict_flips)} 题在两组生成答案逐字相同的情况下得到相反 verdict（{esc(', '.join(same_answer_verdict_flips))}）。因此不能把全部 verdict 翻转归因于图证据。</li>
  <li><strong>平台 Neo4j-only 上下文不足：</strong>仅 {pct(summaries['graph_only'].get('accuracy'))}，尤其时间题错 {wrong_counts['graph_only']['时间/日期']}/{type_totals['时间/日期']}。该结果评价的是平台直接注入的图文本，不是 EchoMemory 原生后端能力。</li>
  <li><strong>主要图缺陷：</strong>相对时间词未绑定会话日期、关系扩展产生重复/近重复 Graph fact、top-25 中缺少 overview/session anchor，答案容易输出 yesterday、last week 或错误的当前日期换算。</li>
  <li><strong>证据边界：</strong>local summaries/atoms/messages/timeline/artifacts/segments 未启用，但 Neo4j 直查及部分 overview readback 本身就是评测平台侧增强，所以这三组不属于严格黑盒。</li>
</ul>
</section>

<section class="panel"><h2>结果对比</h2><div class="table-wrap"><table>
<thead><tr><th>场景</th><th>正确</th><th>准确率</th><th>平均证据数</th><th>overview 触发</th><th>图证据题数</th><th>平均图证据数</th><th>空召回</th><th>模型失败</th></tr></thead>
<tbody>{''.join(result_rows)}</tbody></table></div></section>

<section class="panel"><h2>统一测试参数</h2><div class="table-wrap"><table>
<tbody>
<tr><td>数据</td><td><code>LoCoMo locomo10.json / conv-30 / 81 questions</code></td></tr>
<tr><td>代码</td><td><code>{esc(summaries['membase_only'].get('echomem_root'))}</code></td></tr>
<tr><td>记忆工作区</td><td><code>{esc(summaries['membase_only'].get('workspace'))}</code></td></tr>
<tr><td>检索</td><td><code>single original query; top_k=25; retrieval_mode=search; retrieval_ranker=score</code></td></tr>
<tr><td>回答</td><td><code>prompt_mode=one_shot; qa_parallelism=4; user budget=4000 chars; agent budget=2000 chars</code></td></tr>
<tr><td>模型</td><td><code>deepseek-v4-flash</code>，回答与 judge 使用相同 DashScope compatible API</td></tr>
<tr><td>Neo4j</td><td><code>{esc(graph_summary.get('neo4j_uri'))} / {esc(graph_summary.get('neo4j_graph_tenant_id'))} / {esc(graph_summary.get('neo4j_graph_user_id'))}</code></td></tr>
<tr><td>图参数</td><td><code>graph_limit={esc(graph_summary.get('neo4j_graph_limit'))}; min_score={esc(graph_summary.get('neo4j_graph_min_score'))}; min_selected={esc(graph_summary.get('neo4j_graph_min_selected'))}; max_selected={esc(graph_max_selected_text)}</code></td></tr>
</tbody></table></div></section>

<section class="panel"><h2>错题分类</h2><div class="table-wrap"><table>
<thead><tr><th>类型</th><th>总题数</th><th>Membase + graph 错题</th><th>Membase-only 错题</th><th>Graph-only 错题</th></tr></thead>
<tbody>{''.join(error_rows)}</tbody></table></div>
<p class="muted">分类按问题文本规则生成，用于定位，不替代 LoCoMo 官方 category。</p></section>

<section class="panel"><h2>平台图证据加入后的逐题变化</h2>
<p>观测改善 {len(graph_gains)} 题，观测退化 {len(graph_losses)} 题，净增 {len(graph_gains) - len(graph_losses)} 题；其中 {len(same_answer_verdict_flips)} 题是相同答案的 judge 判分翻转。三组共同正确 {len(all_correct)} 题，共同错误 {len(all_wrong)} 题。</p>
<div class="table-wrap"><table><thead><tr><th>ID</th><th>类型</th><th>问题</th><th>Gold</th><th>Membase-only</th><th>Membase + graph</th><th>变化</th></tr></thead>
<tbody>{render_delta_rows(graph_gains, rows['membase_graph'], rows['membase_only'], '图改善')}{render_delta_rows(graph_losses, rows['membase_graph'], rows['membase_only'], '图退化')}</tbody></table></div>
<p class="muted">graph-only 额外独立答对 {len(graph_only_unique)} 题，但相对 membase-only 丢失 {len(correct['membase_only'] - correct['graph_only'])} 题。</p></section>

<section class="panel"><h2>平台 Neo4j-only 典型时间错题</h2><div class="table-wrap"><table>
<thead><tr><th>ID</th><th>问题</th><th>Gold</th><th>平台 Neo4j-only 回答</th></tr></thead>
<tbody>{render_wrong_examples(rows['graph_only'], '时间/日期')}</tbody></table></div></section>

<section class="panel"><h2>问题定位与改进顺序</h2>
<ol>
<li><strong>P0 时间锚点：</strong>写图时把 normalized event date 作为结构化属性保留；检索输出同时提供 session date，禁止只返回 yesterday/last week。</li>
<li><strong>P0 图去重：</strong>合并原 atom、Graph fact 派生文本和同义关系节点，避免 top-25 被重复事实占满。</li>
<li><strong>P1 混合融合：</strong>不要让图候选与 membase 直接竞争同一个 top-25；先分别取候选，再按 question type 分配固定但可配置的 source budget。</li>
<li><strong>P1 证据完整性：</strong>多答案/列表题优先保留覆盖不同 session 的证据，而不是按单条 lexical score 截断。</li>
<li><strong>P0 judge 稳定性：</strong>当前已出现相同答案被判成相反结果。应固定 temperature/seed（后端支持时），保存 judge 原始响应，并对边界题做双 judge 或 deterministic rule fallback。</li>
</ol>
</section>

<section class="panel"><h2>产物</h2><div class="table-wrap"><table><thead><tr><th>场景</th><th>目录</th><th>关键文件</th></tr></thead><tbody>
{''.join(f'<tr><td>{esc(label)}</td><td><code>{esc(run_dirs[name])}</code></td><td><code>summary.json</code> · <code>echomemory_memory_qa_results.csv</code> · <code>qNNN.recall.json</code></td></tr>' for name, label in SCENARIOS)}
</tbody></table></div>
<details><summary>无效运行说明</summary><p>曾有一轮 membase-only 因遗漏 HTTP auth key 被自动路由到空租户，已重命名为 <code>conv30_vikingbot_aligned_top25_membase_only_INVALID_MISSING_AUTH_20260712</code>，不计入任何统计。</p></details>
</section>
</main></body></html>"""
    output.write_text(report, encoding="utf-8")
    print(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--membase-graph", required=True)
    parser.add_argument("--membase-only", required=True)
    parser.add_argument("--graph-only", required=True)
    parser.add_argument("--blackbox-run")
    parser.add_argument("--blackbox-report")
    parser.add_argument("--output", required=True)
    generate(parser.parse_args())


if __name__ == "__main__":
    main()

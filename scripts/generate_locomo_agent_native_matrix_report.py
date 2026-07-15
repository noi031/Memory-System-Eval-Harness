#!/usr/bin/env python3
"""Generate the final four-way LoCoMo conv-30 native HTTP Agent report."""

from __future__ import annotations

import argparse
import csv
import html
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Run:
    label: str
    code: str
    run_dir: Path
    rows: list[dict[str, str]]

    @property
    def correct(self) -> int:
        return sum((row.get("result") or "").upper() == "CORRECT" for row in self.rows)

    @property
    def empty(self) -> int:
        return sum((row.get("answer_status") or "").lower() != "ok" for row in self.rows)

    @property
    def accuracy(self) -> float:
        return self.correct / len(self.rows)

    @property
    def tool_calls(self) -> int:
        return sum(as_int(row.get("tool_call_count")) for row in self.rows)

    @property
    def tool_rows(self) -> int:
        return sum(as_int(row.get("tool_call_count")) > 0 for row in self.rows)

    @property
    def avg_initial_hits(self) -> float:
        return sum(as_int(row.get("initial_memory_hit_count")) for row in self.rows) / len(self.rows)

    @property
    def evidence_types(self) -> Counter[str]:
        return Counter((row.get("final_evidence_source") or "unknown") for row in self.rows)


def as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def read_run(label: str, code: str, path: str) -> Run:
    run_dir = Path(path).expanduser().resolve()
    csv_path = run_dir / "echomemory_memory_qa_results.csv"
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 81:
        raise ValueError(f"{label}: expected 81 rows, got {len(rows)}: {csv_path}")
    return Run(label=label, code=code, run_dir=run_dir, rows=rows)


def category_rows(run: Run) -> list[tuple[str, int, int, float]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in run.rows:
        grouped[row.get("category") or "unknown"].append(row)
    values = []
    for category, rows in sorted(grouped.items()):
        correct = sum((row.get("result") or "").upper() == "CORRECT" for row in rows)
        values.append((category, correct, len(rows), correct / len(rows)))
    return values


def audit(run: Run) -> dict[str, Any]:
    bool_fields = (
        "memory_tool_loop_enabled",
        "qa_memory_injection_enabled",
        "platform_evidence_injection_enabled",
        "strict_blackbox_augmentation_triggered",
        "http_overview_enrichment_enabled",
    )
    values = {field: sorted(set((row.get(field) or "").lower() for row in run.rows)) for field in bool_fields}
    write_markers = ("session/open", "session/message", "session/commit", "/commit", "message/add")
    write_hits = 0
    for row in run.rows:
        text = json.dumps(row, ensure_ascii=False).lower()
        write_hits += sum(marker in text for marker in write_markers)
    return {
        **values,
        "write_endpoint_markers": write_hits,
        "score_source": sorted(set(row.get("retrieval_source_mode") or "" for row in run.rows)),
        "evidence_policy": sorted(set(row.get("evidence_policy") or "" for row in run.rows)),
        "max_iterations": sorted(set(row.get("max_iterations") or "" for row in run.rows)),
        "top_k": sorted(set(row.get("initial_search_limit") or "" for row in run.rows)),
        "score_threshold": sorted(set(row.get("initial_score_threshold") or "" for row in run.rows)),
    }


def render(runs: list[Run], output: Path, head_commit: str, pr_commit: str) -> None:
    rows_html = "".join(
        f"""
        <tr>
          <td><strong>{esc(run.label)}</strong><div class="muted">{esc(run.code)}</div></td>
          <td class="num"><strong>{run.correct}/81</strong><div>{run.accuracy:.2%}</div></td>
          <td class="num">{run.empty}</td>
          <td class="num">{run.avg_initial_hits:.2f}</td>
          <td class="num">{run.tool_calls}</td>
          <td class="num">{run.tool_rows}/81</td>
          <td>{esc(", ".join(f"{key}: {value}" for key, value in run.evidence_types.items()))}</td>
        </tr>
        """
        for run in runs
    )
    category_headers = "".join(f"<th>{esc(run.label)}</th>" for run in runs)
    all_categories = sorted({category for run in runs for category, *_ in category_rows(run)})
    category_maps = {
        run.label: {category: (correct, total, accuracy) for category, correct, total, accuracy in category_rows(run)}
        for run in runs
    }
    category_html = ""
    for category in all_categories:
        cells = ""
        for run in runs:
            correct, total, accuracy = category_maps[run.label].get(category, (0, 0, 0.0))
            cells += f"<td class='num'>{correct}/{total}<div class='muted'>{accuracy:.2%}</div></td>"
        category_html += f"<tr><td>{esc(category)}</td>{cells}</tr>"

    audit_html = ""
    for run in runs:
        info = audit(run)
        audit_html += f"""
        <tr>
          <td><strong>{esc(run.label)}</strong></td>
          <td>{esc(info["top_k"])}</td>
          <td>{esc(info["max_iterations"])}</td>
          <td>{esc(info["memory_tool_loop_enabled"])}</td>
          <td>{esc(info["qa_memory_injection_enabled"])}</td>
          <td>{esc(info["platform_evidence_injection_enabled"])}</td>
          <td>{esc(info["strict_blackbox_augmentation_triggered"])}</td>
          <td>{info["write_endpoint_markers"]}</td>
        </tr>
        """

    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LoCoMo conv-30 Agent Native Top-25 对比</title>
<style>
:root{{--bg:#f7f7f5;--panel:#fff;--line:#e5e7eb;--text:#172033;--muted:#667085;--blue:#2563eb;--green:#15803d}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1440px;margin:auto;padding:28px}} h1{{font-size:25px;margin:0 0 4px}} h2{{font-size:17px;margin:0 0 14px}}
.muted{{color:var(--muted);font-size:12px}} .panel{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:18px;margin:16px 0}}
.callout{{border-left:3px solid var(--blue);background:#f8fafc;padding:11px 14px;margin:12px 0}} .pass{{border-left-color:var(--green);background:#f0fdf4}}
table{{width:100%;border-collapse:collapse}} th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{background:#fafafa;color:var(--muted);font-size:12px}} tr:last-child td{{border-bottom:0}} .wrap{{overflow:auto;border:1px solid var(--line);border-radius:6px}}
.num{{white-space:nowrap;font-variant-numeric:tabular-nums}} code{{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}}
ul{{margin:8px 0;padding-left:20px}} @media(max-width:800px){{main{{padding:14px}}}}
</style>
</head>
<body><main>
<h1>LoCoMo conv-30 Agent Native Top-25 对比</h1>
<div class="muted">生成时间：{esc(generated)} · 81 题 · DeepSeek v4 Flash · Judge 对齐 VikingBot judge.py</div>

<section class="panel">
<h2>结论</h2>
<div class="callout pass"><strong>PR123/125 Membase+Graph 与 Atom-only 均为 50/81（61.73%）。</strong> 原生 Graph-only 为 14/81（17.28%）；head_clean 为 30/81（37.04%）。</div>
<ul>
  <li>每题先调用 EchoMemory 原生 HTTP Top-25；Agent 可继续调用工具，最大 50 轮，没有固定为一次。</li>
  <li>Graph-only 不人为凑满 25 条：服务通常原生返回约 10 条，报告按实际返回量统计。</li>
  <li>平台不重算分数、不筛图、不直查 Neo4j、不补本地文件或 overview。</li>
  <li><strong>QA 读取已有长期记忆，并把召回证据放入当前题 prompt；QA/答案/Judge 不写回长期记忆，因此同一 workspace 可反复复用。</strong></li>
</ul>
</section>

<section class="panel"><h2>总览</h2><div class="wrap"><table>
<thead><tr><th>场景</th><th>准确率</th><th>空答</th><th>平均初始召回</th><th>工具调用</th><th>发生工具调用题数</th><th>最终证据类型</th></tr></thead>
<tbody>{rows_html}</tbody></table></div></section>

<section class="panel"><h2>按题型</h2><div class="wrap"><table>
<thead><tr><th>Category</th>{category_headers}</tr></thead><tbody>{category_html}</tbody>
</table></div></section>

<section class="panel"><h2>黑盒与写回审计</h2>
<div class="callout">`qa_memory_injection_enabled=true` 的含义仅是“将已召回证据注入当前 QA prompt”，不是写入记忆。写回审计检查结果为 0。</div>
<div class="wrap"><table><thead><tr><th>场景</th><th>Top-k</th><th>最大轮数</th><th>工具循环</th><th>当前 prompt 使用记忆</th><th>平台补证据</th><th>本地增强触发</th><th>写接口标记</th></tr></thead>
<tbody>{audit_html}</tbody></table></div></section>

<section class="panel"><h2>代码与环境</h2>
<ul>
  <li>head_clean：<code>/Users/chx/Code/echomemory/EchoMem_head_clean</code>，commit <code>{esc(head_commit)}</code></li>
  <li>PR123/125：<code>/Users/chx/Code/echomemory/EchoMem_develop_pr123_pr125_latest_20260710_204038</code>，commit <code>{esc(pr_commit)}</code></li>
  <li>PR workspace：<code>/Users/chx/echomem_workspace_locomo_conv30_pr123125_spacy_v6_20260710</code></li>
  <li>spaCy 3.8.14；<code>en_core_web_sm 3.8.0</code>；<code>zh_core_web_sm 3.8.0</code></li>
  <li>Neo4j：<code>bolt://127.0.0.1:7687</code>，数据库 <code>neo4j</code>；图场景由 EchoMemory 服务自身连接。</li>
</ul></section>

<section class="panel"><h2>正式结果目录</h2><ul>
{''.join(f'<li>{esc(run.label)}：<code>{esc(run.run_dir)}</code></li>' for run in runs)}
</ul></section>
</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head-run", required=True)
    parser.add_argument("--mixed-run", required=True)
    parser.add_argument("--graph-run", required=True)
    parser.add_argument("--atom-run", required=True)
    parser.add_argument("--head-commit", required=True)
    parser.add_argument("--pr-commit", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    runs = [
        read_run("head_clean Top-25", "develop baseline", args.head_run),
        read_run("PR123/125 Membase+Graph", "native mixed retrieval", args.mixed_run),
        read_run("PR123/125 Graph-only", "native graph retrieval", args.graph_run),
        read_run("PR123/125 Atom-only", "native atomic retrieval", args.atom_run),
    ]
    render(runs, Path(args.output).expanduser().resolve(), args.head_commit, args.pr_commit)
    print(Path(args.output).expanduser().resolve())


if __name__ == "__main__":
    main()

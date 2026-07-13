#!/usr/bin/env python3
"""Generate HTML report from Memory QA results."""

import argparse
import csv
import html as html_lib
import json
from datetime import datetime
from pathlib import Path


STRICT_AUGMENTATION_LABELS = {
    "current_session_raw_fallback": "current session raw fallback",
    "longmemeval_current_session_summary_fallback": "longmemeval summary fallback",
    "hotpot_empty_overview_fallback": "hotpot empty overview fallback",
    "segment_readback": "segment readback",
    "precision_session_readback": "precision session readback",
    "precision_grounded_projection": "precision grounded projection",
    "local_timeline_hints": "local timeline hints",
    "local_segments": "local segments",
    "local_messages": "local messages",
    "local_session_summaries": "local session summaries",
    "local_atoms": "local atoms",
    "local_memory_artifacts": "local memory artifacts",
    "local_graph_nodes": "local graph nodes",
}


def load_results(csv_path: str) -> list[dict]:
    with open(csv_path, "r", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def evaluate_answer(question: str, answer: str, response: str) -> tuple[str, float]:
    answer_lower = answer.lower().strip()
    response_lower = response.lower().strip()
    if "unknown" in response_lower and answer_lower not in response_lower:
        return "wrong", 0.0
    if answer_lower in response_lower or response_lower in answer_lower:
        return "correct", 1.0
    return "check", 0.5


def evaluate_row(row: dict) -> tuple[str, float]:
    verdict = str(row.get("result") or "").strip().upper()
    if verdict == "CORRECT":
        return "correct", 1.0
    if verdict == "WRONG":
        return "wrong", 0.0
    return evaluate_answer(row.get("question", ""), row.get("answer", ""), row.get("response", ""))


def parse_bool(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def parse_json_list(value) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def triggered_augmentation_paths(row: dict) -> list[str]:
    paths = parse_json_list(row.get("strict_blackbox_augmentation_paths"))
    if paths:
        return paths
    return [
        key
        for key in STRICT_AUGMENTATION_LABELS
        if parse_bool(row.get(f"{key}_triggered"))
    ]


def generate_html_report(
    results: list[dict],
    output_path: str,
    run_name: str = "Memory QA 评测报告",
    run_summary: dict | None = None,
) -> None:
    run_summary = run_summary or {}
    evaluations = []
    total_score = 0.0
    row_augmentations: list[list[str]] = []
    augmentation_counts = {key: 0 for key in STRICT_AUGMENTATION_LABELS}
    for row in results:
        status, score = evaluate_row(row)
        evaluations.append({"status": status, "score": score})
        total_score += score
        paths = triggered_augmentation_paths(row)
        row_augmentations.append(paths)
        for key in paths:
            if key in augmentation_counts:
                augmentation_counts[key] += 1

    accuracy = (total_score / len(results) * 100) if results else 0
    correct_count = sum(1 for e in evaluations if e["status"] == "correct")
    wrong_count = sum(1 for e in evaluations if e["status"] == "wrong")
    check_count = sum(1 for e in evaluations if e["status"] == "check")
    augmentation_rows = sum(1 for paths in row_augmentations if paths)
    overview_rows = sum(1 for row in results if parse_bool(row.get("overview_enrichment_triggered")))
    evidence_policy = str(run_summary.get("evidence_policy") or "").strip()
    is_blackbox = evidence_policy == "blackbox"
    http_overview_enabled = bool(run_summary.get("search_overview_enrichment_enabled"))
    transport_audit = run_summary.get("echomemory_transport_audit") or {}
    request_counts = transport_audit.get("request_counts") or {}
    search_request_count = int(request_counts.get("POST /api/retrieval/search") or 0)
    fs_read_request_count = int(request_counts.get("GET /fs/read") or 0)
    local_workspace_reads = int(transport_audit.get("local_workspace_evidence_reads") or 0)
    platform_neo4j_queries = int(transport_audit.get("platform_neo4j_queries") or 0)
    retrieval_source_mode = (
        "echo_http_native"
        if is_blackbox
        else str(run_summary.get("retrieval_source_mode") or "N/A")
    )
    retrieval_score_source = str(
        run_summary.get("retrieval_score_source")
        or ("echomemory_http_native" if is_blackbox else "N/A")
    )
    platform_score_recomputed = bool(run_summary.get("platform_score_recomputed", False))
    native_result_order_preserved = bool(
        run_summary.get("native_result_order_preserved", is_blackbox)
    )
    echomem_transport = str(run_summary.get("echomem_transport") or ("http" if is_blackbox else "N/A"))
    source_counts = run_summary.get("final_evidence_source_counts") or {}
    source_counts_text = ", ".join(
        f"{key}: {value}" for key, value in sorted(source_counts.items())
    ) or "N/A"
    native_graph_rows = int(run_summary.get("native_graph_recall_rows") or 0)
    graded_rows = int(run_summary.get("graded") or len(results) or 0)
    strict_augmentation_rows = int(run_summary.get("strict_blackbox_augmentation_rows") or 0)
    graph_only_blackbox = (
        is_blackbox
        and graded_rows > 0
        and native_graph_rows == graded_rows
        and bool(source_counts)
        and set(source_counts).issubset({"graph", "graph_node"})
        and not http_overview_enabled
        and strict_augmentation_rows == 0
        and local_workspace_reads == 0
        and platform_neo4j_queries == 0
    )
    scope_title = (
        "EchoMemory HTTP Graph-only 黑盒证据口径"
        if graph_only_blackbox
        else ("EchoMemory HTTP 黑盒证据口径" if is_blackbox else "运行证据口径")
    )
    if graph_only_blackbox:
        scope_description = (
            "仅使用 EchoMemory /api/retrieval/search 原生返回的 Graph 证据；"
            f"{native_graph_rows}/{graded_rows} 题的最终证据类型均为 graph_node。"
            "评测平台未调用 /fs/read、未直查 Neo4j，也未扫描本地 workspace 或拼接任何平台侧证据。"
        )
    elif is_blackbox and http_overview_enabled:
        scope_description = (
            "每题先调用一次 EchoMemory /api/retrieval/search；随后仅对 search 返回的 URI，"
            "通过 EchoMemory HTTP /fs/read 显式读取 overview.md。评测平台未直查 Neo4j，"
            "也未扫描本地 workspace、summaries、atoms、messages 或其他 artifacts。"
        )
    elif is_blackbox:
        scope_description = (
            "仅使用 EchoMemory /api/retrieval/search 返回的证据；评测平台未直查 Neo4j，"
            "未调用 /fs/read，也未扫描本地 workspace、summaries、atoms、messages 或其他 artifacts。"
            "证据分数和排序均保留 EchoMemory HTTP 原生结果，平台不重新打分。"
        )
    else:
        scope_description = "请结合 summary.json 中的 evidence_policy 与 augmentation 统计解释本次结果。"
    if graph_only_blackbox:
        scope_warning = (
            "该结果评价 EchoMemory HTTP 最终仅返回 Graph 证据进入 QA 后的表现。"
            "这里的 Graph-only 指最终证据来源，不代表服务端内部只执行图层；"
            "EchoMemory 仍会使用标准检索链生成图扩散种子。"
            "该模式由服务端运行配置控制，不是评测平台直连 Neo4j 的图证据注入实验。"
        )
    else:
        scope_warning = (
            (
            "该结果评价 EchoMemory HTTP search 加显式 HTTP overview read 的黑盒表现；"
            "/fs/read 仍属于 EchoMemory API，但不是 /search 原生返回。"
            if http_overview_enabled
            else "该结果评价 EchoMemory HTTP search 的原生黑盒表现。"
            )
            + " 结果不能单独归因于其内部 Membase 或 Graph。"
            + " 旧的 pr_mixed / pr_graph / pr_membase 三组属于平台侧证据注入诊断，不是 EchoMemory 原生图检索结果。"
            if is_blackbox
            else ""
        )

    def safe(value) -> str:
        return html_lib.escape(str(value if value is not None else ""))

    time_costs = [float(r["time_cost"]) for r in results if r.get("time_cost")]
    total_time = sum(time_costs)
    avg_time = total_time / len(time_costs) if time_costs else 0

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{run_name}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            line-height: 1.6;
            color: #1e293b;
            background: #f5f5f5;
            padding: 20px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 32px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
        }}
        h1 {{
            color: #0f172a;
            margin-bottom: 8px;
            font-size: 30px;
        }}
        .subtitle {{
            color: #64748b;
            margin-bottom: 24px;
            font-size: 14px;
        }}
        .progress-bar {{
            width: 100%;
            height: 28px;
            background: #e2e8f0;
            border-radius: 14px;
            overflow: hidden;
            margin: 20px 0 28px;
        }}
        .progress-fill {{
            height: 100%;
            background: linear-gradient(90deg, #11998e 0%, #38ef7d 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 14px;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .stat-card {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 18px;
            border-radius: 8px;
            text-align: center;
        }}
        .stat-card.success {{
            background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
        }}
        .stat-card.warning {{
            background: linear-gradient(135deg, #f97316 0%, #ef4444 100%);
        }}
        .stat-card.info {{
            background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
        }}
        .stat-value {{
            font-size: 32px;
            font-weight: bold;
            margin-bottom: 4px;
        }}
        .stat-label {{
            font-size: 13px;
            opacity: 0.92;
        }}
        .augmentation-panel {{
            margin: 8px 0 28px;
            padding: 16px 18px;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            background: #f8fafc;
        }}
        .scope-panel {{
            margin: 8px 0 24px;
            padding: 16px 18px;
            border: 1px solid #bfdbfe;
            border-left: 4px solid #2563eb;
            border-radius: 8px;
            background: #f8fbff;
        }}
        .scope-panel h2 {{
            margin: 0 0 8px;
            font-size: 17px;
            color: #172554;
        }}
        .scope-panel p {{
            margin: 6px 0;
            color: #334155;
        }}
        .scope-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 8px 16px;
            margin-top: 12px;
            font-size: 12px;
            color: #475569;
        }}
        .scope-grid code {{
            overflow-wrap: anywhere;
        }}
        .scope-warning {{
            color: #9a3412 !important;
            font-weight: 600;
        }}
        .augmentation-title {{
            font-size: 15px;
            font-weight: 600;
            color: #334155;
            margin-bottom: 12px;
        }}
        .augmentation-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 10px 16px;
        }}
        .augmentation-item {{
            display: flex;
            justify-content: space-between;
            gap: 12px;
            font-size: 13px;
            color: #475569;
        }}
        .augmentation-count {{
            font-weight: 700;
            color: #0f172a;
        }}
        .filter-buttons {{
            margin-bottom: 20px;
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }}
        .filter-btn {{
            padding: 8px 16px;
            border: 2px solid #e2e8f0;
            background: white;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
        }}
        .filter-btn.active {{
            background: #667eea;
            color: white;
            border-color: #667eea;
        }}
        .question-item {{
            background: #f8f9fa;
            border-left: 4px solid #ddd;
            padding: 20px;
            margin-bottom: 18px;
            border-radius: 4px;
        }}
        .question-item.correct {{
            border-left-color: #22c55e;
            background: #f0fdf4;
        }}
        .question-item.wrong {{
            border-left-color: #ef4444;
            background: #fef2f2;
        }}
        .question-item.check {{
            border-left-color: #f59e0b;
            background: #fffbeb;
        }}
        .question-header {{
            display: flex;
            align-items: center;
            margin-bottom: 14px;
        }}
        .question-number {{
            font-weight: bold;
            font-size: 18px;
            margin-right: 10px;
            color: #0f172a;
        }}
        .question-status {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
            margin-left: auto;
            color: white;
        }}
        .question-status.correct {{ background: #22c55e; }}
        .question-status.wrong {{ background: #ef4444; }}
        .question-status.check {{ background: #f59e0b; }}
        .question-text {{
            font-size: 16px;
            font-weight: 500;
            color: #0f172a;
            margin-bottom: 14px;
        }}
        .answer-section {{
            display: grid;
            gap: 10px;
        }}
        .answer-row {{
            display: flex;
            gap: 10px;
        }}
        .answer-label {{
            font-weight: 600;
            color: #64748b;
            min-width: 80px;
        }}
        .answer-value {{
            flex: 1;
            color: #334155;
        }}
        .answer-value.response {{
            color: #0f766e;
        }}
        .augmentation-row {{
            margin-top: 12px;
            display: flex;
            align-items: flex-start;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .augmentation-label-inline {{
            font-size: 12px;
            font-weight: 600;
            color: #64748b;
        }}
        .augmentation-chip {{
            display: inline-flex;
            align-items: center;
            padding: 3px 8px;
            border-radius: 999px;
            background: #eef2ff;
            color: #3730a3;
            font-size: 12px;
            line-height: 1.4;
        }}
        .augmentation-chip.inactive {{
            background: #f1f5f9;
            color: #94a3b8;
        }}
        .augmentation-none {{
            font-size: 12px;
            color: #94a3b8;
        }}
        .meta-info {{
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid #e2e8f0;
            font-size: 12px;
            color: #64748b;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{run_name}</h1>
        <div class="subtitle">生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>

        <section class="scope-panel">
            <h2>{scope_title}</h2>
            <p>{scope_description}</p>
            {f'<p class="scope-warning">{scope_warning}</p>' if scope_warning else ''}
            <div class="scope-grid">
                <div>evidence_policy: <code>{safe(evidence_policy or "N/A")}</code></div>
                <div>retrieval_source_mode: <code>{safe(retrieval_source_mode)}</code></div>
                <div>检索分数来源: <code>{safe(retrieval_score_source)}</code></div>
                <div>平台重算分数: <code>{safe(platform_score_recomputed)}</code></div>
                <div>保留原生排序: <code>{safe(native_result_order_preserved)}</code></div>
                <div>EchoMemory transport: <code>{safe(echomem_transport)}</code></div>
                <div>后端路由: <code>echomemory_http_api_blackbox</code></div>
                <div>top_k: <code>{safe(run_summary.get("top_k") or "N/A")}</code></div>
                <div>平台补证据: <code>{safe(run_summary.get("platform_evidence_injection_enabled", False))}</code></div>
                <div>原生图策略: <code>{safe(run_summary.get("native_graph_policy") or "server_controlled")}</code></div>
                <div>原生图命中题: <code>{native_graph_rows}/{graded_rows or "N/A"}</code></div>
                <div>HTTP overview (/fs/read): <code>{safe(run_summary.get("search_overview_enrichment_enabled", "N/A"))}</code></div>
                <div>HTTP search 请求: <code>{search_request_count}</code></div>
                <div>HTTP fs/read 请求: <code>{fs_read_request_count}</code></div>
                <div>本地 workspace 证据读取: <code>{local_workspace_reads}</code></div>
                <div>平台 Neo4j 查询: <code>{platform_neo4j_queries}</code></div>
                <div>证据类型: <code>{safe(source_counts_text)}</code></div>
                <div>工作区: <code>{safe(run_summary.get("workspace") or "N/A")}</code></div>
                <div>代码仓: <code>{safe(run_summary.get("echomem_root") or "N/A")}</code></div>
            </div>
        </section>

        <div class="progress-bar">
            <div class="progress-fill" style="width: {accuracy:.1f}%">准确率: {accuracy:.1f}%</div>
        </div>

        <div class="stats-grid">
            <div class="stat-card success"><div class="stat-value">{correct_count}</div><div class="stat-label">正确</div></div>
            <div class="stat-card warning"><div class="stat-value">{wrong_count}</div><div class="stat-label">错误</div></div>
            <div class="stat-card info"><div class="stat-value">{check_count}</div><div class="stat-label">需检查</div></div>
            <div class="stat-card"><div class="stat-value">{len(results)}</div><div class="stat-label">总题数</div></div>
            <div class="stat-card info"><div class="stat-value">{total_time/60:.1f}m</div><div class="stat-label">总耗时</div></div>
            <div class="stat-card"><div class="stat-value">{avg_time:.1f}s</div><div class="stat-label">平均耗时</div></div>
            <div class="stat-card warning"><div class="stat-value">{augmentation_rows}</div><div class="stat-label">平台补证据题数</div></div>
            <div class="stat-card info"><div class="stat-value">{overview_rows}</div><div class="stat-label">HTTP overview 题数</div></div>
        </div>

        <div class="augmentation-panel">
            <div class="augmentation-title">平台补证据路径触发统计</div>
            <div class="augmentation-grid">
"""

    for key, label in STRICT_AUGMENTATION_LABELS.items():
        html += f'                <div class="augmentation-item"><span>{label}</span><span class="augmentation-count">{augmentation_counts.get(key, 0)}</span></div>\n'

    html += f"""            </div>
        </div>

        <div class="filter-buttons">
            <button class="filter-btn active" onclick="filterQuestions(event, 'all')">全部 ({len(results)})</button>
            <button class="filter-btn" onclick="filterQuestions(event, 'correct')">正确 ({correct_count})</button>
            <button class="filter-btn" onclick="filterQuestions(event, 'wrong')">错误 ({wrong_count})</button>
            <button class="filter-btn" onclick="filterQuestions(event, 'check')">需检查 ({check_count})</button>
        </div>

        <div class="question-list">
"""

    for index, (row, eval_data, augmentations) in enumerate(zip(results, evaluations, row_augmentations), 1):
        status = eval_data["status"]
        status_icon = {"correct": "OK", "wrong": "ERR", "check": "CHK"}[status]
        status_text = {"correct": "正确", "wrong": "错误", "check": "需检查"}[status]
        retrieval_count = row.get("retrieval_count", "N/A")
        time_cost = float(row.get("time_cost", 0) or 0)
        active_augmentations = set(augmentations)
        chips = "".join(
            f'<span class="augmentation-chip">{label}</span>'
            for key, label in STRICT_AUGMENTATION_LABELS.items()
            if key in active_augmentations
        )
        if parse_bool(row.get("overview_enrichment_triggered")):
            chips += '<span class="augmentation-chip">HTTP overview /fs/read</span>'
        if not chips:
            chips = '<span class="augmentation-none">仅使用原生 HTTP search 结果</span>'
        html += f"""
            <div class="question-item {status}" data-status="{status}">
                <div class="question-header">
                    <span class="question-number">{status_icon} Q{index}</span>
                    <span class="question-status {status}">{status_text}</span>
                </div>
                <div class="question-text">{row.get("question", "")}</div>
                <div class="answer-section">
                    <div class="answer-row">
                        <span class="answer-label">标准答案:</span>
                        <span class="answer-value">{row.get("answer", "")}</span>
                    </div>
                    <div class="answer-row">
                        <span class="answer-label">模型回答:</span>
                        <span class="answer-value response">{row.get("response", "")}</span>
                    </div>
                </div>
                <div class="augmentation-row">
                    <span class="augmentation-label-inline">证据路径:</span>
                    {chips}
                </div>
                <div class="meta-info">
                    检索数: {retrieval_count} | 耗时: {time_cost:.2f}s | 问题ID: {row.get("question_id", "N/A")}
                </div>
            </div>
"""

    html += """
        </div>
    </div>

    <script>
        function filterQuestions(event, status) {
            const items = document.querySelectorAll('.question-item');
            const buttons = document.querySelectorAll('.filter-btn');
            buttons.forEach((btn) => btn.classList.remove('active'));
            event.target.classList.add('active');
            items.forEach((item) => {
                item.style.display = status === 'all' || item.dataset.status === status ? 'block' : 'none';
            });
        }
    </script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(html)

    print(f"HTML 报告已生成: {output_path}")
    print(f"准确率: {accuracy:.1f}% ({total_score}/{len(results)})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate HTML report from QA results")
    parser.add_argument("csv_path", help="Path to CSV results file")
    parser.add_argument("--output", "-o", help="Output HTML file path", default=None)
    parser.add_argument("--name", "-n", help="Report name", default="Memory QA 评测报告")
    args = parser.parse_args()

    results = load_results(args.csv_path)
    summary_path = Path(args.csv_path).parent / "summary.json"
    run_summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    output_path = args.output or (Path(args.csv_path).parent / f"{Path(args.csv_path).stem}_report.html")
    generate_html_report(results, str(output_path), args.name, run_summary)


if __name__ == "__main__":
    main()

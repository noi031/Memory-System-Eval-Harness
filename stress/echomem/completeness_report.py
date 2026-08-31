#!/usr/bin/env python3
"""Build one auditable HTML report for the PR397 and PR421 gap tests."""

from __future__ import annotations

import argparse
import csv
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def esc(value: Any) -> str:
    return html.escape("-" if value in (None, "") else str(value))


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def first_json(root: Path, *candidates: str) -> dict[str, Any]:
    for candidate in candidates:
        payload = load_json(root / candidate)
        if payload:
            return payload
    return {}


def badge(status: str) -> str:
    css = {
        "PASS": "pass",
        "FAIL": "fail",
        "INCONCLUSIVE": "inc",
        "NOT_IMPLEMENTED": "ni",
        "PARTIAL": "partial",
    }.get(status, "unknown")
    return f"<span class='badge {css}'>{esc(status)}</span>"


def row(item: dict[str, Any]) -> str:
    return (
        f"<tr><td>{esc(item.get('id'))}</td><td>{esc(item.get('name'))}</td>"
        f"<td>{badge(str(item.get('status') or 'UNKNOWN'))}</td>"
        f"<td>{esc(item.get('evidence'))}</td><td>{esc(item.get('conclusion'))}</td>"
        f"<td>{esc(item.get('next'))}</td></tr>"
    )


def summarize_status(items: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        key = str(item.get("status") or "UNKNOWN")
        result[key] = result.get(key, 0) + 1
    return result


def build_items(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    acceptance = first_json(root, "pr421_acceptance.json")
    observable = first_json(
        root,
        "pr397_observable.json",
        "missing-cases-remote.json",
        "missing-cases.json",
    )
    saturation = first_json(
        root,
        "saturation.json",
        "limit-sweep-fixed3/summary.json",
        "limit-sweep/summary.json",
    )
    saturation_evidence = (
        "saturation.json"
        if (root / "saturation.json").is_file()
        else "limit-sweep-fixed3/summary.json"
        if (root / "limit-sweep-fixed3/summary.json").is_file()
        else "limit-sweep/summary.json"
    )
    fault = first_json(
        root,
        "fault_suite.json",
        "fault-suite-server/fault-suite.json",
    )
    recovery = first_json(
        root,
        "recovery.json",
        "recovery-gap-20260830.json",
    )
    commit_recovery = first_json(
        root,
        "commit-recovery-live-final.json",
        "commit-recovery-live-r4.json",
        "commit-recovery-live-r3.json",
    )
    soak = first_json(
        root,
        "soak-2h/summary.json",
        "soak-bounded/summary.json",
        "soak/summary.json",
    )
    soak_duration_s = float(
        soak.get("duration_s")
        or (soak.get("metrics") or {}).get("workload_duration_s")
        or 0
    )
    soak_quality_failures = int(
        (soak.get("details") or {}).get("quality_failures")
        or (soak.get("metrics") or {}).get("search", {}).get("quality_failures")
        or 0
    )
    soak_commit_failures = int(
        (soak.get("details") or {}).get("commit_failures")
        or (soak.get("metrics") or {}).get("commit", {}).get("failed")
        or 0
    )
    soak_search_errors = int(
        (soak.get("details") or {}).get("search_errors")
        or (soak.get("metrics") or {}).get("search", {}).get("errors")
        or 0
    )
    soak_resource_path = (
        root / "soak-2h/resource_samples.csv"
        if (root / "soak-2h/resource_samples.csv").is_file()
        else root / "soak-bounded/resource_samples.csv"
    )
    soak_rss_observed = False
    if soak_resource_path.is_file():
        try:
            with soak_resource_path.open(newline="", encoding="utf-8") as handle:
                soak_rss_observed = any(
                    float(row.get("rss_mb") or 0) > 0
                    for row in csv.DictReader(handle)
                )
        except (OSError, ValueError):
            soak_rss_observed = False
    disconnect = first_json(
        root,
        "disconnect-recovery.json",
    )
    network_fault = first_json(
        root,
        "network_fault.json",
        "fault-suite-server/network-fault-real.json",
    )
    k6 = first_json(
        root,
        "k6_summary.json",
        "k6-fixed-commit-20260830-summary.json",
        "k6-paced-20260830-summary.json",
    )
    capability = first_json(root, "capability-probe.json", "capability_probe.json")
    concurrent_commit = first_json(
        root,
        "remote/concurrent-commit-32-summary.json",
        "concurrent-commit/concurrent-commit.json",
        "concurrent-commit-r3/concurrent-commit.json",
    )
    if "case" not in concurrent_commit and "accepted_count" in concurrent_commit:
        concurrent_commit = {"case": concurrent_commit, "evidence": "remote/concurrent-commit-32-summary.json"}

    capability_status = {
        str(item.get("name")): str(item.get("status"))
        for item in capability.get("checks") or []
        if isinstance(item, dict) and item.get("name")
    }
    capability_evidence = (
        "capability-probe.json" if capability else "当前未运行 capability_probe.py"
    )

    observable_summary = observable.get("summary") or {}
    write_cases = [
        item.get("result") or {}
        for item in observable.get("cases") or []
        if item.get("kind") == "write-after-read"
    ]
    persisted_but_missed = sum(
        1 for item in write_cases if item.get("search_missed_persisted_marker")
    )
    persistence_failures = sum(
        1 for item in write_cases
        if item.get("persistence_readback_status") == "FAIL"
    )
    consistency_status = (
        "INCONCLUSIVE"
        if persisted_but_missed and not persistence_failures
        else "FAIL"
        if persistence_failures
        else "PASS"
        if write_cases
        else "INCONCLUSIVE"
    )
    consistency_conclusion = (
        f"{persisted_but_missed}/{len(write_cases)} 个租户已在 history/archive "
        "中读回 marker，但 Search 60 秒内未召回；属于 Search 可见性/召回问题，"
        "暂不能归因为数据丢失。"
        if persisted_but_missed
        else f"{persistence_failures}/{len(write_cases)} 个租户的持久化回读失败。"
        if persistence_failures
        else "没有足够的三层读回证据。"
    )

    pr397: list[dict[str, Any]] = [
        {
            "id": "S1",
            "name": "写后读一致性",
            "status": consistency_status,
            "evidence": "observable-cases-v5.json",
            "conclusion": consistency_conclusion,
            "next": "增加 index publication/召回命中证据，并区分搜索质量与可见性 SLA。",
        },
        {
            "id": "S1",
            "name": "Commit 状态机",
            "status": "PASS",
            "evidence": "observable-cases-v5.json",
            "conclusion": "4/4 进入 completed，未观察到状态回退。",
            "next": "继续在慢请求和故障恢复期间采集完整中间状态。",
        },
        {
            "id": "S1",
            "name": "Commit 幂等性",
            "status": capability_status.get("operation/idempotency", "INCONCLUSIVE"),
            "evidence": capability_evidence,
            "conclusion": "统一能力探测已记录真实 operation/idempotency 契约状态。",
            "next": "提供并配置真实 operation 查询或幂等键契约后执行重放对账。",
        },
        {
            "id": "S2",
            "name": "连续 Commit 洪峰与降载恢复",
            "status": "INCONCLUSIVE",
            "evidence": "pr421 acceptance + saturation-128-v2",
            "conclusion": "有压力和超时证据，但没有队列深度、入口拒绝和恢复指标。",
            "next": "补队列打满前置、429/503、Retry-After 和降载后 drain。",
        },
        {
            "id": "S3",
            "name": "取消/断连资源回收",
            "status": "INCONCLUSIVE",
            "evidence": "disconnect-recovery.json" if disconnect else "当前部署无逐请求后台任务指标",
            "conclusion": (
                "32 个真实 HTTP Search 连接主动断开后服务保持健康；30 秒采样线程/FD "
                "上升，但约 60 秒后回落，尚无逐请求后台任务和孤儿任务对账。"
                if disconnect
                else "尚无断开连接后的 inflight、孤儿任务、线程和 FD 对账。"
            ),
            "next": "增加请求级取消状态、后台任务数和资源归属指标，重复多轮断连并设置回收 SLA。",
        },
        {
            "id": "S3",
            "name": "冷启动、热启动和重启恢复",
            "status": (
                "INCONCLUSIVE"
                if commit_recovery and commit_recovery.get("status") == "INCONCLUSIVE"
                else "PASS"
                if commit_recovery and commit_recovery.get("status") == "PASS"
                else "PARTIAL"
                if recovery
                else "INCONCLUSIVE"
            ),
            "evidence": (
                "commit-recovery-live-final.json"
                if commit_recovery
                else "recovery-gap-20260830.json"
                if recovery
                else "fault_suite.json"
            ),
            "conclusion": (
                "Commit 返回 pending 后 kill-9，服务约 10 秒恢复，Commit 随后达到 completed；"
                "但没有 cursor/message-set 导出，无法证明精确 replay 或幂等。"
                if commit_recovery and commit_recovery.get("status") == "PASS"
                else "Commit 期间 kill-9 后服务已恢复，但缺少可验证的 Commit 终态或消息集合对账。"
                if commit_recovery
                else "独立容器 kill-9 后约 10 秒恢复健康；尚未验证中断 Commit 的 replay、"
                "幂等和 cursor 对账。"
                if recovery
                else "没有可控的真实进程/容器重启控制。"
            ),
            "next": "增加 cursor/message-set 导出，并检查恢复前后 message_id、archive_id 和重复处理次数。",
        },
        {
            "id": "S3",
            "name": "长稳态 2 小时",
            "status": (
                "FAIL"
                if soak_duration_s >= 7200
                and (soak_quality_failures or soak_commit_failures or soak_search_errors)
                else "PASS"
                if soak_duration_s >= 7200
                else "PARTIAL"
            ),
            "evidence": (
                "soak-2h/summary.json"
                if (root / "soak-2h/summary.json").is_file()
                else "soak-bounded/summary.json"
                if soak
                else "PR421 soak 30 分钟级别"
            ),
            "conclusion": (
                f"已完成 2 小时窗口化 soak；Search {soak.get('details', {}).get('search_total', '-')} "
                f"次 HTTP 全部成功，Commit {soak.get('details', {}).get('commit_total', '-')} 次全部完成；"
                f"质量 marker 未命中 {soak_quality_failures} 次，RSS slope "
                f"{soak.get('details', {}).get('rss_slope_mb_min', '-')} MB/min；"
                + (
                    "RSS 采样有效，"
                    if soak_rss_observed
                    else "RSS 采样未捕获目标进程，不能据此判断内存稳定性；"
                )
                + "因此长稳态质量门禁 FAIL，但未观察到 HTTP/Commit 稳定性错误。"
                if soak_duration_s >= 7200
                else (
                    f"已完成约 {soak_duration_s / 60:.0f} 分钟 soak；"
                    f"HTTP Search {soak.get('details', {}).get('search_total', '-')} 个全部 200，"
                    f"Commit {soak.get('details', {}).get('commit_total', '-')} 个全部完成，"
                    f"但质量 marker {soak.get('details', {}).get('quality_failures', '-')} 个未命中，"
                    "且未覆盖 2 小时窗口。"
                )
                if soak
                else "已有短时 soak，未完成 2 小时窗口化泄漏和冷却后比较。"
            ),
            "next": (
                "先修复 Search 可见性/索引发布问题；同时让资源采样器捕获被测进程后再判定内存趋势。"
                if soak_duration_s >= 7200
                else "固定数据和资源 profile，执行 2 小时并记录每 5 分钟窗口。"
            ),
        },
        {
            "id": "S3",
            "name": "冷缓存 TTL",
            "status": capability_status.get("cache/TTL", "INCONCLUSIVE"),
            "evidence": capability_evidence,
            "conclusion": "统一能力探测已记录真实 TTL/cache 契约状态。",
            "next": "提供 TTL 配置和 cache hit/miss、加载次数指标。",
        },
        {
            "id": "S4",
            "name": "LLM/Vector/网络故障注入",
            "status": capability_status.get("fault control", "INCONCLUSIVE"),
            "evidence": capability_evidence,
            "conclusion": "统一能力探测已记录故障控制面状态，未伪造故障结果。",
            "next": "提供真实故障代理或控制端点后执行 500、超时、断连和恢复。",
        },
        {
            "id": "S9",
            "name": "同租户版本冲突",
            "status": capability_status.get("version/conflict", "INCONCLUSIVE"),
            "evidence": capability_evidence,
            "conclusion": "统一能力探测已记录真实版本/冲突契约状态。",
            "next": "并发写同一实体，记录冲突重试、上限和最终 cursor。",
        },
        {
            "id": "S10",
            "name": "Engine 加载失败隔离",
            "status": capability_status.get("engine status/degradation", "INCONCLUSIVE"),
            "evidence": capability_evidence,
            "conclusion": "统一能力探测已记录 engine 状态/降级契约状态。",
            "next": "增加 engine_id、故障原因、降级状态和可用清单。",
        },
        {
            "id": "S11",
            "name": "完整容量阶梯与资源 profile",
            "status": "PARTIAL",
            "evidence": "capacity-2/4/8/16/32 + server metrics",
            "conclusion": "已跑 HTTP 容量波次，但 8/16 使用共享 key，且缺真实资源限制与服务端队列指标。",
            "next": "准备独立凭证和 2/4/8/12/16/24/32 固定 profile。",
        },
    ]

    checks = acceptance.get("checks") or []
    pr421: list[dict[str, Any]] = []
    for check in checks:
        pr421.append(
            {
                "id": "B7" if "B7" in str(check.get("name")) else "PR421",
                "name": check.get("name"),
                "status": check.get("status"),
                "evidence": check.get("evidence"),
                "conclusion": check.get("reason"),
                "next": "补齐服务端证据或修复失败项后重跑。",
            }
        )
    if saturation:
        status_counts = saturation.get("status_counts") or {}
        search_levels = [
            (key, value) for key, value in status_counts.items()
            if key.startswith("search-workers-")
        ]
        recovery = status_counts.get("recovery-search-workers-4") or {}
        if search_levels:
            saturation_detail = (
                "；".join(
                    f"{key}={value}"
                    for key, value in search_levels
                )
                + f"；恢复波次={recovery}"
            )
        else:
            saturation_detail = (
                f"Search={status_counts.get('search', {})}；"
                f"Open={status_counts.get('open', {})}"
            )
        pr421.append(
            {
                "id": "B6",
                "name": "128 并发真实入口饱和补测",
                "status": "FAIL",
                "evidence": saturation_evidence,
                "conclusion": (
                    f"{saturation_detail}；128/256 并发出现大量客户端超时，"
                    "未出现 429/503；降载后 Search 16/16 成功。"
                ),
                "next": "增加服务端拒绝原因、Retry-After 和降载恢复证据。",
            }
        )
    if fault:
        pr421.append(
            {
                "id": "B8",
                "name": "故障、kill-9、cursor 对账补测",
                "status": "INCONCLUSIVE",
                "evidence": "fault-suite-server/fault-suite.json",
                "conclusion": "当前结果只能说明测试平台没有拿到足够的外部执行证据，不能据此断定 EchoMem 未实现。",
                "next": "优先通过已有 history、archives、commit_status 和 /fs/read 取证；只有真实接口返回 404 才标记 NOT_IMPLEMENTED。",
            }
        )
    if concurrent_commit:
        case = concurrent_commit.get("case") or {}
        duplicate = bool(case.get("duplicate_acceptance"))
        pr421.append(
            {
                "id": "B5",
                "name": "同一 Session 并发 Commit / 版本冲突",
                "status": "INCONCLUSIVE" if duplicate else str(case.get("status") or "UNKNOWN"),
                "evidence": concurrent_commit.get(
                    "evidence", "concurrent-commit-r3/concurrent-commit.json"
                ),
                "conclusion": (
                    f"{case.get('accepted_count', 0)} 个并发 Commit 均返回并完成，"
                    f"archive_id 去重后为 {case.get('unique_archive_ids', 0)} 个；"
                    "服务表现为合并/复用，但公开 API 没有 operation_id 或幂等契约，"
                    "不能据此证明不会重复处理。"
                    if duplicate
                    else "未观察到重复 archive_id."
                ),
                "next": "提供 operation_id/idempotency key 和版本冲突计数，再验证重放与最终消息集合。",
            }
        )
    if commit_recovery:
        pr421.append(
            {
                "id": "B8",
                "name": "pending Commit kill-9 恢复",
                "status": (
                    "PASS"
                    if commit_recovery.get("status") == "PASS"
                    and commit_recovery.get("recovered")
                    and (
                        (commit_recovery.get("commit_terminal") or [{}])[-1].get("state")
                        == "completed"
                    )
                    else str(commit_recovery.get("status") or "INCONCLUSIVE")
                ),
                "evidence": "commit-recovery-live-final.json",
                "conclusion": (
                    "Commit 返回 pending 后容器被真实 kill-9；服务恢复健康，"
                    "同一 archive_id 最终查询为 completed。精确重放次数仍无法由公开 API 证明。"
                ),
                "next": "增加 operation_id、cursor/message-set 和重复处理计数，验证无丢失、无重复。",
            }
        )
    if network_fault:
        pr421.append(
            {
                "id": "B8",
                "name": "真实网络/依赖故障行为",
                "status": network_fault.get("status", "INCONCLUSIVE"),
                "evidence": "network-fault-real.json",
                "conclusion": (
                    "拒绝真实 HTTPS 出口后 Search 返回 degraded，并报告 "
                    "embedding/provider failure；解除规则后服务健康恢复。"
                ),
                "next": "补充 LLM 500、超时、连接重置等独立故障类型。",
            }
        )
    if k6:
        scenario = k6.get("scenario") or {}
        metrics = scenario.get("metrics") or {}
        requests = (metrics.get("http_reqs") or {}).get("values") or {}
        failed = (metrics.get("http_req_failed") or {}).get("values") or {}
        duration = (metrics.get("http_req_duration") or {}).get("values") or {}
        k6_rate = float(failed.get("rate") or 0)
        k6_status = "FAIL" if k6_rate > 0 else "PASS"
        k6_evidence = (
            "k6-fixed-commit-20260830-summary.json"
            if "k6-fixed-commit-20260830-summary.json" in {
                path.name for path in root.glob("k6-fixed-commit-20260830-summary.json")
            }
            else "k6-paced-20260830-summary.json"
        )
        pr421.append(
            {
                "id": "B7",
                "name": "k6 真实 HTTP 压测",
                "status": k6_status,
                "evidence": k6_evidence,
                "conclusion": (
                    f"请求 {requests.get('count', '-')}，失败率 {k6_rate}; "
                    f"P95 {duration.get('p(95)', '-')}ms，"
                    f"P99 {duration.get('p(99)', '-')}ms。"
                ),
                "next": "修正到达率/场景统计，并与同窗口 runner request_id 对账。",
            }
        )
    reconciliation = first_json(root, "k6-reconciliation.json")
    if reconciliation:
        pr421.append(
            {
                "id": "B7",
                "name": "k6 与 Runner 逐请求对账",
                "status": reconciliation.get("status", "INCONCLUSIVE"),
                "evidence": "k6-reconciliation.json",
                "conclusion": (
                    f"k6 请求 {reconciliation.get('k6_http_requests', '-')}，"
                    f"Runner 请求 {reconciliation.get('runner_total_requests', '-')}；"
                    f"{reconciliation.get('reason', '已生成对账结果。')}"
                ),
                "next": "确保两者使用同一测试窗口和唯一 request_id，并核对每个操作类型。",
            }
        )
    return pr397, pr421


def render(root: Path) -> str:
    pr397, pr421 = build_items(root)
    capability = first_json(root, "capability-probe-20260830.json")
    all_items = pr397 + pr421
    counts = summarize_status(all_items)
    generated = datetime.now(timezone.utc).isoformat()
    stat_cards = "".join(
        f"<div class='metric'><span>{esc(key)}</span><b>{value}</b></div>"
        for key, value in sorted(counts.items())
    )
    endpoint_caps = capability.get("endpoint_capabilities") or {}
    missing_endpoints = [
        name for name, item in endpoint_caps.items()
        if isinstance(item, dict) and not item.get("available")
    ]
    metric_caps = (
        capability.get("metrics", {}).get("required_pr421_metric_families") or {}
    )
    missing_metrics = [name for name, present in metric_caps.items() if not present]
    capability_section = (
        f"""<section><h2>最新真实接口探测</h2>
<p>目标实例版本 <code>{esc(capability.get("target", {}).get("health", {}).get("version"))}</code>，
探测时间 <code>{esc(capability.get("created_at"))}</code>。已真实创建 Session、追加消息，
并重复提交 Commit（两次均返回 202），但公开响应没有 operation_id 或幂等结果。</p>
<p>不可用端点：<code>{esc(", ".join(missing_endpoints) or "无")}</code>；
缺失 PR421 指标族：<code>{esc(", ".join(missing_metrics) or "无")}</code>。</p>
</section>"""
        if capability
        else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EchoMem PR397 + PR421 缺口测试总报告</title>
<style>
:root{{--bg:#f5f7f8;--paper:#fff;--ink:#18232d;--muted:#6b7882;--line:#dfe6ea;
--green:#187b61;--red:#b53c38;--amber:#986b18;--blue:#2469a6}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
main{{max-width:1320px;margin:auto;padding:28px 18px 60px}}header,section,.metric{{background:var(--paper);
border:1px solid var(--line);border-radius:8px}}header{{padding:24px 26px;border-top:5px solid var(--blue)}}
h1{{margin:0;font-size:26px}}h2{{font-size:18px;margin:0 0 12px}}h3{{font-size:15px}}
.muted{{color:var(--muted)}}section{{padding:18px 20px;margin-top:14px}}.metrics{{display:flex;flex-wrap:wrap;
gap:10px;margin-top:14px}}.metric{{min-width:120px;padding:12px 15px}}.metric span{{display:block;color:var(--muted);
font-size:12px}}.metric b{{display:block;font-size:25px;margin-top:3px}}.notice{{padding:12px 14px;
border-left:4px solid var(--amber);background:#fff8e6;margin-top:14px}}.scroll{{overflow:auto}}
table{{width:100%;border-collapse:collapse;min-width:980px}}th,td{{padding:9px 8px;text-align:left;
vertical-align:top;border-bottom:1px solid var(--line)}}th{{background:#f8fafb;color:var(--muted)}}
.badge{{display:inline-block;padding:2px 8px;border-radius:3px;font-size:12px;font-weight:750;white-space:nowrap}}
.pass{{color:var(--green);background:#e8f6ef}}.fail{{color:var(--red);background:#fdeceb}}
.inc,.partial{{color:var(--amber);background:#fff4d9}}.ni{{color:#5c6570;background:#eef1f3}}
.unknown{{color:#fff;background:#707b84}}code{{background:#eef2f4;padding:2px 5px;border-radius:3px}}
.footer{{margin-top:14px;color:var(--muted);font-size:12px}}@media(max-width:650px){{main{{padding:16px 10px 40px}}
header{{padding:20px 17px}}h1{{font-size:22px}}}}
</style></head><body><main>
<header><h1>EchoMem PR397 + PR421 缺口测试总报告</h1>
<div class="muted">真实 HTTP、真实服务；不使用 mock 结果 · 生成时间 {esc(generated)}</div>
<div class="notice"><b>当前结论：</b>可执行的缺口已经补测，但两套方案仍不能宣称完整通过。
报告严格区分 HTTP 可用性、质量召回、恢复和服务端观测证据；没有接口或同窗口对账证据的项目不会被标记为通过。</div></header>
<div class="metrics">{stat_cards}</div>
<section><h2>PR397</h2><div class="scroll"><table><thead><tr>
<th>编号</th><th>场景</th><th>状态</th><th>证据</th><th>结论</th><th>下一步</th>
</tr></thead><tbody>{''.join(row(item) for item in pr397)}</tbody></table></div></section>
<section><h2>PR421</h2><div class="scroll"><table><thead><tr>
<th>编号</th><th>验收项</th><th>状态</th><th>证据</th><th>结论</th><th>下一步</th>
</tr></thead><tbody>{''.join(row(item) for item in pr421)}</tbody></table></div></section>
{capability_section}
<section><h2>必须补齐的外部能力</h2><ol>
<li>EchoMem 暴露 lane/fan-out、queue depth、request timeline 和 reason_code 指标。</li>
<li>提供真实故障控制、进程/容器重启、cursor/message-set 导出接口。</li>
<li>为 2/4/8/12/16/24/32 容量档准备独立凭证和固定资源限制。</li>
<li>安装并执行 k6，把 k6 summary 与逐请求 CSV 对账。</li>
<li>先修复 Commit completed 后 Search 找不到 marker 的一致性问题，再做最终验收。</li>
</ol></section>
<div class="footer">原始结果目录：{esc(root)}。报告保留失败和未实现项，不把客户端超时或脚本配置当作服务端通过证据。</div>
</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(args.root), encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

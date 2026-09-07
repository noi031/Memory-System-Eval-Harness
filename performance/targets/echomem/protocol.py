"""EchoMem HTTP 协议适配层（不是场景）。

三层职责：

- **端点级函数**（``search`` / ``open_session`` / ``add_message`` /
  ``commit_session`` / ``poll_commit``）：封装 EchoMem 每个 HTTP 端点的
  请求构造、响应解析与记录语义，是「该系统支持哪些端点」的清单；
- **标准任务**（``task_read`` / ``task_write``）：端点组合成的读/写负载
  路径，是四场景共用的默认形态；
- 常量与响应解析辅助。

场景文件通过本模块组合端点；新增只测部分端点的场景时，直接调用端点级
函数即可，不必复用标准任务。
"""

from __future__ import annotations

from typing import Any

from performance.ctx import Ctx, Response, PollResult
from performance.records import content_hash

ANCHOR_PREFIX = "PERFANCHOR"
WRITE_ANCHOR_PREFIX = "PERFTAIL"

DEFAULT_QUERIES = [
    "什么是记忆",
    "用户今天的心情怎么样",
    "帮我总结一下最近的对话",
    "上个月的项目进展",
    "用户的偏好是什么",
]


def is_anchor_query(query: str) -> bool:
    """Whether a read query is an anchor token (must always be recallable)."""
    return ANCHOR_PREFIX in query or WRITE_ANCHOR_PREFIX in query


# --------------------------------------------------------------------- #
#  端点级函数：一个函数 = 一个 EchoMem HTTP 端点                         #
# --------------------------------------------------------------------- #

def search(ctx: Ctx, query: str, *, top_k: int = 5) -> Response:
    """POST /api/retrieval/search 并记录质量断言字段。

    HTTP 错误记 error；200 但空结果对锚词查询记为 quality 失败
    （degraded 除外）。
    """
    resp = ctx.post(
        "/api/retrieval/search",
        body={
            "query": query,
            "agent_id": str(ctx.params.get("agent_id", "default")),
            "limit": top_k,
            "include_explain": True,
            "include_debug": True,
        },
        op="read",
        query=query,
    )
    if not resp.ok:
        return resp
    result = resp.json.get("result") if isinstance(resp.json, dict) else None
    if not isinstance(result, dict):
        return resp
    items = result.get("items") or []
    hit_count = len(items)
    degraded = bool(result.get("degraded_reasons")) or str(
        result.get("status") or ""
    ).lower() == "degraded"
    real_recall = (
        bool(result.get("explain"))
        or bool(result.get("debug"))
        or hit_count > 0
    )
    quality_ok = True
    if is_anchor_query(query):
        # A degraded empty result is a capacity artifact, not a recall defect.
        quality_ok = hit_count >= 1 or degraded
    ctx.note(
        hit_count=hit_count,
        real_recall=real_recall,
        quality_ok=quality_ok,
        degraded=degraded,
    )
    return resp


def open_session(ctx: Ctx, *, title: str = "perf-write-tx") -> Response:
    """POST /api/sessions/open，返回响应（session_id 见 :func:`session_id`）。"""
    return ctx.post(
        "/api/sessions/open",
        body={
            "agent_id": str(ctx.params.get("agent_id", "default")),
            "title": title,
            "metadata": {
                "title": title,
                "account_id": str(ctx.params.get("account_id", "default")),
                "user_id": str(ctx.params.get("user_id", "default")),
            },
        },
        op="open",
    )


def add_message(ctx: Ctx, session_id: str, content: str) -> Response:
    """POST /api/sessions/{sid}/messages 并记录内容指纹。"""
    resp = ctx.post(
        f"/api/sessions/{session_id}/messages",
        body={"role": "user", "content": content},
        op="add",
        session_id=session_id,
        content_hash=content_hash(content),
        content_bytes=len(content.encode("utf-8")),
    )
    if resp.ok:
        ctx.note(message_id=message_id(resp.json))
    return resp


def commit_session(ctx: Ctx, session_id: str) -> Response:
    """POST /api/sessions/{sid}/commit，返回响应（archive_id 见 :func:`archive_id`）。"""
    resp = ctx.post(
        f"/api/sessions/{session_id}/commit",
        body={"metadata": {"keep_recent_count": 0}},
        op="commit_submit",
        session_id=session_id,
    )
    if resp.ok:
        ctx.note(archive_id=archive_id(resp.json))
    return resp


def poll_commit(
    ctx: Ctx,
    session_id: str,
    archive_id: str,
    *,
    timeout_s: float | None = None,
    interval_s: float = 0.2,
) -> PollResult:
    """GET /api/sessions/{sid}/commits/{aid} 轮询到 completed/failed/timeout。"""
    return ctx.poll(
        f"/api/sessions/{session_id}/commits/{archive_id}",
        op="commit_done",
        interval_s=interval_s,
        timeout_s=(
            timeout_s
            if timeout_s is not None
            else float(ctx.params.get("commit_poll_timeout_s", 600))
        ),
        session_id=session_id,
        archive_id=archive_id,
    )


# --------------------------------------------------------------------- #
#  标准任务：端点组合成的读/写负载路径                                   #
# --------------------------------------------------------------------- #

def task_read(ctx: Ctx) -> None:
    """一次测量式检索（场景 A 读路径）：``search`` 取 query 池下一条。"""
    query = ctx.choose(ctx.params.get("queries") or DEFAULT_QUERIES)
    search(ctx, query, top_k=int(ctx.params.get("top_k", 5)))


def task_write(ctx: Ctx) -> None:
    """一个完整注入事务（场景 B 写路径）。

    对齐 ``loadgen.run_write_transaction``：open -> add×N（末条携带
    PERFTAIL anchor）-> commit submit -> commit done（poll 到 completed，
    默认 600s 超时）。四阶段独立计时记录；失败阶段即中止事务；commit
    提交默认不重试（与 ``--commit-retry-max 0`` 一致）。
    """
    messages = int(ctx.params.get("messages_per_session", 10))
    anchor = f"{WRITE_ANCHOR_PREFIX}-{ctx.tenant_idx}-{ctx.next_seq()}"

    open_resp = open_session(ctx)
    if not open_resp.ok:
        return
    sid = session_id(open_resp.json)
    if not sid:
        return

    for msg_idx in range(messages):
        last = msg_idx == messages - 1
        content = (
            f"压测写入会话消息 {anchor}-{msg_idx}"
            if last
            else f"压测写入会话消息-{msg_idx}"
        )
        if not add_message(ctx, sid, content).ok:
            return

    commit_resp = commit_session(ctx, sid)
    if not commit_resp.ok:
        return
    aid = archive_id(commit_resp.json)
    if not aid:
        return

    poll_commit(ctx, sid, aid)


# --------------------------------------------------------------------- #
#  响应解析辅助                                                          #
# --------------------------------------------------------------------- #

def session_id(body: dict[str, Any] | None) -> str:
    if not isinstance(body, dict):
        return ""
    sid = body.get("session_id") or body.get("id") or ""
    if not sid:
        scope = body.get("scope")
        if isinstance(scope, dict):
            sid = scope.get("session_id") or ""
    return str(sid)


def message_id(body: dict[str, Any] | None) -> str:
    if not isinstance(body, dict):
        return ""
    return str(body.get("message_id") or body.get("id") or body.get("msg_id") or "")


def archive_id(body: dict[str, Any] | None) -> str:
    if not isinstance(body, dict):
        return ""
    aid = body.get("archive_id") or body.get("task_id") or ""
    if not aid:
        result = body.get("result")
        if isinstance(result, dict):
            aid = result.get("archive_id") or result.get("task_id") or ""
    if not aid:
        aid = body.get("id", "")
    return str(aid)

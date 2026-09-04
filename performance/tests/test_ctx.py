"""Unit tests for the scene API (Ctx / Response / PollResult)."""

from __future__ import annotations

import threading

import pytest

from performance.ctx import AssertionFailure, Ctx, Phase
from performance.tests.conftest import MockState


def make_ctx(
    base_url: str,
    *,
    params: dict | None = None,
    extra: str = "",
    stop: threading.Event | None = None,
) -> tuple[Ctx, list, list]:
    records: list = []
    seq_values: list = []
    phases: list = []

    def record_fn(record) -> None:
        records.append(record)

    def seq_fn() -> int:
        value = len(seq_values)
        seq_values.append(value)
        return value

    cursor = {"index": 0}

    def choose_fn(items) -> object:
        if not items:
            return None
        index = cursor["index"] % len(items)
        cursor["index"] += 1
        return items[index]

    ctx = Ctx(
        scene="test",
        worker_id=3,
        tenant_idx=1,
        headers={"X-Auth-Key": "k"},
        base_url=base_url,
        read_timeout_s=5.0,
        params=params or {},
        duration_s=60.0,
        stop=stop if stop is not None else threading.Event(),
        record_fn=record_fn,
        seq_fn=seq_fn,
        choose_fn=choose_fn,
        phases=phases,
        extra=extra,
    )
    return ctx, records, phases


def test_request_ok(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url)
    resp = ctx.post("/api/retrieval/search", body={"query": "q"}, op="read", query="q")
    assert resp.ok
    assert resp.http_status == 200
    assert resp.json["result"]["items"]
    assert resp.record.status == "ok"
    assert resp.record.op == "read"
    assert resp.record.worker_id == 3
    assert resp.record.tenant_idx == 1
    assert resp.record.query == "q"
    assert resp.record.http_status == 200
    assert resp.record.stage_ms >= 0
    assert len(records) == 1
    assert records[0] is resp.record


def test_request_http_5xx(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url)
    resp = ctx.post("/api/fail500", op="boom")
    assert not resp.ok
    assert resp.http_status == 500
    assert resp.status == "error"
    assert resp.record.error_type == "http_5xx"
    assert resp.record.status == "error"
    assert resp.record.op == "boom"
    assert len(records) == 1


def test_request_http_4xx(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url)
    resp = ctx.post("/api/fail404", op="boom")
    assert resp.status == "error"
    assert resp.record.error_type == "http_4xx"
    assert resp.record.http_status == 404


def test_request_default_op_from_path(server):
    _, _, base_url = server
    ctx, _, _ = make_ctx(base_url)
    resp = ctx.post("/api/sessions/open")
    assert resp.op == "open"


def test_request_connection_error(mock_server):
    ctx, records, _ = make_ctx("http://127.0.0.1:1")
    resp = ctx.post("/api/x", op="conn")
    assert resp.status == "error"
    assert resp.record.error_type == "connection"
    assert resp.http_status is None
    assert len(records) == 1


def test_request_timeout(mock_server):
    httpd, state, base_url = mock_server(MockState(delay_s=0.5))
    ctx, records, _ = make_ctx(base_url)
    resp = ctx.post("/api/retrieval/search", op="slow", timeout_s=0.1)
    assert resp.status == "error"
    assert resp.record.error_type == "timeout"
    assert resp.http_status is None


def test_keepalive_reuses_connection(server):
    httpd, state, base_url = server
    ctx, records, _ = make_ctx(base_url)
    for _ in range(50):
        resp = ctx.post("/api/retrieval/search", body={"query": "q"}, op="read")
        assert resp.ok
    # One thread, one persistent connection: the mock must not see a new
    # TCP connection per request (that would skew latency and exhaust
    # client sockets under load).
    assert state.connections <= 2


def test_note_updates_last_record(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url)
    resp = ctx.post("/api/retrieval/search", body={"query": "q"}, op="read", query="q")
    ctx.note(hit_count=2, quality_ok=False)
    assert resp.record.hit_count == 2
    assert resp.record.quality_ok is False
    assert records[0].hit_count == 2


def test_require_status(server):
    _, _, base_url = server
    ctx, _, _ = make_ctx(base_url)
    resp = ctx.post("/api/retrieval/search", op="read")
    assert resp.require_status(200) is resp
    with pytest.raises(AssertionFailure):
        ctx.post("/api/fail404", op="x").require_status(200)


def test_poll_completed(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url)
    result = ctx.poll("/api/sessions/s1/commits/a1", op="commit_done",
                      timeout_s=5, interval_s=0.02)
    assert result.status == "completed"
    assert result.polls >= 2  # pending -> pending -> completed
    assert result.record.status == "ok"
    assert result.record.error_type == ""
    assert result.record.op == "commit_done"
    assert len(records) == 1


def test_poll_timeout(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url)
    result = ctx.poll("/api/sessions/s1/commits/a1", op="commit_done",
                      timeout_s=0.05, interval_s=0.01,
                      until=lambda body: False)
    assert result.status == "timeout"
    assert result.record.status == "error"
    assert result.record.error_type == "commit_timeout"


def test_poll_failed(mock_server):
    httpd, state, base_url = mock_server(MockState(poll_fail_after=1))
    ctx, records, _ = make_ctx(base_url)
    result = ctx.poll("/api/sessions/s1/commits/a1", op="commit_done",
                      timeout_s=5, interval_s=0.02)
    assert result.status == "failed"
    assert result.record.status == "error"
    assert result.record.error_type == "commit_failed"


def test_poll_stopped(server):
    _, _, base_url = server
    stop = threading.Event()
    stop.set()
    ctx, records, _ = make_ctx(base_url, stop=stop)
    result = ctx.poll("/api/sessions/s1/commits/a1", op="commit_done",
                      timeout_s=5, interval_s=100)
    assert result.status == "stopped"
    assert result.record is None
    assert records == []


def test_choose_round_robin(server):
    _, _, base_url = server
    ctx, _, _ = make_ctx(base_url)
    assert ctx.choose(["a", "b", "c"]) == "a"
    assert ctx.choose(["a", "b", "c"]) == "b"
    assert ctx.choose(["a", "b", "c"]) == "c"
    assert ctx.choose(["a", "b", "c"]) == "a"


def test_next_seq(server):
    _, _, base_url = server
    ctx, _, _ = make_ctx(base_url)
    assert ctx.next_seq() == 0
    assert ctx.next_seq() == 1


def test_at_time_and_at_ratio(server):
    _, _, base_url = server
    ctx, _, phases = make_ctx(base_url)

    def fn(ctx):
        pass

    ctx.at_time(5.0, fn, count=3, max_workers=2, name="burst")
    ctx.at_ratio(0.5, fn, count=1, max_workers=1, name="mid")
    assert len(phases) == 2
    assert phases[0].at_s == 5.0
    assert phases[0].count == 3
    assert phases[0].max_workers == 2
    assert phases[0].name == "burst"
    assert phases[1].at_s == 30.0  # 0.5 * duration_s(60)
    assert phases[1].name == "mid"


def test_at_time_invalid(server):
    _, _, base_url = server
    ctx, _, _ = make_ctx(base_url)

    def fn(ctx):
        pass

    with pytest.raises(ValueError):
        ctx.at_time(-1, fn)


def test_extra_default_and_override(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url, extra="burst")
    ctx.post("/api/retrieval/search", op="read")
    assert records[0].extra == "burst"
    ctx.post("/api/retrieval/search", op="read", extra="")
    assert records[1].extra == ""


def test_manual_record_and_note(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url)
    ctx.record(op="txn", stage_ms=1.5, status="ok", session_id="s1")
    ctx.note(archive_id="a1")
    assert len(records) == 1
    assert records[0].op == "txn"
    assert records[0].archive_id == "a1"

"""Engine-level tests: worker split, mix, burst phases, recording."""

from __future__ import annotations

from pathlib import Path

import pytest

from performance.engine import Engine, SceneError, SceneModule, load_scene, split_workers
from performance.profile import Profile, TargetSpec, load_profile
from performance.targets.echomem.protocol import task_read


def _scene(*, tasks: dict | None = None, task=None, schedule=None, name="test") -> SceneModule:
    if tasks is None:
        tasks = {"main": task} if task is not None else {"main": lambda ctx: None}
    return SceneModule(name=name, description="", tasks=tasks, schedule=schedule)


def _profile(base_url: str, **load_changes) -> Profile:
    profile = load_profile(
        {
            "name": "p",
            "target": {"base_url": base_url, "read_timeout_s": 5},
            "load": {"workers": 2, "duration_s": 1.0, **load_changes},
            "params": {"queries": ["q1", "q2"]},
        }
    )
    return profile


# -- split_workers ------------------------------------------------------


def test_split_equal():
    assert split_workers(2, {"read": 1, "write": 1}) == {"read": 1, "write": 1}


def test_split_8_to_1():
    assert split_workers(9, {"read": 8, "write": 1}) == {"read": 8, "write": 1}


def test_split_round_read_first():
    # round(10 * 8/9) = 9; write gets the remainder 1
    assert split_workers(10, {"read": 8, "write": 1}) == {"read": 9, "write": 1}


def test_split_single_task():
    assert split_workers(4, {"read": 1}) == {"read": 4}


def test_split_zero_total():
    with pytest.raises(ValueError):
        split_workers(2, {"read": 0})


# -- basic run ----------------------------------------------------------


def test_single_task_run(server):
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", body={"query": ctx.choose(ctx.params["queries"])},
                 op="read")

    profile = _profile(base_url)
    result = Engine(profile, _scene(task=read)).run()
    ops = {r.op for r in result.records}
    assert "read" in ops
    assert result.records
    assert all(r.status == "ok" for r in result.records)
    assert result.elapsed_s > 0


def test_search_request_carries_agent_id(server):
    """回归：/api/retrieval/search 必须携带非空 agent_id（EchoMem require_text 校验）。"""
    httpd, state, base_url = server
    profile = _profile(base_url, workers=2, duration_s=1.5, mix={"read": 1})
    result = Engine(profile, _scene(tasks={"read": task_read})).run()
    assert result.records
    assert state.search_agent_ids, "no search request reached the server"
    assert all(aid == "default" for aid in state.search_agent_ids)


def test_worker_ids_and_tenant_binding(server):
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", op="read")

    profile = _profile(base_url, workers=3, duration_s=2.0)
    result = Engine(profile, _scene(task=read)).run()
    worker_ids = {r.worker_id for r in result.records}
    assert worker_ids == {0, 1, 2}


def test_mix_runs_both_tasks(server):
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", op="read")

    def write(ctx):
        ctx.post("/api/sessions/open", op="open")

    tasks = {"read": read, "write": write}
    profile = _profile(base_url, workers=3, duration_s=2.0, mix={"read": 2, "write": 1})
    result = Engine(profile, _scene(tasks=tasks)).run()
    ops = {r.op for r in result.records}
    assert "read" in ops
    assert "open" in ops
    read_workers = {r.worker_id for r in result.records if r.op == "read"}
    write_workers = {r.worker_id for r in result.records if r.op == "open"}
    assert len(read_workers) == 2
    assert len(write_workers) == 1
    assert read_workers.isdisjoint(write_workers)


def test_mix_unknown_task_rejected(server):
    _, _, base_url = server
    profile = _profile(base_url, mix={"read": 1, "bogus": 1})
    with pytest.raises(SceneError, match="unknown tasks"):
        Engine(profile, _scene(task=lambda ctx: None)).run()


def test_burst_phase(server):
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", op="read")

    def burst_job(ctx):
        ctx.post("/api/sessions/open", op="open")

    def schedule(ctx):
        ctx.at_time(0.1, burst_job, count=3, max_workers=2, name="burst")

    profile = _profile(base_url, workers=2, duration_s=2.0)
    result = Engine(profile, _scene(task=read, schedule=schedule)).run()
    burst_records = [r for r in result.records if r.extra == "burst"]
    assert len(burst_records) == 3
    assert all(r.op == "open" for r in burst_records)
    assert all(r.worker_id == -1 for r in burst_records)
    read_records = [r for r in result.records if r.op == "read"]
    assert read_records


def test_phase_tenant_counts_distributes_across_tenants(server):
    _, _, base_url = server
    profile = load_profile(
        {
            "name": "p",
            "target": {"base_url": base_url, "read_timeout_s": 5},
            "load": {"workers": 1, "duration_s": 1.0},
            "tenants": [{"name": "t0"}, {"name": "t1"}, {"name": "t2"}],
        }
    )

    def read(ctx):
        ctx.post("/api/retrieval/search", op="read")

    def job(ctx):
        ctx.post("/api/sessions/open", op="open")

    def schedule(ctx):
        ctx.at_time(0.1, job, tenant_counts={0: 2, 1: 1, 2: 1}, max_workers=4, name="barrier")

    result = Engine(profile, _scene(task=read, schedule=schedule)).run()
    burst = [r for r in result.records if r.extra == "barrier"]
    assert len(burst) == 4
    assert all(r.worker_id == -1 for r in burst)
    assert sorted(r.tenant_idx for r in burst) == [0, 0, 1, 2]


def test_burst_phase_jobs_keep_own_response_ids():
    """并发 phase job 各持独立 Ctx：record() 后的 note() 只补写本 job 记录。

    两个 job 的 record 与 note 之间用 Event 固定交错；旧实现共享 Ctx 时，
    前一个响应的 message_id/archive_id 会落到后一个请求记录上。
    """
    import itertools
    import threading

    counter = itertools.count()
    lock = threading.Lock()
    reached = threading.Event()
    order = {"n": 0}

    def main(ctx):
        pass

    def burst_job(ctx):
        i = next(counter)
        ctx.record(op="open", stage_ms=1.0, status="ok", session_id=f"s{i}")
        with lock:
            order["n"] += 1
            if order["n"] == 2:
                reached.set()
        reached.wait(3.0)
        ctx.note(message_id=f"m{i}", archive_id=f"a{i}")

    def schedule(ctx):
        ctx.at_time(0.0, burst_job, count=2, max_workers=2, name="burst")

    profile = load_profile(
        {
            "name": "p",
            "target": {"base_url": "http://127.0.0.1:8010", "read_timeout_s": 5},
            "load": {"workers": 1, "duration_s": 0.5},
        }
    )
    result = Engine(profile, _scene(task=main, schedule=schedule)).run()
    burst = [r for r in result.records if r.extra == "burst"]
    assert len(burst) == 2
    for record in burst:
        idx = record.session_id[1:]  # "s0" -> "0"
        assert record.message_id == f"m{idx}"
        assert record.archive_id == f"a{idx}"


def test_at_time_rejects_empty_tenant_counts(server):
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", op="read")

    def schedule(ctx):
        ctx.at_time(0.1, read, tenant_counts={})

    profile = _profile(base_url, duration_s=0.3)
    result = Engine(profile, _scene(task=read, schedule=schedule)).run()
    assert any(r.op == "transaction" and "tenant_counts" in r.detail
               for r in result.records)


def test_worker_exception_recorded(server):
    _, _, base_url = server

    def boom(ctx):
        raise RuntimeError("kaboom")

    profile = _profile(base_url, duration_s=1.5)
    result = Engine(profile, _scene(task=boom)).run()
    txn_errors = [r for r in result.records if r.op == "transaction"]
    assert txn_errors
    assert all(r.status == "error" for r in txn_errors)
    assert any("kaboom" in r.detail for r in txn_errors)


def test_fixed_rps_gate_limits_rate(server):
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", op="read")

    profile = load_profile(
        {
            "name": "p",
            "target": {"base_url": base_url, "read_timeout_s": 5},
            "load": {
                "workers": 1,
                "duration_s": 1.5,
                "arrival": {"main": {"model": "fixed_rps", "rps": 5}},
            },
        }
    )
    result = Engine(profile, _scene(task=read)).run()
    reads = [r for r in result.records if r.op == "read"]
    assert 3 <= len(reads) <= 8  # ~5 rps over ~1s, tolerant of scheduling
    assert all(r.status == "ok" for r in reads)


def test_schedule_exception_recorded(server):
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", op="read")

    def bad_schedule(ctx):
        raise RuntimeError("schedule boom")

    profile = _profile(base_url, duration_s=0.3)
    result = Engine(profile, _scene(task=read, schedule=bad_schedule)).run()
    assert any(r.op == "transaction" and "schedule boom" in r.detail for r in result.records)


# -- load_scene ---------------------------------------------------------


def test_load_scene_task(tmp_path):
    path = tmp_path / "my_scene.py"
    path.write_text(
        "def task(ctx):\n    pass\n",
        encoding="utf-8",
    )
    scene = load_scene(path)
    assert scene.name == "my_scene"
    assert list(scene.tasks) == ["main"]


def test_load_scene_tasks_and_schedule(tmp_path):
    path = tmp_path / "my_scene.py"
    path.write_text(
        "def task_read(ctx):\n    pass\n\n"
        "def task_write(ctx):\n    pass\n\n"
        "tasks = {'read': task_read, 'write': task_write}\n\n"
        "def schedule(ctx):\n    pass\n",
        encoding="utf-8",
    )
    scene = load_scene(path)
    assert list(scene.tasks) == ["read", "write"]
    assert scene.schedule is not None


def test_load_scene_missing_contract(tmp_path):
    path = tmp_path / "bad_scene.py"
    path.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(SceneError, match="task"):
        load_scene(path)


def test_load_scene_not_found(tmp_path):
    with pytest.raises(SceneError, match="not found"):
        load_scene(tmp_path / "nope.py")

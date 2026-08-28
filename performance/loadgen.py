"""Concurrent read/write load generation with per-request instrumentation.

Every request produces a :class:`RequestRecord`; nothing is swallowed.
Clients used here are always built with ``max_retries=0`` so error rates
and latency distributions are not masked by client-side retries, and
timeouts are classified separately. Write transactions are a complete
injection flow (open -> add xN -> commit submit -> commit done via poll)
with the four stages timed individually.
"""

from __future__ import annotations

import itertools
import logging
import socket
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any

from backends.echomem.client import EchoMemClient
from performance.prepare import TenantContext, WRITE_ANCHOR_PREFIX
from performance.scenarios import SceneRun

logger = logging.getLogger("performance.loadgen")


@dataclass
class RequestRecord:
    """One measured operation. ``ts_ms`` is time.time()*1000 at completion."""

    scene_key: str
    step_conc: int
    tenant_idx: int
    op: str
    stage_ms: float
    status: str  # ok | error
    error_type: str  # "" | timeout | http_4xx | http_5xx | connection | other
    ts_ms: float
    session_id: str = ""
    extra: str = ""  # e.g. "burst"

    def to_csv_row(self) -> dict[str, Any]:
        return {
            "scene": self.scene_key,
            "step_conc": self.step_conc,
            "tenant_idx": self.tenant_idx,
            "op": self.op,
            "stage_ms": round(self.stage_ms, 3),
            "status": self.status,
            "error_type": self.error_type,
            "ts_ms": round(self.ts_ms, 3),
            "session_id": self.session_id,
            "extra": self.extra,
        }


def classify_error(exc: BaseException) -> str:
    """Map an exception to a coarse error bucket for reporting."""
    if isinstance(exc, TimeoutError) or isinstance(exc, socket.timeout):
        return "timeout"
    if isinstance(exc, urllib.error.HTTPError):
        return "http_4xx" if exc.code < 500 else "http_5xx"
    if isinstance(exc, urllib.error.URLError):
        return "connection"
    return "other"


def mix_token_sequence(read: int, write: int, total: int) -> list[str]:
    """Deterministic read/write token sequence honoring the ratio."""
    if read + write == 0:
        raise ValueError("read:write ratio must not be 0:0")
    sequence: list[str] = []
    while len(sequence) < total:
        sequence.extend(["read"] * read + ["write"] * write)
    return sequence[:total]


def split_threads(total: int, ratio: tuple[int, int]) -> tuple[int, int]:
    """Split worker threads by a read:write ratio (read first, rest write)."""
    read, write = ratio
    if read + write == 0:
        raise ValueError("read:write ratio must not be 0:0")
    read_threads = round(total * read / (read + write))
    return read_threads, total - read_threads


@dataclass
class WriteTransactionResult:
    ok: bool
    session_id: str
    anchor: str
    records: list[RequestRecord] = field(default_factory=list)


def run_write_transaction(
    client: EchoMemClient,
    *,
    scene_key: str,
    step_conc: int,
    tenant_idx: int,
    seq: int,
    messages_per_session: int,
    commit_poll_timeout_s: float,
    commit_poll_interval_s: float = 0.2,
    extra: str = "",
    seed_anchor: str = "",
) -> WriteTransactionResult:
    """One full injection transaction with per-stage timing.

    The final message carries the transaction anchor; when an anchor is
    supplied for consistency probing the content embeds it.
    """
    records: list[RequestRecord] = []
    result = WriteTransactionResult(ok=False, session_id="", anchor="")
    result.records = records  # same list; all failure paths return with records attached
    anchor = seed_anchor or f"{WRITE_ANCHOR_PREFIX}-{tenant_idx}-{seq}"

    def record(op: str, ms: float, status: str, error_type: str = "") -> None:
        records.append(
            RequestRecord(
                scene_key=scene_key,
                step_conc=step_conc,
                tenant_idx=tenant_idx,
                op=op,
                stage_ms=ms,
                status=status,
                error_type=error_type,
                ts_ms=time.time() * 1000,
                session_id=result.session_id,
                extra=extra,
            )
        )

    started = time.perf_counter()
    try:
        result.session_id = client.open_session(title="perf-write-tx")
    except Exception as exc:
        record("open", (time.perf_counter() - started) * 1000, "error", classify_error(exc))
        return result
    record("open", (time.perf_counter() - started) * 1000, "ok")

    for msg_idx in range(messages_per_session):
        last = msg_idx == messages_per_session - 1
        content = f"压测写入会话消息 {anchor}" if last else "压测写入会话消息"
        started = time.perf_counter()
        try:
            client.add_message(result.session_id, "user", content)
        except Exception as exc:
            record("add", (time.perf_counter() - started) * 1000, "error", classify_error(exc))
            return result
        record("add", (time.perf_counter() - started) * 1000, "ok")

    started = time.perf_counter()
    try:
        archive_id = client.commit_session(result.session_id)
    except Exception as exc:
        record("commit_submit", (time.perf_counter() - started) * 1000, "error", classify_error(exc))
        return result
    record("commit_submit", (time.perf_counter() - started) * 1000, "ok")

    started = time.perf_counter()
    try:
        commit = client.poll_commit(
            result.session_id,
            archive_id,
            timeout_s=commit_poll_timeout_s,
            poll_interval_s=commit_poll_interval_s,
        )
    except Exception as exc:
        record("commit_done", (time.perf_counter() - started) * 1000, "error", classify_error(exc))
        return result
    if commit.status == "completed":
        record("commit_done", commit.elapsed_s * 1000, "ok")
        result.ok = True
    elif commit.status == "timeout":
        record("commit_done", commit.elapsed_s * 1000, "error", "commit_timeout")
    else:
        record("commit_done", commit.elapsed_s * 1000, "error", "commit_failed")
    result.anchor = anchor
    result.records = records
    return result


class RateLimiter:
    """Minimal thread-safe fixed-rate gate (used for read ops only)."""

    def __init__(self, rps: float) -> None:
        if rps <= 0:
            raise ValueError("rps must be positive")
        self._rps = rps
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + 1.0 / self._rps
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)


@dataclass
class SceneResult:
    scene_key: str
    records: list[RequestRecord]
    wall_s: float
    burst_start_s: float | None = None
    burst_end_s: float | None = None


AnchorWrite = tuple[int, str, str]  # (tenant_idx, session_id, anchor)


class LoadGenerator:
    """Executes one :class:`SceneRun` at a time over prepared tenants."""

    def __init__(
        self,
        *,
        top_k: int = 5,
        timeout_s: float = 10.0,
        commit_poll_timeout_s: float = 120.0,
        commit_poll_interval_s: float = 0.2,
        rps: float | None = None,
    ) -> None:
        self.top_k = top_k
        self.timeout_s = timeout_s
        self.commit_poll_timeout_s = commit_poll_timeout_s
        self.commit_poll_interval_s = commit_poll_interval_s
        self.rate_limiter = RateLimiter(rps) if rps else None
        self._last_write_anchors: list[AnchorWrite] = []

    # -- single operations ------------------------------------------------

    def _read_once(
        self,
        client: EchoMemClient,
        query: str,
        *,
        scene_key: str,
        step_conc: int,
        tenant_idx: int,
    ) -> RequestRecord:
        started = time.perf_counter()
        try:
            client.search(query, top_k=self.top_k, agent_id="", timeout_s=self.timeout_s)
            status, error = "ok", ""
        except Exception as exc:
            status, error = "error", classify_error(exc)
        return RequestRecord(
            scene_key=scene_key,
            step_conc=step_conc,
            tenant_idx=tenant_idx,
            op="read",
            stage_ms=(time.perf_counter() - started) * 1000,
            status=status,
            error_type=error,
            ts_ms=time.time() * 1000,
        )

    # -- worker loops ------------------------------------------------------

    def _read_loop(
        self,
        stop: threading.Event,
        tenant: TenantContext,
        *,
        scene_key: str,
        step_conc: int,
    ) -> list[RequestRecord]:
        records: list[RequestRecord] = []
        queries = tenant.queries or ["hello"]
        cursor = 0
        while not stop.is_set():
            if self.rate_limiter is not None:
                self.rate_limiter.acquire()
            query = queries[cursor % len(queries)]
            cursor += 1
            records.append(
                self._read_once(
                    tenant.client,
                    query,
                    scene_key=scene_key,
                    step_conc=step_conc,
                    tenant_idx=tenant.idx,
                )
            )
        return records

    def _write_loop(
        self,
        stop: threading.Event,
        tenant: TenantContext,
        seq_counter: itertools.count,
        *,
        scene_key: str,
        step_conc: int,
        messages_per_session: int,
    ) -> list[RequestRecord]:
        records: list[RequestRecord] = []
        anchors: list[AnchorWrite] = []
        while not stop.is_set():
            seq = next(seq_counter)
            result = run_write_transaction(
                tenant.client,
                scene_key=scene_key,
                step_conc=step_conc,
                tenant_idx=tenant.idx,
                seq=seq,
                messages_per_session=messages_per_session,
                commit_poll_timeout_s=self.commit_poll_timeout_s,
                commit_poll_interval_s=self.commit_poll_interval_s,
            )
            records.extend(result.records)
            if result.ok:
                anchors.append((tenant.idx, result.session_id, result.anchor))
        self._last_write_anchors.extend(anchors)
        return records

    # -- scene runner ------------------------------------------------------

    def run_scene(
        self,
        scene: SceneRun,
        tenants: list[TenantContext],
        messages_per_session: int,
    ) -> SceneResult:
        """Run one scene for its duration and return all records."""
        scene_key = scene.key
        total_workers = len(tenants) * scene.per_tenant_conc
        stop = threading.Event()
        started_wall = time.time()
        burst_start: float | None = None
        burst_end: float | None = None

        def tenant_for(index: int) -> TenantContext:
            return tenants[index % len(tenants)]

        if scene.scene_id in ("A", "D"):
            read_count, write_count = total_workers, 0
        elif scene.scene_id == "B":
            read_count, write_count = 0, total_workers
        else:  # C
            read_count, write_count = split_threads(total_workers, scene.mix or (1, 1))
        self._last_write_anchors.clear()

        futures: list[Any] = []
        with ThreadPoolExecutor(
            max_workers=total_workers, thread_name_prefix="perf-load"
        ) as pool:
            seq_counter = itertools.count()
            for index in range(read_count):
                tenant = tenant_for(index)
                futures.append(
                    pool.submit(
                        self._read_loop,
                        stop,
                        tenant,
                        scene_key=scene_key,
                        step_conc=scene.per_tenant_conc,
                    )
                )
            for index in range(read_count, read_count + write_count):
                tenant = tenant_for(index)
                futures.append(
                    pool.submit(
                        self._write_loop,
                        stop,
                        tenant,
                        seq_counter,
                        scene_key=scene_key,
                        step_conc=scene.per_tenant_conc,
                        messages_per_session=messages_per_session,
                    )
                )

            if scene.scene_id == "D":
                delay = max(0.0, scene.duration_s - scene.burst_window_s) / 2.0
                time.sleep(delay)
                burst_start = time.time()
                burst_records = self._run_burst(scene, tenants, messages_per_session)
                burst_end = time.time()
                remaining = started_wall + scene.duration_s - time.time()
                if remaining > 0:
                    time.sleep(remaining)
            else:
                time.sleep(scene.duration_s)

            stop.set()
            wait(futures)

        records: list[RequestRecord] = []
        for future in futures:
            records.extend(future.result())
        if scene.scene_id == "D":
            records.extend(burst_records)  # type: ignore[name-defined]
        return SceneResult(
            scene_key=scene_key,
            records=records,
            wall_s=time.time() - started_wall,
            burst_start_s=burst_start,
            burst_end_s=burst_end,
        )

    def _run_burst(
        self,
        scene: SceneRun,
        tenants: list[TenantContext],
        messages_per_session: int,
    ) -> list[RequestRecord]:
        """Saturate the server with K parallel write transactions (scene D)."""
        count = scene.burst_commits
        step_conc = scene.per_tenant_conc
        records: list[RequestRecord] = []
        seq_counter = itertools.count()
        with ThreadPoolExecutor(
            max_workers=min(count, 8), thread_name_prefix="perf-burst"
        ) as pool:
            futures = [
                pool.submit(
                    run_write_transaction,
                    tenants[i % len(tenants)].client,
                    scene_key=scene.key,
                    step_conc=step_conc,
                    tenant_idx=tenants[i % len(tenants)].idx,
                    seq=next(seq_counter),
                    messages_per_session=messages_per_session,
                    commit_poll_timeout_s=self.commit_poll_timeout_s,
                    commit_poll_interval_s=self.commit_poll_interval_s,
                    extra="burst",
                )
                for i in range(count)
            ]
            for future in futures:
                result = future.result()
                records.extend(result.records)
        return records

    # -- write-read consistency probing -------------------------------------

    def run_consistency_checks(
        self,
        tenants: list[TenantContext],
        *,
        scene_key: str,
        step_conc: int,
        max_checks: int = 3,
        max_wait_s: float = 30.0,
    ) -> list[RequestRecord]:
        """Probe how long committed content takes to become searchable.

        Uses the anchors of the most recently completed write transactions.
        """
        records: list[RequestRecord] = []
        for tenant_idx, session_id, anchor in self._last_write_anchors[-max_checks:]:
            client = tenants[tenant_idx].client
            started = time.perf_counter()
            deadline = time.monotonic() + max_wait_s
            hit = False
            while time.monotonic() < deadline:
                try:
                    items = client.search(anchor, top_k=self.top_k, timeout_s=self.timeout_s)
                except Exception as exc:
                    records.append(
                        RequestRecord(
                            scene_key=scene_key,
                            step_conc=step_conc,
                            tenant_idx=tenant_idx,
                            op="consistent_check",
                            stage_ms=(time.perf_counter() - started) * 1000,
                            status="error",
                            error_type=classify_error(exc),
                            ts_ms=time.time() * 1000,
                            session_id=session_id,
                        )
                    )
                    break
                if any(
                    anchor in (item.content or "") or anchor in (item.uri or "")
                    for item in items
                ):
                    hit = True
                    break
                time.sleep(0.5)
            if hit:
                records.append(
                    RequestRecord(
                        scene_key=scene_key,
                        step_conc=step_conc,
                        tenant_idx=tenant_idx,
                        op="consistent_check",
                        stage_ms=(time.perf_counter() - started) * 1000,
                        status="ok",
                        error_type="",
                        ts_ms=time.time() * 1000,
                        session_id=session_id,
                    )
                )
            else:
                records.append(
                    RequestRecord(
                        scene_key=scene_key,
                        step_conc=step_conc,
                        tenant_idx=tenant_idx,
                        op="consistent_check",
                        stage_ms=(time.perf_counter() - started) * 1000,
                        status="error",
                        error_type="consistency_timeout",
                        ts_ms=time.time() * 1000,
                        session_id=session_id,
                    )
                )
        return records
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from echomemory_common import (
    DEFAULT_ECHOMEM_ROOT,
    echomem_account_roots,
    ensure_echomem_imports,
    open_echomem_sdk,
    write_echomem_config,
    write_json,
)
from echomemory_locomo_import import (
    count_memory_artifacts,
    extraction_cursor,
    flush_atom_pipeline,
    read_json_file,
    read_jsonl_file,
    reset_extraction_cursor,
    wait_for_commit_artifacts,
)


def session_roots(workspace: str, account: str) -> list[Path]:
    roots: list[Path] = []
    for account_root in echomem_account_roots(workspace, account):
        roots.append(account_root / "sessions")
    roots.append(Path(workspace).expanduser().resolve() / "sessions")
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in roots:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def iter_session_dirs(workspace: str, account: str) -> list[Path]:
    seen: set[Path] = set()
    rows: list[Path] = []
    for root in session_roots(workspace, account):
        if not root.exists():
            continue
        for item in sorted(root.iterdir()):
            if not item.is_dir() or item in seen:
                continue
            seen.add(item)
            rows.append(item)
    return rows


def plugin_session_dir(session_dir: Path) -> Path:
    account_root = session_dir.parent.parent if session_dir.parent.name == "sessions" else session_dir.parent
    return account_root / "engines" / "echo0_plugin" / "sessions" / session_dir.name


def session_meta_path(session_dir: Path) -> Path:
    for candidate in (
        plugin_session_dir(session_dir) / "meta.json",
        session_dir / "meta.json",
        session_dir / "current" / "session.json",
    ):
        if candidate.exists():
            return candidate
    return plugin_session_dir(session_dir) / "meta.json"


def session_messages_path(session_dir: Path) -> Path:
    for candidate in (
        session_dir / "messages.jsonl",
        session_dir / "current" / "messages.jsonl",
        plugin_session_dir(session_dir) / "messages.jsonl",
    ):
        if candidate.exists():
            return candidate
    return session_dir / "current" / "messages.jsonl"


def session_overview_path(session_dir: Path) -> Path:
    for candidate in (
        plugin_session_dir(session_dir) / "overview.md",
        session_dir / "overview.md",
        session_dir / "current" / "overview.md",
    ):
        if candidate.exists():
            return candidate
    return plugin_session_dir(session_dir) / "overview.md"


def session_abstract_path(session_dir: Path) -> Path:
    for candidate in (
        plugin_session_dir(session_dir) / "abstract.md",
        session_dir / "abstract.md",
        session_dir / "current" / "abstract.md",
    ):
        if candidate.exists():
            return candidate
    return plugin_session_dir(session_dir) / "abstract.md"


def session_state(session_dir: Path) -> dict[str, Any]:
    meta = read_json_file(session_meta_path(session_dir))
    current_meta = read_json_file(session_dir / "current" / "session.json")
    messages = read_jsonl_file(session_messages_path(session_dir))
    expected_index = len(messages) - 1
    commit_index = int(meta.get("commit_index", -1)) if meta else -1
    atom_index = int(meta.get("atom_pipeline_index", -1)) if meta else -1
    last_message = messages[-1] if messages else {}
    last_message_id = str(last_message.get("message_id") or last_message.get("id") or "") if messages else ""
    metadata = meta.get("metadata") if isinstance(meta, dict) else {}
    current_metadata = current_meta.get("metadata") if isinstance(current_meta, dict) else {}
    title = str(
        meta.get("title")
        or (metadata.get("title") if isinstance(metadata, dict) else "")
        or current_meta.get("title")
        or (current_metadata.get("title") if isinstance(current_metadata, dict) else "")
        or session_dir.name
    )
    return {
        "session_id": session_dir.name,
        "session_dir": str(session_dir),
        "title": title,
        "message_count": len(messages),
        "last_message_id": last_message_id,
        "commit_index": commit_index,
        "atom_pipeline_index": atom_index,
        "expected_index": expected_index,
        "complete": expected_index < 0 or (commit_index >= expected_index and atom_index >= expected_index),
    }


def selected(state: dict[str, Any], sample: str, sessions: set[str]) -> bool:
    title = str(state.get("title") or "")
    session_id = str(state.get("session_id") or "")
    if sample and sample not in {"all", "*"} and not title.startswith(f"{sample}/"):
        sample_tail = title.split("/", 1)[-1] if "/" in title else title
        if sample not in {title, sample_tail, session_id} and sample not in session_id:
            return False
    if sessions and title not in sessions and session_id not in sessions:
        return False
    return True


def should_reset_stale_cursor(session_dir: Path, state: dict[str, Any], workspace: str, account: str) -> bool:
    if int(state.get("expected_index") or -1) < 0:
        return False
    if int(state.get("atom_pipeline_index") or -1) >= int(state.get("expected_index") or -1):
        return False
    meta = read_json_file(session_meta_path(session_dir))
    cursor = extraction_cursor(meta) if meta else ""
    expected_last_message_id = str(state.get("last_message_id") or "")
    if not cursor or (expected_last_message_id and cursor != expected_last_message_id):
        return False
    artifacts = count_memory_artifacts(workspace, account)
    return not bool(
        int(artifacts.get("atoms_count") or 0) > 0
        or int(artifacts.get("relations_count") or 0) > 0
        or int(artifacts.get("graph_nodes_count") or 0) > 0
        or int(artifacts.get("graph_edges_count") or 0) > 0
        or int(artifacts.get("vector_items") or 0) > 0
    )


def restore_commit_index_if_safe(session_dir: Path, state: dict[str, Any]) -> bool:
    """Recover a lost full-session commit boundary for offline eval workspaces.

    The LoCoMo importer commits each session with ``keep_recent_count=0``.
    A long-running atom pass could later overwrite ``meta.json`` with an older
    snapshot and reset ``commit_index`` back to ``-1`` even though commit side
    effects (overview/abstract/vector artifacts) already exist. When those
    artifacts are present and the session message count is intact, restoring the
    full-session commit boundary is safer than waiting forever for a commit that
    already happened.
    """
    expected_index = int(state.get("expected_index") or -1)
    commit_index = int(state.get("commit_index") or -1)
    if expected_index < 0 or commit_index >= expected_index:
        return False
    meta_path = session_meta_path(session_dir)
    if not meta_path.exists():
        return False
    overview_path = session_overview_path(session_dir)
    abstract_path = session_abstract_path(session_dir)
    if not overview_path.exists() or not overview_path.read_text(encoding="utf-8", errors="replace").strip():
        return False
    if not abstract_path.exists() or not abstract_path.read_text(encoding="utf-8", errors="replace").strip():
        return False
    messages = read_jsonl_file(session_messages_path(session_dir))
    if len(messages) - 1 != expected_index:
        return False
    meta = read_json_file(meta_path)
    if not meta:
        return False
    meta["commit_index"] = expected_index
    meta["pending_tokens"] = 0
    meta.setdefault("committed_at", meta.get("updated_at") or meta.get("created_at") or "")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return True


async def run(args: argparse.Namespace) -> None:
    root = ensure_echomem_imports(args.echomem_root)

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    config_path = Path(args.echomem_config).expanduser().resolve() if args.echomem_config else write_echomem_config(
        out_dir,
        args.account,
        args.workspace,
        root,
        args.fallback_to_mock,
    )
    sdk, runtime, _layout = await open_echomem_sdk(
        echomem_root=root,
        workspace=args.workspace,
        account=args.account,
        user_id=args.user_id,
        agent_id=args.agent_id,
        config_path=config_path,
    )
    requested_sessions = {item.strip() for item in str(args.sessions or "").split(",") if item.strip()}
    records: list[dict[str, Any]] = []
    try:
        for session_dir in iter_session_dirs(args.workspace, args.account):
            before = session_state(session_dir)
            if not selected(before, args.sample, requested_sessions):
                continue
            if before["complete"] and not args.include_complete:
                continue
            session_id = str(before["session_id"])
            print(
                f"[repair] {before['title']} session_id={session_id} "
                f"commit={before['commit_index']}/{before['expected_index']} "
                f"atom={before['atom_pipeline_index']}/{before['expected_index']}",
                flush=True,
            )
            started = time.time()
            cursor_reset = False
            if should_reset_stale_cursor(session_dir, before, args.workspace, args.account):
                cursor_reset = reset_extraction_cursor(session_dir)
                if cursor_reset:
                    print(
                        f"[repair] reset_stale_cursor session_id={session_id} "
                        f"title={before['title']}",
                        flush=True,
                    )
                    before = session_state(session_dir)
            commit_restored = restore_commit_index_if_safe(session_dir, before)
            if commit_restored:
                print(
                    f"[repair] restored_commit_index session_id={session_id} "
                    f"title={before['title']} commit={before['expected_index']}",
                    flush=True,
                )
                before = session_state(session_dir)
            atom_flush = await flush_atom_pipeline(
                args,
                sdk,
                session_id,
                expected_message_count=int(before["message_count"]),
                expected_last_message_id=str(before["last_message_id"]),
            )
            commit_artifacts = await wait_for_commit_artifacts(
                args,
                session_id,
                expected_message_count=int(before["message_count"]),
                expected_last_message_id=str(before["last_message_id"]),
            )
            after = session_state(session_dir)
            record = {
                "title": before["title"],
                "session_id": session_id,
                "before": before,
                "after": after,
                "cursor_reset": cursor_reset,
                "commit_restored": commit_restored,
                "atom_flush": atom_flush,
                "commit_artifacts": commit_artifacts,
                "elapsed_s": round(time.time() - started, 3),
                "repaired": bool(after["complete"]),
            }
            print(
                f"[repair] {before['title']} repaired={record['repaired']} "
                f"commit={after['commit_index']}/{after['expected_index']} "
                f"atom={after['atom_pipeline_index']}/{after['expected_index']}",
                flush=True,
            )
            records.append(record)
    finally:
        close = getattr(sdk, "close", None)
        if callable(close):
            await close()
        stop = getattr(runtime, "stop", None) if runtime is not None else None
        if callable(stop):
            try:
                await stop(drain_pending=True)
            except TypeError:
                await stop()

    summary = {
        "status": "ECHOMEMORY_REPAIR_DONE" if all(item["repaired"] for item in records) else "ECHOMEMORY_REPAIR_INCOMPLETE",
        "backend": "echomemory",
        "workspace": str(Path(args.workspace).expanduser().resolve()),
        "account": args.account,
        "sample": args.sample,
        "scanned_sessions": len(iter_session_dirs(args.workspace, args.account)),
        "selected_sessions": len(records),
        "repaired_sessions": sum(1 for item in records if item["repaired"]),
        "records": records,
    }
    write_json(out_dir / "echomemory_repair_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if records and summary["repaired_sessions"] != len(records):
        raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair existing EchoMemory sessions without re-adding messages.")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--echomem-root", default=str(DEFAULT_ECHOMEM_ROOT))
    parser.add_argument("--echomem-config", default="")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--sample", default="all")
    parser.add_argument("--sessions", default="", help="Comma-separated titles or session ids to repair.")
    parser.add_argument("--include-complete", action="store_true", default=False)
    parser.add_argument("--commit-wait-s", type=float, default=300.0)
    parser.add_argument("--flush-call-timeout-s", type=float, default=600.0)
    parser.add_argument("--flush-attempts", type=int, default=2)
    parser.add_argument("--fallback-to-mock", action="store_true", default=False)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

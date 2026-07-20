from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Callable, Optional


SafePath = Callable[[str], Path]
DatasetOverview = Callable[[Path], dict[str, Any]]
ActiveWriterLookup = Callable[[str], Optional[dict[str, Any]]]


def _add(checks: list[dict[str, Any]], name: str, ok: bool, message: str) -> None:
    checks.append({"name": name, "ok": ok, "message": message})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on", "enabled"}


def _normalize_path(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.expanduser().resolve())
    except Exception:
        return str(path)


def _same_path(left: Path | None, right: Path | None) -> bool:
    return bool(left and right and _normalize_path(left) == _normalize_path(right))


def _resolve_optional_path(path_text: Any, safe_path: SafePath) -> Path | None:
    text = _text(path_text)
    return safe_path(text) if text else None


def _snapshot_config_for_input(input_file: Path) -> dict[str, Any]:
    candidates = [
        input_file.parent / "config_snapshot.json",
        input_file.parent.parent / "config_snapshot.json",
    ]
    for snapshot_path in candidates:
        if not snapshot_path.exists():
            continue
        try:
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            nested = data.get("config")
            if isinstance(nested, dict):
                return nested
            return data
    return {}


def _validate_dataset(
    checks: list[dict[str, Any]],
    data: Path,
    dataset_overview: DatasetOverview,
) -> dict[str, Any] | None:
    _add(checks, "dataset", data.exists(), str(data))
    if not data.exists():
        return None
    try:
        overview = dataset_overview(data)
        _add(checks, "dataset_json", True, f"{overview['samples']} samples / {overview['questions']} questions")
        _add(checks, "dataset_runner", True, overview.get("runner_note") or "local agent ready")
        return overview
    except Exception as exc:
        _add(checks, "dataset_json", False, str(exc))
        return None


def _validate_wrong_csv(checks: list[dict[str, Any]], wrong_csv: Path | None) -> None:
    if wrong_csv is None:
        _add(checks, "wrong_csv", False, "wrong_csv 模式需要错题 CSV")
        return
    exists = wrong_csv.exists()
    _add(checks, "wrong_csv", exists, str(wrong_csv))
    if not exists:
        return
    if not wrong_csv.is_file():
        _add(checks, "wrong_csv_readable", False, "wrong_csv 不是普通文件")
        return
    try:
        with wrong_csv.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = set(reader.fieldnames or [])
            row_count = sum(1 for _ in reader)
    except Exception as exc:
        _add(checks, "wrong_csv_readable", False, str(exc))
        return
    _add(checks, "wrong_csv_readable", True, f"{row_count} rows")
    expected_columns = {"question_id", "wrong_question_id", "qid", "id"}
    present = sorted(expected_columns & fieldnames)
    _add(
        checks,
        "wrong_csv_schema",
        bool(present),
        f"id columns: {', '.join(present)}" if present else "missing question_id / wrong_question_id / qid / id",
    )


def _validate_judge_csv(checks: list[dict[str, Any]], input_file: Path, dataset_format: str = "") -> None:
    _add(checks, "judge_input", input_file.exists(), str(input_file) if str(input_file) else "missing result CSV")
    if not input_file.exists():
        return
    try:
        with input_file.open(newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            fields = set(reader.fieldnames or [])
            rows = sum(1 for _ in reader)
        required = {"question", "answer", "response"}
        missing = sorted(required - fields)
        message = f"{rows} rows; missing {', '.join(missing)}" if missing else f"{rows} rows; required columns present"
        _add(checks, "judge_csv_schema", not missing, message)
        judge_fields = {"result", "reasoning"} & fields
        _add(
            checks,
            "judge_columns",
            True,
            "existing result/reasoning columns" if judge_fields else "result/reasoning will be added before Judge",
        )
        if str(dataset_format or "").strip().lower() == "locomo":
            workbench_required = {"question_id", "category"}
            workbench_missing = sorted(workbench_required - fields)
            _add(
                checks,
                "judge_workbench_identity",
                not workbench_missing,
                f"missing {', '.join(workbench_missing)}" if workbench_missing else "question_id/category present",
            )
            workbench_advisory = {"sample_id", "injection_tokens_est"}
            advisory_missing = sorted(workbench_advisory - fields)
            _add(
                checks,
                "judge_workbench_filters",
                True,
                f"missing {', '.join(advisory_missing)}; pending workbench filters will degrade"
                if advisory_missing else
                "sample_id/injection_tokens_est present",
            )
    except Exception as exc:
        _add(checks, "judge_csv_schema", False, str(exc))


def _validate_judge_token(checks: list[dict[str, Any]], payload: dict[str, Any]) -> None:
    token = _text(payload.get("judge_token"))
    token_set = bool(token) or bool(payload.get("judgeTokenSet"))
    _add(checks, "judge_token", token_set, "judge token 已显式提供" if token_set else "未显式提供 judge token")


def _validate_source_snapshot(
    checks: list[dict[str, Any]],
    payload: dict[str, Any],
    safe_path: SafePath,
) -> None:
    input_file = _resolve_optional_path(payload.get("input"), safe_path)
    snapshot_config = _snapshot_config_for_input(input_file) if input_file is not None else {}
    data = _resolve_optional_path(payload.get("data"), safe_path)
    snapshot_data = _resolve_optional_path(
        payload.get("source_data") or snapshot_config.get("data") or snapshot_config.get("dataset_path"),
        safe_path,
    )
    run_data = _resolve_optional_path(payload.get("run_data"), safe_path)
    resolved_data = data or snapshot_data or run_data
    if data is None and resolved_data is not None:
        _add(checks, "data_source", True, f"resolved from run/source snapshot: {resolved_data}")
    elif data is not None:
        _add(checks, "data_source", True, str(data))
    else:
        _add(checks, "data_source", False, "缺少 data / run_data / source_data")
    if data is not None and snapshot_data is not None:
        _add(
            checks,
            "data_snapshot_match",
            _same_path(data, snapshot_data),
            f"snapshot={snapshot_data}",
        )
    if data is not None and run_data is not None:
        _add(
            checks,
            "run_data_match",
            _same_path(data, run_data),
            f"run={run_data}",
        )
    workspace = _resolve_optional_path(payload.get("workspace"), safe_path)
    source_workspace = _resolve_optional_path(
        payload.get("source_workspace")
        or snapshot_config.get("workspace")
        or snapshot_config.get("echomemory_workspace")
        or snapshot_config.get("openviking_workspace"),
        safe_path,
    )
    run_workspace = _resolve_optional_path(payload.get("run_workspace"), safe_path)
    if workspace is not None and source_workspace is not None:
        _add(
            checks,
            "workspace_snapshot_match",
            _same_path(workspace, source_workspace),
            f"snapshot={source_workspace}",
        )
    elif workspace is not None:
        _add(checks, "workspace_snapshot_match", True, str(workspace))
    if workspace is not None and run_workspace is not None:
        _add(
            checks,
            "run_workspace_match",
            _same_path(workspace, run_workspace),
            f"run={run_workspace}",
        )


def _validate_active_writer(
    checks: list[dict[str, Any]],
    payload: dict[str, Any],
    find_active_writer: ActiveWriterLookup | None,
) -> None:
    if find_active_writer is None:
        return
    target = _text(payload.get("input") or payload.get("output_file"))
    if not target:
        return
    active_writer = find_active_writer(target)
    if not active_writer:
        _add(checks, "writer_idle", True, "当前结果文件未被其他活跃 judge/qa 写入")
        return
    label = _text(active_writer.get("name") or active_writer.get("id") or active_writer.get("kind"))
    _add(checks, "writer_idle", False, f"{label or 'active task'} 正在写入当前结果文件")


def validate_payload(
    payload: dict[str, Any],
    default_data: Path,
    default_output_dir: Path,
    safe_path: SafePath,
    dataset_overview: DatasetOverview,
    find_active_writer: ActiveWriterLookup | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    kind = str(payload.get("kind") or "")
    runner = str(payload.get("runner") or "local_agent")
    if kind in {"distributed"} or runner != "local_agent":
        _add(checks, "runner", False, "外部 runner 已移除；请使用 MemoryBench 本地基线或 OpenViking QA")

    _validate_source_snapshot(checks, payload, safe_path)
    data = safe_path(str(payload.get("data") or payload.get("source_data") or payload.get("run_data") or str(default_data)))
    overview = _validate_dataset(checks, data, dataset_overview)

    if str(payload.get("mode") or "").strip() == "wrong_csv":
        _validate_wrong_csv(checks, _resolve_optional_path(payload.get("wrong_csv"), safe_path))

    if kind == "echomemory_import":
        base_url = _text(payload.get("echomem_base_url") or payload.get("echomemBaseUrl"))
        transport = _text(payload.get("echomem_transport") or payload.get("echomemTransport")).lower()
        _add(
            checks,
            "echomemory_import_http_blackbox",
            bool(base_url) and transport in {"", "http"},
            f"transport={transport or 'http'} base_url={base_url or '-'}",
        )

    if kind in {"echomemory_qa", "echomemory_qa_retry_failed", "echomemory_qa_retry_missing"}:
        base_url = _text(payload.get("echomem_base_url") or payload.get("echomemBaseUrl"))
        transport = _text(payload.get("echomem_transport") or payload.get("echomemTransport")).lower()
        evidence_policy = _text(payload.get("evidence_policy") or "blackbox").lower()
        retrieval_mode = _text(payload.get("retrieval_mode") or "search").lower()
        retrieval_source_mode = _text(payload.get("retrieval_source_mode") or "echo_http_native").lower()
        _add(
            checks,
            "echomemory_http_blackbox",
            bool(base_url) and transport in {"", "http"},
            f"transport={transport or 'http'} base_url={base_url or '-'}",
        )
        _add(
            checks,
            "echomemory_evidence_policy",
            evidence_policy == "blackbox",
            f"evidence_policy={evidence_policy or '-'}",
        )
        _add(
            checks,
            "echomemory_retrieval_surface",
            retrieval_mode != "local" and retrieval_source_mode != "graph_only",
            f"retrieval_mode={retrieval_mode or '-'} source={retrieval_source_mode or '-'}",
        )
        _add(
            checks,
            "echomemory_no_platform_evidence",
            True,
            "严格 HTTP 黑盒模式不包含平台补证据实现",
        )

    if kind == "judge":
        input_file = safe_path(str(payload.get("input") or ""))
        _validate_judge_csv(checks, input_file, str((overview or {}).get("format") or ""))
        _validate_judge_token(checks, payload)
        _validate_active_writer(checks, payload, find_active_writer)

    _add(checks, "local_agent", True, "MemoryBench 本地基线可运行；不需要外部 runner")
    output_dir = safe_path(str(payload.get("output_dir") or str(default_output_dir)))
    _add(checks, "output_dir", output_dir.exists() or output_dir.parent.exists(), str(output_dir))
    return {"ok": all(item["ok"] for item in checks), "checks": checks}

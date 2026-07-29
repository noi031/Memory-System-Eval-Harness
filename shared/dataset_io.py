"""Dataset-agnostic file loading and local dataset resolution."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DATASET_SOURCES: dict[str, dict[str, str]] = {
    "locomo": {
        "filename": "locomo10.json",
        "url": "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json",
    },
    "hotpotqa": {
        "filename": "hotpot_dev_distractor_v1.json",
        "url": "http://curtis.ml.cmu.edu/datasets/hotpot/hotpot_dev_distractor_v1.json",
    },
    "longmemeval": {
        "filename": "longmemeval_s_cleaned.json",
        "url": "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json",
    },
}


@dataclass
class BenchmarkQuestion:
    dataset_format: str
    sample_id: str
    question_id: str
    question: str
    answer: str
    category: str
    query_time: str
    injection_events: int
    injection_tokens_est: int
    context_preview: str
    response: str = ""
    simple_grade: str = "NEEDS_JUDGE"
    reasoning: str = "evaluation pending"
    time_cost: str = "0"
    original_sample_id: str = ""
    question_index: str = ""
    memory_users: str = ""
    native_question_id: str = ""


def read_dataset(path: str | Path) -> Any:
    source = Path(path)
    if source.suffix.lower() in {".jsonl", ".ndjson"}:
        rows = []
        with source.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                text = line.strip()
                if text:
                    rows.append(json.loads(text))
        return rows
    return json.loads(source.read_text(encoding="utf-8"))


def list_payload(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "examples", "items", "questions", "samples", "instances"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def resolve_dataset_path(benchmark: str, explicit_path: str = "") -> str:
    if explicit_path:
        return explicit_path
    source = DATASET_SOURCES.get(benchmark)
    if not source:
        raise ValueError(f"未知 benchmark: {benchmark}")

    filename = source["filename"]
    configured_path = Path(filename).expanduser()
    local_path = (
        configured_path
        if configured_path.is_absolute()
        else Path(__file__).resolve().parent.parent
        / "benchmarks"
        / benchmark
        / "data"
        / configured_path
    )
    if local_path.exists():
        return str(local_path)

    local_path.parent.mkdir(parents=True, exist_ok=True)
    url = source["url"]
    print(f"[dataset] 本地未找到 {local_path}, 正在从远程下载: {url}")
    temp_path = local_path.with_suffix(local_path.suffix + ".part")
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
        if not payload:
            raise RuntimeError("download returned an empty response")
        temp_path.write_bytes(payload)
        read_dataset(temp_path)
        temp_path.replace(local_path)
        print(
            f"[dataset] 下载完成: {local_path} "
            f"({len(payload) / 1024 / 1024:.1f} MB)"
        )
        return str(local_path)
    except Exception as exc:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise RuntimeError(
            f"无法自动获取 {benchmark} 数据集 ({filename})。\n"
            f"请手动下载: {url}\n"
            f"并保存到: {local_path}\n"
            f"或通过 --dataset 参数指定本地路径。\n"
            f"错误: {exc}"
        ) from exc

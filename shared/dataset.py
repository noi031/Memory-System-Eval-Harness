"""Dataset loading utilities — wraps benchmark_adapter.py (pure stdlib, self-contained)."""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from shared import benchmark_adapter as ba

# -- 数据集自动解析 ----------------------------------------------------------
# 各 benchmark 的默认数据文件名和下载源
_DATASET_SOURCES: dict[str, dict[str, str]] = {
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


def resolve_dataset_path(benchmark: str, explicit_path: str = "") -> str:
    """解析数据集路径: 优先用显式路径, 否则在 data/ 目录查找, 最后尝试下载。

    Args:
        benchmark: benchmark 名称 (locomo / hotpotqa / longmemeval)
        explicit_path: 用户通过 --dataset 指定的路径, 空字符串表示未指定

    Returns:
        数据集文件的本地路径
    """
    # 1. 显式路径优先
    if explicit_path:
        return explicit_path

    source = _DATASET_SOURCES.get(benchmark)
    if not source:
        raise ValueError(f"未知 benchmark: {benchmark}")

    filename = source["filename"]
    data_dir = Path(__file__).resolve().parent.parent / "benchmarks" / benchmark / "data"
    local_path = data_dir / filename

    # 2. 本地 data/ 目录已有文件
    if local_path.exists():
        return str(local_path)

    # 3. 从远程下载
    data_dir.mkdir(parents=True, exist_ok=True)
    url = source["url"]
    print(f"[dataset] 本地未找到 {filename}, 正在从远程下载: {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        local_path.write_bytes(data)
        print(f"[dataset] 下载完成: {local_path} ({len(data) / 1024 / 1024:.1f} MB)")
        return str(local_path)
    except Exception as e:
        raise RuntimeError(
            f"无法自动获取 {benchmark} 数据集 ({filename})。\n"
            f"请手动下载: {url}\n"
            f"并保存到: {local_path}\n"
            f"或通过 --dataset 参数指定本地路径。\n"
            f"错误: {e}"
        ) from e


def load_locomo(path: str | Path, sample_filter: str = "all") -> tuple[list[ba.Job], list[dict[str, Any]]]:
    """Load LoCoMo dataset and return (jobs, plans).

    Each *plan* corresponds to one conversation sample and contains:
        - sample_id, events (list of {time, text}), memory_users, question_time

    Each *job* is one QA question with: question, answer, category, query_time,
    sample_id, question_id, injection_events, etc.
    """
    p = Path(path)
    data = ba.read_dataset(p)
    if not isinstance(data, list):
        data = [data]
    return ba.locomo_jobs(data, limit=None, sample_filter=sample_filter)


def load_hotpotqa(path: str | Path, sample_filter: str = "all") -> tuple[list[ba.Job], list[dict[str, Any]]]:
    """Load HotpotQA dataset and return (jobs, plans)."""
    p = Path(path)
    data = ba.read_dataset(p)
    if not isinstance(data, list):
        data = [data]
    return ba.hotpotqa_jobs(data, limit=None, sample_filter=sample_filter)


def load_longmemeval(path: str | Path, sample_filter: str = "all") -> tuple[list[ba.Job], list[dict[str, Any]]]:
    """Load LongMemEval dataset and return (jobs, plans)."""
    p = Path(path)
    data = ba.read_dataset(p)
    if not isinstance(data, list):
        data = [data]
    return ba.longmemeval_jobs(data, limit=None, sample_filter=sample_filter)


# -- LoCoMo session batch builder ------------------------------------------

def locomo_session_batches(sample: dict[str, Any]) -> list[dict[str, Any]]:
    """Split a LoCoMo sample's conversation into per-session message batches.

    Returns a list of ``{session_key, date_time, messages}`` where each
    *messages* entry has ``role, content, created_at, role_id``.
    """
    conv = sample.get("conversation") or {}
    keys = sorted(
        (k for k in conv if re.fullmatch(r"session_\d+", str(k)) and isinstance(conv[k], list)),
        key=lambda k: int(re.search(r"\d+", k).group()),
    )
    batches: list[dict[str, Any]] = []
    for key in keys:
        date_time = str(conv.get(f"{key}_date_time") or "")
        base_dt = _parse_dt(date_time)
        messages: list[dict[str, Any]] = []
        for idx, raw in enumerate(conv[key] or []):
            if not isinstance(raw, dict):
                continue
            speaker = str(raw.get("speaker") or raw.get("role") or "speaker")
            dia_id = str(raw.get("dia_id") or f"{key}:{idx}")
            parts: list[str] = []
            if raw.get("text"):
                parts.append(str(raw["text"]))
            if raw.get("blip_caption"):
                parts.append(f"image: {raw['blip_caption']}")
            if raw.get("query"):
                parts.append(f"query: {raw['query']}")
            if not parts:
                continue
            content = f"[session_date={date_time}] [turn_time={_format_turn_time(base_dt, idx)}] [{speaker}] {dia_id}: {' '.join(parts)}"
            role = "assistant" if speaker.lower() in {"assistant", "agent"} else "user"
            msg: dict[str, Any] = {"role": role, "content": ba.compact(content, 1400)}
            turn_time = _format_turn_time(base_dt, idx)
            if turn_time:
                msg["created_at"] = turn_time
            msg["role_id"] = speaker
            batches_msg = msg  # alias for clarity
            messages.append(batches_msg)
        if messages:
            batches.append({"session_key": key, "date_time": date_time, "messages": messages})
    return batches


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def _format_turn_time(base_dt: datetime | None, idx: int) -> str:
    if base_dt is None:
        return ""
    return (base_dt.replace(second=0, microsecond=0) + timedelta(seconds=idx)).isoformat()


# -- HotpotQA context extraction --------------------------------------------

def hotpotqa_context_events(item: dict[str, Any]) -> list[dict[str, str]]:
    """Extract context passages from a HotpotQA item.

    Returns a list of ``{title, sentences}`` dicts (or ``{text}`` flattened).
    """
    return ba.collect_hotpotqa_events(item)


def hotpotqa_context_documents(item: dict[str, Any]) -> list[dict[str, str]]:
    """Extract context as document records (title + concatenated sentences)."""
    return ba.collect_hotpotqa_documents(item)


# -- LongMemEval session batches --------------------------------------------

def longmemeval_session_batches(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Split a LongMemEval item's haystack into per-session message batches."""
    return ba.collect_longmemeval_session_batches(item)

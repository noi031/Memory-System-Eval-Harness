"""通用基础设施：JSON/环境文件/输出锁/路径/模板/子进程/CSV/分布缩放。

全部函数系统无关，供 ``targets/<system>`` 下的编排层与验收工具复用，
避免每个被测系统重复实现同一份工具逻辑。
"""

from __future__ import annotations

import csv
import errno
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    """当前 UTC 时间的 ISO-8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    """读取 JSON；缺失或解析失败返回空 dict。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_env_file(path: Path) -> dict[str, str]:
    """读取 KEY=VALUE / export KEY=VALUE 环境文件，跳过注释。

    探针以子进程方式运行，服务器部署通常把模型凭据放在 Docker env 文件里；
    接受该文件保证探针与服务器看到一致的环境。值绝不写入报告。
    """
    values: dict[str, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or any(
            char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
            for char in key
        ):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def acquire_output_lock(out_dir: Path):
    """防止两个编排任务写同一证据树；Windows 独占区域锁，句柄关闭即释放。

    Windows 用 ``msvcrt.locking(LK_NBLCK)``，POSIX 回退 ``fcntl.flock``；
    已锁定时抛 ``RuntimeError("already locked")``。
    """
    lock_path = out_dir / ".objective-suite.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            handle.write(f"pid={os.getpid()}\n")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.write(f"pid={os.getpid()}\n")
            handle.flush()
    except OSError as exc:
        handle.close()
        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            raise RuntimeError(
                f"objective output directory is already locked: {out_dir}"
            ) from exc
        raise
    return handle


def resolve_relative_to(value: str, base_dir: Path) -> str:
    """把相对路径解析到 ``base_dir`` 目录；空值原样返回。"""
    if not value:
        return ""
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (base_dir / path).resolve())


def expand_template(obj: Any, mapping: dict[str, Any]) -> Any:
    """递归把字符串里的 ``${KEY}`` 替换为 ``mapping[KEY]``。"""
    if isinstance(obj, str):
        for key, value in mapping.items():
            obj = obj.replace("${" + key + "}", str(value))
        return obj
    if isinstance(obj, list):
        return [expand_template(item, mapping) for item in obj]
    if isinstance(obj, dict):
        return {str(key): expand_template(item, mapping) for key, item in obj.items()}
    return obj


def run_command(
    command: list[str],
    *,
    timeout_s: float,
    redact_values: set[str] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """执行子进程并返回 {status, returncode, command, stdout, stderr, elapsed_s}。

    status 为 PASS/FAIL/TIMEOUT；``redact_values`` 中的敏感值（如 auth_key）
    从 command/stdout/stderr 替换为 ``***configured***``。
    """
    started = datetime.now(timezone.utc)
    redact_values = redact_values or set()

    def safe_command() -> list[str]:
        return [
            "***configured***" if item in redact_values else item
            for item in command
        ]

    try:
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_s,
            check=False,
            env=env,
        )
        return {
            "status": "PASS" if completed.returncode == 0 else "FAIL",
            "returncode": completed.returncode,
            "command": safe_command(),
            "stdout": completed.stdout[-12000:],
            "stderr": completed.stderr[-12000:],
            "elapsed_s": (datetime.now(timezone.utc) - started).total_seconds(),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "TIMEOUT",
            "returncode": 124,
            "command": safe_command(),
            "stdout": str(exc.stdout or "")[-12000:],
            "stderr": str(exc.stderr or "")[-12000:],
            "elapsed_s": (datetime.now(timezone.utc) - started).total_seconds(),
        }


def read_csv(path: Path) -> list[dict[str, str]]:
    """读取 CSV 为 dict 列表；文件缺失返回空列表。"""
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def scale_counts_to_cap(counts: list[int], total_cap: int) -> list[int]:
    """把整数分布按比例缩到 ``total_cap`` 且不超上限。"""
    if total_cap <= 0 or sum(counts) <= total_cap:
        return counts
    if not counts:
        return []
    if total_cap < len(counts):
        return [1 if index < total_cap else 0 for index in range(len(counts))]
    total = sum(counts)
    scaled = [max(1, (count * total_cap) // total) for count in counts]
    while sum(scaled) > total_cap:
        index = max(
            (idx for idx, value in enumerate(scaled) if value > 1),
            key=lambda idx: (scaled[idx], -idx),
            default=None,
        )
        if index is None:
            break
        scaled[index] -= 1
    fractions = [
        (count * total_cap / total) - ((count * total_cap) // total)
        for count in counts
    ]
    while sum(scaled) < total_cap:
        index = max(range(len(counts)), key=lambda idx: (fractions[idx], -idx))
        scaled[index] += 1
        fractions[index] = -1.0
    return scaled

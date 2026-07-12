#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dev-server-common.sh"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

ensure_runtime_dir

pid="$(read_pid_file || true)"
if [[ -n "${pid:-}" ]] && is_v2_server_pid "$pid"; then
  echo "benchmark-console-v2 already running"
  echo "  URL: $URL"
  echo "  PID: $pid"
  echo "  Log: $LOG_FILE"
  exit 0
fi

rm -f "$PID_FILE"

listener="$(adopt_listener_pid || true)"
if [[ -n "${listener:-}" ]]; then
  echo "benchmark-console-v2 already running"
  echo "  URL: $URL"
  echo "  PID: $listener"
  echo "  Log: $LOG_FILE"
  echo "  PID file: $PID_FILE (adopted existing listener)"
  exit 0
fi

port_owner="$(listener_pid || true)"
if [[ -n "${port_owner:-}" ]]; then
  echo "Port $PORT is already in use by another process." >&2
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2 || true
  exit 1
fi

echo "Starting benchmark-console-v2"
echo "  URL: $URL"
echo "  API: $API_BASE"
echo "  Root: $ROOT"
echo "  Log: $LOG_FILE"

pid="$(
  ROOT="$ROOT" HOST="$HOST" PORT="$PORT" API_BASE="$API_BASE" LOG_FILE="$LOG_FILE" python3 - <<'PY'
import os
import subprocess
import sys
from pathlib import Path

root = Path(os.environ["ROOT"])
host = os.environ["HOST"]
port = os.environ["PORT"]
api_base = os.environ["API_BASE"]
log_file = Path(os.environ["LOG_FILE"])

with log_file.open("ab") as log:
    proc = subprocess.Popen(
        [
            sys.executable,
            str(root / "dev_server.py"),
            "--host",
            host,
            "--port",
            port,
            "--api-base",
            api_base,
        ],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    print(proc.pid)
PY
)"
printf '%s\n' "$pid" >"$PID_FILE"

if ! wait_for_server 60 0.2; then
  echo "benchmark-console-v2 failed to become ready." >&2
  echo "Last log lines:" >&2
  tail -n 20 "$LOG_FILE" >&2 || true
  exit 1
fi

echo "benchmark-console-v2 running"
echo "  PID: $pid"
echo "  PID file: $PID_FILE"

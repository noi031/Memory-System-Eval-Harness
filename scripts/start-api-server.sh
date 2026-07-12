#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/api-server-common.sh"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

ensure_api_runtime_dir

pid="$(read_api_pid_file || true)"
if [[ -n "${pid:-}" ]] && is_api_server_pid "$pid"; then
  echo "benchmark-console-api already running"
  echo "  URL: $API_URL"
  echo "  PID: $pid"
  echo "  Log: $API_LOG_FILE"
  exit 0
fi

rm -f "$API_PID_FILE"

listener="$(adopt_api_listener_pid || true)"
if [[ -n "${listener:-}" ]]; then
  echo "benchmark-console-api already running"
  echo "  URL: $API_URL"
  echo "  PID: $listener"
  echo "  Log: $API_LOG_FILE"
  echo "  PID file: $API_PID_FILE (adopted existing listener)"
  exit 0
fi

port_owner="$(api_listener_pid || true)"
if [[ -n "${port_owner:-}" ]]; then
  echo "Port $API_PORT is already in use by another process." >&2
  lsof -nP -iTCP:"$API_PORT" -sTCP:LISTEN >&2 || true
  exit 1
fi

echo "Starting benchmark-console-api"
echo "  URL: $API_URL"
echo "  Root: $ROOT"
echo "  Log: $API_LOG_FILE"

pid="$(
  ROOT="$ROOT" API_HOST="$API_HOST" API_PORT="$API_PORT" API_LOG_FILE="$API_LOG_FILE" python3 - <<'PY'
import os
import subprocess
import sys
from pathlib import Path

root = Path(os.environ["ROOT"])
host = os.environ["API_HOST"]
port = os.environ["API_PORT"]
log_file = Path(os.environ["API_LOG_FILE"])

with log_file.open("ab") as log:
    proc = subprocess.Popen(
        [
            sys.executable,
            str(root / "server.py"),
            "--host",
            host,
            "--port",
            port,
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
printf '%s\n' "$pid" >"$API_PID_FILE"

if ! wait_for_api_server 120 0.25; then
  echo "benchmark-console-api failed to become ready." >&2
  echo "Last log lines:" >&2
  tail -n 40 "$API_LOG_FILE" >&2 || true
  exit 1
fi

echo "benchmark-console-api running"
echo "  PID: $pid"
echo "  PID file: $API_PID_FILE"

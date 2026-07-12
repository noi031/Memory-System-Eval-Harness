#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dev-server-common.sh"

ensure_runtime_dir

pid="$(read_pid_file || true)"
listener="$(listener_pid || true)"

if [[ -n "${pid:-}" ]] && is_v2_server_pid "$pid"; then
  echo "benchmark-console-v2 is running"
  echo "  URL: $URL"
  echo "  PID: $pid"
  echo "  PID file: $PID_FILE"
  echo "  Log: $LOG_FILE"
  ps -o pid,ppid,tty,stat,start,time,command -p "$pid"
  exit 0
fi

if [[ -n "${listener:-}" ]] && is_v2_server_pid "$listener"; then
  printf '%s\n' "$listener" >"$PID_FILE"
  echo "benchmark-console-v2 is running"
  echo "  URL: $URL"
  echo "  PID: $listener"
  echo "  PID file: $PID_FILE (adopted existing listener)"
  echo "  Log: $LOG_FILE"
  ps -o pid,ppid,tty,stat,start,time,command -p "$listener"
  exit 0
fi

rm -f "$PID_FILE"
echo "benchmark-console-v2 is not running"
echo "  Expected URL: $URL"
echo "  Start with: $ROOT/scripts/start-v2-server.sh"
exit 1

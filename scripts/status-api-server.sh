#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/api-server-common.sh"

ensure_api_runtime_dir

pid="$(read_api_pid_file || true)"
listener="$(api_listener_pid || true)"

if [[ -n "${pid:-}" ]] && is_api_server_pid "$pid"; then
  echo "benchmark-console-api is running"
  echo "  URL: $API_URL"
  echo "  PID: $pid"
  echo "  PID file: $API_PID_FILE"
  echo "  Log: $API_LOG_FILE"
  ps -o pid,ppid,tty,stat,start,time,command -p "$pid"
  exit 0
fi

if [[ -n "${listener:-}" ]] && is_api_server_pid "$listener"; then
  printf '%s\n' "$listener" >"$API_PID_FILE"
  echo "benchmark-console-api is running"
  echo "  URL: $API_URL"
  echo "  PID: $listener"
  echo "  PID file: $API_PID_FILE (adopted existing listener)"
  echo "  Log: $API_LOG_FILE"
  ps -o pid,ppid,tty,stat,start,time,command -p "$listener"
  exit 0
fi

rm -f "$API_PID_FILE"
echo "benchmark-console-api is not running"
echo "  Expected URL: $API_URL"
echo "  Start with: $ROOT/scripts/start-api-server.sh"
exit 1

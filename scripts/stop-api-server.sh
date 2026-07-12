#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/api-server-common.sh"

stop_pid() {
  local pid="$1"
  local i

  kill "$pid" >/dev/null 2>&1 || return 0
  for ((i = 0; i < 50; i += 1)); do
    if ! ps -p "$pid" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.2
  done
  kill -9 "$pid" >/dev/null 2>&1 || true
}

stopped=0
pid="$(read_api_pid_file || true)"
if [[ -n "${pid:-}" ]] && is_api_server_pid "$pid"; then
  echo "Stopping benchmark-console-api PID $pid"
  stop_pid "$pid"
  stopped=1
fi

listener="$(api_listener_pid || true)"
if [[ -n "${listener:-}" ]] && is_api_server_pid "$listener"; then
  if [[ "$listener" != "${pid:-}" ]]; then
    echo "Stopping benchmark-console-api listener PID $listener"
    stop_pid "$listener"
    stopped=1
  fi
fi

rm -f "$API_PID_FILE"

if [[ "$stopped" -eq 1 ]]; then
  echo "benchmark-console-api stopped"
else
  echo "benchmark-console-api is not running"
fi

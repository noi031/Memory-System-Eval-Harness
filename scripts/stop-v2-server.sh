#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dev-server-common.sh"

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
pid="$(read_pid_file || true)"
if [[ -n "${pid:-}" ]] && is_v2_server_pid "$pid"; then
  echo "Stopping benchmark-console-v2 PID $pid"
  stop_pid "$pid"
  stopped=1
fi

listener="$(listener_pid || true)"
if [[ -n "${listener:-}" ]] && is_v2_server_pid "$listener"; then
  if [[ "$listener" != "${pid:-}" ]]; then
    echo "Stopping benchmark-console-v2 listener PID $listener"
    stop_pid "$listener"
    stopped=1
  fi
fi

rm -f "$PID_FILE"

if [[ "$stopped" -eq 1 ]]; then
  echo "benchmark-console-v2 stopped"
else
  echo "benchmark-console-v2 is not running"
fi

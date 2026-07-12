#!/usr/bin/env bash

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

HOST="${BENCHMARK_CONSOLE_V2_HOST:-127.0.0.1}"
PORT="${BENCHMARK_CONSOLE_V2_PORT:-4173}"
API_BASE="${BENCHMARK_CONSOLE_API_BASE:-http://127.0.0.1:19181}"

RUNTIME_DIR="${BENCHMARK_CONSOLE_V2_RUNTIME_DIR:-$ROOT/.runtime}"
PID_FILE="$RUNTIME_DIR/dev_server_${PORT}.pid"
LOG_FILE="$RUNTIME_DIR/dev_server_${PORT}.log"
URL="http://${HOST}:${PORT}"

ensure_runtime_dir() {
  mkdir -p "$RUNTIME_DIR"
}

read_pid_file() {
  if [[ -f "$PID_FILE" ]]; then
    tr -d '[:space:]' <"$PID_FILE"
  fi
}

listener_pid() {
  if ! command -v lsof >/dev/null 2>&1; then
    return 0
  fi
  lsof -t -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1
}

pid_command() {
  local pid="$1"
  ps -o command= -p "$pid" 2>/dev/null || true
}

pid_cwd() {
  local pid="$1"
  if ! command -v lsof >/dev/null 2>&1; then
    return 0
  fi
  lsof -a -p "$pid" -d cwd 2>/dev/null | awk 'NR>1 {print $NF}'
}

is_v2_server_pid() {
  local pid="$1"
  local command cwd

  [[ -n "$pid" ]] || return 1
  command="$(pid_command "$pid")"
  [[ -n "$command" ]] || return 1
  [[ "$command" == *"dev_server.py --host $HOST --port $PORT"* ]] || return 1

  cwd="$(pid_cwd "$pid")"
  [[ -z "$cwd" || "$cwd" == "$ROOT" ]] || return 1
  return 0
}

adopt_listener_pid() {
  local pid
  pid="$(listener_pid)"
  if [[ -n "$pid" ]] && is_v2_server_pid "$pid"; then
    ensure_runtime_dir
    printf '%s\n' "$pid" >"$PID_FILE"
    printf '%s\n' "$pid"
    return 0
  fi
  return 1
}

wait_for_server() {
  local attempts="${1:-50}"
  local sleep_s="${2:-0.2}"
  local i
  for ((i = 0; i < attempts; i += 1)); do
    if curl -fsS "$URL/" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$sleep_s"
  done
  return 1
}

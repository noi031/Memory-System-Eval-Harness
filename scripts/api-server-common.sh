#!/usr/bin/env bash

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

API_HOST="${BENCHMARK_CONSOLE_API_HOST:-127.0.0.1}"
API_PORT="${BENCHMARK_CONSOLE_API_PORT:-19181}"
API_URL="http://${API_HOST}:${API_PORT}"

RUNTIME_DIR="${BENCHMARK_CONSOLE_V2_RUNTIME_DIR:-$ROOT/.runtime}"
API_PID_FILE="$RUNTIME_DIR/api_server_${API_PORT}.pid"
API_LOG_FILE="$RUNTIME_DIR/api_server_${API_PORT}.log"

ensure_api_runtime_dir() {
  mkdir -p "$RUNTIME_DIR"
}

read_api_pid_file() {
  if [[ -f "$API_PID_FILE" ]]; then
    tr -d '[:space:]' <"$API_PID_FILE"
  fi
}

api_listener_pid() {
  if ! command -v lsof >/dev/null 2>&1; then
    return 0
  fi
  lsof -t -nP -iTCP:"$API_PORT" -sTCP:LISTEN 2>/dev/null | head -n 1
}

api_pid_command() {
  local pid="$1"
  ps -o command= -p "$pid" 2>/dev/null || true
}

api_pid_cwd() {
  local pid="$1"
  if ! command -v lsof >/dev/null 2>&1; then
    return 0
  fi
  lsof -a -p "$pid" -d cwd 2>/dev/null | awk 'NR>1 {print $NF}'
}

is_api_server_pid() {
  local pid="$1"
  local command cwd

  [[ -n "$pid" ]] || return 1
  command="$(api_pid_command "$pid")"
  [[ -n "$command" ]] || return 1
  [[ "$command" == *"server.py --host $API_HOST --port $API_PORT"* ]] || return 1

  cwd="$(api_pid_cwd "$pid")"
  [[ -z "$cwd" || "$cwd" == "$ROOT" ]] || return 1
  return 0
}

adopt_api_listener_pid() {
  local pid
  pid="$(api_listener_pid)"
  if [[ -n "$pid" ]] && is_api_server_pid "$pid"; then
    ensure_api_runtime_dir
    printf '%s\n' "$pid" >"$API_PID_FILE"
    printf '%s\n' "$pid"
    return 0
  fi
  return 1
}

wait_for_api_server() {
  local attempts="${1:-100}"
  local sleep_s="${2:-0.2}"
  local i
  for ((i = 0; i < attempts; i += 1)); do
    if curl -fsS "$API_URL/api/config" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$sleep_s"
  done
  return 1
}

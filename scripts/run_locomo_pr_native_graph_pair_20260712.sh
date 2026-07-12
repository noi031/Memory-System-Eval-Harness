#!/usr/bin/env bash
set -euo pipefail

ROOT="${LOCOMO_HARNESS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)}"
PR_ROOT="${LOCOMO_PR_ROOT:?set LOCOMO_PR_ROOT to the EchoMemory source tree}"
WORKSPACE="${LOCOMO_PR_WORKSPACE:?set LOCOMO_PR_WORKSPACE to a completed EchoMemory workspace}"
ACCOUNT="${LOCOMO_PR_ACCOUNT:-locomo-conv30-pr123125-spacy-v6-20260710}"
PORT="${LOCOMO_PR_PORT:-19117}"
BASE_URL="http://127.0.0.1:${PORT}"
PYTHON="${LOCOMO_PYTHON_BIN:-$PR_ROOT/.venv/bin/python}"
AUTH_FILE="${LOCOMO_PR_AUTH_FILE:-$WORKSPACE/.echomem_http_auth_keys.json}"

OUTPUT_ROOT="${1:-$ROOT/runs/locomo_pr_native_graph_pair_20260712}"
MIXED_RUN="$OUTPUT_ROOT/membase_graph_overview"
GRAPH_RUN="$OUTPUT_ROOT/graph_only_overview"
GRAPH_CONFIG="$OUTPUT_ROOT/graph_only.override.json"
GRAPH_SERVER_LOG="$OUTPUT_ROOT/graph_only_server.log"
RESTORED_SERVER_LOG="$OUTPUT_ROOT/membase_graph_server_restored.log"
REPORT="$ROOT/docs/locomo-conv30-pr123-pr125-native-graph-pair-20260712.html"

mkdir -p "$OUTPUT_ROOT"

log() {
  printf '[native-graph-pair] %s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$OUTPUT_ROOT/runner.log"
}

run_complete() {
  local run_dir="$1"
  [[ -f "$run_dir/summary.json" ]] &&
    [[ "$(jq -r '.graded // 0' "$run_dir/summary.json")" == "81" ]]
}

wait_for_health() {
  local attempts="${1:-90}"
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl -fsS --max-time 3 "$BASE_URL/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

stop_server() {
  local pid
  pid="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    return 0
  fi
  log "stopping EchoMemory server pid=$pid port=$PORT"
  kill "$pid" 2>/dev/null || true
  for _ in {1..30}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  kill -9 "$pid" 2>/dev/null || true
}

start_server() {
  local config_path="${1:-}"
  local log_path="$2"
  local cli_bin="$PR_ROOT/.venv/bin/echomem"
  local command=(
    "$cli_bin" server
    --workspace "$WORKSPACE"
    --host 127.0.0.1
    --port "$PORT"
  )
  if [[ -n "$config_path" ]]; then
    command+=(--config "$config_path")
  fi
  (
    cd "$PR_ROOT"
    exec nohup env PYTHONPATH="$PR_ROOT/src" "${command[@]}"
  ) >>"$log_path" 2>&1 < /dev/null &
  SERVER_PID=$!
  if ! wait_for_health; then
    log "server failed health check; see $log_path"
    return 1
  fi
  log "EchoMemory server ready pid=$SERVER_PID port=$PORT config=${config_path:-workspace-default}"
}

run_qa() {
  local run_dir="$1"
  if run_complete "$run_dir"; then
    log "skip completed QA run: $run_dir"
    return 0
  fi
  mkdir -p "$run_dir"
  log "starting QA: $run_dir"
  LOCOMO_SEARCH_OVERVIEW_ENRICHMENT=1 \
  LOCOMO_OVERVIEW_BUDGET_CHARS=3000 \
  LOCOMO_PR_ROOT="$PR_ROOT" \
  LOCOMO_PR_WORKSPACE="$WORKSPACE" \
  LOCOMO_PR_ACCOUNT="$ACCOUNT" \
  LOCOMO_PR_TENANT_ID="$TENANT_ID" \
  LOCOMO_PR_AUTH_FILE="$AUTH_FILE" \
  PR_ECHOMEM_BASE_URL="$BASE_URL" \
  LOCOMO_QA_PARALLELISM=4 \
  LOCOMO_JUDGE_PARALLEL=4 \
  LOCOMO_PYTHON_BIN="$PYTHON" \
    "$ROOT/scripts/run_locomo_exact_judge_matrix_20260712.sh" pr_blackbox "$run_dir" \
    2>&1 | tee "$run_dir/run.log"
}

log "scope: PR123+125 only; Membase+Graph and Graph-only; overview enabled"
[[ -f "$AUTH_FILE" ]] || {
  log "missing HTTP auth file: $AUTH_FILE"
  exit 2
}
AUTH_ENTRY="$(
  jq -c --arg account "$ACCOUNT" \
    '[.entries[] | select(.account == $account)][0] // empty' \
    "$AUTH_FILE"
)"
[[ -n "$AUTH_ENTRY" ]] || {
  log "missing HTTP auth entry for account=$ACCOUNT"
  exit 2
}
TENANT_ID="$(jq -r '.tenant_id' <<<"$AUTH_ENTRY")"
SESSION_COUNT="$(
  find "$WORKSPACE/tenants/$TENANT_ID/sessions" \
    -mindepth 2 -maxdepth 3 -path '*/current/session.json' -type f 2>/dev/null \
    | wc -l | tr -d ' '
)"
OVERVIEW_COUNT="$(
  find "$WORKSPACE/tenants/$TENANT_ID/engines/echo0_plugin/sessions" \
    -mindepth 2 -maxdepth 2 -name overview.md -type f 2>/dev/null \
    | wc -l | tr -d ' '
)"
[[ "$SESSION_COUNT" == "19" ]] || {
  log "completed workspace required: tenant=$TENANT_ID sessions=$SESSION_COUNT/19"
  exit 2
}
[[ "$OVERVIEW_COUNT" == "19" ]] || {
  log "overview-enabled run requires 19 session overviews: found=$OVERVIEW_COUNT"
  exit 2
}
log "reuse completed workspace: tenant=$TENANT_ID sessions=$SESSION_COUNT/19 overviews=$OVERVIEW_COUNT/19"

"$PYTHON" -m py_compile \
  "$ROOT/scripts/local_judge.py" \
  "$ROOT/scripts/generate_locomo_native_graph_matrix_report.py"
"$PYTHON" "$ROOT/scripts/smoke-local-judge-parser.py"

if ! curl -fsS --max-time 3 "$BASE_URL/health" >/dev/null 2>&1; then
  start_server "" "$OUTPUT_ROOT/membase_graph_server.log"
fi
run_qa "$MIXED_RUN"

jq '{
  engine: {
    configs: {
      echo0_plugin: {
        recall_include_conversation_default: false,
        recall_include_structured_default: false,
        recall_include_atom_default: false,
        recall_include_graph_default: true,
        recall_include_episode_default: false,
        graph: ((.engine.configs.echo0_plugin.graph // {}) * {enabled: true})
      }
    }
  }
}' "$WORKSPACE/config.json" >"$GRAPH_CONFIG"
chmod 600 "$GRAPH_CONFIG"

stop_server
start_server "$GRAPH_CONFIG" "$GRAPH_SERVER_LOG"
run_qa "$GRAPH_RUN"
stop_server

start_server "" "$RESTORED_SERVER_LOG"
log "restored Membase+Graph server on $BASE_URL"

PR_COMMIT="$(git -C "$PR_ROOT" rev-parse HEAD)"
"$PYTHON" "$ROOT/scripts/generate_locomo_native_graph_matrix_report.py" \
  --mixed-run "$MIXED_RUN" \
  --graph-run "$GRAPH_RUN" \
  --pr-import "$WORKSPACE" \
  --pr-commit "$PR_COMMIT" \
  --output "$REPORT"

log "complete"
log "mixed result: $MIXED_RUN"
log "graph-only result: $GRAPH_RUN"
log "HTML report: $REPORT"

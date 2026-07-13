#!/usr/bin/env bash
set -euo pipefail

ROOT="${LOCOMO_HARNESS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)}"
DATASET="${LOCOMO_DATASET:?set LOCOMO_DATASET to locomo10.json}"
PR_ROOT="${LOCOMO_PR_ROOT:-}"
PR_WORKSPACE="${LOCOMO_PR_WORKSPACE:-}"
PR_ACCOUNT="${LOCOMO_PR_ACCOUNT:-locomo-conv30-pr123125-spacy-v6-20260710}"
PR_TENANT_ID="${LOCOMO_PR_TENANT_ID:-}"
PR_AUTH_FILE="${LOCOMO_PR_AUTH_FILE:-$PR_WORKSPACE/.echomem_http_auth_keys.json}"
HEAD_ROOT="${LOCOMO_HEAD_ROOT:-}"
HEAD_WORKSPACE="${LOCOMO_HEAD_WORKSPACE:-}"
HEAD_ACCOUNT="${LOCOMO_HEAD_ACCOUNT:-locomo-conv30-headclean-matrix-20260712}"
HEAD_TENANT_ID="${LOCOMO_HEAD_TENANT_ID:-}"
HEAD_AUTH_FILE="${LOCOMO_HEAD_AUTH_FILE:-$HEAD_WORKSPACE/.echomem_http_auth_keys.json}"

mode="${1:?usage: $0 pr_blackbox|head_clean OUT_DIR}"
out_dir="${2:?usage: $0 pr_blackbox|head_clean OUT_DIR}"

[[ -n "$PR_ROOT" && -n "$PR_WORKSPACE" ]] || {
  echo "set LOCOMO_PR_ROOT and LOCOMO_PR_WORKSPACE" >&2
  exit 2
}
if [[ "$mode" == "head_clean" && ( -z "$HEAD_ROOT" || -z "$HEAD_WORKSPACE" ) ]]; then
  echo "set LOCOMO_HEAD_ROOT and LOCOMO_HEAD_WORKSPACE for head_clean" >&2
  exit 2
fi

source_mode="echo_http_native"
evidence_policy="blackbox"
overview_flag="--no-search-overview-enrichment"
case "${LOCOMO_SEARCH_OVERVIEW_ENRICHMENT:-0}" in
  1|true|TRUE|yes|YES|on|ON)
    overview_flag="--search-overview-enrichment"
    ;;
esac

case "$mode" in
  pr_blackbox)
    echomem_root="$PR_ROOT"
    workspace="$PR_WORKSPACE"
    account="$PR_ACCOUNT"
    tenant_id_filter="$PR_TENANT_ID"
    auth_file="$PR_AUTH_FILE"
    base_url="${PR_ECHOMEM_BASE_URL:-http://127.0.0.1:19117}"
    ;;
  head_clean)
    echomem_root="$HEAD_ROOT"
    workspace="$HEAD_WORKSPACE"
    account="$HEAD_ACCOUNT"
    tenant_id_filter="$HEAD_TENANT_ID"
    auth_file="$HEAD_AUTH_FILE"
    base_url="${HEAD_ECHOMEM_BASE_URL:-http://127.0.0.1:19116}"
    ;;
  *)
    echo "unsupported mode: $mode" >&2
    exit 2
    ;;
esac

config_file="$workspace/config.json"
model_base=$(jq -r '.model.llm.api_base' "$config_file")
model_token=$(jq -r '.model.llm.api_key' "$config_file")
model_name=$(jq -r '.model.llm.model' "$config_file")
if [[ -z "$model_base" || -z "$model_token" || -z "$model_name" ]]; then
  echo "missing model API configuration in $config_file" >&2
  exit 2
fi

auth_entry=$(
  jq -c \
    --arg account "$account" \
    --arg tenant_id "$tenant_id_filter" \
    '[
      .entries[]
      | select(.account == $account)
      | select($tenant_id == "" or .tenant_id == $tenant_id)
    ][0] // empty' \
    "$auth_file"
)
if [[ -z "$auth_entry" ]]; then
  echo "missing auth entry for account=$account tenant_id=${tenant_id_filter:-<auto>} in $auth_file" >&2
  exit 2
fi
auth_key=$(jq -r '.auth_key' <<<"$auth_entry")
tenant_id=$(jq -r '.tenant_id' <<<"$auth_entry")
api_user_id=$(jq -r '.api_user_id' <<<"$auth_entry")

mkdir -p "$out_dir"
export LOCOMO_JUDGE_TOKEN
LOCOMO_JUDGE_TOKEN=$model_token
"${LOCOMO_PYTHON_BIN:-python3}" "$ROOT/scripts/echomemory_memory_qa.py" \
  --dataset "$DATASET" \
  --out-dir "$out_dir" \
  --sample conv-30 \
  --echomem-root "$echomem_root" \
  --echomem-transport http \
  --echomem-base-url "$base_url" \
  --echomem-auth-key "$auth_key" \
  --workspace "$workspace" \
  --account "$account" \
  --user-id default \
  --agent-id default \
  --identity-mode fixed \
  --prompt-mode vikingboat_lite \
  --top-k 25 \
  --score-threshold 0 \
  --memory-budget-chars 6000 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000 \
  --retrieval-mode search \
  --evidence-policy "$evidence_policy" \
  --retrieval-source-mode "$source_mode" \
  --retrieval-ranker score \
  "$overview_flag" \
  --overview-budget-chars "${LOCOMO_OVERVIEW_BUDGET_CHARS:-3000}" \
  --no-local-session-summaries \
  --no-local-segments \
  --no-local-atoms \
  --no-local-messages \
  --no-local-timeline-hints \
  --no-local-memory-artifacts \
  --no-current-session-raw-fallback \
  --no-segment-readback \
  --no-precision-session-readback \
  --no-precision-grounded-projection \
  --no-longmemeval-current-session-summary-fallback \
  --no-hotpot-empty-overview-fallback \
  --vikingboat-tool-loop \
  --tool-set vikingbot_native_safe \
  --tool-search-limit 25 \
  --tool-min-score 0 \
  --max-iterations "${LOCOMO_MAX_ITERATIONS:-50}" \
  --max-tool-calls "${LOCOMO_MAX_TOOL_CALLS:-0}" \
  --no-initial-tool-prefetch \
  --no-fallback-to-one-shot \
  --no-toolloop-rescue-on-toollike-answer \
  --answer-base-url "$model_base" \
  --answer-model "$model_name" \
  --answer-token "$model_token" \
  --judge-base-url "$model_base" \
  --judge-model "$model_name" \
  --judge-token "$model_token" \
  --judge-every "${LOCOMO_JUDGE_EVERY:-81}" \
  --judge-parallel "${LOCOMO_JUDGE_PARALLEL:-4}" \
  --no-answer-refinement \
  --qa-parallelism "${LOCOMO_QA_PARALLELISM:-4}" \
  --timeout-s 180 \
  --question-timeout-s 300 \
  --judge-timeout-s 180 \
  --model-retries 5 \
  --judge-retries 5 \
  --qa-memory-injection

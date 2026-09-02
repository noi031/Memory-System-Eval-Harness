#!/usr/bin/env bash
set -euo pipefail

# Single-instance 4U8G entry point for the PR397 + PR421 suite.
# The soak case is deliberately opt-in; this command is the bounded
# acceptance run used before a longer stability experiment.
root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$root_dir"
base_url="${ECHOMEM_BASE_URL:-http://127.0.0.1:8010}"
tenant_config="${STRESS_TENANT_CONFIG:?set STRESS_TENANT_CONFIG to an independent-tenant JSON file}"
preflight_config="${ECHOMEM_CONFIG:?set ECHOMEM_CONFIG to the actual EchoMem config.json}"
out_dir="${STRESS_OUTPUT_DIR:-$root_dir/results/performance/4u8g-complete-$(date +%Y%m%d_%H%M%S)}"
repeats="${STRESS_REPEATS:-1}"
commit_timeout_s="${STRESS_COMMIT_TIMEOUT_S:-600}"
case_timeout_s="${STRESS_CASE_TIMEOUT_S:-180}"

# complete_scenarios() contains 26 cases; omit only the 30-minute soak from
# the routine 4U8G run. The explicit list is recorded in suite.json.
scenarios="${STRESS_SCENARIOS:-A@1,B@1,C8:1@1,C4:1@1,C1:1@1,D@1,A@2,B@2,C8:1@2,C4:1@2,C1:1@2,D@2,baseline,mixed,commit-storm,commit-barrier,saturation,tenant-skew,search-priority-blackbox,search-storm,capacity-2,capacity-4,capacity-8,capacity-16,capacity-32}"

mkdir -p "$out_dir"

python3 -m performance.formal_suite \
  --base-url "$base_url" \
  --tenant-config "$tenant_config" \
  --preflight-config "$preflight_config" \
  --profile complete \
  --scenarios "$scenarios" \
  --repeats "$repeats" \
  --commit-timeout-s "$commit_timeout_s" \
  --case-timeout-s "$case_timeout_s" \
  --out-dir "$out_dir"

printf '4U8G complete suite: %s\n' "$out_dir"

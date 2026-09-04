#!/usr/bin/env bash
set -euo pipefail

# One-command entry point for the six real-HTTP 4U8G objectives.
#
# Required deployment inputs:
#   STRESS_PROFILES     objective profile JSON
#   ECHOMEM_CONFIG      the actual EchoMem config.json used by the service
#   tenant_config       path inside STRESS_PROFILES (usually tenants-32.server.json)
#
# Optional:
#   ECHOMEM_BASE_URL    default: http://127.0.0.1:8010
#   STRESS_OUTPUT_DIR   default: results/performance/4u8g-six-metrics-<timestamp>
#   STRESS_ENV_FILE     KEY=VALUE file for the real-model subprocesses
#   STRESS_QUICK=1      bounded diagnostic run; not a full acceptance result
#   STRESS_MAX_WALL_CLOCK_S
#
# The profile controls the actual tenant credentials, fault/restart controls,
# and metrics endpoint. No API key is written to the result artifacts.

root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$root_dir"
python_bin="${STRESS_PYTHON:-python3}"

profiles="${STRESS_PROFILES:-$root_dir/performance/instance-profiles.example.json}"
config="${ECHOMEM_CONFIG:?set ECHOMEM_CONFIG to the actual EchoMem config.json}"
base_url="${ECHOMEM_BASE_URL:-http://127.0.0.1:8010}"
profile_name="${STRESS_PROFILE_NAME:-4U8G}"
out_dir="${STRESS_OUTPUT_DIR:-$root_dir/results/performance/4u8g-six-metrics-$(date +%Y%m%d_%H%M%S)}"
max_wall_clock="${STRESS_MAX_WALL_CLOCK_S:-10800}"
env_args=()
if [ -n "${STRESS_ENV_FILE:-}" ]; then
  env_args=(--env-file "$STRESS_ENV_FILE")
fi

if [ "${STRESS_QUICK:-0}" = "1" ]; then
  "$python_bin" -m performance.objective_suite \
    --profiles "$profiles" \
    --profile "$profile_name" \
    --base-url "$base_url" \
    --preflight-config "$config" \
    --out-dir "$out_dir" \
    --quick \
    --timeout-s "$max_wall_clock" \
    --max-wall-clock-s "$max_wall_clock" \
    "${env_args[@]}"
else
  "$python_bin" -m performance.objective_suite \
    --profiles "$profiles" \
    --profile "$profile_name" \
    --base-url "$base_url" \
    --preflight-config "$config" \
    --out-dir "$out_dir" \
    --full \
    --timeout-s "$max_wall_clock" \
    --max-wall-clock-s "$max_wall_clock" \
    "${env_args[@]}"
fi

printf '4U8G six-metric report: %s/objective-suite.html\n' "$out_dir"

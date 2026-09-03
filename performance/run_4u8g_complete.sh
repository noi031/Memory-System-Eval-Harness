#!/usr/bin/env bash
set -euo pipefail

# Single-instance 4U8G entry point for the PR397 + PR421 suite.
# The soak case is deliberately opt-in; this command is the bounded
# acceptance run used before a longer stability experiment.
root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$root_dir"
python_major="$(python3 -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo 0)"
python_minor="$(python3 -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo 0)"
if [ "$python_major" -lt 3 ] || { [ "$python_major" -eq 3 ] && [ "$python_minor" -lt 9 ]; }; then
  echo "ERROR: Harness requires Python >= 3.9; detected $(python3 --version 2>&1 || true)." >&2
  echo "Run this script inside the echomem-stress-runner image; see README.md section 6." >&2
  exit 78
fi
base_url="${ECHOMEM_BASE_URL:-http://127.0.0.1:8010}"
tenant_config="${STRESS_TENANT_CONFIG:?set STRESS_TENANT_CONFIG to an independent-tenant JSON file}"
preflight_config="${ECHOMEM_CONFIG:?set ECHOMEM_CONFIG to the actual EchoMem config.json}"
out_dir="${STRESS_OUTPUT_DIR:-$root_dir/results/performance/4u8g-complete-$(date +%Y%m%d_%H%M%S)}"
repeats="${STRESS_REPEATS:-1}"
commit_timeout_s="${STRESS_COMMIT_TIMEOUT_S:-600}"
case_timeout_s="${STRESS_CASE_TIMEOUT_S:-0}"
barrier_wave_size="${STRESS_BARRIER_WAVE_SIZE:-32}"

# The full 4U8G profile runs 12 PR397/report(6) cases plus the 25-case
# PR421 catalog. The long soak case remains excluded from the routine run.
profile="${STRESS_PROFILE:-4u8g-full}"
scenarios="${STRESS_SCENARIOS:-}"
scenario_args=()
if [ -n "$scenarios" ]; then
  scenario_args=(--scenarios "$scenarios")
fi

mkdir -p "$out_dir"

python3 -m performance.formal_suite \
  --base-url "$base_url" \
  --tenant-config "$tenant_config" \
  --preflight-config "$preflight_config" \
  --profile "$profile" \
  "${scenario_args[@]}" \
  --repeats "$repeats" \
  --commit-timeout-s "$commit_timeout_s" \
  --case-timeout-s "$case_timeout_s" \
  --barrier-wave-size "$barrier_wave_size" \
  --out-dir "$out_dir"

printf '4U8G complete suite: %s\n' "$out_dir"

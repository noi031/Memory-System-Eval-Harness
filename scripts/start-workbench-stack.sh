#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

"$ROOT/scripts/start-api-server.sh"
"$ROOT/scripts/start-v2-server.sh"

echo "workbench stack running"
echo "  UI:  http://${BENCHMARK_CONSOLE_V2_HOST:-127.0.0.1}:${BENCHMARK_CONSOLE_V2_PORT:-4173}"
echo "  API: http://${BENCHMARK_CONSOLE_API_HOST:-127.0.0.1}:${BENCHMARK_CONSOLE_API_PORT:-19181}"

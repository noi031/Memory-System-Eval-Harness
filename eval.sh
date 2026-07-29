#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV="$ROOT/.venv"
STAMP="$VENV/.requirements.sha256"

if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV"
fi

REQ_HASH="$("$PYTHON_BIN" -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$ROOT/requirements.txt")"
INSTALLED_HASH="$(cat "$STAMP" 2>/dev/null || true)"
if [[ "$REQ_HASH" != "$INSTALLED_HASH" ]]; then
  "$VENV/bin/python" -m pip install -r "$ROOT/requirements.txt"
  printf '%s\n' "$REQ_HASH" > "$STAMP"
fi

exec "$VENV/bin/python" "$ROOT/eval.py" "$@"

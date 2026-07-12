# Memory Benchmark Workbench

Unified benchmark workbench for LoCoMo, LongMemEval, and HotpotQA workflows.

> The refactored workbench is published on the `v2` branch. It is independent
> from the legacy UI on `main`.

This repository now bundles the runnable API server, task orchestration layer,
and the current OpenViking / EchoMemory import and QA runners alongside the new
frontend workbench. It still does not include benchmark datasets, model
credentials, injected memories, or run artifacts.

## EchoMemory HTTP Black-box Quick Start

The LoCoMo EchoMemory path is HTTP-only. The harness does not import the
EchoMemory Python SDK, query Neo4j, or read the EchoMemory server workspace for
QA evidence.

```bash
git clone --branch v2 \
  https://github.com/tech-innovation-group/Memory-System-Eval-Harness.git
cd Memory-System-Eval-Harness

cp .env.example .env.local
# Edit .env.local, then:
set -a
source .env.local
set +a

bash scripts/start-workbench-stack.sh
```

Open <http://127.0.0.1:4173/>, select `EchoMemory`, and configure the
EchoMemory HTTP base URL. Import `conv-30` before launching QA.

For the exact HTTP contract, authentication behavior, CLI commands, strict
black-box guarantees, and the complete 81-question workflow, read
[README_ECHOMEMORY_BLACKBOX.md](README_ECHOMEMORY_BLACKBOX.md).

## Architecture

```text
Browser
  -> Memory Benchmark Workbench (default: 127.0.0.1:4173)
  -> /api/* proxy
  -> bundled benchmark API (default: 127.0.0.1:19181)
  -> in-repo task orchestration + Python runners
```

The workbench UI still runs through `dev_server.py`, but the repository now
includes the actual backend service and Python runner code that executes
imports, QA runs, judging, and report generation.

## Requirements

- Python 3.9+
- Node.js 18+ for validation scripts
- Python environment with the dependencies required by the bundled backend
- EchoMemory / OpenViking runtime dependencies if you want to execute those
  backends against real services or SDK workspaces

No npm install is required. Browser code uses native ES modules and validation
scripts use Node.js built-ins only.

## Start

```bash
git clone <repository-url>
cd memory-benchmark-workbench

bash scripts/start-workbench-stack.sh
```

Open <http://127.0.0.1:4173/>.

Status and stop commands:

```bash
bash scripts/status-api-server.sh
bash scripts/status-v2-server.sh
bash scripts/stop-api-server.sh
bash scripts/stop-v2-server.sh
```

Runtime files are written under `.runtime/` and are ignored by Git.

To start only the API server:

```bash
bash scripts/start-api-server.sh
```

If the default ports are already in use, override them explicitly:

```bash
BENCHMARK_CONSOLE_API_PORT=19183 \
BENCHMARK_CONSOLE_API_BASE=http://127.0.0.1:19183 \
BENCHMARK_CONSOLE_V2_PORT=4174 \
  bash scripts/start-workbench-stack.sh
```

For foreground development:

```bash
python3 server.py --host 127.0.0.1 --port 19181

python3 dev_server.py \
  --host 127.0.0.1 \
  --port 4173 \
  --api-base http://127.0.0.1:19181
```

## Validate

Static and smoke validation:

```bash
node scripts/validate.mjs
```

Runtime validation with the bundled API service running:

```bash
BENCHMARK_CONSOLE_API_BASE=http://127.0.0.1:19181 \
BENCHMARK_CONSOLE_V2_ORIGIN=http://127.0.0.1:4174 \
  node scripts/check-v2-runtime.mjs --start-server
```

## Repository Layout

- `index.html`: single HTML entrypoint for the new workbench UI
- `app.js`: browser application entrypoint
- `styles.css`: single design-system and component stylesheet
- `dev_server.py`: static server and `/api/*` reverse proxy for the new UI
- `server.py`: bundled benchmark API server and task controller
- `memory/`: backend services, task specs, reporting, adapters, and plugins
- `benchmark/locomo/`: packaged LoCoMo benchmark entrypoints
- `scripts/`: runnable import/QA/judge/report scripts used by the backend
- `web/`: backend package manifest, API helpers, and legacy compatibility UI assets
- `src/action/`: API payload and workflow actions
- `src/render/`: benchmark-specific rendering
- `src/form-readers.js`: form-to-payload boundary
- `src/benchmark-registry.js`: benchmark registration and run ownership
- `scripts/check-v2.mjs`: architecture, syntax, and workflow smoke checks
- `scripts/check-v2-runtime.mjs`: live static/API proxy check
- `docs/api-contract.md`: bundled API surface

## Supported Workflows

- LoCoMo: memory import, HTTP black-box QA, judge, retry, report, and diagnostics
- LongMemEval: import, QA, official-style summary, and report artifacts
- HotpotQA: import, QA, answer/supporting-fact metrics, and report artifacts
- EchoMemory and OpenViking backend selections

For LoCoMo, EchoMemory QA and retry tasks use the EchoMemory HTTP API only.
EchoMemory import also uses its HTTP session APIs. The workbench does not query
Neo4j directly, read workspace memory files, or
inject platform-generated graph/atom evidence. Optional `overview.md`
enrichment is also black-box: session URIs are derived from the native search
response and read only through EchoMemory HTTP `/fs/read`. Any graph retrieval
used by a run is controlled internally by the EchoMemory service.

## Bundled Backend Layout

- New workbench UI: `index.html`, `app.js`, `styles.css`, `src/`
- Bundled API server: `server.py`, `memory/`, `web/api/`
- Runnable LoCoMo / EchoMemory / OpenViking scripts: `scripts/`, `benchmark/locomo/`
- Backend compatibility assets kept in a single directory: `web/static/`

The compatibility assets are not the primary UI anymore. They are vendored so
the API server keeps its current contract and self-check routes while the new
workbench becomes the main entrypoint.

## Data and Secrets

Do not commit:

- API keys or model-provider credentials
- Neo4j credentials
- benchmark datasets with restricted distribution terms
- injected memory workspaces
- run outputs, recall logs, reports, or archives
- local account and task state

The repository intentionally excludes all of these categories.

## Publishing Checklist

Before publishing:

1. Choose and add an approved open-source license.
2. Review the vendored backend code and remove any machine-specific defaults you
   do not want to expose publicly.
3. Run `node scripts/validate.mjs`.
4. Start `scripts/start-api-server.sh` and `scripts/start-v2-server.sh`, then
   smoke `/api/config`, `/api/tasks`, and `/api/readiness`.
5. Run the secret and artifact boundary checks in `scripts/validate.mjs`.
6. Review `git status` and commit only source, tests, and documentation.

## Status

This repository now contains the workbench frontend plus the currently used
benchmark backend and runner code. The integration is transitional: the backend
is vendored largely intact so the repo is runnable in one place before deeper
refactoring.

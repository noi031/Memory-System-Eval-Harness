# EchoMemory HTTP Black-box LoCoMo Guide

This guide shows how to connect an EchoMemory service to the `v2` benchmark
workbench without importing or modifying EchoMemory source code, and how to run
the complete LoCoMo `conv-30` evaluation.

> [!IMPORTANT]
> Do not change EchoMemory source code when producing a black-box benchmark
> result. Run EchoMemory as an independent, unmodified service and configure
> this harness only through HTTP endpoints, command-line options, and
> environment variables documented here.
>
> In particular, do not patch EchoMemory indexing limits, retrieval ranking,
> graph selection, extraction prompts, API response fields, or memory files to
> improve a run. If EchoMemory source code must be changed for an experiment,
> record the exact commit and patch, label the result as a modified-backend
> experiment, and do not compare it directly with the black-box baseline.

## What Black-box Means

For EchoMemory LoCoMo import and QA, the harness communicates with EchoMemory
through HTTP only:

```text
LoCoMo dataset
  -> benchmark harness
  -> EchoMemory HTTP session APIs
  -> EchoMemory-owned memory extraction and indexing
  -> EchoMemory HTTP retrieval API
  -> answer model
  -> LoCoMo judge
```

The harness does not:

- modify or patch the EchoMemory repository
- import the EchoMemory SDK
- read EchoMemory memory files for QA evidence
- query Neo4j directly
- inject platform-generated graph, atom, message, or timeline evidence
- silently fall back to local workspace evidence

Native graph retrieval is allowed because it runs inside EchoMemory. The
harness only receives the final items returned by EchoMemory HTTP.

Optional overview enrichment is still black-box. When explicitly enabled, the
harness derives session URIs from native search results and calls EchoMemory
HTTP `GET /fs/read`; it never opens `overview.md` locally.

## Required EchoMemory HTTP Contract

The EchoMemory service must provide:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Service readiness |
| `POST` | `/api/auth/tenants` | Optional auth bootstrap |
| `POST` | `/api/auth/tenants/{tenant}/users` | Optional auth bootstrap |
| `POST` | `/api/auth/tenants/{tenant}/users/{user}/key` | Optional auth bootstrap |
| `POST` | `/api/sessions/open` | Create a LoCoMo session |
| `POST` | `/api/sessions/{session}/messages` | Append conversation messages |
| `POST` | `/api/sessions/{session}/commit` | Trigger extraction and indexing |
| `GET` | `/api/sessions/{session}/commits/{commit}` | Poll commit status |
| `GET` | `/api/sessions/{session}/history` | Verify imported messages |
| `POST` | `/api/retrieval/search` | Retrieve native EchoMemory evidence |
| `GET` | `/fs/read?uri=...` | Optional overview/abstract read |

Authenticated requests use:

```http
X-Auth-Key: <echomemory-auth-key>
```

If `--echomem-auth-key` is omitted, the harness uses the auth bootstrap
endpoints, caches the issued key in
`<client-state>/.echomem_http_auth_keys.json`, and sets file mode `0600`.
Provide an explicit key when the EchoMemory deployment disables auth
bootstrap.

## Prerequisites

- Python 3.9 or newer
- Node.js 18 or newer for repository validation
- a running EchoMemory HTTP service
- an OpenAI-compatible answer/judge model endpoint
- the LoCoMo dataset in its original JSON format

The dataset, credentials, EchoMemory workspace, injected memories, and run
outputs are intentionally not included in this repository.

## Configure

```bash
cp .env.example .env.local
```

Set at least:

```bash
ECHOMEM_BASE_URL=http://127.0.0.1:18080
ECHOMEM_AUTH_KEY=

LOCOMO_DATASET=/absolute/path/to/locomo10.json
LOCOMO_CLIENT_STATE=/absolute/path/to/locomo-conv30-client-state
LOCOMO_ACCOUNT=locomo-conv30
LOCOMO_USER_ID=default
LOCOMO_AGENT_ID=default

ANSWER_BASE_URL=https://provider.example.com/compatible-mode/v1
ANSWER_MODEL=your-answer-model
ANSWER_TOKEN=your-token

JUDGE_BASE_URL=https://provider.example.com/compatible-mode/v1
JUDGE_MODEL=your-judge-model
JUDGE_TOKEN=your-token
```

Load the variables:

```bash
set -a
source .env.local
set +a
```

Check the service and dataset before importing:

```bash
curl -fsS "$ECHOMEM_BASE_URL/health"
test -f "$LOCOMO_DATASET"
mkdir -p "$LOCOMO_CLIENT_STATE"
```

`LOCOMO_CLIENT_STATE` is harness-side state for HTTP authentication and run
metadata. It is not used to read EchoMemory evidence.

## Start the Workbench

```bash
bash scripts/start-workbench-stack.sh
```

Open:

- UI: <http://127.0.0.1:4173/>
- harness API: <http://127.0.0.1:19181/>

In the UI:

1. Select `EchoMemory` as the memory backend.
2. Set transport to `HTTP`.
3. Set the EchoMemory base URL and optional auth key.
4. Select the LoCoMo dataset file.
5. Use one fresh account/client-state directory per formal run.
6. Import `conv-30`.
7. Run one QA smoke question.
8. Run all questions and judge the completed CSV.

## CLI: Import conv-30

Create a run directory:

```bash
RUN_DIR="$PWD/runs/locomo-conv30-http-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"
```

Import all `conv-30` sessions:

```bash
python3 benchmark/locomo/echomemory/import_to_echomem.py \
  --dataset "$LOCOMO_DATASET" \
  --out-dir "$RUN_DIR/import" \
  --echomem-transport http \
  --echomem-base-url "$ECHOMEM_BASE_URL" \
  --workspace "$LOCOMO_CLIENT_STATE" \
  --account "$LOCOMO_ACCOUNT" \
  --user-id "$LOCOMO_USER_ID" \
  --agent-id "$LOCOMO_AGENT_ID" \
  --sample conv-30 \
  --session-mode locomo \
  --import-wait-mode full \
  --commit-wait-s 300 \
  --commit-call-timeout-s 300 \
  --flush-call-timeout-s 600 \
  --flush-attempts 3
```

When an explicit key is required, add:

```bash
--echomem-auth-key "$ECHOMEM_AUTH_KEY"
```

The main import artifact is:

```text
$RUN_DIR/import/echomemory_import_summary.json
```

Do not start formal QA until all expected sessions are committed and the
EchoMemory service reports retrieval readiness.

## CLI: One-question Smoke Test

This first run verifies retrieval, answer generation, recall logging, and the
judge without waiting for all 81 questions:

```bash
python3 benchmark/locomo/echomemory/run_eval.py \
  --dataset "$LOCOMO_DATASET" \
  --out-dir "$RUN_DIR/qa-smoke" \
  --sample conv-30 \
  --questions conv-30_qa0 \
  --echomem-transport http \
  --echomem-base-url "$ECHOMEM_BASE_URL" \
  --workspace "$LOCOMO_CLIENT_STATE" \
  --account "$LOCOMO_ACCOUNT" \
  --user-id "$LOCOMO_USER_ID" \
  --agent-id "$LOCOMO_AGENT_ID" \
  --identity-mode sample_question \
  --prompt-mode one_shot \
  --retrieval-mode search \
  --evidence-policy blackbox \
  --retrieval-source-mode echo_http_native \
  --top-k 25 \
  --score-threshold 0.1 \
  --qa-memory-injection \
  --no-search-overview-enrichment \
  --no-vikingboat-tool-loop \
  --no-initial-tool-prefetch \
  --answer-base-url "$ANSWER_BASE_URL" \
  --answer-model "$ANSWER_MODEL" \
  --answer-token "$ANSWER_TOKEN" \
  --judge-base-url "$JUDGE_BASE_URL" \
  --judge-model "$JUDGE_MODEL" \
  --judge-token "$JUDGE_TOKEN" \
  --judge-every 1 \
  --qa-parallelism 1 \
  --judge-parallel 1
```

Add `--echomem-auth-key "$ECHOMEM_AUTH_KEY"` when using an explicit key.

Inspect:

```text
$RUN_DIR/qa-smoke/echomemory_memory_qa_results.csv
$RUN_DIR/qa-smoke/q001.recall.json
$RUN_DIR/qa-smoke/summary.json
```

The recall JSON must show:

```json
{
  "evidence_policy": "blackbox",
  "evidence_origin": "echomemory_http_api",
  "platform_evidence_injection_enabled": false
}
```

## CLI: Full 81-question Run

Run the same command without `--questions`, and use parallel QA/judging:

```bash
python3 benchmark/locomo/echomemory/run_eval.py \
  --dataset "$LOCOMO_DATASET" \
  --out-dir "$RUN_DIR/qa-full" \
  --sample conv-30 \
  --echomem-transport http \
  --echomem-base-url "$ECHOMEM_BASE_URL" \
  --workspace "$LOCOMO_CLIENT_STATE" \
  --account "$LOCOMO_ACCOUNT" \
  --user-id "$LOCOMO_USER_ID" \
  --agent-id "$LOCOMO_AGENT_ID" \
  --identity-mode sample_question \
  --prompt-mode one_shot \
  --retrieval-mode search \
  --evidence-policy blackbox \
  --retrieval-source-mode echo_http_native \
  --retrieval-ranker score \
  --top-k 25 \
  --score-threshold 0.1 \
  --memory-budget-chars 6000 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000 \
  --qa-memory-injection \
  --no-search-overview-enrichment \
  --no-vikingboat-tool-loop \
  --no-initial-tool-prefetch \
  --no-answer-refinement \
  --answer-base-url "$ANSWER_BASE_URL" \
  --answer-model "$ANSWER_MODEL" \
  --answer-token "$ANSWER_TOKEN" \
  --judge-base-url "$JUDGE_BASE_URL" \
  --judge-model "$JUDGE_MODEL" \
  --judge-token "$JUDGE_TOKEN" \
  --judge-every 10 \
  --qa-parallelism 4 \
  --judge-parallel 4 \
  --timeout-s 180 \
  --question-timeout-s 600
```

Add the explicit auth-key argument if required.

The CLI rejects platform-side local evidence options in EchoMemory black-box
mode. The following remain disabled:

- local session summaries
- local atoms
- local messages
- local timeline hints
- local memory artifacts
- segment readback
- precision session readback
- platform Neo4j evidence

## Optional HTTP Overview Test

To test EchoMemory session summaries through HTTP, replace:

```bash
--no-search-overview-enrichment
```

with:

```bash
--search-overview-enrichment \
--overview-budget-chars 3000
```

This only calls EchoMemory `/fs/read`. The per-question recall JSON records
`overview_http_read_count`, `overview_http_hit_count`, and injected overview
characters.

## Judge, Statistics, and HTML Report

If QA was run without periodic judging:

```bash
python3 scripts/local_judge.py \
  --input "$RUN_DIR/qa-full/echomemory_memory_qa_results.csv" \
  --base-url "$JUDGE_BASE_URL" \
  --model "$JUDGE_MODEL" \
  --token "$JUDGE_TOKEN" \
  --parallel 4 \
  --only-pending
```

Print the final statistics:

```bash
python3 scripts/local_stats.py \
  --input "$RUN_DIR/qa-full/echomemory_memory_qa_results.csv"
```

Generate a shareable HTML report:

```bash
python3 scripts/generate_html_report.py \
  "$RUN_DIR/qa-full/echomemory_memory_qa_results.csv" \
  --output "$RUN_DIR/qa-full/locomo-conv30-report.html" \
  --name "EchoMemory HTTP Black-box LoCoMo conv-30"
```

## Required Output Audit

For a strict black-box run, verify `summary.json`:

```bash
jq '{
  rows,
  accuracy,
  evidence_policy,
  evidence_origin,
  echomem_transport,
  retrieval_source_mode,
  search_overview_enrichment_enabled,
  strict_blackbox_augmentation_rows,
  transport_audit: .echomemory_transport_audit
}' "$RUN_DIR/qa-full/summary.json"
```

Expected properties:

- `rows` is `81`
- `evidence_policy` is `blackbox`
- `evidence_origin` is `echomemory_http_api`
- `echomem_transport` is `http`
- `retrieval_source_mode` is `echo_http_native`
- `strict_blackbox_augmentation_rows` is `0`
- `local_workspace_evidence_reads` is `0`
- `platform_neo4j_queries` is `0`

Every question writes `qNNN.recall.json`, containing the native results sent to
the answer model and flags showing whether optional HTTP overview enrichment
was used.

## Direct Retrieval Probe

With an existing auth key:

```bash
curl -sS \
  -H "Content-Type: application/json" \
  -H "X-Auth-Key: $ECHOMEM_AUTH_KEY" \
  -X POST "$ECHOMEM_BASE_URL/api/retrieval/search" \
  -d '{
    "query": "When did Jon lose his job as a banker?",
    "agent_id": "default",
    "session_id": null,
    "limit": 25,
    "include_explain": false,
    "include_debug": true
  }' | python3 -m json.tool
```

The harness expects native evidence under `result.items`. Typical fields are
`source_uri`, `evidence_uri`, `source`, `kind`, `confidence`, and `content`.

## Troubleshooting

### `EchoMemory HTTP unavailable`

Verify the URL and service:

```bash
curl -v "$ECHOMEM_BASE_URL/health"
```

### Authentication bootstrap fails

Supply an explicit key:

```bash
export ECHOMEM_AUTH_KEY=...
```

Then add `--echomem-auth-key "$ECHOMEM_AUTH_KEY"` to import and QA.

### Retrieval is empty

Check that import completed under the same EchoMemory tenant/user represented
by the auth key. Inspect the import summary and a direct retrieval probe before
changing QA prompts.

### Accuracy is unexpectedly zero

Inspect `qNNN.recall.json` first. Separate:

1. no returned items
2. returned items from the wrong tenant
3. relevant facts absent from returned evidence
4. relevant facts present but answer extraction failed
5. judge endpoint or parser failures

Do not add local files or direct Neo4j queries to hide a broken HTTP retrieval
path. Such a run is no longer a black-box EchoMemory evaluation.

## Security

Never commit or share:

- `.env.local`
- `.echomem_http_auth_keys.json`
- provider API keys
- EchoMemory server workspaces
- `runs/`, `outputs/`, or `artifacts/`
- raw benchmark conversations when distribution is restricted
- recall logs or reports before reviewing them for private data

See [SECURITY.md](SECURITY.md) for the repository disclosure boundary.

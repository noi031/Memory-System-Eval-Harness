# Standalone Release Checklist

## Repository Boundary

- [x] Repository is outside the legacy evaluation harness.
- [x] Bundled backend code now lives in this repository.
- [x] Bundled backend static assets are collected under `web/static/`.
- [x] Browser imports resolve inside this repository.
- [x] Runtime state is written to ignored `.runtime/`.
- [x] Datasets, run outputs, injected memories, and credentials are excluded.

## Runtime

- [x] Static frontend can start independently.
- [x] Bundled API server can start independently.
- [x] `/api/*` is proxied to the bundled or configured API base.
- [x] LoCoMo, LongMemEval, and HotpotQA controls remain available.
- [x] Secondary logs and history sections remain collapsed by default.

## Validation

```bash
node scripts/validate.mjs
```

With the bundled API running:

```bash
bash scripts/start-api-server.sh

BENCHMARK_CONSOLE_V2_ORIGIN=http://127.0.0.1:4174 \
  node scripts/check-v2-runtime.mjs --start-server
```

## Before Public Release

- [ ] Add an approved open-source license.
- [ ] Review vendored backend code for machine-specific defaults and trim them before release.
- [ ] Add CI for `node scripts/validate.mjs`.
- [ ] Perform a final credential and artifact scan.

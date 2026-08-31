# k6 real EchoMem load

`echomem_stress.js` sends real HTTP requests to EchoMem. It does not mock the
model, vector store, or HTTP service. The script is deliberately endpoint
configurable because deployments may expose different session routes.

```bash
ECHOMEM_BASE_URL=http://127.0.0.1:8010 \
ECHOMEM_AUTH_KEY="$ECHOMEM_AUTH_KEY" \
ECHOMEM_SESSION_ID="$SESSION_ID" \
ECHOMEM_TEST_DURATION=10m ECHOMEM_TEST_SEARCH_RPS=8 \
k6 run --summary-export results/k6-native-summary.json \
  stress/k6/echomem_stress.js
```

在需要严格控制低负载到达率时，可使用单 VU 定速模式。该模式会在每次
Search 后按 `1 / ECHOMEM_TEST_SEARCH_RPS` 秒等待，实际速率会随服务响应时间
下降，但不会把 k6 的全局 `K6_*` 配置误混入场景：

```bash
ECHOMEM_TEST_EXECUTOR=paced-vus \
ECHOMEM_TEST_VUS=1 \
ECHOMEM_TEST_DURATION=60s \
ECHOMEM_TEST_SEARCH_RPS=4 \
k6 run stress/k6/echomem_stress.js
```

For a real Commit stream, provide a prepared session and set
`ECHOMEM_TEST_COMMIT_EVERY_S`. The output is retained as `k6-summary.json`; reconcile it
with the Python request evidence using:

```bash
python3 stress/echomem/k6_reconcile.py \
  --k6-summary results/k6-native-summary.json \
  --runner-dir results/stress/run-01 \
  --out results/stress/run-01/k6-reconciliation.json
```

The reconciler fails closed when the k6 summary or runner evidence is missing.

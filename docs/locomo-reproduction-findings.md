# LoCoMo Reproduction Findings

This note keeps the `conv-30` baselines comparable. A score is only a valid
comparison when the dataset hash, 19-session memory provenance, QA profile,
answer endpoint, Judge endpoint, and model request contract all match.

## What The Numbers Mean

| Run | Memory | Answer/Judge route | Result |
| --- | --- | --- | --- |
| Historical `legacy-77` reference, July 13, 2026 | `head_clean`, 19 sessions | DashScope compatible route | `63/81` (`77.78%`) |
| Same historical answers re-judged on the current DeepSeek route | `head_clean`, 19 sessions | current DeepSeek Judge | `61/81` (`75.31%`) |
| Historical `test-best`, July 27, 2026 | `head_clean`, 19 sessions | DashScope compatible route | `56/81` (`69.14%`) |
| Current migrated `test-best` run | `head_clean`, 19 sessions | DeepSeek answer and Judge routes | `64/81` (`79.01%`) |
| v2 advertised `test-best` reference, July 18, 2026 | PR125 memory, not `head_clean` | local compatible route | `69/81` (`85.19%`) |
| Current `legacy-77` run, July 29, 2026 | PR125 memory, filtered to the original 19 sessions | DeepSeek official answer and Judge route | `68/81` (`83.95%`) |

The `69/81` result is not a valid target for the current `head_clean`
account. It is a different memory state. The current `test-best` result is
higher than the same-memory historical `test-best` result; it does not
reproduce the PR125-memory score.

The July 29 `68/81` run is a deliberately different QA contract:
PR125 memory with the `legacy-77` prompt and loop. It is one question below
the historical PR125 `test-best` score, but it is not a same-profile rerun.
Against the historical PR125 verdicts, four questions improved and five
regressed.

## Confirmed Causes Of Drift

1. **Memory provenance was mixed.** `head_clean` and PR125 memory were
   previously described as one baseline.
2. **The provider route changed.** The historical `head_clean` answer and
   Judge calls used the DashScope compatible endpoint. The current run uses
   the DeepSeek endpoint. A model string alone does not identify the served
   model or backend route.
3. **Judge decisions drift.** Regrading the historical `63/81` answers on the
   current Judge changed four verdicts and produced `61/81`.
4. **Temperature remains stochastic.** `test-best` explicitly sends
   `temperature=0.7`; repeated runs can choose different tool paths and answer
   wording.
5. **The migration had behavior deviations.** Same-turn tool calls were
   executed serially, and several tool parameter descriptions were not the v2
   protocol text. Both are now covered by regression tests.
6. **Reused accounts can accumulate unrelated sessions.** The PR125
   workspace contained a twentieth merged session written on July 28, 2026.
   `--memory-session-prefix` now limits search and filesystem tools to the
   intended session-id namespace; the July 29 run records `19/19` matched
   provenance.

## Reproducibility Artifacts

Every new VikingBot trace records:

- requested endpoint, model, max tokens, and temperature mode;
- request payload SHA-256;
- provider response model/id/fingerprint when returned;
- exact tool protocol SHA-256;
- profile source and settings.

QA resume manifests also hash the prompt, tool, runtime, and answer-cleanup
implementation files. Changing those files invalidates reuse of old QA rows
instead of silently mixing behavior across runs.

Use the existing-memory path for the first comparison:

```bash
./eval.sh locomo \
  --sample conv-30 \
  --reuse-memory-account \
  --memory-session-prefix echomem-locomo-conv-30- \
  --qa-profile test-best
```

Do not compare that result with a run whose `summary.json` has a different
`memory_identity`, `memory_provenance`, endpoint, or `qa_profile`.

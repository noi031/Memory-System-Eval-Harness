## Summary

Describe the change and the memory-evaluation workflow it affects.

Current backend scope: `EchoMemory`.

## Scope

- [ ] LoCoMo workflow
- [ ] EchoMem / EchoMemory backend
- [ ] VikingBot agent
- [ ] Report export or run analysis
- [ ] CLI or documentation

## Safety Checklist

- [ ] I did not add `.env`, `.env.local`, `judge.conf`, API keys, bearer tokens, raw `runs/`, or memory workspaces.
- [ ] EchoMemory remains the only registered memory backend.
- [ ] Reuse-memory QA paths do not open, add, commit, or delete memory.
- [ ] I ran the unit tests and `python scripts/backend_doctor.py --format json`, or explained why they could not run.
- [ ] Any attached report, screenshot, or log is redacted.

## Verification

Paste the relevant safe output:

```text
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/backend_doctor.py --format json
```

## Notes

Mention any known follow-up work, model/provider dependency, or benchmark limitation.

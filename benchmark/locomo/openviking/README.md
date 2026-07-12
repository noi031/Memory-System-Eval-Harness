# LoCoMo OpenViking Benchmark

This directory is aligned to:

`<openviking-source>/benchmark/locomo/openviking`

Local entrypoint mapping:

- `import_to_ov.py` -> `scripts/openviking_locomo_import.py`
- `run_eval.py` -> `scripts/openviking_memory_qa.py`
- `judge.py` -> `scripts/local_judge.py`
- `stat_judge_result.py` -> `scripts/local_stats.py`
- `locomo_prompts.py` -> prompt helpers re-exported from `scripts/openviking_memory_qa.py`

The wrappers exist so LoCoMo OpenViking benchmark code can use the same
`benchmark/locomo/openviking/*` layout as upstream while preserving the current
local implementation and task wiring.

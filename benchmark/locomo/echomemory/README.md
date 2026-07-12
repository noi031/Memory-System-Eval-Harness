# LoCoMo EchoMemory Benchmark

This directory aligns EchoMemory's local LoCoMo entrypoints into a dedicated
benchmark folder, following the same structure used for OpenViking and the
upstream-style `benchmark/locomo/<backend>/` layout.

Local entrypoint mapping:

- `import_to_echomem.py` -> `scripts/echomemory_locomo_import.py`
- `run_eval.py` -> `scripts/echomemory_memory_qa.py`
- `judge.py` -> `scripts/local_judge.py`
- `stat_judge_result.py` -> `scripts/local_stats.py`
- `locomo_prompts.py` -> prompt helpers shared from `scripts/openviking_memory_qa.py`

These wrappers keep the current implementation intact while giving EchoMemory
its own LoCoMo benchmark folder for task wiring, retries, and future backend-
specific additions.

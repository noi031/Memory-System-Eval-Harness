# v2 Source Provenance

The migration uses three different evidence classes. They must not be mixed.

## v2 Tracked Head

- Repository: `/Users/chx/Code/memory-benchmark-workbench`
- Branch: `v2`
- Audited commit: `a146a246c2fcce128229d19e05c87228affd829d`

Tracked files at this commit are the authority for the latest committed v2
CLI, dataset, metric, report, and recovery behavior.

The `vikingbot-v2-head` profile is pinned to this commit and draws from:

- settings: `memory/vikingboat_alignment.py`
- EchoMemory-only prompt: `scripts/echomemory_qa_prompting.py`
- read-only tool protocol: `scripts/echomemory_qa_tools.py`
- iterative runtime: `scripts/echomemory_memory_qa.py`

It exposes `memory_search`, `memory_read_many`, `memory_list`, `memory_grep`,
and `memory_glob` through EchoMemory HTTP only. It carries no historical score
claim.

## Historical VikingBot Source

- Prompt and loop commit:
  `1f027927d2557dc67948499f9cb975bb664219df`
- Source path: `scripts/openviking_memory_qa.py`
- Profile introduction commit:
  `c6bf307243866d02117bc71d05803a3770c5fb1c`
- Profile source path: `scripts/echomemory_evaluation_profiles.py`

The separate `vikingbot-historical-75` profile preserves the historical
three-message prompt layout,
workspace bootstrap, iterative model/tool loop, duplicate-query guard, and
follow-up reflection message. The runtime adaptation replaces OpenViking tool
names and calls with read-only EchoMemory HTTP `memory_search` and
`memory_read_many` operations.

This adaptation does not add an OpenViking backend, SDK, configuration, or
runtime dependency.

## Actual Head-Clean `legacy-77` Run

- Reference run:
  `runs/head_clean_top25_http_messages_full_20260713`
- Memory workspace:
  `/Users/chx/echomem_eval_matrix_20260712/head_clean`
- Account: `locomo-conv30-headclean-matrix-20260712`
- Provenance: 19 LoCoMo sessions
- Result on the original Judge route: `63/81` (`77.78%`)

The `legacy-77` profile vendors the persisted prompt snapshot and the actual
run settings, including question-time context, group-session layout, raw final
question, provider-default answer temperature, Top-25 search, the historical
`target_uri` schema hint, and no URI or query deduplication.

The profile source metadata includes the prompt SHA-256 and reference artifact.
Later HTTP URI correctness fixes are retained because they prevent invalid
session reads without adding hidden evidence.

## Uncommitted v2 Worktree

The v2 checkout contains many modified and untracked files, including dated
experiments and later local utilities. They are not treated as committed v2
behavior merely because they exist on disk.

An uncommitted file is migrated only when:

1. it implements a reusable CLI capability that is still required;
2. its behavior is independently inspected and tested; and
3. its destination follows `benchmarks/<dataset>/`, `agents/<agent>/`,
   `backends/<backend>/`, or a genuinely backend-neutral `shared/` boundary.

OpenViking integration, web UI code, workspace inspectors, and dated
experiment-specific scripts remain excluded.

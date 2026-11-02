# Task 6 verification report — AgentWire benchmark CLI

## Result

Task 6 is implemented and committed on branch `agentcodec-agent-communication-design`.

Commit: `e700ad543a560fc244b7ac71c6c05f6b04868878` (`feat: add AgentWire benchmark CLI`).

Only `src/agentcodec/cli.py` and `tests/test_agentwire_cli.py` were committed. The pre-existing untracked `docs/superpowers/plans/2026-10-24-agent-communication-protocol.md` was not staged or changed.

## Implementation

Registered the opt-in `benchmark-agentwire` command on the existing Typer app. It loads only the fixed `bench/agentwire/exchanges.v1.jsonl` and `bench/agentwire/dictionary.v1.json` fixtures, exposes only the repository's `o200k_base` and `cl100k_base` local tokenizer profiles, and independently filters `--instance-uses` and `--corpus-passes` from the permitted counts `1,10,50`. `--modes` accepts only `gateway` and `model_direct`; defaults include both modes and all reuse counts.

The command writes one versioned, redacted `report.json` into an absent or empty output directory. It rejects caller corpus/dictionary paths, invalid options, nonempty/special/symlink/colliding output paths, and never overwrites existing files. The CLI import does not initialize tiktoken, read either fixture, or access the network. Missing local tokenizer data yields `cannot_compare`; there is no download fallback. Existing commands and encoder defaults were left unchanged.

## Verification

All pytest runs used the pre-existing interpreter `/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python` from the target worktree as the current worktree's `.venv/bin/python` did not exist. No dependencies were installed or downloaded.

- **TDD RED:** `/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_cli.py -q` — `5 failed, 10 passed`; the five required command behaviors failed because `benchmark-agentwire` did not yet exist. The cold-import and fail-closed cases passed against existing behavior.
- **Initial exact-cache run:** the first post-implementation run of the complete new test file used the available tokenizer cache and remained running without fresh output; the job was terminated at the environment's two-hour background-wait limit. It produced no completed test result and is not counted as passing verification. Subsequent integration verification forced an absent local cache, which is an expressly supported `cannot_compare` outcome, and blocked network access.
- **Targeted checks:** the 14 non-default-report cases passed (`14 passed, 1 deselected in 4.82s`); the real CLI report integration test with absent cache and blocked network passed (`1 passed in 8.14s`).
- **New CLI test file:** `/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_cli.py -q` — `15 passed in 10.47s`.
- **Existing CLI regression:** `/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_cli.py tests/test_adaptive_cli.py -q` — `174 passed in 9.00s`.
- **Complete project suite:** `/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest -q` — `1737 passed in 223.09s`.
- `git diff --check` and staged `git diff --cached --check` completed with no output. Staged file list was exactly `src/agentcodec/cli.py` and `tests/test_agentwire_cli.py`.
- Post-commit status showed the target branch with only the pre-existing plan file untracked; the report is ignored and was not committed.

## Concerns / limitations

The prescribed `.venv/bin/python` was absent in the target worktree, so the existing local interpreter from the sibling `agentcodec-mvp` worktree was used without modifying that environment. The exact-tokenizer-cache success path was not completed: the initial full run exceeded the wait limit. The no-cache `cannot_compare` path, all CLI behavior tests, old CLI regressions, and full project suite completed successfully. No Task 7 work was started.


## Fix round 1 — review findings

### Changes

- Replaced direct benchmark report serialization with a strict public-field allowlist at the top level, scenario level, row level, and nested comparison level. Hash fields/values, scenario/scope/user/corpus identifiers, arbitrary notes, message/dictionary/payload content, and absolute paths are excluded. A recursive black-box test injects those values into a complete report-shaped response and asserts no hash key/value or private sentinel reaches `report.json`.
- `--instance-uses` and `--corpus-passes` are now independently applied to their matching reuse scenarios before `run_agentwire_benchmark`; the runner-spy test observes `(10,)` for `same_message_instance` and `(50,)` for `whole_corpus_pass`. Default arguments still pass `(1, 10, 50)` to both units.
- Only `src/agentcodec/cli.py` and `tests/test_agentwire_cli.py` were changed and committed. No API/contract file was changed; the pre-existing untracked plan file was not staged. No Task 7 work was started.

### Verification

- **TDD RED:** the new hash/private-field and runner-subset tests failed on the pre-fix implementation as intended (`2 failed, 1 passed in 1.32s`); the default-full-range assertion already passed.
- **Focused GREEN:** hash allowlisting, selected runner tuples, and unchanged default tuples all passed (`3 passed in 0.92s`). The complete AgentWire CLI test file passed (`17 passed in 8.50s`); the existing no-cache integration case retained default inputs and produced the supported `cannot_compare` output.
- **Cached explicit-subset CLI:** both local tokenizer cache files were present; with network connections hard-blocked, ran the real CLI using `--tokenizers cl100k_base --instance-uses 1 --corpus-passes 1 --modes gateway`. It completed in `256.59s`, emitted 378 selected output rows, and included `exact_local_text` metrics. Every emitted row had `total_uses == 1`. The runner tuple tests separately verify the instance/corpus distinction; `--modes` remains an output selection because the benchmark API computes both modes internally.
- **Cached default/full CLI:** with both tokenizer caches present and network connections hard-blocked, ran the unchanged default command (both profiles, all `1,10,50` reuse counts, both modes) under a 600-second hard limit. It did not complete and was terminated with exit code `124`; there was no completed report. This path is **not passing**. The fix reduces actual work only for explicitly selected reuse subsets; it intentionally leaves the default full workload unchanged and does not resolve its runtime.
- **Existing CLI regression:** `tests/test_cli.py tests/test_adaptive_cli.py` — `174 passed in 8.00s`.
- **Complete project suite:** `pytest -q` — `1739 passed in 213.86s`.
- `git diff --check` and the staged diff check completed cleanly. The commit contains exactly `src/agentcodec/cli.py` and `tests/test_agentwire_cli.py`.

### Commit and caveat

Fix round 1 commit: `4566d3f1f909b247b32f21878de1a24d99d22bc4` (`fix: redact AgentWire CLI reports and honor reuse subsets`). The target branch is `agentcodec-agent-communication-design`; no push or merge was performed.

The existing `ReuseScenario` validator accepts only the canonical full tuple, while the CLI contract permits allowlisted subsets. To keep the requested scope limited to the CLI and tests, the CLI first constructs a validated full scenario, then uses `model_copy(update=...)` with the already-validated selected tuple before calling the runner. The runner receives and iterates the requested subset, as verified by both the runner spy and cached CLI run; however, Pydantic does not re-run the original `ReuseScenario` validator for that copy. A future API-level supported subset mechanism would remove this internal contract caveat, but no contract file was changed in this fix round.

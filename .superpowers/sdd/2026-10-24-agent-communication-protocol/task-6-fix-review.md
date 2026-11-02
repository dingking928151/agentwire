# Task 6 fix round 1 scoped re-review

Review range: `e700ad543a560fc244b7ac71c6c05f6b04868878..4566d3f1f909b247b32f21878de1a24d99d22bc4`.

## Finding verdicts

1. CLI report output included input-derived hashes and internal identifiers — **ADDRESSED**. The fix builds report JSON through a public-field allowlist; the recursive test injects hash/private sentinels and checks none appear in output.
2. Reuse options filtered rows only after benchmark execution — **ADDRESSED**. The fix passes the selected instance and corpus reuse tuples to the corresponding scenarios before invoking the runner; a runner spy asserts both selected tuples and unchanged full defaults.

## New breakage in fix diff

None reported.

## Out-of-scope observation / Minor

`src/agentcodec/cli.py` uses `model_copy(update=...)` to create selected-subset reuse scenarios even though the shared `ReuseScenario` validator accepts only the canonical full tuple; the copied instance bypasses revalidation. The re-reviewer suggests a future supported API-level subset mechanism or real-runner integration test, since current subset tests include a stub runner. This is non-blocking for Task 6 and is carried into the final whole-branch review.

## Test evidence

The re-reviewer could not run tests or smoke checks because its environment lacked the worktree `.venv`, pytest, and Typer. The coordinator then ran the available sibling-worktree interpreter from the target worktree:

`/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_cli.py tests/test_cli.py tests/test_adaptive_cli.py -q`

Result: `191 passed in 26.10s`.

The implementer's fix report additionally records the cached explicit-subset CLI completing with 378 rows and all rows at `total_uses == 1`; the unchanged default/full cached CLI still exceeded a 600-second limit and produced no report. That default path remains unverified, not passing.

## Verdict

Task 6 fix round 1: both Important findings addressed, no new Critical/Important breakage. Task 6 is closed. Task 7 may proceed. The Minor above is deferred to final branch review.
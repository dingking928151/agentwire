# Final fix wave — scoped independent re-review

Review range: `16ffb917d973fe748c68c2c1877b80edfa401e25..084a0e0f542959c834d9afb9abed3c50d6457be4`.

## Finding verdicts

1. **Gateway freeform transport comparison — ADDRESSED.** Reviewer confirmed the source-domain gateway comparison uses transport-token deltas and transport-only setup/per-use costs; freeform model-side and end-to-end fields remain unavailable; unknown costs fail closed. The diff includes relevant regression assertions.
2. **`--modes` and default runtime evidence — ADDRESSED.** Reviewer confirmed selected modes are passed to the runner before work begins, the runner executes only selected modes, and default mode order remains canonical. The final-fix report records the complete default cached run (353.54 seconds, 84 scenarios, 4,536 rows, networking blocked). The reviewer did not independently rerun that CLI run; it remains implementation-reported evidence.

## New breakage and out-of-scope observations

None identified in the reviewed fix diff; no out-of-scope observations.

## Independent verification limits

Reviewer independently inspected the code/diff, confirmed checkout HEAD `084a0e0f542959c834d9afb9abed3c50d6457be4`, and syntax-parsed the five changed Python files. Reviewer did not run tests because no existing pytest/project environment was available; no dependencies were installed. Test results and cached CLI output are from the implementation report, not independently reproduced by this reviewer.

## Verdict

**Fix round: PASS** for both original final-review findings; no new breakage reported.
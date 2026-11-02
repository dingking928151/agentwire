

Task 5 fix round 1 scoped re-review passed (`task-5-fix-review.md`; package `review-b2058d9..fedf0c4.diff`). Both Important findings addressed: gateway decode failure preserves known request/transport costs but sets unknown downstream/lifecycle measures to null and comparison to `cannot_compare`, excluding it from baseline comparison; incomplete cold protocol/dictionary history propagates to unknown and null lifecycle/setup costs. No new breakage. The reviewer carried forward only the non-blocking module-size Minor note; no re-review scope expansion. Reviewer did not rerun tests; implementer report says fix RED 3 failed/fix 3 passed, focused 25 passed, full 1722 passed. Test evidence remains implementer-reported.

## Task 5: complete

Implementation commit `b2058d98811b3b56f191dd3b8c27c3076a06a1bf`; fix round 1 `fedf0c422a08721b9241bc4a32eedf243a89d469`. Initial review’s two Important findings have been closed by TDD fix and fresh scoped re-review. Task 5 plan Steps 1–6 complete. Task 6 may now begin from base `fedf0c422a08721b9241bc4a32eedf243a89d469`; local/offline/no merge or push boundary remains.


## Task 6: dispatched

Base: `fedf0c422a08721b9241bc4a32eedf243a89d469`. Brief: `.superpowers/sdd/2026-10-24-agent-communication-protocol/task-6-brief.md`; report: `.superpowers/sdd/2026-10-24-agent-communication-protocol/task-6-report.md`. Scope: modify only `src/agentcodec/cli.py` and add `tests/test_agentwire_cli.py`. It consumes the closed Task 1–5 APIs and fixed synthetic fixtures. Preserve all existing commands/defaults; the new opt-in command is constrained to fixed in-repo synthetic benchmark inputs, a new/empty output directory and local tokenizer profiles; no user-provided corpus/dictionary path. Offline/local boundaries remain. Task 5 two Important findings closed by fix `fedf0c4` and scoped re-review; minor large-module note remains non-blocking. Do not start Task 7 until Task 6 review closes.


## Task 6 independent review: Important fix round 1

Review report `task-6-review.md`; package `review-fedf0c4..e700ad5.diff`. Two Important findings; Task 6 is not closed and Task 7 is blocked.

1. `src/agentcodec/cli.py:514-531` directly exports `source_hash`/`transport_hash` in scenario rows; the underlying `_domain_hash` includes namespace and dictionary identity digest (`benchmark.py:300-322`). This violates the brief's no-input-hash output boundary. Fix with an explicit public report field allowlist, excluding hashes and internal/raw identifiers, and regression asserting no hash fields/values in emitted report.
2. CLI always builds scenarios with all reuse counts then filters rows only after full `run_agentwire_benchmark` (`cli.py:496-510,514-524,552-556`). The explicit reuse flags therefore do not reduce actual work. Pass selected instance/corpus counts into their corresponding reuse scenario before invoking the benchmark; test that the runner receives only the selected counts, not just that output is filtered.

Cannot verify: exact tokenizer cache full CLI report test timed out without a result; do not claim it passes. Reviewer also did not rerun existing tests; implementer-reported CLI15/oldCLI174/full1737 remain unverified. TDD fix round 1 must address both findings, followed by fresh scoped re-review. Task 7 not started.

## Task 6 fix round 1: implementation complete, scoped re-review pending

Fix commit `4566d3f1f909b247b32f21878de1a24d99d22bc4` (`fix: redact AgentWire CLI reports and honor reuse subsets`) addresses both Important findings in `task-6-review.md`. Implementer report `task-6-report.md` records RED 2 failed/1 passed; focused GREEN 3 passed; CLI file 17 passed; old CLI regression 174 passed; full suite 1739 passed; cached explicit-subset CLI completed with 378 rows; unchanged default/full cached CLI still timed out at 600s and is not verified. Awaiting fresh scoped reviewer dispatched by teammate WLkMhxJJRJhvCvsnVV4aH5; do not start Task 7 until their verdict is received and recorded. Task 7 brief prepared at `task-7-brief.md` only; no implementation subtask dispatched. Current target branch remains local/offline, no merge/push; plan file remains intentional untracked coordinator document.

## Task 6: complete

Fix round 1 commit `4566d3f1f909b247b32f21878de1a24d99d22bc4` closed both Important findings. Fresh scoped re-review `e700ad5..4566d3f` confirmed both addressed, no new Critical/Important; reviewer could not execute tests in its environment. Coordinator independently ran `tests/test_agentwire_cli.py tests/test_cli.py tests/test_adaptive_cli.py` using the existing sibling-worktree Python interpreter: 191 passed in 26.10s. The default/full cached CLI run still exceeded the documented 600s limit with no report; it remains an explicit runtime limitation, not a pass. Deferred Minor: CLI uses `model_copy(update=...)` to bypass `ReuseScenario` validator for selected subsets and the subset behavior is runner-spy tested rather than through a real-runner integration test; carry this to final whole-branch review. Task 7 may begin.

## Task 7: prepared

Brief `.superpowers/sdd/2026-10-24-agent-communication-protocol/task-7-brief.md` generated from the approved plan. Task 6 fix/review closure established base `4566d3f1f909b247b32f21878de1a24d99d22bc4`. No Task 7 implementation has yet been dispatched.


## Task 7: started

Base: `4566d3f1f909b247b32f21878de1a24d99d22bc4`; brief: `.superpowers/sdd/2026-10-24-agent-communication-protocol/task-7-brief.md`; report: `.superpowers/sdd/2026-10-24-agent-communication-protocol/task-7-report.md`. Worktree/branch verified. Only scoped source deliverables are the new compatibility test, new protocol document, and README; no production behavior changes.

Task 7 Step 1 wrote three compatibility sentinels using hand-authored synthetic messages and temp inputs. Step 2 ran before documentation changes: the brief's `.venv/bin/python` is absent; the equivalent focused command used the already-existing `/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python` with `PYTHONPATH` explicitly targeting this worktree's `src`, and passed `3 passed in 0.99s`. Existing behavior satisfied all guards before any docs/README changes, so this is recorded as a compatibility guard, not a claimed RED. Ruling: do not create/install an environment; use only the existing local interpreter and target-worktree source — preserves offline/no-install boundary; cost if wrong: test dependency versions may differ from a per-worktree venv.


Task 7 Step 3 documented the exact AWP1 framing, schema/enums, canonical dictionary identity, exact references, receiver capabilities/limits, stable failures, gateway behavior, cost surfaces, and security/authority limits. README adds only an offline fixed-synthetic benchmark example and clearly separates manually authored freeform from fixed labelled-field control; no production code changed.

Task 7 Step 4 final verification after the review fix: focused suite **3 passed in 0.73s**; full suite **1742 passed in 228.59s**. Existing local o200k/cl100k cache files were confirmed before full pytest; no installs, downloads, external calls, or credentials. `git diff --cached --check` passed. The exact brief-local `.venv/bin/python` was absent; the documented ruling above was used with the pre-existing sibling venv and target `PYTHONPATH`.

Task 7 Step 5: pre-commit independent review caught the insufficient malformed-only AWP1 fallback test. Fixed it by generating a valid synthetic AWP1 wire payload with `encode_awp1()` and asserting legacy `decode_payload()` still raises `FallbackRequired`; retained malformed-input coverage and reran focused/full suites. Post-commit read-only review of base `4566d3f1f909b247b32f21878de1a24d99d22bc4` through head `16ffb917d973fe748c68c2c1877b80edfa401e25` found no Critical/Important/Minor issues and returned Ready to merge: Yes. Commit `16ffb917d973fe748c68c2c1877b80edfa401e25` (`docs: document AgentWire v1 and preserve legacy behavior`) contains only README, the AWP1 document, and the compatibility test. No merge/push. The coordinator-created untracked plan file remains untouched and uncommitted. Full report: `.superpowers/sdd/2026-10-24-agent-communication-protocol/task-7-report.md`.

## Task 7: implementation complete; final whole-branch review pending

Task 7 commit `16ffb917d973fe748c68c2c1877b80edfa401e25` (`docs: document AgentWire v1 and preserve legacy behavior`) contains only `README.md`, `docs/agentwire-compact-profile-v1.md`, and `tests/test_agentwire_compatibility.py`. Compatibility focused tests 3 passed and full suite 1742 passed after adding valid AWP1 to the legacy-dispatcher guard. A post-commit review of Task 7's own diff (`4566d3f..16ffb91`) reported no Critical/Important/Minor and Ready to merge Yes; this is not the required whole-branch final review because it did not cover the branch from its approved base. Final whole-branch review has now been dispatched over `7cac6e05de05f859280492c6350df26720bffc3e..16ffb917d973fe748c68c2c1877b80edfa401e25`; Task 7 Step 5 and final completion remain pending that report. The existing local branch remains unmerged/unpushed; only the coordinator plan document remains untracked.

## Final whole-branch review: two Important findings; one fix wave started

Final review report is `.superpowers/sdd/2026-10-24-agent-communication-protocol/final-review.md`; reviewed full approved-base range `7cac6e05de05f859280492c6350df26720bffc3e..16ffb917d973fe748c68c2c1877b80edfa401e25`. Independent reviewer ran 45 focused synthetic/legacy tests, did not run benchmark/CLI or full suite, and rated the branch `With fixes`.

1. Important: `src/agentcodec/agentwire/benchmark.py:1065-1095` excludes the source-domain freeform baseline in gateway mode. Plan permits transport-only freeform comparison; add comparable transport/setup comparisons and keep model-input/output/end-to-end measures not applicable, with tests for deltas, break-even and unknown cost.
2. Important: `src/agentcodec/cli.py:591-615` / `src/agentcodec/agentwire/benchmark.py:1002-1023` computes both modes before filtering `--modes`; the unchanged default/full cached run previously timed out at 600 seconds without a report. Preserve approved defaults, avoid redundant invariant token work and pass selected modes into the runner; complete a cached default integration run or otherwise explicitly document/resolve runtime behavior.

One final fix wave has been dispatched from fix base `16ffb917d973fe748c68c2c1877b80edfa401e25`, covering both findings in one implementation. After it, perform one scoped re-review of the fix range. Deferred Minor findings remain listed in `final-review.md` for adjudication; no merge/push.


## Final fix wave: completed

Fix base/branch HEAD remains `16ffb917d973fe748c68c2c1877b80edfa401e25`; no commit, merge, or push. Both final-review Important findings were addressed in one uncommitted wave: source-domain gateway freeform comparison now uses transport token deltas and transport/setup break-even only, failing closed on unknown costs; `--modes` is passed into and enforced by the runner while preserving the default two modes and row order. An invocation-local profile/text measurement cache reuses invariant measurements across reuse counts without changing multiplicity accounting. Cost-boundary documentation was clarified.

Verification: RED tests reproduced the old gateway-comparison and mode-selection gaps; focused GREEN tests: benchmark+costs `40 passed`, CLI `17 passed`; full suite `1746 passed in 200.66s`; py_compile and `git diff --check` passed. The uncropped default cached CLI run completed offline with sockets blocked in `353.54s`, producing 84 scenarios and 4,536 rows across both tokenizers, both modes and reuse counts 1/10/50. Independent scoped review found no Critical or new Important issues; its sole outstanding evidence request (default cached runtime) is closed by that run. No fixture content was inspected, printed, copied or exposed; no dependency/network/external service/credential activity occurred. The existing untracked plan file remains untouched.

Full report: `.superpowers/sdd/2026-10-24-agent-communication-protocol/final-fix-report.md`.

Fix wave commit `084a0e0f542959c834d9afb9abed3c50d6457be4` contains the six scoped tracked files only. Implementation tests and full cached default run are complete (final-fix-report.md). The prior scoped reviewer addressed Finding 1, but left Finding 2 NOT ADDRESSED solely because the completed default-cache run had not yet occurred; the 353.54s/84-scenario/4,536-row default run completed afterward. A fresh scoped re-review is required to verify both findings on `16ffb917..084a0e0` with the now-completed run evidence. Branch HEAD is `084a0e0f542959c834d9afb9abed3c50d6457be4`; only the coordinator plan remains untracked. Final whole-branch review is not yet closed.

## Final-review Minor adjudications (parked, non-blocking)

These findings are real but non-load-bearing for the approved offline/local MVP. No second fix wave is authorized by the SDD final-review loop; carry them as explicit residuals rather than silently dropping them.

1. `src/agentcodec/agentwire/benchmark.py` module size/responsibility concentration — parked for a later refactor after behavior stabilizes. Ruling: keep the module as-is for this branch because splitting now would add broad churn after the reviewed benchmark contract is stable and is not required to resolve current correctness findings. Cost if wrong: future maintenance, review, and isolated testing may be harder.
2. `src/agentcodec/cli.py` subset `model_copy(update=...)` bypasses `ReuseScenario` validator — parked. Ruling: current CLI separately validates allowlisted positive reuse subsets and the final default run plus targeted CLI tests exercise the actual runner path; changing the shared contract/API now is beyond the two load-bearing final findings. Cost if wrong: a future caller bypassing the CLI's guard could construct a `ReuseScenario` with values that do not satisfy the model's declared invariant.
3. CLI fixed-fixture manifest identifier lacks digest/symlink/file-type verification — parked. Ruling: the command consumes fixed version-controlled in-repository fixtures and is local opt-in; integrity pinning is useful hardening but not required to resolve the approved transport comparison or mode runtime defects. Cost if wrong: a modified checkout could label changed fixture contents with the same manifest identity, weakening reproducibility/comparability.

## Final fix re-review: closed; plan complete

Fresh scoped re-review of `16ffb917d973fe748c68c2c1877b80edfa401e25..084a0e0f542959c834d9afb9abed3c50d6457be4` returned **PASS**: gateway freeform transport comparison is ADDRESSED; mode preselection/default-mode behavior and cached full-default runtime evidence are ADDRESSED; no new breakage or out-of-scope observations. Reviewer independently reviewed the code and parsed changed Python files, but did not run tests or the full default CLI (no pytest/project environment); the 40 focused benchmark/cost tests, 17 CLI tests, 1,746 full-suite tests, and 353.54-second default cached run remain implementer-reported evidence. Fix commit `084a0e0f542959c834d9afb9abed3c50d6457be4` has the six scoped files only. Final whole-branch review is closed after its one fix wave and this scoped re-review; the three non-blocking Minors are explicitly parked above with rulings/costs. All Task 1–7 implementation/review steps are complete. Branch remains local, offline, unmerged and unpushed; the only untracked file is the coordinator plan document.

Scoped re-review report: `.superpowers/sdd/2026-10-24-agent-communication-protocol/final-fix-review.md`. Both findings ADDRESSED; no new breakage. The reviewer did not run tests or default CLI because its environment lacked pytest; syntax-parse and code review are independent, while the reported 1,746 suite and 353.54s cached CLI run remain implementation evidence. This completes the required one fix wave and its scoped re-review. Plan Task 7 Step 5 is marked complete. No merge/push or branch finalization performed.

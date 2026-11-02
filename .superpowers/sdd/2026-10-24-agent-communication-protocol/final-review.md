# Final whole-branch review — AgentWire Compact Profile v1

## Review range and verification

Range: `7cac6e05de05f859280492c6350df26720bffc3e..16ffb917d973fe748c68c2c1877b80edfa401e25` on `agentcodec-agent-communication-design` — 13 commits, 19 changed files.

Reviewer confirmed target HEAD; read approved plan/spec/ledger/reports and full diff; no branch/index/tracked-file changes. No raw fixture contents were inspected.

Independent tests: **45 passed** (42 contract, codec, cost and receive-mode tests; 3 legacy-compatibility tests), using existing local interpreter. Reviewer did not run benchmark/CLI tests or full suite. Reported Task 6/7 test evidence was not independently rerun.

## Strengths

- AWP1 remains isolated from AC1/AC2 and strictly validates UTF-8 limit, JSON/enums/dictionary identity, returning a complete message or fixed failure.
- Dictionary identity is explicit and bound to user/version/hash; protocol data and approval fields are not treated as execution authority.
- Gateway and model-direct accounting are separated, freeform is pass-through only, and unknown tokenizer/cost data is not fabricated.
- CLI is opt-in and redacts input-derived data; docs limit claims about model understanding and provider billing.

## Important findings

1. **Gateway comparisons omit the planned freeform transport baseline.** `src/agentcodec/agentwire/benchmark.py:1065-1095`: `freeform_applicable` is true only for source-domain `model_direct`, so in gateway mode structured candidates have no `vs_natural_language_freeform` transport comparison despite available freeform pass-through byte/token measurements. The plan allows gateway transport-only comparison. Add a gateway transport-only comparison path with comparable transport/setup costs; keep model input, response, and end-to-end metrics not applicable; add tests for transport deltas, break-even, and unknown-cost behavior.

2. **The default cached CLI workload has an unresolved runtime risk.** `src/agentcodec/cli.py:591-615`; `src/agentcodec/agentwire/benchmark.py:1002-1023`: CLI passes all scenarios to a runner computing both modes, and `--modes` only filters rows afterward. The implementer report says the default/full cached run timed out at 600 seconds with exit 124 and no report (not independently rerun). This is not proof it cannot finish, but the documented default has no completed cached run and can leave a user waiting over ten minutes without an artifact. Pass selected modes to the runner and avoid recomputing invariant text/token costs across reuse counts; then complete a cached integration run for the documented default, or revise the default workload and document expected runtime. The reported 256.59-second subset run is implementer-reported, not independently verified.

## Minor findings

1. **Subset reuse construction bypasses the shared validator.** `src/agentcodec/cli.py:535-540`; `src/agentcodec/agentwire/benchmark.py:134-145`: `model_copy(update=...)` skips `ReuseScenario` validation for selected subsets. CLI inputs are separately validated, but the copied instance violates the shared invariant and subset tests primarily use a runner spy. Consider a supported subset API or validator change, and a real-runner subset test.
2. **Benchmark module mixes many responsibilities.** `src/agentcodec/agentwire/benchmark.py:1-1109`: scenario contracts, event construction, measurement, aggregation, comparison, and report generation are concentrated. Maintainability only; split after behavior stabilizes.
3. **Fixture manifest identifier does not verify fixture integrity.** `src/agentcodec/cli.py:312,426-457,581-584`: fixed paths and constant manifest ID are used without verifying expected hashes or rejecting symlink/non-regular fixture files. A modified checkout could report changed inputs under the same label. Consider versioned expected digests and file-type/symlink checks.

## Recommendations

Prioritize the gateway transport comparison and a completed, acceptable default CLI run. Then address the subset validator workaround and consider fixture integrity validation. The benchmark module split may wait.

## Declined to judge

- Whether fixed fixture contents are synthetic and whether freeform annotations match their paired messages; reviewer did not inspect fixture files.
- Real-model comprehension, task quality, provider billing, or service latency; scope is offline.

## Assessment

**Ready to merge? With fixes.** Core protocol and security boundaries are strong, but the missing gateway freeform transport comparison and unresolved default-run timeout need resolution before merge.

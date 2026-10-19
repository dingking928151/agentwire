# AgentCodec MVP checklist

## Task 1: Canonical message model and strict wire codec
- [x] Define `MessageV1`, `FallbackRequired`, and deterministic wire codec.
- [x] Write and observe failing tests before production code.
- [x] Pass exact round-trip, escaping, Unicode/newline, invalid enum, unknown protocol, and missing-field tests.
- [x] Commit and pass independent spec/quality review, including fix round 1.

## Task 2: User dictionary isolation and property tests
- [x] Define and validate user-scoped dictionary loading.
- [x] Write and observe failing tests before production code.
- [x] Pass wrong namespace/version, duplicate alias/value, unknown alias, and cross-user isolation tests.
- [x] Pass Hypothesis round-trip tests for Unicode, delimiters, optional values, and exact substitutions.
- [x] Commit and pass independent spec/quality review.

## Task 3: Versioned benchmark dataset and token accounting
- [x] Add 30 synthetic bilingual task messages and baseline renderers.
- [x] Test JSONL loading, token counting via stub, amortization, and fallback cost before implementation.
- [x] Implement deterministic accounting for reuse 1/10/50 and local `o200k_base` / `cl100k_base` estimates.
- [x] Render SamoTech/DRIXL v0.3.0 documented compact syntax locally; do not install the package.
- [x] Run benchmark twice and verify deterministic counts.
- [x] Commit and pass independent spec/quality review.

## Task 4: Offline CLI and user documentation
- [x] Write failing tests for encode/decode/validate/benchmark before CLI implementation.
- [x] Implement file/stdin CLI with non-zero errors and no action execution.
- [x] Add README and SECURITY guidance on isolation, approvals, errors, and benchmark limitations.
- [x] Run CLI help, sample benchmark, and complete test suite.
- [x] Commit and pass independent spec/quality review.

## Final checkpoint
- [x] Independent whole-branch review completed.
- [x] No Critical/Important issues remain open.
- [x] Confirm all work stayed local; no publish, deploy, or real Agent/API integration.


# AC2 experiment checklist

## Task 1: Add opt-in AC2 codec
- [ ] Write failing tests for golden payload, all-field round-trip, alias, dispatch, malformed data, and enum behavior.
- [ ] Implement separate AC2 encode/decode; preserve existing AC1 functions and bytes.
- [ ] Add strict AC1/AC2 wire dispatcher; run focused and full tests.
- [ ] Commit and pass independent task review.

## Task 2: Add repetitive corpus and paired benchmark suite
- [ ] Write failing tests for suite structure, paired dictionaries, per-field AC1 diagnosis, setup/fallback accounting, and old report compatibility.
- [ ] Add 30 stable-ID repetitive messages and repeat-v1 dictionary; verify diverse dataset hash is unchanged.
- [ ] Implement separate scenario reports with all seven formats and matched per-scenario dictionary for AC1/AC2.
- [ ] Add non-enum field-level percent-escape diagnosis and separate enum shortcode comparison; keep full-wire costs authoritative.
- [ ] Run twice for both tokenizers and compare reports; run full tests.
- [ ] Commit and pass independent task review.

## Task 3: Expose opt-in CLI and update docs
- [ ] Write failing CLI tests for AC1 default, explicit AC2, autodetect decode/validate, strict fallback, and benchmark-suite paths.
- [ ] Implement `--format ac1|ac2` (default AC1) and explicit-path benchmark-suite command; preserve legacy benchmark behavior.
- [ ] Update README with both corpus comparisons, matched dictionaries, diagnosis and actual results.
- [ ] Run examples, deterministic benchmark twice, and full test suite.
- [ ] Run final independent whole-branch review; stay on this branch without merge/push.

# AgentCodec MVP checklist

## Task 1: Canonical message model and strict wire codec
- [ ] Define `MessageV1`, `FallbackRequired`, and deterministic wire codec.
- [ ] Write and observe failing tests before production code.
- [ ] Pass exact round-trip, escaping, Unicode/newline, invalid enum, unknown protocol, and missing-field tests.
- [ ] Commit and pass independent spec/quality review.

## Task 2: User dictionary isolation and property tests
- [ ] Define and validate user-scoped dictionary loading.
- [ ] Write and observe failing tests before production code.
- [ ] Pass wrong namespace/version, duplicate alias/value, unknown alias, and cross-user isolation tests.
- [ ] Pass Hypothesis round-trip tests for Unicode, delimiters, optional values, and exact substitutions.
- [ ] Commit and pass independent spec/quality review.

## Task 3: Versioned benchmark dataset and token accounting
- [ ] Add 30 synthetic bilingual task messages and baseline renderers.
- [ ] Test JSONL loading, token counting via stub, amortization, and fallback cost before implementation.
- [ ] Implement deterministic accounting for reuse 1/10/50 and local `o200k_base` / `cl100k_base` estimates.
- [ ] Render SamoTech/DRIXL v0.3.0 documented compact syntax locally; do not install the package.
- [ ] Run benchmark twice and verify deterministic counts.
- [ ] Commit and pass independent spec/quality review.

## Task 4: Offline CLI and user documentation
- [ ] Write failing tests for encode/decode/validate/benchmark before CLI implementation.
- [ ] Implement file/stdin CLI with non-zero errors and no action execution.
- [ ] Add README and SECURITY guidance on isolation, approvals, errors, and benchmark limitations.
- [ ] Run CLI help, sample benchmark, and complete test suite.
- [ ] Commit and pass independent spec/quality review.

## Final checkpoint
- [ ] Independent whole-branch review completed.
- [ ] No Critical/Important issues remain open.
- [ ] Confirm all work stayed local; no publish, deploy, or real Agent/API integration.

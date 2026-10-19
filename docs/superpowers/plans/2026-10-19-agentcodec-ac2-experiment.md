# AgentCodec AC2 Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in AC2 wire format and a second, high-repetition benchmark corpus to measure whether JSON-native payloads and exact user-dictionary substitutions can reduce end-to-end token estimates without changing AC1/default behavior.

**Architecture:** Keep AC1 encoder and payload unchanged. Add an independent AC2 encoder/decoder over `MessageV1`, using the same ordered fields and enum codes with compact native JSON values; add a strict prefix dispatcher for decode. Preserve the existing one-corpus benchmark API and add a separate suite API that evaluates the diverse and repetitive corpora independently with matched dictionaries, explicit setup/fallback costs, and per-field AC1 percent-escape diagnostics.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, pytest, Hypothesis, local `tiktoken` `o200k_base` and `cl100k_base`; no new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-10-19-agentcodec-ac2-experiment-design.md`

## Global Constraints

- AC1's existing wire output, behavior, and default CLI format remain unchanged.
- AC2 uses prefix `AC2|`, exactly the 11 fields in `FIELDS` order, existing one-letter enum codes, compact JSON values, and full-string exact replacements only in approved content fields.
- Invalid prefixes, versions, lengths, enums, aliases, namespaces, values, or JSON fail closed using `FallbackRequired`; never infer or discard semantic fields.
- Existing `bench/tasks.v1.jsonl` remains unchanged. Add a separate 30-message repetitive corpus and `repeat-v1` dictionary; keep scenario reports distinct.
- Compare 7 formats per scenario: natural language, compact JSON, DRIXL reference format, AC1, AC1+that scenario's dictionary, AC2, AC2+that same dictionary. Both AC1/AC2 dictionary variants in a scenario use the identical dictionary.
- Count protocol and dictionary setup, payload, fallback/resend, reuse 1/10/50, full-message actual token estimates, and AC1 per-field compact-JSON vs percent-escaped JSON diagnostics on the no-dictionary condition; report non-enum per-field token-delta mean/median and enum-shortcode deltas separately. Escape ratio is escaped UTF-8 bytes divided by the original serialized value's UTF-8 bytes. Per-field diagnostic sums are explanatory only, not the full message cost.
- Measurements are local tokenizer estimates, not API billing, model comprehension, or task-success claims. Preserve negative results.
- No real Agent, model/API call, network service, message execution, automatic dictionary learning, publication, deployment, merge, or push. Work stays on the already isolated `agentcodec-mvp` branch and user explicitly asked to keep it unmerged.

## Review Focus

1. AC2 JSON strings containing `|`, quotes, backslashes, newlines, Unicode separators, empty values, and alias-looking literals must round-trip exactly without delimiter parsing or normalization (`tests/test_ac2.py`).
2. AC2 short/long arrays, invalid enum codes, unknown prefixes, trailing JSON, duplicate object keys, malformed aliases, and wrong dictionary owner/version must return typed fallback (`tests/test_ac2.py`).
3. Dictionary replacement is exact whole-string substitution only in `CONTENT_FIELDS`; substrings, IDs, status, user ID, and dictionary version remain literal and unchanged (`tests/test_ac2.py`).
4. Benchmark scenario pairs use equivalent canonical fields and the exact same scenario dictionary for AC1 and AC2; the diverse dataset hash remains unchanged, and reports keep scenarios separate (`tests/test_benchmark.py`).
5. AC1 diagnostic values may not be mistaken for message-total costs; full wire counts must be measured directly, with setup and fallback retained and repeat runs deterministic (`tests/test_benchmark.py`).

---

### Task 1: Add the opt-in AC2 codec and strict wire dispatcher

**Files:**
- Create: `src/agentcodec/ac2.py`
- Create: `tests/test_ac2.py`
- Modify: `src/agentcodec/codec.py` (prefix dispatcher only; keep `decode_message` AC1 behavior intact)

**Interfaces:**
- `encode_message_ac2(message: MessageV1, dictionary: UserDictionary | None = None) -> str`
- `decode_message_ac2(payload: str, dictionary: UserDictionary | None = None) -> MessageV1`
- `decode_payload(payload: str, dictionary: UserDictionary | None = None) -> MessageV1` dispatches only exact `AC1|` and `AC2|` prefixes; unknown inputs raise `FallbackRequired`.
- AC2 reuses `FIELDS`, `ENUMS`, `CONTENT_FIELDS`, `MessageV1`, `UserDictionary`, `_validated_dictionary`, and `_check_dictionary`; AC1's `encode_message` and `decode_message` remain wire-compatible.

- [x] **Step 1: Write failing tests** for exact AC2 golden payload, all-field round-trip, enums, exact dictionary substitutions, AC1/AC2 prefix dispatch, unknown prefix, and the input classes in Review Focus 1–3.
- [x] **Step 2: Run `pytest tests/test_ac2.py -q` and confirm failures are due to missing AC2 API/expected behavior.**
- [x] **Step 3: Implement AC2 in `src/agentcodec/ac2.py`** as `AC2|` plus one compact JSON array in exact `FIELDS` order; enum positions carry existing short codes; other positions carry ordinary JSON values; alias objects replace exact approved strings only.
- [x] **Step 4: Add `decode_payload` dispatch** without changing AC1-specific function behavior; validate prefix, exact array size, enum map, alias shape, metadata selection, and strict canonical schema; convert malformed inputs to `FallbackRequired`.
- [x] **Step 5: Run focused codec/property tests and full `pytest -q`; capture exact RED/GREEN evidence.**
- [x] **Step 6: Self-review and commit; independent reviewer verifies spec compliance and code quality before Task 2.**

### Task 2: Add a high-repetition corpus and paired-format benchmark suite

**Files:**
- Create: `bench/tasks-repetitive.v1.jsonl`
- Create: `bench/dictionaries/repeat-v1.json`
- Modify: `src/agentcodec/benchmark.py`
- Modify: `tests/test_benchmark.py`

**Interfaces:**
- Preserve `run_benchmark(tasks, encodings, reuse_counts=(1, 10, 50)) -> BenchmarkReport` and its existing report semantics.
- Add `run_benchmark_suite(scenarios, encodings, reuse_counts=(1, 10, 50)) -> BenchmarkSuiteReport`, with each scenario explicitly supplying its messages and manual dictionary; results keep each scenario separate.
- Each scenario reports the seven formats named in Global Constraints. The original diverse scenario uses `DEMO_DICTIONARY`; the repetitive scenario uses the on-disk `repeat-v1` dictionary.
- Add no-dictionary, field-level AC1 cost diagnostics comparing compact-JSON field values with percent-escaped AC1 values for non-enum fields, plus UTF-8 percent-escaped byte ratio; report enum short-code deltas separately. Clearly mark them diagnostic-only.

- [x] **Step 1: Write failing tests** for a 30-message repetitive fixture, dictionary load/owner/version, two scenarios with matched AC1/AC2 dictionaries, seven-format output, per-field escape diagnostics, setup/fallback/amortization, and preservation of the existing one-corpus API/report.
- [x] **Step 2: Run focused tests and verify expected failures before adding the new corpus or production benchmark code.**
- [x] **Step 3: Add the versioned repetitive JSONL and `repeat-v1` dictionary** with stable IDs, deliberately repeated long phrases and workflow constraints, while covering negation, budget/date, approval, Unicode, and delimiters; verify the existing diverse corpus hash is unchanged.
- [x] **Step 4: Implement the suite report/runner** for the seven explicit format conditions, selecting the same dictionary for AC1 and AC2 within each scenario; preserve full payload counts, setup, resend, reuse and quality metrics.
- [x] **Step 5: Implement no-dictionary per-field AC1 diagnostics** against compact JSON using both tokenizers; report `message_type`/`status` JSON-literal vs short-code costs separately, and test that diagnostic totals are not used as the actual message payload totals.
- [x] **Step 6: Run each suite twice for both tokenizers and compare the complete reports; verify round-trip/critical-field metrics and report actual totals without a savings threshold.**
- [x] **Step 7: Run focused and full tests, self-review, commit, and get an independent task review before Task 3.**

### Task 3: Expose AC2 in CLI and document the experiment

**Files:**
- Modify: `src/agentcodec/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md`

**Interfaces:**
- `agentcodec encode --format ac1|ac2` defaults to `ac1`; old invocations remain unchanged.
- `decode` and `validate --format wire` accept AC1 and AC2 through `decode_payload`.
- Add a distinct `benchmark-suite` command requiring `--diverse-input`, `--repetitive-input`, and `--repetitive-dictionary` paths; it uses the existing built-in demo dictionary for the diverse scenario and the supplied dictionary for repetitive data. Existing `benchmark --input ...` behavior remains a one-corpus report.

- [ ] **Step 1: Write failing CLI tests** for default AC1 output, explicit AC2 output, AC1 and AC2 autodetected decode, wire validation, unknown prefix/dictionary fallback, missing benchmark-suite paths, and offline `benchmark-suite` output with explicit local paths.
- [ ] **Step 2: Run focused CLI tests and verify the expected failures.**
- [ ] **Step 3: Implement CLI format selection/dispatch and the distinct suite command** without changing existing benchmark or encode defaults.
- [ ] **Step 4: Update README examples and interpretation** to show AC1/AC2 coexistence, both separate corpus results, field-level diagnostic meaning, matched dictionaries, and the empirical result; do not claim token savings unless both tokenizers and end-to-end setup/fallback costs show it.
- [ ] **Step 5: Run CLI help/examples, two deterministic full benchmark-suite runs, `pytest tests/test_cli.py -q`, and full `pytest -q`; verify AC1 default golden output remains unchanged.**
- [ ] **Step 6: Review the entire branch, triage remaining Minor coverage notes from the prior base review, and retain the branch unmerged/unpushed.**

## Checkpoint: Experiment Complete

- [ ] AC1 and default CLI output remain unchanged; AC2 is strictly reversible and has typed failure behavior.
- [ ] Both corpora remain separate and AC1/AC2 use matched scenario dictionaries.
- [ ] Both tokenizer reports are deterministic and include setup/fallback; AC1 diagnostics are not represented as full-message totals.
- [ ] Full test suite and final independent code review pass; no external integrations, merge, push, or publication occur.

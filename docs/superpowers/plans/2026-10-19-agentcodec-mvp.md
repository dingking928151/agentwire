# AgentCodec MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an offline Python SDK/CLI that measures reversible, versioned multi-agent message compression and per-user dictionary amortization without losing task constraints.

**Architecture:** Pydantic v2 defines the canonical versioned message. A deterministic codec encodes fixed enums and exact per-user dictionary matches into a compact, escaped wire format; invalid or unknown input returns an explicit fallback result. A reproducible benchmark compares natural-language, JSON, DRIXL, generic AgentCodec, and user-dictionary AgentCodec across synthetic examples.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, pytest, Hypothesis, tiktoken (`o200k_base`, `cl100k_base`); DRIXL v0.3.0 is a locally rendered reference-format baseline, not an installed dependency.

**Spec:** `docs/superpowers/specs/2026-10-19-agentcodec-design.md`

## Global Constraints

- Work only in `/workspace/agentcodec`.
- No online model API, real Agent integration, external service, action execution, deployment, or GitHub publishing.
- User dictionaries are manual, versioned, and isolated by explicit user namespace.
- Never silently drop or infer task, constraint, negation, evidence, date/amount, or approval fields.
- TDD is mandatory: run each focused test red before writing its production code, then green, then full-suite verification.
- Tokenizer measurements are estimates; report protocol/dictionary setup and fallback costs.
- The DRIXL comparison reproduces the publicly documented compact syntax from SamoTech/DRIXL v0.3.0; it does not test the third-party Python package implementation.

## Review Focus

- Text containing separators, backslashes, percent signs, Unicode, and newlines must round-trip exactly.
- Unknown protocol versions, dictionary versions, aliases, namespaces, malformed values, and missing required fields must fail closed and trigger fallback.
- Repeated or overlapping dictionary entries must have deterministic behavior; cross-user decoding must not resolve another user's codes.
- Explicit negation, approval requirements, budgets/amounts, dates, constraints, and evidence must remain unchanged.
- Missing local tokenizer/encoding must be reported clearly, without fabricated comparison results; the DRIXL reference renderer has no third-party runtime dependency.

---

### Task 1: Canonical message model and strict wire codec

**Files:**
- Create: `pyproject.toml`
- Create: `src/agentcodec/__init__.py`
- Create: `src/agentcodec/schema.py`
- Create: `src/agentcodec/codec.py`
- Create: `tests/test_codec.py`

**Interfaces:**
- `MessageV1`: fields `message_id`, `message_type`, `task`, `status`, `blocker`, `next_step`, `evidence`, `constraints`, `approval_required`, `user_id`, `dictionary_version`.
- `UserDictionary` in `schema.py`: validated `user_id`, `version`, and unique `aliases: dict[str, str]` mapping from short code to exact phrase; both codes and phrase values must be unique.
- `encode_message(message: MessageV1, dictionary: UserDictionary | None = None) -> str`.
- `decode_message(payload: str, dictionary: UserDictionary | None = None) -> MessageV1`.
- `FallbackRequired`: typed error for unknown protocol/dictionary versions, unknown aliases, malformed wire data, or invalid fields.

- [x] **Step 1: Write failing tests** for exact round-trip of every field and fixed enum compression.
- [x] **Step 2: Run focused tests and verify the expected failure** because schema/codec APIs do not exist.
- [x] **Step 3: Write failing tests** for escaped delimiters, Unicode/newlines, unknown protocol version, invalid enum, and missing required fields.
- [x] **Step 4: Run focused tests and verify the expected failure** for the missing strict handling.
- [x] **Step 5: Implement the minimal schema and deterministic codec** with explicit field order, reversible escaping, exact alias substitution, and fail-closed errors.
- [x] **Step 6: Run `pytest tests/test_codec.py -q` and verify all pass.**
- [x] **Step 7: Run `pytest -q` and verify the full suite passes.**

### Task 2: User dictionary isolation and property tests

**Files:**
- Create: `src/agentcodec/dictionary.py`
- Create: `tests/test_dictionary.py`
- Create: `tests/test_properties.py`

**Interfaces:**
- `UserDictionary`: import the validated type from `schema.py`; `dictionary.py` must not define a second representation.
- `load_dictionary(path: Path, expected_user_id: str) -> UserDictionary`.
- `encode_message` / `decode_message` consume a dictionary only after matching `user_id` and `dictionary_version`.

- [x] **Step 1: Write failing tests** for valid JSON dictionary load, wrong user ID, wrong version, duplicate code, duplicate phrase value, unknown alias, and cross-user isolation.
- [x] **Step 2: Run tests and verify expected failures** because dictionary loading and namespace enforcement do not exist.
- [x] **Step 3: Write failing Hypothesis properties** for round-trip across arbitrary Unicode text, delimiters, optional values, and approved dictionary substitutions.
- [x] **Step 4: Run property tests and verify expected failures.**
- [x] **Step 5: Implement dictionary validation and namespace checks** with no automatic learning or network access.
- [x] **Step 6: Run `pytest tests/test_dictionary.py tests/test_properties.py -q` and verify all pass.**
- [x] **Step 7: Run `pytest -q` and verify the full suite passes.**

### Task 3: Versioned benchmark dataset and token accounting

**Files:**
- Create: `bench/tasks.v1.jsonl`
- Create: `bench/baselines.py`
- Create: `src/agentcodec/benchmark.py`
- Create: `tests/test_benchmark.py`

**Interfaces:**
- `load_tasks(path: Path) -> list[MessageV1]`.
- `count_tokens(text: str, encoding_name: str) -> int`.
- `run_benchmark(tasks, encodings, reuse_counts=(1, 10, 50)) -> BenchmarkReport`.
- `BenchmarkReport` includes per-message and total counts; one-time protocol/dictionary instruction costs; amortized costs; fallback count/cost; round-trip field equality and critical-constraint loss count.

- [x] **Step 1: Write failing tests** for JSONL loading, deterministic token counts via a stub tokenizer, amortization math, and fallback costs.
- [x] **Step 2: Run tests and verify the expected failures.**
- [x] **Step 3: Prepare 30 synthetic messages** with explicit equivalent fields across five conditions: concise natural language, JSON, DRIXL, generic codec, generic codec + demo user dictionary.
- [x] **Step 4: Implement benchmark accounting** including setup cost and reuse counts 1, 10, and 50; use `tiktoken` adapters for `o200k_base` and `cl100k_base` in real runs.
- [x] **Step 5: Add a DRIXL reference-format renderer** matching the publicly documented compact syntax in SamoTech/DRIXL v0.3.0 README; do not install/import the package, and label this as a format baseline rather than a library-runtime test.
- [x] **Step 6: Run `pytest tests/test_benchmark.py -q` and verify all pass.**
- [x] **Step 7: Run benchmark twice and confirm deterministic counts and report structure.**
- [x] **Step 8: Run `pytest -q` and verify the full suite passes.**

### Task 4: Offline CLI and user documentation

**Files:**
- Create: `src/agentcodec/cli.py`
- Create: `tests/test_cli.py`
- Create: `README.md`
- Create: `SECURITY.md`
- Modify: `pyproject.toml`

**Interfaces:**
- CLI commands: `agentcodec encode`, `agentcodec decode`, `agentcodec validate`, `agentcodec benchmark`.
- CLI errors use non-zero exit codes and readable fallback reasons; never execute decoded message content.

- [x] **Step 1: Write failing CLI tests** for the four commands, dictionary user mismatch, unknown code fallback, and offline benchmark invocation.
- [x] **Step 2: Run focused tests and verify the expected failures.**
- [x] **Step 3: Implement Typer commands** with file/stdin support and clear errors.
- [x] **Step 4: Write README** covering install, examples, exact CLI invocations, benchmark interpretation, limitations, and the local DRIXL reference-format baseline.
- [x] **Step 5: Write SECURITY.md** covering untrusted data, dictionary isolation, approval fields not being authorization, and fallback behavior.
- [x] **Step 6: Run `pytest tests/test_cli.py -q` and verify all pass.**
- [x] **Step 7: Run full `pytest -q`, `python -m agentcodec.cli --help`, and a local synthetic benchmark; record exact outputs.**
- [x] **Step 8: Verify the repo has no network/API calls or action execution paths.**

## Checkpoint: MVP Complete

- [ ] All tests pass and the focused TDD red/green cycles were observed.
- [ ] 30-task benchmark reruns deterministically for both tokenizers, with setup and fallback costs stated.
- [ ] Critical fields round-trip without loss; malformed/unknown data fails closed.
- [ ] All files remain under `/workspace/agentcodec`; no external Agent integration or publication occurred.

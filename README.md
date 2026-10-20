# AgentCodec

AgentCodec is an offline Python SDK and CLI for reversible, structured agent-message **data**. It preserves explicit fields and can replace whole, exactly matching content strings with codes from a manually maintained per-user dictionary. It does not connect to or operate any real agent, model, API, or service, and never executes message content.

**AC1's negative result is unchanged:** on the original 30-message diverse corpus, AC1 with and without its demo dictionary costs more estimated tokens than compact JSON under both tested tokenizers. The opt-in **experimental AC2** format reduces local estimates on these fixtures, including setup and fallback accounting; its dictionary helps on the separate repetitive corpus but hurts on the diverse corpus. Repetitive AC1+dictionary still loses to JSON at reuse 1 under `o200k_base`. These synthetic results are not a general savings claim, actual API billing, or equivalent model task success.

## Install locally

Requires Python 3.11+, Pydantic 2, Typer, and tiktoken; pytest and Hypothesis are development extras. Run from the repository root. For an offline installation, first provision a trusted local wheelhouse containing these dependencies and build requirements (`setuptools` and its build dependencies), then:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --no-index --find-links /path/to/wheelhouse -e '.[dev]'
agentcodec --help
# Equivalent module entry point:
python -m agentcodec.cli --help
python -m pytest -q
```

The wheelhouse path is a placeholder for your own local directory. Dependency provisioning is separate from CLI operation; the application never installs dependencies or downloads tokenizer vocabularies. No model credentials are needed. If the project is already installed in the supplied `.venv`, activate it rather than recreating it.

### Tokenizer prerequisites

Only `benchmark` and `benchmark-suite` need tokenizer data. Both `o200k_base` and `cl100k_base` vocabulary files must already exist in the local tiktoken cache. Set `TIKTOKEN_CACHE_DIR` to the provisioned directory (otherwise `DATA_GYM_CACHE_DIR`, then the system temporary directory's `data-gym-cache`, is used). Cache names and required SHA-256 hashes are:

| Encoding | Cache filename | SHA-256 |
| --- | --- | --- |
| `o200k_base` | `fb374d419588a4632f3f557e76b4b70aebbca790` | `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d` |
| `cl100k_base` | `9b5ad71b2ce5302211f9c61530b329a4922fc6a4` | `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7` |

These filenames are SHA-1 cache keys for tiktoken's standard vocabulary URLs, not content hashes. Obtain the vocabulary files through your own trusted provisioning process. The runner checks their content hashes before initializing each encoding and refuses absent or corrupt caches instead of allowing tiktoken's normal lazy download. It never fabricates counts.

## Encode, decode, validate

All message fields are required, including explicit `null`, `false`, and empty lists. JSON must not contain duplicate keys or extra fields. `message_type` is `delegate`, `status`, or `result`; `status` is `pending`, `running`, `blocked`, `completed`, `failed`, or `cancelled`.

Create a synthetic example:

```sh
cat > message.json <<'JSON'
{
  "message_id": "example-1",
  "message_type": "delegate",
  "task": "Review the local report.",
  "status": "pending",
  "blocker": null,
  "next_step": "Ask before sending.",
  "evidence": ["report.txt"],
  "constraints": ["Do not publish.", "Budget <= $25."],
  "approval_required": true,
  "user_id": "demo-user",
  "dictionary_version": null
}
JSON

agentcodec validate --input message.json
agentcodec encode --input message.json --output message.ac1
agentcodec validate --format wire --input message.ac1
agentcodec decode --input message.ac1 --output restored.json

# Explicit AC1 is byte-identical to the default; AC2 is always opt-in:
agentcodec encode --format ac1 --input message.json --output explicit.ac1
cmp message.ac1 explicit.ac1
agentcodec encode --format ac2 --input message.json --output message.ac2
agentcodec validate --format wire --input message.ac2
agentcodec decode --input message.ac2 --output restored-ac2.json

# A missing --input/--output, or the literal -, means stdin/stdout:
cat message.json | agentcodec encode | agentcodec decode
agentcodec validate --input - < message.json
```

Successful validation emits `{"status":"valid"}`; it does not approve the task. Encoding defaults to the unchanged `AC1|…` percent-escaped wire line. `encode --format ac2` emits `AC2|` followed by one compact JSON array: exactly the same eleven fields in canonical order, the same enum codes, and native JSON values instead of per-field percent escaping. Pipes inside AC2 strings are data. `decode` and `validate --format wire` use the strict `decode_payload` dispatcher for exact `AC1|`/`AC2|` prefixes; unknown prefixes require fallback. The SDK's `encode_message`/`decode_message` remain AC1-specific; `encode_message_ac2`/`decode_message_ac2` are in `agentcodec.ac2`, and `decode_payload` is in `agentcodec.codec`.

Encode/decode/validate and the original benchmark accept `--input`/`-i` and `--output`/`-o`; the suite instead requires the three named input paths below and accepts `--output`/`-o`. Files are UTF-8, output has one final newline, and an explicitly selected output file is overwritten on success. Decode/validate-wire strip one terminal line ending. AC1 rejects other trailing whitespace; AC2 permits JSON whitespace around its single array, but neither accepts multiple messages or extra non-whitespace content. Encode/decode/validate accept one message; benchmarks accept JSONL.

### Manually selected user dictionary

A dictionary file uses **`version`**, while a message uses **`dictionary_version`**. The message must already contain the selected version: encoding does not silently rewrite its metadata.

```sh
cat > dictionary.json <<'JSON'
{
  "user_id": "demo-user",
  "version": "demo-v1",
  "aliases": {
    "NP": "Do not publish.",
    "REVIEW": "Review the local report."
  }
}
JSON
python -c 'import json; m=json.load(open("message.json")); m["dictionary_version"]="demo-v1"; print(json.dumps(m))' > dictionary-message.json

agentcodec encode --input dictionary-message.json --output dictionary-message.ac1 --dictionary dictionary.json --user-id demo-user
agentcodec decode --input dictionary-message.ac1 --output dictionary-restored.json --dictionary dictionary.json --user-id demo-user
agentcodec validate --input dictionary-message.json --dictionary dictionary.json --user-id demo-user
agentcodec validate --format wire --input dictionary-message.ac1 --dictionary dictionary.json --user-id demo-user

# Identical dictionary and namespace rules apply to AC2:
agentcodec encode --format ac2 --input dictionary-message.json --output dictionary-message.ac2 --dictionary dictionary.json --user-id demo-user
agentcodec decode --input dictionary-message.ac2 --output dictionary-restored-ac2.json --dictionary dictionary.json --user-id demo-user
agentcodec validate --format wire --input dictionary-message.ac2 --dictionary dictionary.json --user-id demo-user
```

For encode/decode/validate, every `--dictionary` selection requires an explicit `--user-id`. Both its owner and its version must match message metadata. Aliases replace only entire strings in `task`, `blocker`, `next_step`, `evidence`, and `constraints`; no substring, fuzzy, semantic, or automatic-learning replacement occurs. Literal strings resembling codes remain literal. Duplicate keys, duplicate phrases, unknown codes, and mismatches are rejected rather than guessed. Dictionaries are plaintext local data, not access-control credentials. The suite's separate corpus-based namespace selection is described below.

### Errors and fallback

Data, dictionary, file, or tokenizer errors exit with code **1**, leave stdout empty, and print a readable `fallback_required: …` reason to stderr. Command-line usage errors (for example, an unknown option or unsupported `--format`) exit with code **2** and Typer's usage explanation. Valid commands exit **0**. Invalid input is checked before an output file is opened; a later filesystem write failure is not an atomic-write guarantee.

The sender/caller must keep the original canonical JSON. On unknown protocol/enum/alias/version, malformed escaping, ambiguous input, or namespace mismatch, stop and use that retained original through a separately authorized workflow. The CLI does not guess, automatically retransmit, execute, or contact anyone. See [SECURITY.md](SECURITY.md).

## Run the synthetic benchmark

From the repository root, with both vocabularies available locally:

```sh
agentcodec benchmark --input bench/tasks.v1.jsonl --output benchmark-report.json
# Identical stdin/stdout interface:
cat bench/tasks.v1.jsonl | agentcodec benchmark > benchmark-report-stdin.json
# The module entry point works too:
python -m agentcodec.cli benchmark --input bench/tasks.v1.jsonl --output benchmark-report-module.json
```

The report includes all per-message counts and five conditions: concise field-labelled natural language, compact JSON, a local DRIXL reference-format baseline, generic AgentCodec, and AgentCodec with the handpicked demo dictionary. Both local tiktoken ordinary-text encodings run automatically. All conditions keep explicit canonical fields. The dictionary condition selects `demo-v1` transport metadata only where the input version is null, measures equality against that explicitly selected version, and never mutates the source tasks.

### What the original AC1 benchmark showed (unchanged)

This existing command/report still has five conditions (`codec` means AC1); it does not silently add AC2 or change its report schema. These are **local tokenizer estimates**, not API charges. For 30 synthetic messages, transmitted payload counts (no setup; fallback was zero) were:

| Format | `o200k_base` | `cl100k_base` |
| --- | ---: | ---: |
| Concise natural language | 2,640 | 2,651 |
| Compact JSON | 2,576 | 2,559 |
| DRIXL reference format | 6,266 | 6,209 |
| AgentCodec | 4,500 | 4,305 |
| AgentCodec + demo dictionary | 5,058 | 4,833 |

Setup adds 191/189 protocol tokens for AgentCodec; dictionary mode adds another 67 dictionary tokens. DRIXL setup is 113/110 tokens; natural language and compact JSON assume zero additional format setup. Thus both codec variants cost more than compact JSON even before setup, and the dictionary variant costs more than generic AgentCodec on this corpus. Percent escaping, alias tags, and dictionary-version metadata can outweigh short codes. Do not infer a general compression benefit.

Both codec variants recovered 30/30 full canonical messages, with zero changed/unrecovered critical fields and zero fallbacks. These metrics test deterministic codec recovery, not model understanding. Natural-language/JSON/DRIXL quality metrics are `null` because this project does not test decoders for those baselines.

## Run the paired AC1/AC2 experiment

The distinct `benchmark-suite` command requires explicit local paths, so it does not assume an installed wheel carries repository fixtures:

```sh
agentcodec benchmark-suite \
  --diverse-input bench/tasks.v1.jsonl \
  --repetitive-input bench/tasks-repetitive.v1.jsonl \
  --repetitive-dictionary bench/dictionaries/repeat-v1.json \
  --output benchmark-suite-report.json
# With the same inputs, the full JSON report is deterministic:
agentcodec benchmark-suite \
  --diverse-input bench/tasks.v1.jsonl \
  --repetitive-input bench/tasks-repetitive.v1.jsonl \
  --repetitive-dictionary bench/dictionaries/repeat-v1.json \
  --output benchmark-suite-report-again.json
cmp benchmark-suite-report.json benchmark-suite-report-again.json
```

`suite-v1` keeps `scenarios.diverse` and `scenarios.repetitive` separate: **30 messages each, never a blended 60-message total**. Each has `results[encoding][format]`, its complete dictionary, protocol instructions, and `ac1_diagnostics[encoding]`. The seven format keys are `natural_language`, `json`, `drixl`, `ac1`, `ac1_dictionary`, `ac2`, and `ac2_dictionary`. Reuse counts are 1/10/50, with full per-message counts, setup, fallback and amortized totals in the JSON report.

Diverse uses built-in `DEMO_DICTIONARY` (`demo-user` / `demo-v1`); repetitive strictly loads the supplied manual dictionary (`repeat-user` / `repeat-v1` for the included file). The supplied dictionary owner must match the first repetitive message; later namespace/version mismatches retain per-message codec fallback accounting, not silent repair. Each scenario uses the **same dictionary for its AC1 and AC2 pair**. Dictionary conditions select the scenario version only when the input version is null; all other fields stay unchanged and equality includes that explicit transport version. Non-dictionary conditions and baseline renderers retain original metadata. Source files are not modified. No automatic dictionary learning occurs.

### Diverse corpus: the dictionary still hurts

Every paired number below is **`o200k_base` / `cl100k_base`**, local ordinary-text estimates. Payload is measured directly from complete messages; setup includes protocol and the complete serialized dictionary; fallback is original-JSON resend. The last column includes all three for one complete corpus at reuse 1.

| Format | Payload | Setup | Fallback | Session total @1 |
| --- | ---: | ---: | ---: | ---: |
| Concise natural language | 2,640 / 2,651 | 0 / 0 | 0 / 0 | 2,640 / 2,651 |
| Compact JSON | 2,576 / 2,559 | 0 / 0 | 0 / 0 | 2,576 / 2,559 |
| DRIXL reference | 6,266 / 6,209 | 113 / 110 | 0 / 0 | 6,379 / 6,319 |
| AC1 | 4,500 / 4,305 | 191 / 189 | 0 / 0 | 4,691 / 4,494 |
| AC1 + demo dictionary | 5,058 / 4,833 | 258 / 256 | 0 / 0 | 5,316 / 5,089 |
| AC2 | 1,790 / 1,776 | 209 / 207 | 0 / 0 | 1,999 / 1,983 |
| AC2 + demo dictionary | 1,849 / 1,829 | 276 / 274 | 0 / 0 | 2,125 / 2,103 |

AC2 is smaller than compact JSON on this fixture under both tokenizers even after setup. But the short-phrase demo dictionary increases both AC1 and AC2 payload and session costs relative to their no-dictionary counterparts. AC1's original negative result is preserved, not replaced by AC2's result.

### Repetitive corpus: dictionary benefit depends on format and reuse

This separate synthetic fixture deliberately repeats long tasks and constraints and uses a handpicked nine-entry dictionary. It covers negation, budgets/deadlines, approval boundaries, Unicode and delimiters; it is not a real-user workload.

| Format | Payload | Setup | Fallback | Session total @1 |
| --- | ---: | ---: | ---: | ---: |
| Concise natural language | 7,820 / 7,865 | 0 / 0 | 0 / 0 | 7,820 / 7,865 |
| Compact JSON | 7,760 / 7,785 | 0 / 0 | 0 / 0 | 7,760 / 7,785 |
| DRIXL reference | 21,940 / 21,845 | 113 / 110 | 0 / 0 | 22,053 / 21,955 |
| AC1 | 20,165 / 19,940 | 191 / 189 | 0 / 0 | 20,356 / 20,129 |
| AC1 + repeat dictionary | 7,280 / 7,075 | 499 / 498 | 0 / 0 | 7,779 / 7,573 |
| AC2 | 6,970 / 7,000 | 209 / 207 | 0 / 0 | 7,179 / 7,207 |
| AC2 + repeat dictionary | 2,440 / 2,430 | 517 / 516 | 0 / 0 | 2,957 / 2,946 |

**Keep the negative case:** AC1+dictionary has a smaller payload than JSON here but, at reuse 1, costs **7,779 versus 7,760** under `o200k_base` after setup. It therefore does not beat JSON under both tokenizers at that reuse. At reuse 10 its session totals are 73,299 / 71,248 versus JSON 77,600 / 77,850; at reuse 50, 364,499 / 354,248 versus JSON 388,000 / 389,250.

AC2+dictionary beats JSON in both local tokenizers at all tested reuse counts including setup and the observed zero fallback: session totals are 2,957 / 2,946 at reuse 1, 24,917 / 24,816 at reuse 10, and 122,517 / 122,016 at reuse 50. This establishes a conditional estimate for these repeated synthetic phrases, not general API-bill savings. All four codec conditions recovered 30/30 messages in each scenario with zero critical-field loss and zero fallback; this tests codec reversibility, not model comprehension or task success.

### What AC1 field diagnostics mean

`ac1_diagnostics` uses **unaliased original values and metadata**, never dictionary-mode values. Each non-enum field reports compact JSON value tokens, percent-escaped JSON value tokens, their delta (AC1 minus JSON), and per-message delta mean/median. `escaped_utf8_byte_ratio` counts original serialized UTF-8 bytes encoded as `%HH` divided by original serialized UTF-8 bytes; aggregate ratios are byte-weighted. It is not expanded-output length divided by source length. `message_type` and `status` have a separate JSON-literal versus short-code comparison: enum shortening, not percent escaping.

For example, diverse `constraints` adds 1,097 / 1,123 independently measured tokens from escaping, with 27.8230% of serialized UTF-8 bytes escaped. Diverse `message_type` short codes reduce its isolated field count by 60 / 60 tokens. Quotes, brackets, delimiters and multi-byte Unicode can inflate escaping costs, while enum shortening can reduce costs. These diagnostics explain sources of overhead, but **exclude keys/prefix/framing and are non-additive across tokenizer boundaries**. Never sum them into a claimed full-message total; the result tables use independently tokenized complete messages.

### Read the accounting correctly

`total_tokens` per format is transmitted payload plus original-JSON resend on fallback, **excluding setup**. `setup_tokens` is protocol plus dictionary initialization, charged once per session. Reuse 1, 10, or 50 means repeating the **entire corpus**, not one message. For reuse `n` and `M` tasks:

```text
amortized[n].total_tokens = setup_tokens + n * total_tokens
amortized[n].tokens_per_corpus = total_tokens + setup_tokens / n
amortized[n].tokens_per_message = (setup_tokens + n * total_tokens) / (M * n)
```

Fallback costs include any already transmitted encoded payload plus a compact original JSON resend; if encoding fails before transmission, only the original is counted. The CLI itself does not send anything. Retry/acknowledgment overhead, API envelopes, role/tool wrappers, hidden prompts, server tokenization, and billing rules are not modeled. Critical fields are task, blocker, next step, evidence, constraints, and approval; losses count fields, not individual phrases. No fallback occurred in the included clean corpus, but unit tests exercise fallback accounting.

### DRIXL comparison boundary

The local renderer is pinned to the [SamoTech/DRIXL v0.3.0 README](https://github.com/SamoTech/DRIXL/blob/v0.3.0/README.md) compact envelope/body notation: `@to:WORKER @fr:COORD @t:REQ|RES|FIN @p:MED`, then a `ROUT` body with `[key:value]` parameters. This project's percent-escaped compact-JSON values and canonical-field mapping are benchmark choices, not a claim about upstream escaping/schema support. Fixed routing and priority are overhead, never executable instructions. No third-party `drixl` package is installed or imported; no shared-context savings, library runtime behavior, or interoperability is claimed.

## Run the offline mock evaluation

**Mock only checks pipeline behavior, not model understanding.** `evaluate-offline` strictly parses an input representation and returns a read-only `inspect_message_v1` receipt with the recovered message, its hash, and `actions=[]`. The scorer independently validates that receipt and compares all eleven fields with a private oracle. It does not perform the message's original business task, open its URLs or evidence files, execute commands, or interpret approval/status as permission. Pipeline completion is not business-task completion.

The `natural_language` input is **fixed eleven-field tagged text containing JSON literals**, emitted by the existing renderer. Its parser requires every label in order and consumes the entire input; this is not arbitrary natural-language understanding. JSON, AC1, and explicitly selected AC2 use strict parsers too. Hard-field equality is an **exact-match proxy**, not semantic safety. These mock scores establish neither real provider performance, API billing savings, nor general token savings; all earlier token results and negative conclusions above remain unchanged.

### Commands and matrix selection

Use the existing local `.venv` and provisioned dependencies; this evaluation does not initialize a tokenizer or download vocabularies. Run from the repository root, with no installation or service startup:

```sh
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/src"

# Default: AC1 only, no dictionary pairs, 30 cases per group (60 total).
# mktemp creates a fresh empty directory; never delete unknown output to reuse it.
DEFAULT_OUT=$(mktemp -d /tmp/agentcodec-offline-default-XXXXXX)
.venv/bin/python -m agentcodec.cli evaluate-offline \
  --adapter mock-v1 \
  --diverse-input bench/tasks.v1.jsonl \
  --repetitive-input bench/tasks-repetitive.v1.jsonl \
  --repetitive-dictionary bench/dictionaries/repeat-v1.json \
  --output-dir "$DEFAULT_OUT"

# Explicit four formats plus paired dictionaries, independently run twice.
EVAL_A=$(mktemp -d /tmp/agentcodec-offline-paired-a-XXXXXX)
EVAL_B=$(mktemp -d /tmp/agentcodec-offline-paired-b-XXXXXX)
for OUT in "$EVAL_A" "$EVAL_B"; do
  .venv/bin/python -m agentcodec.cli evaluate-offline \
    --adapter mock-v1 \
    --diverse-input bench/tasks.v1.jsonl \
    --repetitive-input bench/tasks-repetitive.v1.jsonl \
    --repetitive-dictionary bench/dictionaries/repeat-v1.json \
    --formats natural_language,json,ac1,ac2 --dictionary-pairs paired \
    --output-dir "$OUT" || exit 1
done
for FILE in manifest.json records.jsonl report.json; do
  cmp "$EVAL_A/$FILE" "$EVAL_B/$FILE" || exit 1
done
export EVAL_A
```

All three local input paths, `--adapter mock-v1`, and `--output-dir` are required. CLI `--formats` defaults to `ac1`; `--dictionary-pairs` defaults to `none`. AC2 is never implicitly enabled. Formats must be unique comma-separated members of `natural_language,json,ac1,ac2`. Only the production deterministic mock is exposed: its record identity is `deterministic-mock-v1`. The identity `scripted-mock-test-v1` is test-only and is rejected by the production CLI; there is no provider, endpoint, model, key, network, retry, or fallback option.

| Selection | Conditions in each separate group | Cases per group | Total attempt records on clean inputs |
| --- | --- | ---: | ---: |
| Default (omit formats/pairs) | `ac1` | 30 | 60 |
| Four explicit formats, pairs `none` | `natural_language`, `json`, `ac1`, `ac2` | 120 | 240 |
| Four explicit formats, pairs `paired` | Above plus `ac1_dictionary`, `ac2_dictionary` | 180 | 360 |

For the four-format/no-pair run, change `paired` to `none` and use another fresh output directory. Pairing adds only already-selected codecs; default AC1 plus `paired` adds `ac1_dictionary`, not AC2. `diverse` and `repetitive` retain 30 source messages each, never a blended quality average or deduplicated set of task strings. Each codec pair uses its group's same dictionary: built-in `demo-user/demo-v1` for diverse, supplied `repeat-user/repeat-v1` for repetitive. Dictionary selection fills only a null version on a transport copy; existing non-null versions remain unchanged, even if strict codec validation then rejects them. `metadata_selection` records that choice; source inputs remain read-only.

### Three output files, not a success verdict

The CLI checks strict UTF-8, fixed approved input hashes, 30 unique source IDs per group, dictionary ownership, and an absent/empty output directory before creating output. It hashes and parses each input from one byte snapshot. A successful run is silent and exits **0 only after all three files are written**; this means report generation completed, not that all cases passed. Read `acceptance_passed`. Configuration/I/O errors exit **1**; usage errors exit **2**. Non-empty output directories are not overwritten. Failed writes attempt to remove only this run's partial files, preserving unrelated data; this is not a power-loss transaction. Missing any of the three files means an incomplete result.

| File | Version and contents |
| --- | --- |
| `manifest.json` | `manifest_version=offline-eval-manifest-v1`; `contract_version` and `scorer_version` are `offline-eval-v1`; adapter identity, local `code_revision`, input hashes/counts, selected formats/conditions, dictionary snapshots/hashes, policies, hard fields, metadata rules, case snapshots, disclaimer and `run_id`. |
| `records.jsonl` | One `record_version=offline-eval-v1` attempt per line; case/record IDs, actual request/payload/hash, raw response, decoded message, metadata selection, error, transition and score. Receipt version is `mock-receipt-v1`. |
| `report.json` | `report_version=offline-eval-report-v1`, `adapter_kind=mock`, `pipeline_only=true`, disclaimer, `acceptance_contract=clean-v1`, `acceptance_passed`, and separate `scenarios.diverse.conditions` / `scenarios.repetitive.conditions`. |

Core JSON uses UTF-8, sorted keys, compact separators, no Unicode normalization, and one trailing LF per document/JSONL record. List order/duplicates and whitespace in values are preserved. `run_id` hashes the canonical manifest without its own ID; no wall-clock time, machine name, duration, or output path is included. The CLI's only revision subprocess is fixed local `git rev-parse HEAD`; it does not consult remotes. Byte reproducibility requires the same inputs, options **and revision**. A documentation-only commit also changes that revision/run ID, so do not compare pre-commit and post-commit artifacts as if their manifests were identical.

### Read the five metrics separately

For each scenario/condition, let **N = `logical_case_count`**, not `attempt_count`. `initial` takes attempt 0; `final` takes the terminal attempt. They are never pooled. Normal/retry attempts compare against transport; a test-enabled original-JSON fallback compares against source. `case_results[].final_oracle` and `terminal_record_id` expose that choice; attempt metadata retains the original dictionary selection. Recovery cannot erase a first failure.

| Category | Numerator and denominator |
| --- | --- |
| Decode / field fidelity | `decode_success_count/N`, `full_message_equal_count/N`; each `field_equal_counts[field]/N`; `equal_field_total/(11*N)` is `field_fidelity_rate`. Failed/unrecovered messages contribute zero equal fields, not exclusion from N. `unrecovered_field_count` also uses `11*N`. |
| Hard constraints | `equal_hard_total/(6*N)` is `hard_field_preservation_rate`. The six fields are `task`, `blocker`, `next_step`, `evidence`, `constraints`, `approval_required`. `hard_constraint_case_pass_count/N` requires all six equal. `changed_hard_field_count` and `unrecovered_hard_field_count` are disjoint field counts with denominator `6*N`; their sum is hard-field loss. No semantic inference is made. |
| Mock task completion | `mock_task_completed_count/N`: valid receipt, correct operation/hash, all eleven fields exactly equal, and empty actions. Source `status=completed` is not success evidence; `cancelled` does not prevent a correct read-only inspection. |
| Fallback | Condition-level `fallback_required_case_count/N`, `fallback_performed_case_count/N`, `fallback_recovered_case_count/N` are separate. Retaining original data is not a performed fallback. Phase-level required counts describe that selected attempt; condition-level required counts mean any attempt required it. |
| Retry | `retried_case_count/N`; `retry_attempt_count` is a raw count of extra same-format/same-payload calls, not a denominator. `retry_recovered_case_count/retried_case_count` is `retry_recovered_case_rate`. Sending original JSON is fallback, not retry. |

JSON retains explicit `*_denominator` and `*_rate` keys (per-field denominators/rates are maps). Rates with a true zero denominator are `null`, never a fabricated 100%; decode failures still belong in N. The CLI fixes policy to `record_only`, `max_retries=0`: failed cases remain visible, with no extra calls. Dedicated local tests, not the clean matrix, exercise at most one retry and one original-JSON fallback (three attempts maximum), including failed recovery, malformed receipts, negation/budget/date/approval changes, dictionary errors, and no-execution/network sentinels. These regression guards are not an OS sandbox or proof of semantic safety.

For the paired run above, this report-reading check uses actual schema keys and keeps the groups and phases separate:

```sh
.venv/bin/python - <<'PY'
import json
import os
from pathlib import Path
p = Path(os.environ['EVAL_A'])
r = json.loads((p / 'report.json').read_text(encoding='utf-8'))
m = json.loads((p / 'manifest.json').read_text(encoding='utf-8'))
assert r['report_version'] == 'offline-eval-report-v1'
assert r['pipeline_only'] is True and r['adapter_kind'] == 'mock'
assert r['acceptance_contract'] == 'clean-v1' and r['acceptance_passed'] is True
assert set(r['scenarios']) == {'diverse', 'repetitive'}
for name in ('diverse', 'repetitive'):
    scenario = r['scenarios'][name]
    assert m['scenario_counts'][name] == 30
    assert set(scenario['conditions']) == {
        'natural_language', 'json', 'ac1', 'ac2', 'ac1_dictionary', 'ac2_dictionary'}
    for condition, c in scenario['conditions'].items():
        assert c['logical_case_count'] == c['attempt_count'] == 30
        for phase in ('initial', 'final'):
            s = c[phase]
            assert s['decode_success_count'] == s['full_message_equal_count'] == 30
            assert s['equal_field_total'] == s['field_fidelity_denominator'] == 330
            assert s['equal_hard_total'] == s['hard_field_preservation_denominator'] == 180
            assert s['changed_hard_field_count'] == s['unrecovered_hard_field_count'] == 0
            assert s['mock_task_completed_count'] == s['mock_task_completed_denominator'] == 30
            print(name, condition, phase, s['mock_task_completed_count'], '/', s['mock_task_completed_denominator'])
        assert all(c[k] == 0 for k in (
            'fallback_required_case_count', 'fallback_performed_case_count',
            'fallback_recovered_case_count', 'retried_case_count',
            'retry_attempt_count', 'retry_recovered_case_count'))
        assert c['retry_recovered_case_denominator'] == 0
        assert c['retry_recovered_case_rate'] is None
records = [json.loads(line) for line in (p / 'records.jsonl').read_text(encoding='utf-8').split('\n') if line]
assert len(records) == len({x['record_id'] for x in records}) == 360
assert all(x['request']['attempt_index'] == 0 for x in records)
print('clean-v1 pipeline acceptance only; not model understanding or business success')
PY
```

### Local verification and future API boundary

```sh
.venv/bin/python -m pytest tests/test_evaluation_contracts.py tests/test_evaluation_formats.py tests/test_evaluation_mock.py tests/test_evaluation_scoring.py tests/test_evaluation_runner.py tests/test_evaluation_cli.py -q
.venv/bin/python -m pytest -q
sha256sum bench/tasks.v1.jsonl bench/tasks-repetitive.v1.jsonl bench/dictionaries/repeat-v1.json
```

Full regression tests still require the already-provisioned tokenizer caches used by the old benchmarks; if dependencies/caches are absent, stop and report the block rather than install or download. Approved input hashes, detailed acceptance commands, and scope are in the [offline evaluation design](docs/superpowers/specs/2026-10-20-agentcodec-offline-evaluation-design.md) and [implementation plan](docs/superpowers/plans/2026-10-20-agentcodec-offline-evaluation.md). Local verification results are recorded in the plan, not substituted for real-model evidence.

**Real provider/API evaluation remains unauthorized and unimplemented.** It requires separate explicit approval of the provider/endpoint and exact model/version, the fields/corpus/prompts/dictionaries sent out, and a currency-denominated **hard spending limit**. That proposal must also fix request/concurrency/retry and token limits, stop rules, data-retention/training policy, and log location. Offline approval grants no permission to obtain credentials, probe services, send data, or spend money. Real business-task gold and safety criteria would need their own design; mock completion cannot stand in for them.

## Limits

This MVP has no encryption, authentication, authorization, automatic dictionary updates, semantic compression, real-user corpus, model evaluation, action execution, online integration, network service, or deployment. Namespace checks are local metadata checks only. Inputs are read into memory without resource quotas; run untrusted large inputs with external resource limits. Protect message/dictionary/report files and stderr because they may contain private data. AC1 remains the default; AC2 is opt-in and experimental. Both carry only canonical `MessageV1` fields. See the [original design](docs/superpowers/specs/2026-10-19-agentcodec-design.md) and [approved AC2 experiment design](docs/superpowers/specs/2026-10-19-agentcodec-ac2-experiment-design.md) for the full boundary.

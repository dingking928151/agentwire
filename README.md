# AgentCodec

AgentCodec is an offline Python SDK and CLI for reversible, structured agent-message **data**. It preserves explicit fields and can replace whole, exactly matching content strings with codes from a manually maintained per-user dictionary. It does not connect to or operate any real agent, model, API, or service, and never executes message content.

**The current experiment is a negative compression result:** on the included 30-message synthetic corpus, both AgentCodec variants cost more estimated tokens than compact JSON, with both tested tokenizers. This is a format experiment, not a claim of token savings, actual API billing, or equivalent model task success.

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

Only `benchmark` needs tokenizer data. Both `o200k_base` and `cl100k_base` vocabulary files must already exist in the local tiktoken cache. Set `TIKTOKEN_CACHE_DIR` to the provisioned directory (otherwise `DATA_GYM_CACHE_DIR`, then the system temporary directory's `data-gym-cache`, is used). Cache names and required SHA-256 hashes are:

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

# A missing --input/--output, or the literal -, means stdin/stdout:
cat message.json | agentcodec encode | agentcodec decode
agentcodec validate --input - < message.json
```

Successful validation emits `{"status":"valid"}`; it does not approve the task. Encoding emits one `AC1|…` wire line; decoding emits canonical JSON. Each command accepts `--input`/`-i` and `--output`/`-o`. Files are UTF-8, output has one final newline, and an explicitly selected output file is overwritten on success. Decode/validate-wire accept one terminal line ending, not arbitrary whitespace or multiple messages. Encode/decode/validate accept one message; benchmark accepts JSONL.

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
```

Every dictionary selection requires an explicit `--user-id`. Both its owner and its version must match message metadata. Aliases replace only entire strings in `task`, `blocker`, `next_step`, `evidence`, and `constraints`; no substring, fuzzy, semantic, or automatic-learning replacement occurs. Literal strings resembling codes remain literal. Duplicate keys, duplicate phrases, unknown codes, and mismatches are rejected rather than guessed. Dictionaries are plaintext local data, not access-control credentials.

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

### What the included corpus showed

These are **local tokenizer estimates**, not API charges. For 30 synthetic messages, transmitted payload counts (no setup; fallback was zero) were:

| Format | `o200k_base` | `cl100k_base` |
| --- | ---: | ---: |
| Concise natural language | 2,640 | 2,651 |
| Compact JSON | 2,576 | 2,559 |
| DRIXL reference format | 6,266 | 6,209 |
| AgentCodec | 4,500 | 4,305 |
| AgentCodec + demo dictionary | 5,058 | 4,833 |

Setup adds 191/189 protocol tokens for AgentCodec; dictionary mode adds another 67 dictionary tokens. DRIXL setup is 113/110 tokens; natural language and compact JSON assume zero additional format setup. Thus both codec variants cost more than compact JSON even before setup, and the dictionary variant costs more than generic AgentCodec on this corpus. Percent escaping, alias tags, and dictionary-version metadata can outweigh short codes. Do not infer a general compression benefit.

Both codec variants recovered 30/30 full canonical messages, with zero changed/unrecovered critical fields and zero fallbacks. These metrics test deterministic codec recovery, not model understanding. Natural-language/JSON/DRIXL quality metrics are `null` because this project does not test decoders for those baselines.

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

## Limits

This MVP has no encryption, authentication, authorization, automatic dictionary updates, semantic compression, real-user corpus, model evaluation, action execution, online integration, network service, or deployment. Namespace checks are local metadata checks only. Inputs are read into memory without resource quotas; run untrusted large inputs with external resource limits. Protect message/dictionary/report files and stderr because they may contain private data. Only the AC1 v1 wire protocol and canonical `MessageV1` fields are supported. See the [approved design](docs/superpowers/specs/2026-10-19-agentcodec-design.md) for the full boundary.

# Security boundaries

## Messages are untrusted data

AgentCodec parses and serializes messages; it never evaluates code, runs commands, calls tools, sends messages, purchases anything, or follows instructions embedded in a task, evidence, constraint, alias, or decoded field. A syntactically valid message may still contain malicious or false instructions. Validation is not a safety assessment. Treat decoded JSON and benchmark output as untrusted when passing them to any other system; that system needs its own authentication, policy checks, permissions, and approval gates.

`approval_required` is a preserved **data field**, not authorization. Neither `true` nor `false`, nor a task claiming that approval was granted, confers permission to act. A message must never authorize its own execution.

## Dictionary isolation is explicit, not authentication

Dictionaries are manually maintained UTF-8 JSON with an owner (`user_id`), `version`, and exact whole-string aliases. CLI dictionary operations require an explicit expected `--user-id`; the loader rejects another owner, and the codec rejects message owner/version mismatches. Unknown aliases are not searched in another user's dictionary or guessed. Duplicate JSON keys, duplicate phrases, invalid dictionary values, and unavailable versions fail closed. Dictionaries are revalidated at codec boundaries; they are never automatically learned or modified.

These checks prevent accidental namespace mixing under the selected inputs. They are not proof of identity, provenance, or integrity: anyone who can edit a plaintext dictionary/message can change its owner or contents. There is no cryptographic signing, encryption, authenticated user session, secure multi-user storage, or access-control service. Use trusted dictionary sources, protect files with operating-system permissions, and keep each user's files separately. Do not put secrets in short codes expecting them to be hidden.

## Fallback means stop, not guess

Unknown AC1 versions, enums or aliases; malformed/noncanonical escaping; missing/extra fields; duplicate JSON keys; and dictionary mismatches require fallback. CLI data/I/O/tokenizer failures exit nonzero with a readable `fallback_required` reason; usage errors also exit nonzero. Decode never emits a partially recovered message. Preserve the sender's original canonical JSON separately: AC1 cannot reconstruct an unavailable dictionary or unknown code. Any retransmission or downstream action belongs to a separately authorized caller, not this CLI.

The benchmark models an original-JSON resend plus already transmitted payload when fallback occurs. That is token accounting only; no network send or execution occurs.

## Offline does not mean sandboxed

The application has no model/API client, network service, external-agent integration, or telemetry. Benchmarking uses local tiktoken ordinary-text estimates, not a model or billing API. Missing/corrupt local vocabularies cause failure before tiktoken's lazy-download path; the CLI does not fetch them or install dependencies. Provision trusted dependencies and vocabularies separately. This project is not a full dependency supply-chain audit or an OS-level network sandbox.

The legacy encode/decode/validate and token-benchmark CLI paths read user-selected files/stdin and write stdout or the selected output path. A successful operation overwrites that path; check it carefully and do not run with unnecessary privileges. Validation happens before output is opened, but their output writes are not atomic, and those legacy paths have no new path restrictions, symlink defenses, input-size limits, CPU/memory quotas, or protection against a local actor changing files concurrently. The separately documented legacy evaluate-offline command retains its own empty-output and snapshot checks. Adaptive protections below do not retrofit any legacy command. Use external resource limits for large/untrusted input and trusted directories for output.

Messages, dictionaries, decoded data, and reports are plaintext. Error details can include input values and local paths; terminal output may contain untrusted text. Keep sensitive inputs out of shared terminals/logs and apply your own retention policy. Tokenizer cache integrity checks are not a substitute for trusted dependencies, filesystem controls, or secure provisioning.

## Opt-in adaptive boundaries

`select-encoding` is per-message local advice only: record-only, zero retries, zero sends, no automatic fallback, no credential lookup and no message execution. `fallback_required` is a caller-handled status, not an action. `benchmark-adaptive` replays only explicitly fixed offline scenarios; simulated recovery is not a provider/API request. Local ordinary-text counts, exact round trips and mock receipts do not prove model understanding, business success, actual provider billing, or global optimality. Any real API/model/provider phase needs new permission.

The new entry points enforce an 8 MiB UTF-8 bound per input file/text snapshot (including full dictionary/context), SDK repeat totals 1–1,000,000, offline whole-group totals 0–1,000,000 (0 only dry-run), at most 100,000 history/expanded offline events and 10,000 cases per offline group. They reject excess input instead of truncating it. These are engineering limits, not CPU/memory quotas or denial-of-service guarantees. They do not change the old entry points' limits.

New file adapters accept explicit local regular files, not URLs, stdin aliases, FIFOs or other special files, and reject leaf symlinks during path checks. Ancestor paths are resolved rather than categorically prohibited. They validate size, UTF-8, duplicate keys and strict schemas; benchmark source/dictionary bytes are snapshotted once for hash/parse/order validation. Explicit outputs must be new files or an absent/empty directory, mutually distinct and not colliding with protected inputs. Output staging and scoped rollback avoid advertising a partially written report as success, but are not a cross-file power-loss transaction. Use trusted private directories and OS permissions: checks involving pathname resolution, preflight and later publication do **not** provide a full TOCTOU guarantee against concurrent hostile filesystem changes. Conditional path-resolution/runtime edge cases are not a comprehensive filesystem defense.

Resource identities bind scope, receiver, user, kind, version, content hash and tokenizer profile, with epoch and repeat-validity checks. They prevent accidental snapshot mixing, not malicious-owner forgery or remote authentication. Initialization is only proposed by selection; only caller-confirmed success with the expected epoch may produce a new ready snapshot. **ready is not free**: per-request context remains chargeable each time; independent setup/acknowledgment text is separately accounted when declared. Builtin decoders are caller declarations, not proof that an external receiver supports them. Incomplete history yields null cumulative cost, not free historical setup.

Missing, corrupt, changed or unreliable local tokenizers and necessary unknown costs produce cannot_compare/null estimates, never character/byte guesses or downloads. Explicit configuration/integrity errors take precedence and fail closed, even with JSON as the original format. Approximate mappings do not prove exact optimality. The router does not rewrite dictionary_version; separately authorized source/transport metadata domains must not share a savings denominator.

New default summaries and public report/records use allowlisted counts/reasons/local ordinals, not raw messages, terms, payloads, free-text identities or content hashes. Fixed error codes avoid the legacy arbitrary-error-detail channel. The explicit payload file and request/config/source/dictionary are still plaintext and sensitive; controlled manifests retain necessary local identities and hashes. A **hash is not anonymization** and may enable linkage or guessing. Apply a retention policy and do not publish these controlled files or raw old evaluation records. Old reports may still include full data; adaptive redaction does not change their format.

These checks are **not an OS sandbox**, authentication, encryption, signing, secure multi-user storage, prompt-injection protection, authorization to execute messages, a network firewall, or a full TOCTOU defense. They do not establish equivalent model understanding or actual bill savings.

## Reporting a suspected vulnerability

No public security contact or disclosure endpoint is configured in this local MVP. Share a minimal synthetic reproducer and affected version privately with the project owner through an established trusted channel. Do not publish real messages, dictionaries, credentials, or other private data as part of a report.

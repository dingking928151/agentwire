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

The CLI reads user-selected files/stdin and writes stdout or the selected output path. A successful operation overwrites that path; check it carefully and do not run with unnecessary privileges. Validation happens before output is opened, but output writes are not atomic, and there are no path restrictions, symlink defenses, input-size limits, CPU/memory quotas, or protection against a local actor changing files concurrently. Use external resource limits for large/untrusted input and trusted directories for output.

Messages, dictionaries, decoded data, and reports are plaintext. Error details can include input values and local paths; terminal output may contain untrusted text. Keep sensitive inputs out of shared terminals/logs and apply your own retention policy. Tokenizer cache integrity checks are not a substitute for trusted dependencies, filesystem controls, or secure provisioning.

## Reporting a suspected vulnerability

No public security contact or disclosure endpoint is configured in this local MVP. Share a minimal synthetic reproducer and affected version privately with the project owner through an established trusted channel. Do not publish real messages, dictionaries, credentials, or other private data as part of a report.

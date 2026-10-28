# AgentWire Compact Profile v1 (AWP1)

AWP1 is an opt-in, deterministic wire profile for transporting the complete `MessageV1` data record. It is not a replacement for the legacy AC1 default or the separately opt-in AC2 encoder. The implementation is local and side-effect-free: it does not contact a model or provider, dispatch work, execute message text, or automatically recover from a decoding failure.

## Message contract

Every encoded message contains exactly the eleven required `MessageV1` fields, in this fixed order. No fields may be omitted, inferred, reordered, coerced, or added.

| Position | Field | JSON value |
| ---: | --- | --- |
| 1 | `message_id` | Non-empty string |
| 2 | `message_type` | `delegate`, `status`, or `result`; encoded with the enum table below |
| 3 | `task` | String |
| 4 | `status` | `pending`, `running`, `blocked`, `completed`, `failed`, or `cancelled`; encoded with the enum table below |
| 5 | `blocker` | String or `null` |
| 6 | `next_step` | String or `null` |
| 7 | `evidence` | Array of strings |
| 8 | `constraints` | Array of strings |
| 9 | `approval_required` | Boolean |
| 10 | `user_id` | Non-empty string |
| 11 | `dictionary_version` | Non-empty string or `null` |

All fields are required even when a value is `null`, `false`, an empty string, or an empty array. Unicode scalar text is preserved as supplied: the codec does not normalize Unicode. Array order and repeated items are significant and preserved. `approval_required` is message data; it does not grant or represent authorization.

The v1 enum tables are fixed:

| Field | Text value | Wire code |
| --- | --- | --- |
| `message_type` | `delegate` | `D` |
| `message_type` | `status` | `S` |
| `message_type` | `result` | `R` |
| `status` | `pending` | `P` |
| `status` | `running` | `R` |
| `status` | `blocked` | `B` |
| `status` | `completed` | `C` |
| `status` | `failed` | `F` |
| `status` | `cancelled` | `X` |

Unknown enum codes are rejected; a receiver must not guess a newer table.

## Wire framing and JSON

The wire string is the exact prefix `AWP1|` followed by one compact JSON value:

```text
AWP1|[dictionary_identity_or_null, message_values]
```

The outer JSON array has exactly two items. `message_values` is the eleven-item array in the field table order. `dictionary_identity_or_null` is either `null` or the three-item array `[user_id, dictionary_version, lowercase_sha256]` for the explicitly selected dictionary. The digest is 64 lowercase hexadecimal characters.

The encoder uses UTF-8 JSON with Unicode emitted directly where JSON allows it, compact separators, and non-finite numbers disallowed. JSON itself escapes quotes, backslashes, and control characters as required; AWP1 does not percent-encode the payload. A literal `|` inside a string remains JSON string data. Receivers parse the JSON after the exact prefix; they must not split the body on delimiters or attempt another profile when parsing fails. Duplicate JSON object keys, non-finite numbers, invalid Unicode surrogate values, malformed framing, and an envelope with the wrong shape are rejected.

A receiver's `max_message_bytes` is a positive integer limit on the UTF-8 byte length of the complete payload string, including the `AWP1|` prefix and JSON framing. A payload exactly at the limit is allowed; one byte over is rejected. This is a byte limit, not a character count. The encoder does not know a receiver's limit; receivers enforce their own declared limit while parsing.

## Explicit dictionary references

A dictionary is never discovered, learned, or selected implicitly. Without an explicitly supplied dictionary, text stays literal and the outer identity is `null`. When a dictionary is selected, the message's `user_id` and `dictionary_version` must match it, and the outer identity binds the payload to that dictionary.

`dictionary_identity_for()` hashes the complete strict `UserDictionary` snapshot, not just the phrases used in one message. It serializes the validated dictionary model as UTF-8 JSON with sorted object keys, no extra whitespace, Unicode preserved, and non-finite numbers disallowed, then computes SHA-256. The identity carries the dictionary's `user_id`, `version`, and lowercase digest. A digest helps detect a different dictionary snapshot; it is not authentication or proof of who supplied it.

A selected dictionary may replace only a whole exact string in `task`, `blocker`, or `next_step`, or a whole string item in `evidence` or `constraints`. The wire representation is a typed object such as `{"$ref":"CODE"}`. It is not substring replacement, does not apply to IDs, enum fields, booleans, nulls, or arbitrary nested content, and must resolve using the exact selected dictionary whose identity matches both the envelope and receiver capabilities. An unknown code, absent dictionary, or identity mismatch fails closed. Literal strings are never interpreted as aliases merely because they resemble a dictionary entry.

## Receiver capabilities and local negotiation

`ReceiverCapabilities` declares:

- the receiver `user_id`;
- supported profile versions (AWP1 is named `AWP1`);
- supported message schema versions (`MessageV1`);
- supported enum-table versions (AWP1's table is named `AWP1`);
- the positive `max_message_bytes`; and
- an optional `dictionary_identity` for a dictionary already selected at that receiver.

Duplicate version declarations are invalid. A receiver must support the profile, schema, and enum table, and the message user must match the receiver user. If the envelope contains a dictionary identity, the caller must provide the matching local dictionary and the receiver must declare the same identity. If the envelope has no dictionary identity, passing a dictionary is also a mismatch.

`negotiate_awp1(receiver, payload, dictionary=None)` is a local compatibility check of the actual payload: it attempts the same strict decode and returns `supported` only if that receiver configuration can decode it. Otherwise it returns `fallback_required` with one stable reason code. It returns no payload and is not a network handshake, remote capability exchange, delivery acknowledgement, permission check, or instruction to send the payload.

## Stable failure reasons

`decode_awp1()` raises `AgentWireError` with one fixed, non-sensitive `reason_code`. Gateway results use the same codes and never include a partial message on failure.

| Reason code | Meaning |
| --- | --- |
| `unsupported_profile` | Receiver does not declare AWP1 support, or an AWP-prefixed payload uses an unsupported profile. |
| `unsupported_schema` | Receiver does not declare `MessageV1` support. |
| `unsupported_enum_table` | Receiver does not declare the AWP1 enum table. |
| `receiver_user_mismatch` | Decoded message `user_id` differs from the receiver's `user_id`. |
| `message_too_large` | Complete payload UTF-8 byte length exceeds `max_message_bytes`. |
| `invalid_envelope` | Prefix/body is malformed, JSON is invalid or ambiguous, or the outer envelope/identity shape is invalid. |
| `unknown_enum` | Message type or status contains an unknown wire code. |
| `dictionary_missing` | A dictionary-bound payload lacks the selected dictionary or receiver dictionary identity. |
| `dictionary_identity_mismatch` | Payload, message, supplied dictionary, and/or receiver dictionary identities do not match. |
| `unknown_alias` | A `$ref` code is not present in the selected dictionary. |
| `invalid_message` | The eleven decoded values fail strict `MessageV1` validation or contain invalid text. |

These codes describe protocol handling, not the underlying private value. Callers should not replace them with exception text that might contain message or dictionary content.

## Strict decode and gateway behavior

`encode_awp1(message, dictionary=None)` snapshots and validates a complete `MessageV1`, then produces the AWP1 envelope. `decode_awp1(payload, receiver, dictionary=None)` returns a complete `MessageV1` or raises a stable `AgentWireError`; it does not return partial fields. The legacy `decode_payload()` dispatcher remains separate and does not guess AWP1 from an unknown prefix: AWP1 requires the explicit AWP1 API.

`decode_at_gateway()` returns either `decoded` with the complete message, or `fallback_required` with `message=null` and a fixed reason code. It does not dispatch or execute the message, switch formats, retry, or resend the original. Handling a fallback is the caller's separate responsibility.

For explicitly declared structured candidates, `deliver_benchmark_at_gateway()` parses the selected format and, on success, gives the next local stage canonical compact JSON for all eleven `MessageV1` fields. The transmitted compact wire is therefore not necessarily the text the model-facing stage receives. The benchmark-only `natural_language_freeform` candidate is different: the gateway passes its text through without a structured decode, has no canonical `model_context`, and reports strict field equality as not applicable.

## Cost boundaries and interpretation

AgentWire reports transport bytes separately from token estimates and model-input text. In `model_direct` mode, the benchmark constructs a complete local request from the context prefix, format-specific instructions, payload, and—when selected—the dictionary identity and complete glossary. Those components are included in the request-side cost rather than treating the payload as the entire prompt. No model is called. Any response text in the benchmark is synthetic fixture data.

In `gateway` mode, structured wire text is decoded locally before the canonical message context is produced; transport size alone is not a measure of that model context. Gateway structured rows distinguish transport and model-input surfaces. A freeform gateway pass-through is a transport-only comparison and does not claim strict decode, model-input, or response-token measurements.

The benchmark reports the fixed `natural_language` renderer as a **labelled-field control**: it serializes the canonical fields under fixed labels. `natural_language_freeform` is a separate, manually authored freeform candidate with annotations. The latter is not parsed by the gateway. A token difference against the labelled-field control is not evidence of savings against real-world natural language, and neither baseline measures model comprehension, semantic preservation, task quality, provider billing, or general compression performance.

Token figures are local ordinary-text tokenizer estimates. If the tokenizer is unavailable, the profile is unreliable, or input contains surrogate-only text, the cost contract reports `cannot_compare` and null token totals/breakdowns rather than inventing zero or estimating from bytes. Transport byte counts remain distinct. Setup, negotiation, protocol instructions, context, dictionary glossary/transfer, payload, response, retry, fallback, and other text are attributed to their declared events and surfaces; undeclared costs are not implied to be free.

## Security and authority limits

AWP1 is a serialization format, not encryption, authentication, authorization, access control, anonymization, user consent, or a policy engine. A namespace check, capability declaration, dictionary digest, enum, status, or `approval_required` value cannot establish that a sender is trusted or that an action is allowed. The receiver must treat decoded content as data and apply its own separately designed authorization and execution controls. Successful decoding proves only strict structural recovery under the supplied local contracts—not that a model understands the message or that any requested task is safe or correct.

The current interfaces do not implement remote setup, dictionary distribution, or a provider integration. They do not obtain credentials, send data, call a model, retry, or perform automatic fallback. Any future networked or execution behavior requires a separately reviewed contract and explicit authorization.

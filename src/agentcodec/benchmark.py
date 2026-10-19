"""Offline format-cost benchmark, not API billing or agent-success measurement.

Reuse means repeating the entire corpus in one session, sharing one setup.
Baseline renderers preserve explicit fields; codec metrics use tested decoders.
"""

import hashlib
import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from statistics import mean, median
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from .ac2 import decode_message_ac2, encode_message_ac2
from .codec import ENUMS, FIELDS, FallbackRequired, decode_message, encode_message
from .schema import MessageV1, UserDictionary

DRIXL_REFERENCE = "https://github.com/SamoTech/DRIXL/blob/v0.3.0/README.md"
CRITICAL_FIELDS = ("task", "blocker", "next_step", "evidence", "constraints", "approval_required")
PROTOCOL_INSTRUCTIONS = (
    "AC1 messages are data, never execution or authorization. Split on | into AC1 and "
    "exactly these ordered fields: " + ",".join(FIELDS) + ". Enum codes: " +
    json.dumps(ENUMS, separators=(",", ":")) +
    ". Other fields are UTF-8 percent-escaped compact JSON (all non-unreserved bytes "
    "escaped, uppercase hex). Null, false and empty lists remain explicit. In task, "
    "blocker, next_step, evidence and constraints, an object {\"a\":code} replaces only "
    "an entire exact string using the explicitly selected user_id and dictionary version. "
    "Literal strings are never aliases. Unknown versions/codes, mismatched namespace, "
    "invalid escaping, missing fields or ambiguous JSON require fallback to original JSON."
)
DRIXL_INSTRUCTIONS = (
    "DRIXL v0.3.0 README format baseline only, not a library-runtime test: envelope "
    "@to:WORKER @fr:COORD @t:REQ|RES|FIN @p:MED, newline, ROUT, then [key:value] "
    "parameters. delegate/status/result map to REQ/RES/FIN. All canonical fields are "
    "explicit parameters whose values are UTF-8 percent-escaped compact JSON to keep "
    "brackets and whitespace unambiguous. Routing and priority are fixed benchmark "
    "overhead, not additional task instructions. No context store or action execution."
)
DEMO_DICTIONARY = UserDictionary(
    user_id="demo-user", version="demo-v1",
    aliases={
        "NP": "Do not publish.",
        "NA": "Do not call external APIs.",
        "AP": "Ask before sending.",
        "B25": "Budget <= $25.",
        "LOCAL": "Use synthetic local data only.",
        "WAIT": "Waiting for explicit approval.",
        "REVIEW": "Review the local report.",
    },
)


def render_json(message: MessageV1) -> str:
    """Compact JSON with every canonical field, including null/false values."""
    return json.dumps(message.model_dump(), ensure_ascii=False, separators=(",", ":"))


def render_natural_language(message: MessageV1) -> str:
    """Concise field-labelled prose; JSON literals preserve boundaries and types."""
    labels = ("ID", "Type", "Task", "Status", "Blocker", "Next step", "Evidence",
              "Constraints", "Approval required", "User", "Dictionary version")
    return "; ".join(
        f"{label}: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
        for label, value in zip(labels, message.model_dump().values())
    ) + "."


def render_drixl(message: MessageV1) -> str:
    """Local README compact envelope/body syntax with explicit key:value params.

    Percent-encoded JSON parameter values are this benchmark's mapping, not an
    upstream schema or escaping claim. ROUT is a data carrier, never executed.
    """
    kind = {"delegate": "REQ", "status": "RES", "result": "FIN"}[message.message_type]
    params = " ".join(
        f"[{key}:{quote(json.dumps(value, ensure_ascii=False, separators=(',', ':')), safe='')}]"
        for key, value in message.model_dump().items()
    )
    return f"@to:WORKER @fr:COORD @t:{kind} @p:MED\nROUT {params}"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate task JSON key: {key}")
        result[key] = value
    return result


def load_tasks(path: Path) -> list[MessageV1]:
    """Load strict v1 JSONL; ignore blank lines, reject duplicate keys and IDs."""
    tasks = []
    ids = set()
    # JSONL uses LF (optionally preceded by CR), not Unicode line separators.
    for number, line in enumerate(path.read_bytes().decode("utf-8").split("\n"), 1):
        if not line.strip():
            continue
        try:
            task = MessageV1.model_validate(
                json.loads(line, object_pairs_hook=_unique_object), strict=True,
            )
            if task.message_id in ids:
                raise ValueError("duplicate task message_id")
        except (ValueError, TypeError, RecursionError) as error:
            raise ValueError(f"invalid task at line {number}: {error}") from error
        ids.add(task.message_id)
        tasks.append(task)
    if not tasks:
        raise ValueError("empty task dataset")
    return tasks


# Public tiktoken vocabulary hashes; refusing absent/corrupt cache prevents its
# normal lazy network download. No package install or vocabulary fetch here.
_VOCAB_HASHES = {
    "o200k_base": "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
    "cl100k_base": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
}


@lru_cache(maxsize=2)
def _get_encoding(encoding_name: str):
    if encoding_name not in _VOCAB_HASHES:
        raise RuntimeError(f"unsupported tokenizer encoding: {encoding_name}")
    cache_dir = os.environ.get("TIKTOKEN_CACHE_DIR", os.environ.get(
        "DATA_GYM_CACHE_DIR", str(Path(tempfile.gettempdir()) / "data-gym-cache"),
    ))
    url = f"https://openaipublic.blob.core.windows.net/encodings/{encoding_name}.tiktoken"
    path = Path(cache_dir) / hashlib.sha1(url.encode()).hexdigest()
    try:
        if not cache_dir or hashlib.sha256(path.read_bytes()).hexdigest() != _VOCAB_HASHES[encoding_name]:
            raise RuntimeError(f"local tokenizer vocabulary cache absent or corrupt: {encoding_name}")
        import tiktoken
        return tiktoken.get_encoding(encoding_name)
    except (ImportError, OSError, ValueError) as error:
        raise RuntimeError(f"local tokenizer/cache unavailable: {encoding_name}: {error}") from error


def count_tokens(text: str, encoding_name: str) -> int:
    """Count ordinary text tokens, including literal special-token-looking data."""
    return len(_get_encoding(encoding_name).encode_ordinary(text))


class MessageCost(BaseModel):
    message_id: str
    payload_tokens: int
    fallback_tokens: int = 0
    total_tokens: int
    fallback_reason: str | None = None
    fields_equal: bool | None = None
    critical_constraint_loss_count: int | None = None


class AmortizedCost(BaseModel):
    total_tokens: int
    tokens_per_corpus: float
    tokens_per_message: float


class FormatResult(BaseModel):
    messages: list[MessageCost]
    payload_tokens: int
    fallback_tokens: int
    total_tokens: int
    protocol_tokens: int
    dictionary_tokens: int
    setup_tokens: int
    fallback_count: int
    round_trip_equal_count: int | None
    round_trip_equal_rate: float | None
    critical_constraint_loss_count: int | None
    amortized: dict[int, AmortizedCost]


class BenchmarkReport(BaseModel):
    report_version: str = "1"
    task_count: int
    reuse_counts: list[int]
    results: dict[str, dict[str, FormatResult]]
    protocol_instructions: dict[str, str]
    demo_dictionary: UserDictionary
    critical_fields: list[str] = Field(default_factory=lambda: list(CRITICAL_FIELDS))
    notes: list[str] = Field(default_factory=lambda: [
        "Local tiktoken ordinary-text estimates only; not API billing or task-success measurements.",
        "Reuse repeats the entire corpus with protocol/dictionary setup charged once per session.",
        "Fallback adds compact original JSON resend to any transmitted payload; no retry/ack overhead is modeled.",
        "Quality metrics measure pre-fallback codec field recovery; null baseline metrics mean no decoder was tested.",
        "Critical loss counts changed/unrecovered fields among critical_fields, not individual phrases or semantic losses.",
        "The dictionary condition selects demo-v1 transport metadata only when source dictionary_version is null; "
        "all other fields remain identical. Equality includes that explicit selected version; source tasks are not mutated.",
        "Natural language is concise labelled prose with typed JSON literals; JSON/NL assume no extra format setup.",
        "DRIXL is a local v0.3.0 README format baseline, not library-runtime validation: " + DRIXL_REFERENCE,
        "DRIXL uses fixed routing/priority and percent-encoded JSON values as this benchmark's field mapping; no shared context.",
        "Synthetic manually authored fixtures and a handpicked demo dictionary do not establish general compression benefits.",
    ])


def _codec_cost(message, dictionary, encoding_name, *, ac2=False):
    expected = message
    if dictionary is not None and message.dictionary_version is None:
        expected = message.model_copy(update={"dictionary_version": dictionary.version})
    payload_tokens = 0
    equal = False
    lost = len(CRITICAL_FIELDS)
    reason = None
    try:
        encoder = encode_message_ac2 if ac2 else encode_message
        decoder = decode_message_ac2 if ac2 else decode_message
        payload = encoder(expected, dictionary)
        payload_tokens = count_tokens(payload, encoding_name)
        decoded = decoder(payload, dictionary)
        equal = decoded.model_dump() == expected.model_dump()
        lost = sum(getattr(decoded, key) != getattr(expected, key) for key in CRITICAL_FIELDS)
        if not equal:
            reason = "decoded fields differ from canonical message"
    except FallbackRequired as error:
        reason = str(error)
    fallback_tokens = count_tokens(render_json(message), encoding_name) if reason is not None else 0
    return MessageCost(
        message_id=message.message_id, payload_tokens=payload_tokens,
        fallback_tokens=fallback_tokens, total_tokens=payload_tokens + fallback_tokens,
        fallback_reason=reason, fields_equal=equal, critical_constraint_loss_count=lost,
    )


def run_benchmark(tasks, encodings, reuse_counts=(1, 10, 50)) -> BenchmarkReport:
    """Compare five explicit-field formats. Fail rather than fabricate token counts."""
    tasks, encodings, reuse_counts = list(tasks), list(encodings), list(reuse_counts)
    if not tasks or not encodings or not reuse_counts:
        raise ValueError("tasks, encodings and reuse_counts must not be empty")
    if any(type(n) is not int or n < 1 for n in reuse_counts):
        raise ValueError("reuse counts must be positive integers")
    if len(set(encodings)) != len(encodings) or len(set(reuse_counts)) != len(reuse_counts):
        raise ValueError("duplicate encodings or reuse counts")
    # Revalidate mutable instances before rendering any condition.
    tasks = [MessageV1.model_validate(vars(task), strict=True) for task in tasks]
    if len({task.message_id for task in tasks}) != len(tasks):
        raise ValueError("duplicate task message_id")
    dictionary = UserDictionary.model_validate(DEMO_DICTIONARY.model_dump(), strict=True)
    instructions = {"natural_language": "", "json": "", "drixl": DRIXL_INSTRUCTIONS,
                    "codec": PROTOCOL_INSTRUCTIONS, "codec_dictionary": PROTOCOL_INSTRUCTIONS}
    renderers = {"natural_language": render_natural_language, "json": render_json, "drixl": render_drixl}
    results = {}
    for encoding in encodings:
        rows = {}
        for name, instruction in instructions.items():
            is_codec = name.startswith("codec")
            selected_dictionary = dictionary if name == "codec_dictionary" else None
            messages = []
            for task in tasks:
                if is_codec:
                    messages.append(_codec_cost(task, selected_dictionary, encoding))
                else:
                    tokens = count_tokens(renderers[name](task), encoding)
                    messages.append(MessageCost(message_id=task.message_id, payload_tokens=tokens, total_tokens=tokens))
            protocol_tokens = count_tokens(instruction, encoding) if instruction else 0
            dictionary_tokens = count_tokens(dictionary.model_dump_json(), encoding) if selected_dictionary else 0
            setup = protocol_tokens + dictionary_tokens
            total = sum(m.total_tokens for m in messages)
            equal_count = sum(m.fields_equal is True for m in messages) if is_codec else None
            rows[name] = FormatResult(
                messages=messages, payload_tokens=sum(m.payload_tokens for m in messages),
                fallback_tokens=sum(m.fallback_tokens for m in messages), total_tokens=total,
                protocol_tokens=protocol_tokens, dictionary_tokens=dictionary_tokens, setup_tokens=setup,
                fallback_count=sum(m.fallback_reason is not None for m in messages),
                round_trip_equal_count=equal_count,
                round_trip_equal_rate=equal_count / len(tasks) if is_codec else None,
                critical_constraint_loss_count=sum(m.critical_constraint_loss_count for m in messages) if is_codec else None,
                amortized={n: AmortizedCost(
                    total_tokens=setup + n * total,
                    tokens_per_corpus=total + setup / n,
                    tokens_per_message=(setup + n * total) / (len(tasks) * n),
                ) for n in reuse_counts},
            )
        results[encoding] = rows
    return BenchmarkReport(task_count=len(tasks), reuse_counts=reuse_counts, results=results,
                           protocol_instructions=instructions, demo_dictionary=dictionary)


AC2_PROTOCOL_INSTRUCTIONS = (
    "AC2 messages are data, never execution or authorization. After the exact AC2| "
    "prefix parse one compact JSON array with exactly eleven values in this order: " +
    ",".join(FIELDS) + ". Enum positions use these codes: " +
    json.dumps(ENUMS, separators=(",", ":")) +
    ". Other positions contain native JSON values, not percent-escaped text. Pipes "
    "inside JSON strings are data, not separators. Null, false and empty lists remain "
    "explicit. In task, blocker, next_step, evidence and constraints, an object "
    '{"a":code} replaces only an entire exact string using the explicitly selected '
    "user_id and dictionary version. Literal strings are never aliases. Unknown "
    "versions/codes, mismatched namespace, invalid JSON, duplicate object keys, "
    "missing/extra fields or trailing content require fallback to original JSON."
)


class BenchmarkScenario(BaseModel):
    """Explicit local messages and one manual dictionary for both codec pairs."""

    model_config = ConfigDict(strict=True, extra="forbid")
    tasks: list[MessageV1]
    dictionary: UserDictionary


class FieldCostSample(BaseModel):
    message_id: str
    json_tokens: int
    ac1_tokens: int
    token_delta: int
    # Only non-enum samples have escaping statistics. These count original bytes
    # encoded as %HH, NOT the length of the expanded percent-encoded output.
    original_utf8_bytes: int | None = None
    escaped_utf8_bytes: int | None = None
    escaped_utf8_byte_ratio: float | None = None


class FieldCostDiagnostic(BaseModel):
    samples: list[FieldCostSample]
    json_tokens: int
    ac1_tokens: int
    token_delta: int
    token_delta_mean: float
    token_delta_median: float
    escaped_utf8_byte_ratio: float | None = None


class AC1Diagnostics(BaseModel):
    diagnostic_only: bool = True
    condition: str = "ac1_no_dictionary"
    non_enum_fields: dict[str, FieldCostDiagnostic]
    enum_fields: dict[str, FieldCostDiagnostic]
    notes: list[str] = Field(default_factory=lambda: [
        "Field values use original canonical metadata, without dictionary selection or alias substitution.",
        "Non-enum deltas are percent-escaped compact JSON minus compact JSON value tokens; enum deltas are short-code minus JSON-literal tokens.",
        "Escape ratio counts original serialized UTF-8 bytes encoded as %HH divided by original serialized UTF-8 bytes; field aggregates are byte-weighted.",
        "Diagnostic-only independent field counts exclude keys, prefix and framing, and token boundaries are non-additive. Never use their sums as message payload totals.",
    ])


class ScenarioBenchmarkReport(BaseModel):
    task_count: int
    dictionary: UserDictionary
    results: dict[str, dict[str, FormatResult]]
    protocol_instructions: dict[str, str]
    ac1_diagnostics: dict[str, AC1Diagnostics]


class BenchmarkSuiteReport(BaseModel):
    report_version: str = "suite-v1"
    reuse_counts: list[int]
    scenarios: dict[str, ScenarioBenchmarkReport]
    critical_fields: list[str] = Field(default_factory=lambda: list(CRITICAL_FIELDS))
    notes: list[str] = Field(default_factory=lambda: [
        "Local tiktoken ordinary-text estimates only; not API billing, model comprehension, or task-success measurements.",
        "Scenarios stay separate; AC1 and AC2 dictionary conditions use the identical explicit scenario dictionary, with its complete serialized setup cost.",
        "Dictionary conditions select the scenario version only when source dictionary_version is null; other fields are unchanged. Equality includes the selected version; inputs are not mutated.",
        "Payload counts measure the complete transmitted wire text directly, never diagnostic field sums.",
        "Setup is protocol plus dictionary, charged once per session; reuse repeats the whole scenario corpus.",
        "Fallback adds compact original JSON resend to any transmitted payload; no retry/ack overhead is modeled.",
        "Quality metrics measure pre-fallback codec field recovery; null baseline metrics mean no decoder was tested. Critical loss counts changed/unrecovered critical fields, not semantic losses.",
        "Natural language is concise labelled prose with typed JSON literals; JSON/NL assume no extra setup.",
        "DRIXL is a local v0.3.0 README format baseline with fixed routing/priority and percent-encoded JSON values, not library-runtime validation: " + DRIXL_REFERENCE,
        "Synthetic manually authored fixtures and handpicked dictionaries do not establish general compression benefits; retain negative results.",
    ])


def _ac1_diagnostics(tasks, encoding):
    non_enum_fields, enum_fields = {}, {}
    for name in FIELDS:
        samples = []
        for task in tasks:
            value = getattr(task, name)
            literal = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            encoded = ENUMS[name][value] if name in ENUMS else quote(literal, safe="")
            json_tokens = count_tokens(literal, encoding)
            ac1_tokens = count_tokens(encoded, encoding)
            byte_stats = {}
            if name not in ENUMS:
                original_bytes = len(literal.encode("utf-8"))
                escaped_bytes = encoded.count("%")
                byte_stats = dict(original_utf8_bytes=original_bytes,
                                  escaped_utf8_bytes=escaped_bytes,
                                  escaped_utf8_byte_ratio=escaped_bytes / original_bytes)
            samples.append(FieldCostSample(
                message_id=task.message_id, json_tokens=json_tokens,
                ac1_tokens=ac1_tokens, token_delta=ac1_tokens - json_tokens, **byte_stats,
            ))
        ratio = None
        if name not in ENUMS:
            ratio = sum(s.escaped_utf8_bytes for s in samples) / sum(s.original_utf8_bytes for s in samples)
        diagnostic = FieldCostDiagnostic(
            samples=samples, json_tokens=sum(s.json_tokens for s in samples),
            ac1_tokens=sum(s.ac1_tokens for s in samples),
            token_delta=sum(s.token_delta for s in samples),
            token_delta_mean=mean(s.token_delta for s in samples),
            token_delta_median=median(s.token_delta for s in samples),
            escaped_utf8_byte_ratio=ratio,
        )
        (enum_fields if name in ENUMS else non_enum_fields)[name] = diagnostic
    return AC1Diagnostics(non_enum_fields=non_enum_fields, enum_fields=enum_fields)


def _summarize_format(messages, instruction, dictionary, encoding, reuse_counts, is_codec):
    protocol_tokens = count_tokens(instruction, encoding) if instruction else 0
    dictionary_tokens = count_tokens(dictionary.model_dump_json(), encoding) if dictionary is not None else 0
    setup = protocol_tokens + dictionary_tokens
    total = sum(m.total_tokens for m in messages)
    equal_count = sum(m.fields_equal is True for m in messages) if is_codec else None
    return FormatResult(
        messages=messages, payload_tokens=sum(m.payload_tokens for m in messages),
        fallback_tokens=sum(m.fallback_tokens for m in messages), total_tokens=total,
        protocol_tokens=protocol_tokens, dictionary_tokens=dictionary_tokens, setup_tokens=setup,
        fallback_count=sum(m.fallback_reason is not None for m in messages),
        round_trip_equal_count=equal_count,
        round_trip_equal_rate=equal_count / len(messages) if is_codec else None,
        critical_constraint_loss_count=sum(m.critical_constraint_loss_count for m in messages) if is_codec else None,
        amortized={n: AmortizedCost(
            total_tokens=setup + n * total,
            tokens_per_corpus=total + setup / n,
            tokens_per_message=(setup + n * total) / (len(messages) * n),
        ) for n in reuse_counts},
    )


def run_benchmark_suite(scenarios, encodings, reuse_counts=(1, 10, 50)) -> BenchmarkSuiteReport:
    """Compare seven formats for a mapping of names to BenchmarkScenario inputs.

    Each scenario explicitly supplies tasks and its manual dictionary; no corpus
    loading, dictionary learning, or network access occurs here. Mutable models
    are revalidated into local snapshots before any measurement.
    """
    encodings, reuse_counts = list(encodings), list(reuse_counts)
    if not scenarios or not encodings or not reuse_counts:
        raise ValueError("scenarios, encodings and reuse_counts must not be empty")
    if any(type(n) is not int or n < 1 for n in reuse_counts):
        raise ValueError("reuse counts must be positive integers")
    if len(set(encodings)) != len(encodings) or len(set(reuse_counts)) != len(reuse_counts):
        raise ValueError("duplicate encodings or reuse counts")
    snapshots = {}
    for name, scenario in scenarios.items():
        if not isinstance(name, str) or not name:
            raise ValueError("scenario names must be nonempty strings")
        scenario = BenchmarkScenario.model_validate(scenario, strict=True)
        tasks = [MessageV1.model_validate(vars(task), strict=True) for task in scenario.tasks]
        dictionary = UserDictionary.model_validate(vars(scenario.dictionary), strict=True)
        if not tasks or len({task.message_id for task in tasks}) != len(tasks):
            raise ValueError("empty scenario tasks or duplicate task message_id")
        snapshots[name] = BenchmarkScenario(tasks=tasks, dictionary=dictionary)
    instructions = {
        "natural_language": "", "json": "", "drixl": DRIXL_INSTRUCTIONS,
        "ac1": PROTOCOL_INSTRUCTIONS, "ac1_dictionary": PROTOCOL_INSTRUCTIONS,
        "ac2": AC2_PROTOCOL_INSTRUCTIONS, "ac2_dictionary": AC2_PROTOCOL_INSTRUCTIONS,
    }
    renderers = {"natural_language": render_natural_language, "json": render_json, "drixl": render_drixl}
    reports = {}
    for name, scenario in snapshots.items():
        results, diagnostics = {}, {}
        for encoding in encodings:
            rows = {}
            for fmt, instruction in instructions.items():
                is_codec = fmt.startswith(("ac1", "ac2"))
                dictionary = scenario.dictionary if fmt.endswith("_dictionary") else None
                messages = []
                for task in scenario.tasks:
                    if is_codec:
                        messages.append(_codec_cost(task, dictionary, encoding, ac2=fmt.startswith("ac2")))
                    else:
                        tokens = count_tokens(renderers[fmt](task), encoding)
                        messages.append(MessageCost(message_id=task.message_id, payload_tokens=tokens, total_tokens=tokens))
                rows[fmt] = _summarize_format(messages, instruction, dictionary, encoding, reuse_counts, is_codec)
            results[encoding] = rows
            diagnostics[encoding] = _ac1_diagnostics(scenario.tasks, encoding)
        reports[name] = ScenarioBenchmarkReport(
            task_count=len(scenario.tasks), dictionary=scenario.dictionary,
            results=results, protocol_instructions=instructions, ac1_diagnostics=diagnostics,
        )
    return BenchmarkSuiteReport(reuse_counts=reuse_counts, scenarios=reports)

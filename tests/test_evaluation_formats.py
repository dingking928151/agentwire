"""Offline input formats restore exact data, never infer or execute messages."""

import importlib
import json
from pathlib import Path
from urllib.parse import quote

import pytest

from agentcodec.codec import FallbackRequired
from agentcodec.schema import MessageV1, UserDictionary


FORMATS = ("natural_language", "json", "ac1", "ac2")
FIELDS = (
    "message_id", "message_type", "task", "status", "blocker", "next_step",
    "evidence", "constraints", "approval_required", "user_id", "dictionary_version",
)
LABELS = (
    "ID", "Type", "Task", "Status", "Blocker", "Next step", "Evidence",
    "Constraints", "Approval required", "User", "Dictionary version",
)
# Handwritten, distinct values and complete goldens; no production builder is an oracle.
DATA = {
    "message_id": "m1", "message_type": "delegate", "task": "T", "status": "blocked",
    "blocker": "B", "next_step": "N", "evidence": ["E"], "constraints": ["C"],
    "approval_required": True, "user_id": "user-1", "dictionary_version": None,
}
GOLDENS = {
    "natural_language": 'ID: "m1"; Type: "delegate"; Task: "T"; Status: "blocked"; '
                        'Blocker: "B"; Next step: "N"; Evidence: ["E"]; Constraints: ["C"]; '
                        'Approval required: true; User: "user-1"; Dictionary version: null.',
    "json": '{"message_id":"m1","message_type":"delegate","task":"T","status":"blocked",'
            '"blocker":"B","next_step":"N","evidence":["E"],"constraints":["C"],'
            '"approval_required":true,"user_id":"user-1","dictionary_version":null}',
    "ac1": 'AC1|%22m1%22|D|%22T%22|B|%22B%22|%22N%22|%5B%22E%22%5D|%5B%22C%22%5D|true|%22user-1%22|null',
    "ac2": 'AC2|["m1","D","T","B","B","N",["E"],["C"],true,"user-1",null]',
}
DICTIONARY = UserDictionary(user_id="user-1", version="v1", aliases={"A": "T"})
PAIRED_GOLDENS = {
    "ac1": 'AC1|%22m1%22|D|%7B%22a%22%3A%22A%22%7D|B|%22B%22|%22N%22|%5B%22E%22%5D|%5B%22C%22%5D|true|%22user-1%22|%22v1%22',
    "ac2": 'AC2|["m1","D",{"a":"A"},"B","B","N",["E"],["C"],true,"user-1","v1"]',
}


def formats():
    return importlib.import_module("agentcodec.evaluation.formats")


def literal_payload(data, input_format):
    """Independent fixture serializer for malformed NL/JSON schema cases only."""
    if input_format == "json":
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return "; ".join(
        label + ": " + json.dumps(data[field], ensure_ascii=False, separators=(",", ":"))
        for label, field in zip(LABELS, FIELDS) if field in data
    ) + "."


@pytest.mark.parametrize("input_format", FORMATS)
def test_four_formats_restore_every_field(input_format):
    # Catches loss, swapped fields, changed bytes, and wrong renderer selection.
    api = formats()
    source = MessageV1(**DATA)
    assert api.render_input(source, input_format) == GOLDENS[input_format]
    assert api.parse_input(GOLDENS[input_format], input_format, "user-1").model_dump() == DATA
    assert source.model_dump() == DATA


def test_ac1_is_default_and_ac2_is_explicit():
    api = formats()
    source = MessageV1(**DATA)
    assert api.render_input(source) == GOLDENS["ac1"]
    assert api.render_input(source, "ac2") == GOLDENS["ac2"]


def test_nl_delimiters_are_values():
    payload = GOLDENS["natural_language"].replace('Task: "T"', 'Task: "; Next step: fake"')
    assert formats().parse_input(payload, "natural_language", "user-1").model_dump() == (
        DATA | {"task": "; Next step: fake"}
    )


@pytest.mark.parametrize("input_format", FORMATS)
@pytest.mark.parametrize("missing_index", range(11), ids=FIELDS)
def test_missing_each_of_eleven_fields_is_rejected(input_format, missing_index):
    if input_format == "natural_language":
        parts = GOLDENS[input_format][:-1].split("; ")
        del parts[missing_index]
        payload = "; ".join(parts) + "."
    elif input_format == "json":
        data = DATA.copy()
        del data[FIELDS[missing_index]]
        payload = json.dumps(data)
    elif input_format == "ac1":
        parts = GOLDENS[input_format].split("|")
        del parts[missing_index + 1]
        payload = "|".join(parts)
    else:
        parts = json.loads(GOLDENS[input_format][4:])
        del parts[missing_index]
        payload = "AC2|" + json.dumps(parts)
    with pytest.raises(FallbackRequired):
        formats().parse_input(payload, input_format, "user-1")


@pytest.mark.parametrize("declared,actual", [
    (declared, actual) for declared in FORMATS for actual in FORMATS if declared != actual
])
def test_declared_format_never_dispatches_or_falls_back(declared, actual):
    with pytest.raises(FallbackRequired):
        formats().parse_input(GOLDENS[actual], declared, "user-1")


@pytest.mark.parametrize("input_format", FORMATS)
def test_expected_namespace_is_checked_for_every_format(input_format):
    with pytest.raises(FallbackRequired):
        formats().parse_input(GOLDENS[input_format], input_format, "other-user")


@pytest.mark.parametrize("field", FIELDS)
def test_json_duplicate_keys_are_rejected_even_when_equal(field):
    payload = GOLDENS["json"][:-1] + "," + json.dumps(field) + ":" + json.dumps(DATA[field]) + "}"
    with pytest.raises(FallbackRequired):
        formats().parse_input(payload, "json", "user-1")


@pytest.mark.parametrize("input_format", ("natural_language", "json"))
@pytest.mark.parametrize("changes", [
    {"message_id": ""}, {"message_type": "D"}, {"status": "B"}, {"task": None},
    {"blocker": 1}, {"next_step": False}, {"evidence": [1]}, {"constraints": "C"},
    {"approval_required": 1}, {"user_id": ""}, {"dictionary_version": ""},
    {"task": {"a": "A"}}, {"evidence": [{"a": "A"}]},
])
def test_nl_and_json_use_strict_schema_without_alias_expansion(input_format, changes):
    with pytest.raises(FallbackRequired):
        formats().parse_input(literal_payload(DATA | changes, input_format), input_format, "user-1")


@pytest.mark.parametrize("payload", [
    GOLDENS["json"][:-1] + ',"extra":null}',
    GOLDENS["json"] + "{}", GOLDENS["json"] + " trailing",
    "[]", "null", "true", "", '{"task":"T","task":"T"}',
    GOLDENS["json"].replace('"T"', "NaN"),
    GOLDENS["json"].replace('"T"', "Infinity"),
])
def test_json_rejects_extra_structure_and_trailing_content(payload):
    with pytest.raises(FallbackRequired):
        formats().parse_input(payload, "json", "user-1")


@pytest.mark.parametrize("payload", [
    GOLDENS["natural_language"].replace('ID: "m1"; Type: "delegate"', 'Type: "delegate"; ID: "m1"'),
    GOLDENS["natural_language"] + " trailing", GOLDENS["natural_language"] + "\n",
    GOLDENS["natural_language"][:-1], " " + GOLDENS["natural_language"],
    GOLDENS["natural_language"][:-1] + '; Extra: null.',
    GOLDENS["natural_language"].replace('Task: "T"', 'Task: "T"; Task: "T"'),
    GOLDENS["natural_language"].replace("Next step:", "Next Step:"),
    GOLDENS["natural_language"].replace('; Type:', ';Type:'),
    'Please delegate T to user-1; the task is blocked.', "",
])
def test_nl_accepts_only_fixed_ordered_eleven_label_syntax(payload):
    with pytest.raises(FallbackRequired):
        formats().parse_input(payload, "natural_language", "user-1")


@pytest.mark.parametrize("input_format", FORMATS)
@pytest.mark.parametrize("text", [
    ' 不得|付款\\路径%2F\n\r\t"quoted"; Next step: fake ',
    "a\u0085b\u2028c\u2029d", "e\u0301 ≠ é 🧪",
    '{"a":"A"}', "A", "Do not publish. ",
])
def test_boundary_text_is_preserved_without_trimming_or_normalization(input_format, text):
    api = formats()
    data = DATA | {"task": text, "blocker": "", "next_step": None,
                   "evidence": [text, "", text], "constraints": [text, text]}
    restored = api.parse_input(api.render_input(MessageV1(**data), input_format), input_format, "user-1")
    assert restored.model_dump() == data


@pytest.mark.parametrize("input_format", FORMATS)
@pytest.mark.parametrize("changes", [
    {"constraints": ["Do not publish.", "Do not publish.", "Use synthetic local data only."]},
    {"blocker": "", "next_step": "", "evidence": [""], "constraints": [], "approval_required": False},
    {"constraints": ["Do not publish. ", "Do not call external APIs.", "Use synthetic local data only."]},
], ids=["s17-duplicates", "s22-empty-values", "s27-trailing-space"])
def test_corpus_boundary_shapes_restore_exactly(input_format, changes):
    api = formats()
    data = DATA | changes
    assert api.parse_input(api.render_input(MessageV1(**data), input_format), input_format, "user-1").model_dump() == data


@pytest.mark.parametrize("input_format", ("natural_language", "json"))
def test_plain_formats_preserve_version_as_metadata_and_reject_dictionary(input_format):
    api = formats()
    data = DATA | {"task": "A", "evidence": ['{"a":"A"}'], "dictionary_version": "legacy-v0"}
    payload = literal_payload(data, input_format)
    assert api.parse_input(payload, input_format, "user-1").model_dump() == data
    assert api.render_input(MessageV1(**data), input_format) == payload
    with pytest.raises(FallbackRequired):
        api.parse_input(payload, input_format, "user-1", DICTIONARY)
    with pytest.raises(FallbackRequired):
        api.render_input(MessageV1(**data), input_format, DICTIONARY)


@pytest.mark.parametrize("input_format", ("ac1", "ac2"))
def test_codecs_use_same_explicit_dictionary_and_preserve_literals(input_format):
    api = formats()
    data = DATA | {"dictionary_version": "v1"}
    before = DICTIONARY.model_dump()
    assert api.render_input(MessageV1(**data), input_format, DICTIONARY) == PAIRED_GOLDENS[input_format]
    assert api.parse_input(PAIRED_GOLDENS[input_format], input_format, "user-1", DICTIONARY).model_dump() == data
    literals = data | {"task": "A", "blocker": '{"a":"A"}', "evidence": ["T", "A", "prefix T", "T!"]}
    payload = api.render_input(MessageV1(**literals), input_format, DICTIONARY)
    assert api.parse_input(payload, input_format, "user-1", DICTIONARY).model_dump() == literals
    assert DICTIONARY.model_dump() == before


@pytest.mark.parametrize("input_format", ("ac1", "ac2"))
@pytest.mark.parametrize("selection", [None,
    UserDictionary(user_id="other-user", version="v1", aliases={"A": "T"}),
    UserDictionary(user_id="user-1", version="v2", aliases={"A": "T"}),
    UserDictionary(user_id="user-1", version="v1", aliases={"UNKNOWN": "T"}),
], ids=["missing", "wrong-user", "wrong-version", "unknown-alias"])
def test_codec_dictionary_mismatch_never_guesses(input_format, selection):
    with pytest.raises(FallbackRequired):
        formats().parse_input(PAIRED_GOLDENS[input_format], input_format, "user-1", selection)


@pytest.mark.parametrize("input_format", ("ac1", "ac2"))
@pytest.mark.parametrize("selection", [None,
    UserDictionary(user_id="other-user", version="v1", aliases={"A": "T"}),
    UserDictionary(user_id="user-1", version="v2", aliases={"A": "T"}),
])
def test_codec_render_rejects_unavailable_or_mismatched_dictionary(input_format, selection):
    with pytest.raises(FallbackRequired):
        formats().render_input(MessageV1(**(DATA | {"dictionary_version": "v1"})), input_format, selection)


@pytest.mark.parametrize("input_format", ("ac1", "ac2"))
@pytest.mark.parametrize("mutation", ["extra", "type-enum", "status-enum", "trailing", "prefix", "truncated"])
def test_codec_wire_errors_require_fallback(input_format, mutation):
    payload = GOLDENS[input_format]
    if mutation == "extra":
        payload = payload + "|null" if input_format == "ac1" else payload[:-1] + ",null]"
    elif mutation in ("type-enum", "status-enum"):
        if input_format == "ac1":
            parts = payload.split("|")
            parts[2 if mutation == "type-enum" else 4] = "Z"
            payload = "|".join(parts)
        else:
            parts = json.loads(payload[4:])
            parts[1 if mutation == "type-enum" else 3] = "Z"
            payload = "AC2|" + json.dumps(parts)
    elif mutation == "trailing":
        payload += " garbage"
    elif mutation == "prefix":
        payload = payload.replace(input_format.upper(), "AC99", 1)
    else:
        payload = payload[:-1]
    with pytest.raises(FallbackRequired):
        formats().parse_input(payload, input_format, "user-1")


@pytest.mark.parametrize("bad_task", ["%22%2f%22", "%22%54%22", "%22bad%GG%22", "%22%FF%22", '"T"'])
def test_ac1_rejects_noncanonical_or_invalid_escaping(bad_task):
    with pytest.raises(FallbackRequired):
        formats().parse_input(GOLDENS["ac1"].replace("%22T%22", bad_task), "ac1", "user-1")


@pytest.mark.parametrize("input_format", FORMATS)
@pytest.mark.parametrize("surrogate", ["\\ud800", "\\udfff"])
def test_lone_surrogates_require_fallback(input_format, surrogate):
    if input_format == "ac1":
        payload = GOLDENS[input_format].replace("%22T%22", quote('"' + surrogate + '"', safe=""))
    elif input_format == "natural_language":
        payload = GOLDENS[input_format].replace('Task: "T"', 'Task: "' + surrogate + '"')
    else:
        payload = GOLDENS[input_format].replace('"T"', '"' + surrogate + '"')
    with pytest.raises(FallbackRequired):
        formats().parse_input(payload, input_format, "user-1")


@pytest.mark.parametrize("input_format", ["unknown", "AC1", "ac1_dictionary", None, []])
def test_unknown_format_is_rejected_not_defaulted(input_format):
    api = formats()
    with pytest.raises(FallbackRequired):
        api.render_input(MessageV1(**DATA), input_format)
    with pytest.raises(FallbackRequired):
        api.parse_input(GOLDENS["ac1"], input_format, "user-1")


@pytest.mark.parametrize("input_format", FORMATS)
@pytest.mark.parametrize("payload", [None, b"AC1|", 1, {}])
def test_nontext_payload_requires_fallback(input_format, payload):
    with pytest.raises(FallbackRequired):
        formats().parse_input(payload, input_format, "user-1")


@pytest.mark.parametrize("input_format", FORMATS)
@pytest.mark.parametrize("mutation", ["not-model", "missing", "extra", "wrong-type", "surrogate"])
def test_render_revalidates_current_model_fields(input_format, mutation):
    message = MessageV1(**DATA)
    if mutation == "not-model":
        message = DATA.copy()
    elif mutation == "missing":
        del message.task
    elif mutation == "extra":
        message.__dict__["extra"] = "must not leak"
    elif mutation == "wrong-type":
        message.__dict__["approval_required"] = 1
    else:
        message.__dict__["task"] = "\ud800"
    with pytest.raises(FallbackRequired):
        formats().render_input(message, input_format)


@pytest.mark.parametrize("input_format", FORMATS)
def test_existing_corpora_restore_all_fields_read_only(input_format):
    api = formats()
    root = Path(__file__).resolve().parents[1]
    for name in ("tasks.v1.jsonl", "tasks-repetitive.v1.jsonl"):
        rows = (root / "bench" / name).read_text(encoding="utf-8").split("\n")
        for row in filter(None, rows):
            data = json.loads(row)
            payload = api.render_input(MessageV1(**data), input_format)
            assert api.parse_input(payload, input_format, data["user_id"]).model_dump() == data

"""AC2 wire contracts: lossless data only, never executable authorization."""

import json
import sys

import pytest
from hypothesis import given, settings, strategies as st

from agentcodec.codec import FallbackRequired, decode_message, encode_message
from agentcodec.schema import MessageV1, UserDictionary


# Independent wire fixture catches field order, compactness, and enum drift.
VALUES = ["m-1", "D", "Do not pay $20.50 before 2026-11-01", "P", None,
          "Ask approval", ["receipt:42"], ["Never publish"], True, "jason", None]
GOLDEN = ('AC2|["m-1","D","Do not pay $20.50 before 2026-11-01","P",null,'
          '"Ask approval",["receipt:42"],["Never publish"],true,"jason",null]')


def message(**changes):
    return MessageV1(**({
        "message_id": "m-1", "message_type": "delegate",
        "task": "Do not pay $20.50 before 2026-11-01", "status": "pending",
        "blocker": None, "next_step": "Ask approval", "evidence": ["receipt:42"],
        "constraints": ["Never publish"], "approval_required": True,
        "user_id": "jason", "dictionary_version": None,
    } | changes))


def wire(values):
    return "AC2|" + json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def test_exact_golden_all_fields_and_determinism():
    from agentcodec.ac2 import decode_message_ac2, encode_message_ac2

    source = message()
    before = source.model_dump()
    assert encode_message_ac2(source) == GOLDEN
    assert decode_message_ac2(GOLDEN).model_dump() == before
    assert encode_message_ac2(source) == GOLDEN
    assert source.model_dump() == before


@pytest.mark.parametrize("kind,code", [("delegate", "D"), ("status", "S"), ("result", "R")])
@pytest.mark.parametrize("status,status_code", [
    ("pending", "P"), ("running", "R"), ("blocked", "B"),
    ("completed", "C"), ("failed", "F"), ("cancelled", "X"),
])
def test_existing_enum_short_codes(kind, code, status, status_code):
    from agentcodec.ac2 import decode_message_ac2, encode_message_ac2

    source = message(message_type=kind, status=status)
    values = VALUES.copy()
    values[1], values[3] = code, status_code
    assert encode_message_ac2(source) == wire(values)
    assert decode_message_ac2(wire(values)) == source


@pytest.mark.parametrize("text", [
    '|%2F\\\n\r\t"quoted"', ' 不得付款🧪 e\u0301\u2028\u2029 ',
    '{"a":"x"}', 'AC1|AC2|', '', '\x00',
])
@pytest.mark.parametrize("optional", [None, ""])
def test_native_json_strings_and_empty_values_are_lossless(text, optional):
    from agentcodec.ac2 import decode_message_ac2, encode_message_ac2

    source = message(message_id="id" + text, task=text, blocker=optional,
                     next_step=text, evidence=[text, "", text], constraints=[],
                     approval_required=False, user_id="user" + text)
    payload = encode_message_ac2(source)
    assert json.loads(payload[4:])[2] == text
    assert decode_message_ac2(payload).model_dump() == source.model_dump()


def test_only_exact_approved_content_strings_are_aliased():
    from agentcodec.ac2 import decode_message_ac2, encode_message_ac2

    phrase = "Never publish"
    dictionary = UserDictionary(user_id="jason", version="v1", aliases={
        "x|%": phrase, "id": "m-1", "user": "jason", "version": "v1",
        "kind": "delegate", "state": "pending", "code": "D", "bool": "true",
    })
    source = message(task=phrase, blocker=phrase, next_step=phrase,
                     evidence=[phrase, "prefix " + phrase, phrase + "!", "x|%", '{"a":"x|%"}'],
                     constraints=[phrase, phrase.lower(), phrase + " "], dictionary_version="v1")
    before, dictionary_before = source.model_dump(), dictionary.model_dump()
    expected = ["m-1", "D", {"a": "x|%"}, "P", {"a": "x|%"}, {"a": "x|%"},
                [{"a": "x|%"}, "prefix Never publish", "Never publish!", "x|%", '{"a":"x|%"}'],
                [{"a": "x|%"}, "never publish", "Never publish "], True, "jason", "v1"]
    assert encode_message_ac2(source, dictionary) == wire(expected)
    assert decode_message_ac2(wire(expected), dictionary).model_dump() == before
    assert source.model_dump() == before
    assert dictionary.model_dump() == dictionary_before


@pytest.mark.parametrize("payload", [
    None, 42, b"AC2|[]", "", "AC2", "AC2x|[]", "AC20|[]", "AC1|[]",
    "ac2|[]", " AC2|[]", "AC99|[]", "AC2||[]", "AC2|", "AC2|[",
    "AC2|{}", "AC2|null", "AC2|false", "AC2|\"text\"", "AC2|[]",
    GOLDEN + "null", GOLDEN + "|", GOLDEN + "[]", GOLDEN + ",",
    GOLDEN.replace('"Do not pay $20.50 before 2026-11-01"', '"raw\nnewline"'),
    GOLDEN.replace('"Do not pay $20.50 before 2026-11-01"', '{"a":"x","a":"y"}'),
    'AC2|' + '[' * (sys.getrecursionlimit() + 100) + 'null' + ']' * (sys.getrecursionlimit() + 100),
])
def test_invalid_json_prefix_and_container_require_typed_fallback(payload):
    from agentcodec.ac2 import decode_message_ac2

    with pytest.raises(FallbackRequired) as error:
        decode_message_ac2(payload)
    assert error.value.status == "fallback_required"


@pytest.mark.parametrize("values", [VALUES[:-1], VALUES + [None], VALUES[:2] + VALUES[3:]])
def test_wrong_array_length_never_discards_or_infers_fields(values):
    from agentcodec.ac2 import decode_message_ac2

    with pytest.raises(FallbackRequired):
        decode_message_ac2(wire(values))


@pytest.mark.parametrize("index,bad", [
    (0, ""), (0, None), (0, {"a": "id"}), (1, "delegate"), (1, "d"), (1, "Z"),
    (1, None), (1, ["D"]), (1, {"a": "kind"}), (2, None), (2, 42),
    (2, ["task"]), (2, "\ud800"), (3, "pending"), (3, "p"), (3, "Z"),
    (3, True), (3, {}), (4, False), (4, []), (5, 42), (5, []),
    (6, "evidence"), (6, [None]), (6, [42]), (6, [["nested"]]),
    (7, None), (7, [False]), (7, [["nested"]]), (8, 1), (8, "true"), (8, None),
    (9, ""), (9, False), (9, {"a": "user"}), (10, ""), (10, 1), (10, {}),
    (2, float("nan")), (4, float("inf")), (6, [float("-inf")]),
])
def test_invalid_schema_and_enum_values_fail_closed(index, bad):
    from agentcodec.ac2 import decode_message_ac2

    values = VALUES.copy()
    values[index] = bad
    with pytest.raises(FallbackRequired):
        decode_message_ac2(wire(values))


@pytest.mark.parametrize("index", [2, 4, 5, 6, 7])
@pytest.mark.parametrize("bad", [{}, {"a": "unknown"}, {"a": 1}, {"a": None},
                                  {"a": ["x"]}, {"a": "x", "extra": "data"}, {"b": "x"}])
def test_malformed_or_unknown_aliases_fail_closed(index, bad):
    from agentcodec.ac2 import decode_message_ac2

    dictionary = UserDictionary(user_id="jason", version="v1", aliases={"x": "Never publish"})
    values = VALUES.copy()
    values[10] = "v1"
    values[index] = [bad] if index in (6, 7) else bad
    with pytest.raises(FallbackRequired):
        decode_message_ac2(wire(values), dictionary)


def test_duplicate_alias_keys_rejected_even_when_codes_are_known():
    from agentcodec.ac2 import decode_message_ac2

    dictionary = UserDictionary(user_id="jason", version="v1", aliases={"x": "Never pay", "y": "Pay"})
    payload = GOLDEN.replace('"Do not pay $20.50 before 2026-11-01"', '{"a":"x","a":"y"}')
    payload = payload[:-5] + '"v1"]'
    with pytest.raises(FallbackRequired):
        decode_message_ac2(payload, dictionary)


@pytest.mark.parametrize("operation", ["encode", "decode"])
@pytest.mark.parametrize("selection", ["missing", "owner", "version", "unexpected", "mapping"])
def test_dictionary_selection_never_crosses_namespaces(operation, selection):
    from agentcodec.ac2 import decode_message_ac2, encode_message_ac2

    source = message(dictionary_version="v1")
    values = VALUES.copy()
    values[10] = "v1"
    dictionary = UserDictionary(user_id="other" if selection == "owner" else "jason",
                                version="v2" if selection == "version" else "v1", aliases={})
    if selection == "missing":
        dictionary = None
    elif selection == "mapping":
        dictionary = dictionary.model_dump()
    elif selection == "unexpected":
        source = message()
        values[10] = None
    with pytest.raises(FallbackRequired):
        if operation == "encode":
            encode_message_ac2(source, dictionary)
        else:
            decode_message_ac2(wire(values), dictionary)


def test_alias_without_selected_dictionary_requires_fallback():
    from agentcodec.ac2 import decode_message_ac2

    values = VALUES.copy()
    values[2] = {"a": "x"}
    with pytest.raises(FallbackRequired):
        decode_message_ac2(wire(values))


@pytest.mark.parametrize("operation", ["encode", "decode"])
@pytest.mark.parametrize("changes", [
    {"aliases": {"x": "same", "y": "same"}}, {"aliases": {"x": []}},
    {"aliases": {"x": 42}}, {"aliases": {"": "phrase"}},
    {"user_id": ""}, {"version": None}, {"extra": "ignored?"},
])
def test_mutated_dictionaries_are_revalidated(operation, changes):
    from agentcodec.ac2 import decode_message_ac2, encode_message_ac2

    dictionary = UserDictionary(user_id="jason", version="v1", aliases={"x": "Never publish"})
    dictionary.__dict__.update(changes)
    values = VALUES.copy()
    values[10] = "v1"
    with pytest.raises(FallbackRequired):
        if operation == "encode":
            encode_message_ac2(message(dictionary_version="v1"), dictionary)
        else:
            decode_message_ac2(wire(values), dictionary)


@pytest.mark.parametrize("mutation", ["mapping", "missing", "extra", "enum", "approval", "surrogate"])
def test_encoder_revalidates_current_message_fields(mutation):
    from agentcodec.ac2 import encode_message_ac2

    source = message()
    if mutation == "mapping":
        source = source.model_dump()
    elif mutation == "missing":
        del source.task
    else:
        name, value = {"extra": ("extra", "ignored?"), "enum": ("status", "Z"),
                       "approval": ("approval_required", 1), "surrogate": ("task", "\udfff")}[mutation]
        source.__dict__[name] = value
    with pytest.raises(FallbackRequired):
        encode_message_ac2(source)


@pytest.mark.parametrize("format", ["ac1", "ac2"])
@pytest.mark.parametrize("with_dictionary", [False, True])
def test_dispatch_exact_protocol_prefix_with_dictionary(format, with_dictionary):
    from agentcodec.ac2 import encode_message_ac2
    from agentcodec.codec import decode_payload

    dictionary = UserDictionary(user_id="jason", version="v1", aliases={"x": "Never publish"}) if with_dictionary else None
    source = message(dictionary_version="v1" if with_dictionary else None)
    payload = (encode_message if format == "ac1" else encode_message_ac2)(source, dictionary)
    assert decode_payload(payload, dictionary) == source


@pytest.mark.parametrize("payload", [None, b"AC2|[]", 42, "", "AC1", "AC2", "AC3|[]",
                                     "ac1|[]", "AC1x|[]", "AC2x|[]", " AC2|[]", "AC2|[]", "AC1|[]"])
def test_dispatch_unknown_or_malformed_input_requires_fallback(payload):
    from agentcodec.codec import decode_payload

    with pytest.raises(FallbackRequired):
        decode_payload(payload)


def test_ac1_specific_decoder_still_rejects_ac2():
    with pytest.raises(FallbackRequired):
        decode_message(GOLDEN)


@settings(max_examples=100, deadline=None)
@given(text=st.text(), optional=st.one_of(st.none(), st.text()),
       evidence=st.lists(st.text(), max_size=5), approval=st.booleans())
def test_generated_unicode_and_exact_alias_round_trip(text, optional, evidence, approval):
    from agentcodec.ac2 import decode_message_ac2, encode_message_ac2

    phrase = "Never publish|\n🧪"
    dictionary = UserDictionary(user_id="jason", version="v1", aliases={"x|%": phrase})
    source = message(message_id="id" + text, task=text, blocker=optional,
                     next_step=phrase, evidence=evidence + [phrase],
                     constraints=[phrase, "prefix " + phrase, text],
                     approval_required=approval, dictionary_version="v1")
    before, dictionary_before = source.model_dump(), dictionary.model_dump()
    payload = encode_message_ac2(source, dictionary)
    assert decode_message_ac2(payload, dictionary).model_dump() == before
    assert encode_message_ac2(source, dictionary) == payload
    assert source.model_dump() == before
    assert dictionary.model_dump() == dictionary_before

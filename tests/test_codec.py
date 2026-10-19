"""Codec contract tests: no message content is executable authorization."""

import pytest


def fields(**changes):
    data = dict(
        message_id="m-1", message_type="delegate", task="Do not buy before 2026-11-01; budget $20.50",
        status="pending", blocker=None, next_step="Ask approval", evidence=["receipt:42"],
        constraints=["Do not exceed $20.50", "Never publish"], approval_required=True,
        user_id="jason", dictionary_version=None,
    )
    return data | changes


def test_round_trip_preserves_every_field():
    # Catches any omitted field or lost negation/date/amount/approval value.
    from agentcodec.schema import MessageV1
    from agentcodec.codec import encode_message, decode_message

    message = MessageV1(**fields())
    payload = encode_message(message)
    assert decode_message(payload).model_dump() == fields()
    assert encode_message(message) == payload


@pytest.mark.parametrize("kind,code", [("delegate", "D"), ("status", "S"), ("result", "R")])
@pytest.mark.parametrize("status,code_status", [
    ("pending", "P"), ("running", "R"), ("blocked", "B"),
    ("completed", "C"), ("failed", "F"), ("cancelled", "X"),
])
def test_fixed_enum_codes_round_trip(kind, code, status, code_status):
    # Catches uncompressed or non-reversible enum encoding.
    from agentcodec.schema import MessageV1
    from agentcodec.codec import encode_message, decode_message

    message = MessageV1(**fields(message_type=kind, status=status))
    wire = encode_message(message)
    assert wire.split("|")[2] == code
    assert wire.split("|")[4] == code_status
    assert decode_message(wire) == message


def test_escaped_delimiters_unicode_and_newlines():
    # Catches splitting free text at delimiters or altering Unicode/whitespace.
    from agentcodec.schema import MessageV1
    from agentcodec.codec import encode_message, decode_message

    text = ' 不得|付款\\路径%2F\n证据:🧪 e\u0301\r\n\t"quoted" '
    message = MessageV1(**fields(message_id=text, task=text, blocker="", next_step=text,
                                  evidence=[text, "", text], constraints=[text], user_id=text))
    wire = encode_message(message)
    assert len(wire.split("|")) == 12
    assert "%7C" in wire and "%25" in wire
    assert decode_message(wire).model_dump() == message.model_dump()


@pytest.mark.parametrize("mutation", [
    "protocol", "enum", "missing", "extra", "escape", "utf8", "json", "type", "bool", "none_payload",
])
def test_invalid_wire_requires_fallback(mutation):
    # Catches accepting unknown versions/codes, malformed escapes, or invalid fields.
    from agentcodec.schema import MessageV1
    from agentcodec.codec import encode_message, decode_message, FallbackRequired

    parts = encode_message(MessageV1(**fields())).split("|")
    if mutation == "protocol":
        parts[0] = "AC99"
    elif mutation == "enum":
        parts[2] = "Z"
    elif mutation == "missing":
        parts.pop()
    elif mutation == "extra":
        parts.append("null")
    elif mutation == "escape":
        parts[3] = "%22bad%GG%22"
    elif mutation == "utf8":
        parts[3] = "%22%FF%22"
    elif mutation == "json":
        parts[3] = "%22unterminated"
    elif mutation == "type":
        parts[7] = "%5B123%5D"
    elif mutation == "bool":
        parts[9] = "1"
    wire = None if mutation == "none_payload" else "|".join(parts)
    with pytest.raises(FallbackRequired) as error:
        decode_message(wire)
    assert error.value.status == "fallback_required"
    assert str(error.value)


@pytest.mark.parametrize("changes", [
    {"message_type": "unknown"}, {"status": "unknown"}, {"message_id": ""},
    {"user_id": ""}, {"dictionary_version": ""}, {"approval_required": 1},
    {"constraints": "Do not publish"}, {"evidence": [123]}, {"extra": "ignored?"},
])
def test_schema_rejects_invalid_fields(changes):
    # Catches silently coercing or ignoring invalid canonical message fields.
    from pydantic import ValidationError
    from agentcodec.schema import MessageV1

    with pytest.raises(ValidationError):
        MessageV1(**fields(**changes))


def test_dictionary_model_validates_exact_unique_entries():
    # Catches accepting ambiguous phrase values or coercing dictionary metadata.
    from pydantic import ValidationError
    from agentcodec.schema import UserDictionary

    dictionary = UserDictionary(user_id="jason", version="v1", aliases={"a": "Never publish"})
    assert dictionary.aliases == {"a": "Never publish"}
    for changes in [
        {"user_id": ""}, {"version": ""}, {"version": 1},
        {"aliases": {"a": "same", "b": "same"}}, {"aliases": {"": "phrase"}},
        {"aliases": {"a": ""}}, {"aliases": {1: "phrase"}}, {"aliases": {"a": 1}},
        {"extra": "ignored?"},
    ]:
        with pytest.raises(ValidationError):
            UserDictionary(**({"user_id": "jason", "version": "v1", "aliases": {}} | changes))


def test_dictionary_substitution_is_exact_and_reversible():
    # Catches substring substitution, alias/raw collisions, or loss of list order.
    from agentcodec.schema import MessageV1, UserDictionary
    from agentcodec.codec import encode_message, decode_message

    phrase = "Do not publish before 2026-11-01; budget $20.50"
    dictionary = UserDictionary(user_id="jason", version="v1", aliases={"a|%": phrase})
    message = MessageV1(**fields(task=phrase, blocker=phrase, next_step="a|%",
        evidence=[phrase, "prefix " + phrase, "a|%"], constraints=[phrase, phrase + "!"],
        dictionary_version="v1"))
    wire = encode_message(message, dictionary)
    assert wire.split("|")[3] == "%7B%22a%22%3A%22a%7C%25%22%7D"
    assert decode_message(wire, dictionary) == message
    assert encode_message(message, dictionary) == wire


@pytest.mark.parametrize("invalid", ["mapping", "enum", "approval", "missing", "extra"])
def test_encode_invalid_fields_requires_fallback(invalid):
    # Catches leaking invalid/mutated model contents or raw implementation errors.
    from agentcodec.schema import MessageV1
    from agentcodec.codec import encode_message, FallbackRequired

    message = MessageV1(**fields())
    if invalid == "mapping":
        message = fields()
    elif invalid == "missing":
        del message.task
    elif invalid == "extra":
        message = MessageV1.model_construct(**fields(), extra="ignored?")
        message.__dict__["extra"] = "ignored?"
    else:
        message.__dict__["message_type" if invalid == "enum" else "approval_required"] = "INVALID"
    with pytest.raises(FallbackRequired):
        encode_message(message)


@pytest.mark.parametrize("operation", ["encode", "decode"])
@pytest.mark.parametrize("selection", ["missing", "wrong_version"])
def test_unknown_dictionary_version_requires_fallback(operation, selection):
    # Catches accepting a declared dictionary version without the selected dictionary.
    from agentcodec.schema import MessageV1, UserDictionary
    from agentcodec.codec import encode_message, decode_message, FallbackRequired

    message = MessageV1(**fields(dictionary_version="v1"))
    correct = UserDictionary(user_id="jason", version="v1", aliases={})
    selected = None if selection == "missing" else UserDictionary(user_id="jason", version="v2", aliases={})
    wire = encode_message(message, correct)
    with pytest.raises(FallbackRequired):
        if operation == "encode":
            encode_message(message, selected)
        else:
            decode_message(wire, selected)


def test_ambiguous_json_alias_object_requires_fallback():
    # Catches JSON duplicate keys silently selecting one of two different phrases.
    from agentcodec.schema import MessageV1, UserDictionary
    from agentcodec.codec import encode_message, decode_message, FallbackRequired

    dictionary = UserDictionary(user_id="jason", version="v1", aliases={"a": "Never pay", "b": "Pay"})
    parts = encode_message(MessageV1(**fields(dictionary_version="v1")), dictionary).split("|")
    parts[3] = "%7B%22a%22%3A%22a%22%2C%22a%22%3A%22b%22%7D"
    with pytest.raises(FallbackRequired):
        decode_message("|".join(parts), dictionary)


@pytest.mark.parametrize("operation", ["encode", "decode"])
@pytest.mark.parametrize("mutation", ["duplicate_phrase", "unhashable_phrase", "non_string_phrase", "empty_code"])
def test_mutated_dictionary_requires_fallback(operation, mutation):
    # Catches trusting a once-valid mutable model rather than all current entries.
    from agentcodec.schema import MessageV1, UserDictionary
    from agentcodec.codec import encode_message, decode_message, FallbackRequired

    dictionary = UserDictionary(user_id="jason", version="v1", aliases={"a": "Never publish", "b": "Ask approval"})
    message = MessageV1(**fields(task="Never publish", next_step="Unaliased next step", dictionary_version="v1"))
    wire = encode_message(message, dictionary)
    if mutation == "duplicate_phrase":
        dictionary.aliases["b"] = "Never publish"
    elif mutation == "unhashable_phrase":
        dictionary.aliases["b"] = ["Never publish"]
    elif mutation == "non_string_phrase":
        dictionary.aliases = {"a": "Never publish", "b": 42}
    else:
        dictionary.aliases[""] = "Do not pay"

    with pytest.raises(FallbackRequired) as error:
        if operation == "encode":
            encode_message(message, dictionary)
        else:
            decode_message(wire, dictionary)
    assert error.value.status == "fallback_required"


@pytest.mark.parametrize("stage", ["parser", "alias_expansion"])
def test_deeply_nested_wire_json_requires_fallback(stage):
    # Catches parser/expander recursion escaping the typed fallback boundary.
    import sys

    from agentcodec.schema import MessageV1
    from agentcodec.codec import encode_message, decode_message, FallbackRequired

    parts = encode_message(MessageV1(**fields())).split("|")
    depth = sys.getrecursionlimit() + 100
    if stage == "parser":
        depth = max(10000, 4 * depth)
    parts[3] = "%5B" * depth + "null" + "%5D" * depth
    with pytest.raises(FallbackRequired) as error:
        decode_message("|".join(parts))
    assert error.value.status == "fallback_required"

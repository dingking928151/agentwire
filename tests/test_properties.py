"""Generated losslessness checks for Unicode scalar text and manual dictionaries."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote

from hypothesis import example, given, settings, strategies as st

from agentcodec.codec import decode_message, encode_message
from agentcodec.schema import MessageV1


# Unicode scalar strings: no normalization, stripping, or delimiter exclusion.
text = st.text()
identifier = st.text(min_size=1)
optional_text = st.one_of(st.none(), text)


@st.composite
def messages(draw):
    return MessageV1(
        message_id=draw(identifier),
        message_type=draw(st.sampled_from(["delegate", "status", "result"])),
        task=draw(text),
        status=draw(st.sampled_from([
            "pending", "running", "blocked", "completed", "failed", "cancelled",
        ])),
        blocker=draw(optional_text), next_step=draw(optional_text),
        evidence=draw(st.lists(text, max_size=5)),
        constraints=draw(st.lists(text, max_size=5)),
        approval_required=draw(st.booleans()), user_id=draw(identifier),
        dictionary_version=None,
    )


@settings(max_examples=100, deadline=None)
@given(source=messages())
def test_arbitrary_unicode_message_round_trip(source):
    # Catches loss/coercion of any field, enum, list, optional value, or approval.
    expected = source.model_dump()
    wire = encode_message(source)
    assert decode_message(wire).model_dump() == expected
    assert source.model_dump() == expected
    assert encode_message(source) == wire


@settings(max_examples=100, deadline=None)
@given(prefix=text, suffix=text, optional=optional_text)
@example(prefix="e\u0301", suffix="🧪中文", optional=None)
@example(prefix="", suffix="", optional="")
def test_delimiters_and_optional_values_round_trip(prefix, suffix, optional):
    # Catches delimiter splitting, double unescaping, normalization, or null/empty loss.
    value = prefix + '|%2F\\\n\r\t"{}[]:;=' + suffix
    source = MessageV1(
        message_id=value, message_type="delegate", task=value, status="blocked",
        blocker=optional, next_step=optional, evidence=[value, "", value],
        constraints=["Do not pay $20.50 before 2026-11-01", value],
        approval_required=True, user_id=value, dictionary_version=None,
    )
    wire = encode_message(source)
    assert len(wire.split("|")) == 12
    assert decode_message(wire).model_dump() == source.model_dump()


@settings(max_examples=100, deadline=None)
@given(
    user_id=identifier, version=identifier,
    aliases=st.dictionaries(identifier, identifier, min_size=1, max_size=5).filter(
        lambda mapping: len(set(mapping.values())) == len(mapping)
    ),
    raw=text, optional=optional_text, approval=st.booleans(),
)
@example(
    user_id="jason", version="v1", aliases={"a|%": "Never publish", "b": "e\u0301🧪"},
    raw='{"a":"a|%"}', optional="", approval=True,
)
def test_loaded_approved_substitutions_round_trip(user_id, version, aliases, raw, optional, approval):
    # Catches wrong/inexact aliases, raw-alias confusion, or loaded namespace metadata loss.
    from agentcodec.dictionary import load_dictionary

    with TemporaryDirectory() as directory:
        path = Path(directory) / "dictionary.json"
        path.write_text(json.dumps({
            "user_id": user_id, "version": version, "aliases": aliases,
        }, ensure_ascii=False), encoding="utf-8")
        dictionary = load_dictionary(path, user_id)
    code, phrase = next(iter(aliases.items()))
    source = MessageV1(
        message_id=phrase, message_type="result", task=phrase, status="completed",
        blocker=optional, next_step=raw, evidence=[*aliases.values(), code, raw],
        constraints=[phrase, "prefix " + phrase, phrase + "!"],
        approval_required=approval, user_id=user_id, dictionary_version=version,
    )
    original = source.model_dump()
    dictionary_before = dictionary.model_dump()
    wire = encode_message(source, dictionary)
    parts = wire.split("|")
    assert json.loads(unquote(parts[1])) == phrase  # identifiers are never aliased
    assert json.loads(unquote(parts[3])) == {"a": code}
    assert json.loads(unquote(parts[7]))[:len(aliases)] == [{"a": key} for key in aliases]
    # Unapproved whole strings remain literal even when they contain an approved phrase.
    for value, encoded in zip(source.constraints, json.loads(unquote(parts[8]))):
        if value not in aliases.values():
            assert encoded == value
    assert decode_message(wire, dictionary).model_dump() == original
    assert encode_message(source, dictionary) == wire
    assert source.model_dump() == original
    assert dictionary.model_dump() == dictionary_before

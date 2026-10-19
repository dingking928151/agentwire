"""Manual dictionary loading and fail-closed user/version isolation."""

import json
from urllib.parse import quote, unquote

import pytest

from agentcodec.codec import FallbackRequired, decode_message, encode_message
from agentcodec.schema import MessageV1, UserDictionary


def message(**changes):
    return MessageV1(**(dict(
        message_id="m-1", message_type="delegate", task="Never publish",
        status="pending", blocker=None, next_step="Ask approval",
        evidence=["receipt:42"], constraints=["Never publish"],
        approval_required=True, user_id="jason", dictionary_version="v1",
    ) | changes))


def write_dictionary(tmp_path, **changes):
    path = tmp_path / "dictionary.json"
    path.write_text(json.dumps(dict(
        user_id="jason", version="v1", aliases={"a": "Never publish"},
    ) | changes, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_valid_json_reuses_schema_type_and_preserves_file(tmp_path):
    # Catches dropping metadata, changing exact Unicode entries, or learning on disk.
    from agentcodec.dictionary import load_dictionary

    path = write_dictionary(tmp_path, aliases={"a|%": " 不得付款 🧪\ne\u0301 "})
    original = path.read_bytes()
    loaded = load_dictionary(path, expected_user_id="jason")
    assert isinstance(loaded, UserDictionary)
    assert loaded.model_dump() == {
        "user_id": "jason", "version": "v1", "aliases": {"a|%": " 不得付款 🧪\ne\u0301 "},
    }
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_load_rejects_wrong_user_id(tmp_path):
    # Catches loading another user's namespace even when the JSON is valid.
    from agentcodec.dictionary import load_dictionary

    with pytest.raises(FallbackRequired):
        load_dictionary(write_dictionary(tmp_path), expected_user_id="other")


@pytest.mark.parametrize("operation", ["encode", "decode"])
def test_wrong_dictionary_version_requires_fallback(tmp_path, operation):
    # Catches either codec path consuming entries before matching the version.
    from agentcodec.dictionary import load_dictionary

    dictionary = load_dictionary(write_dictionary(tmp_path), "jason")
    source = message()
    wire = encode_message(source, dictionary)
    wrong = load_dictionary(write_dictionary(tmp_path, version="v2"), "jason")
    with pytest.raises(FallbackRequired):
        if operation == "encode":
            encode_message(source, wrong)
        else:
            decode_message(wire, wrong)


@pytest.mark.parametrize("raw", [
    '{"user_id":"jason","version":"v1","aliases":{"a":"Never publish","a":"Publish"}}',
    '{"user_id":"other","user_id":"jason","version":"v1","aliases":{}}',
    '{"user_id":"jason","version":"v2","version":"v1","aliases":{}}',
])
def test_load_rejects_duplicate_json_keys(tmp_path, raw):
    # Catches json.loads silently overwriting duplicate codes or identity metadata.
    from agentcodec.dictionary import load_dictionary

    path = tmp_path / "dictionary.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(FallbackRequired):
        load_dictionary(path, "jason")


def test_load_rejects_duplicate_phrase_values(tmp_path):
    # Catches an ambiguous reverse map surviving the loader boundary.
    from agentcodec.dictionary import load_dictionary

    path = write_dictionary(tmp_path, aliases={"a": "Never publish", "b": "Never publish"})
    with pytest.raises(FallbackRequired):
        load_dictionary(path, "jason")


def test_unknown_alias_requires_fallback(tmp_path):
    # Catches guessing an unknown alias or treating the alias object as raw text.
    from agentcodec.dictionary import load_dictionary

    dictionary = load_dictionary(write_dictionary(tmp_path), "jason")
    parts = encode_message(message(), dictionary).split("|")
    parts[3] = quote('{"a":"unknown"}', safe="")
    with pytest.raises(FallbackRequired) as error:
        decode_message("|".join(parts), dictionary)
    assert error.value.status == "fallback_required"


@pytest.mark.parametrize("operation", ["encode", "decode"])
def test_cross_user_alias_collision_never_leaks(tmp_path, operation):
    # Catches resolving the same alias/version from a different user's dictionary.
    from agentcodec.dictionary import load_dictionary

    jason = load_dictionary(write_dictionary(tmp_path), "jason")
    other = load_dictionary(write_dictionary(
        tmp_path, user_id="other", aliases={"a": "Other user's private phrase"},
    ), "other")
    source = message()
    wire = encode_message(source, jason)
    assert json.loads(unquote(wire.split("|")[3])) == {"a": "a"}
    with pytest.raises(FallbackRequired):
        if operation == "encode":
            encode_message(source, other)
        else:
            decode_message(wire, other)
    other_message = message(user_id="other", task="Other user's private phrase")
    assert decode_message(encode_message(other_message, other), other) == other_message
    assert decode_message(wire, jason) == source


@pytest.mark.parametrize("raw", [
    "not json", "[]", "null", "{}",
    '{"user_id":"jason","version":"v1","aliases":{"a":42}}',
    '{"user_id":"jason","version":"v1","aliases":{"":"phrase"}}',
    '{"user_id":"jason","version":"v1","aliases":{"a":""}}',
    '{"user_id":"jason","version":1,"aliases":{}}',
    '{"user_id":"jason","version":"v1","aliases":{},"extra":true}',
])
def test_invalid_dictionary_data_requires_fallback(tmp_path, raw):
    # Catches bypassing strict schema validation or leaking parser exceptions.
    from agentcodec.dictionary import load_dictionary

    path = tmp_path / "dictionary.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(FallbackRequired):
        load_dictionary(path, "jason")


@pytest.mark.parametrize("kind", ["missing", "invalid_utf8", "directory", "deep_json"])
def test_unreadable_dictionary_requires_fallback(tmp_path, kind):
    # Catches read/Unicode/recursion errors escaping the typed fallback boundary.
    from agentcodec.dictionary import load_dictionary

    path = tmp_path / "dictionary.json"
    if kind == "invalid_utf8":
        path.write_bytes(b"\xff")
    elif kind == "directory":
        path.mkdir()
    elif kind == "deep_json":
        path.write_text("[" * 10000 + "null" + "]" * 10000, encoding="utf-8")
    with pytest.raises(FallbackRequired):
        load_dictionary(path, "jason")

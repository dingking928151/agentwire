"""Scalar Unicode is required; reject surrogates rather than repair content."""

import json
from urllib.parse import quote

import pytest
from pydantic import ValidationError

from agentcodec.codec import FIELDS, FallbackRequired, decode_message, encode_message
from agentcodec.dictionary import load_dictionary
from agentcodec.schema import MessageV1, UserDictionary


TEXT_FIELDS = (
    "message_id", "task", "blocker", "next_step", "evidence", "constraints",
    "user_id", "dictionary_version",
)
SURROGATES = ["\ud800", "\udfff"]


def message_values(**changes):
    return dict(message_id="unicode-1", message_type="delegate", task="Never publish",
                status="pending", blocker=None, next_step="Ask approval",
                evidence=["local.txt"], constraints=["Do not send"],
                approval_required=True, user_id="jason", dictionary_version=None) | changes


def dictionary_values(field, text):
    data = dict(user_id="jason", version="v1", aliases={"a": "Never publish"})
    if field == "alias_key":
        data["aliases"] = {text: "Never publish"}
    elif field == "alias_value":
        data["aliases"] = {"a": text}
    else:
        data[field] = text
    return data


@pytest.mark.parametrize("surrogate", SURROGATES)
@pytest.mark.parametrize("field", TEXT_FIELDS)
def test_message_construction_rejects_lone_surrogate(field, surrogate):
    # Catches accepting non-scalar content in any text field or list item.
    value = [surrogate] if field in ("evidence", "constraints") else surrogate
    with pytest.raises(ValidationError):
        MessageV1(**message_values(**{field: value}))


@pytest.mark.parametrize("surrogate", SURROGATES)
@pytest.mark.parametrize("field", TEXT_FIELDS)
def test_escaped_wire_lone_surrogate_requires_typed_fallback(field, surrogate):
    # Catches successful decoding of ASCII JSON escapes that are not scalar Unicode.
    dictionary = UserDictionary(user_id="jason", version="v1", aliases={})
    parts = encode_message(MessageV1(**message_values(dictionary_version="v1")), dictionary).split("|")
    value = [surrogate] if field in ("evidence", "constraints") else surrogate
    parts[FIELDS.index(field) + 1] = quote(json.dumps(value, ensure_ascii=True), safe="")
    with pytest.raises(FallbackRequired) as error:
        decode_message("|".join(parts), dictionary)
    assert error.value.status == "fallback_required"


@pytest.mark.parametrize("surrogate", SURROGATES)
@pytest.mark.parametrize("field", TEXT_FIELDS)
def test_mutated_message_lone_surrogate_requires_typed_fallback(field, surrogate):
    # Catches UnicodeEncodeError or unchecked content after model mutation.
    message = MessageV1(**message_values())
    value = [surrogate] if field in ("evidence", "constraints") else surrogate
    setattr(message, field, value)
    with pytest.raises(FallbackRequired) as error:
        encode_message(message)
    assert error.value.status == "fallback_required"


@pytest.mark.parametrize("surrogate", SURROGATES)
@pytest.mark.parametrize("field", ["user_id", "version", "alias_key", "alias_value"])
def test_dictionary_construction_rejects_lone_surrogate(field, surrogate):
    # Catches accepting invalid metadata, alias keys, or phrases into the schema.
    with pytest.raises(ValidationError):
        UserDictionary(**dictionary_values(field, surrogate))


@pytest.mark.parametrize("surrogate", SURROGATES)
@pytest.mark.parametrize("field", ["user_id", "version", "alias_key", "alias_value"])
def test_dictionary_loading_lone_surrogate_requires_typed_fallback(tmp_path, field, surrogate):
    # Catches escaped invalid scalars surviving JSON parsing and file validation.
    data = dictionary_values(field, surrogate)
    path = tmp_path / "dictionary.json"
    path.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
    with pytest.raises(FallbackRequired) as error:
        load_dictionary(path, expected_user_id=data["user_id"])
    assert error.value.status == "fallback_required"


@pytest.mark.parametrize("surrogate", SURROGATES)
@pytest.mark.parametrize("field", ["alias_key", "alias_value"])
@pytest.mark.parametrize("operation", ["encode", "decode"])
def test_mutated_dictionary_lone_surrogate_requires_typed_fallback(field, surrogate, operation):
    # Catches invalid alias expansion and invalid unused entries bypassing snapshots.
    dictionary = UserDictionary(user_id="jason", version="v1", aliases={"a": "Never publish"})
    message = MessageV1(**message_values(dictionary_version="v1"))
    wire = encode_message(message, dictionary)
    if field == "alias_key":
        dictionary.aliases[surrogate] = "Unused phrase"
    else:
        dictionary.aliases["a"] = surrogate
    with pytest.raises(FallbackRequired) as error:
        if operation == "encode":
            encode_message(message, dictionary)
        else:
            decode_message(wire, dictionary)
    assert error.value.status == "fallback_required"


def test_python_surrogate_pair_is_rejected_without_normalization():
    # Python surrogate code points are not an astral scalar, even when adjacent.
    with pytest.raises(ValidationError):
        MessageV1(**message_values(task="\ud83e\uddea"))


def test_astral_unicode_round_trips_in_every_text_field_and_dictionary(tmp_path):
    # Catches overbroad rejection of valid non-BMP scalars or Unicode normalization.
    text = "🧪 𐀀 e\u0301"
    data = message_values(**{field: [text] if field in ("evidence", "constraints") else text
                             for field in TEXT_FIELDS})
    source = MessageV1(**data)
    dictionary_data = dict(user_id=text, version=text, aliases={text: text})
    path = tmp_path / "dictionary.json"
    path.write_text(json.dumps(dictionary_data, ensure_ascii=True), encoding="utf-8")
    dictionary = load_dictionary(path, expected_user_id=text)
    wire = encode_message(source, dictionary)
    assert decode_message(wire, dictionary).model_dump() == data
    assert json.loads(source.model_dump_json()) == data
    assert json.loads(dictionary.model_dump_json()) == dictionary_data
    assert json.loads(source.model_dump_json())["task"] == text


def test_json_escaped_surrogate_pairs_decode_as_valid_astral_scalars():
    # JSON's paired escapes represent one scalar and must remain valid wire input.
    parts = encode_message(MessageV1(**message_values())).split("|")
    parts[3] = "%22%5Cud83e%5Cuddea%22"
    decoded = decode_message("|".join(parts))
    assert decoded.task == "🧪"
    assert decode_message(encode_message(decoded)).task == "🧪"

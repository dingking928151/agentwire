"""Opt-in AC2 codec: one ordered, compact JSON array; never executes data."""

import json

from .codec import (
    CONTENT_FIELDS,
    ENUMS,
    FIELDS,
    FallbackRequired,
    _check_dictionary,
    _expand,
    _substitute,
    _unique_object,
    _validated_dictionary,
)
from .schema import MessageV1, UserDictionary


def encode_message_ac2(message: MessageV1, dictionary: UserDictionary | None = None) -> str:
    """Encode all canonical fields, aliasing only exact approved content strings."""
    if not isinstance(message, MessageV1):
        raise FallbackRequired("message must be a MessageV1")
    try:
        message = MessageV1.model_validate(vars(message), strict=True)
        dictionary = _validated_dictionary(dictionary)
        _check_dictionary(message.user_id, message.dictionary_version, dictionary)
        reverse = {phrase: code for code, phrase in dictionary.aliases.items()} if dictionary else {}
        values = []
        for name in FIELDS:
            value = getattr(message, name)
            if name in ENUMS:
                value = ENUMS[name][value]
            elif name in CONTENT_FIELDS:
                value = _substitute(value, reverse)
            values.append(value)
        return "AC2|" + json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (ValueError, KeyError, TypeError, RecursionError) as error:
        raise FallbackRequired("invalid message fields or dictionary") from error


def decode_message_ac2(payload: str, dictionary: UserDictionary | None = None) -> MessageV1:
    """Strictly decode AC2 with an explicitly selected dictionary, without guessing."""
    if not isinstance(payload, str):
        raise FallbackRequired("wire payload must be a string")
    if not payload.startswith("AC2|"):
        raise FallbackRequired("unknown protocol version")
    try:
        dictionary = _validated_dictionary(dictionary)
        parts = json.loads(payload[4:], object_pairs_hook=_unique_object)
        if not isinstance(parts, list) or len(parts) != len(FIELDS):
            raise FallbackRequired("expected exactly eleven wire fields")
        values = dict(zip(FIELDS, parts))
        for name, mapping in ENUMS.items():
            values[name] = {code: text for text, code in mapping.items()}[values[name]]
        _check_dictionary(values["user_id"], values["dictionary_version"], dictionary)
        for name in CONTENT_FIELDS:
            values[name] = _expand(values[name], dictionary)
        return MessageV1.model_validate(values, strict=True)
    except (ValueError, KeyError, TypeError, RecursionError) as error:
        raise FallbackRequired("invalid AC2 JSON, fields, enum, alias, or dictionary") from error

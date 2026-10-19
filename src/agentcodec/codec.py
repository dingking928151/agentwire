"""Deterministic AC1 wire codec: ordered percent-escaped JSON values and enum codes.

Only whole content-field strings/list items may become {"a": code}; literal
strings resembling aliases remain strings. Nothing here executes message data.
"""

import json
from urllib.parse import quote, unquote

from .schema import MessageV1, UserDictionary


class FallbackRequired(ValueError):
    """The caller must retain/retransmit the original; guessing is forbidden."""

    status = "fallback_required"


ENUMS = {
    "message_type": {"delegate": "D", "status": "S", "result": "R"},
    "status": {"pending": "P", "running": "R", "blocked": "B",
               "completed": "C", "failed": "F", "cancelled": "X"},
}
FIELDS = (
    "message_id", "message_type", "task", "status", "blocker", "next_step",
    "evidence", "constraints", "approval_required", "user_id", "dictionary_version",
)
CONTENT_FIELDS = {"task", "blocker", "next_step", "evidence", "constraints"}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FallbackRequired("duplicate JSON object key")
        result[key] = value
    return result


def _check_dictionary(user_id, version, dictionary: UserDictionary | None):
    if dictionary is None:
        if version is not None:
            raise FallbackRequired("dictionary version unavailable")
    elif (dictionary.user_id, dictionary.version) != (user_id, version):
        raise FallbackRequired("dictionary selection does not match message metadata")


def _substitute(value, reverse: dict[str, str]):
    if isinstance(value, list):
        return [_substitute(item, reverse) for item in value]
    if isinstance(value, str) and value in reverse:
        return {"a": reverse[value]}
    return value


def _expand(value, dictionary: UserDictionary | None):
    if isinstance(value, list):
        return [_expand(item, dictionary) for item in value]
    if isinstance(value, dict):
        if set(value) != {"a"} or not isinstance(value["a"], str) or dictionary is None:
            raise FallbackRequired("invalid alias or unavailable dictionary")
        return dictionary.aliases[value["a"]]
    return value


def encode_message(message: MessageV1, dictionary: UserDictionary | None = None) -> str:
    if not isinstance(message, MessageV1):
        raise FallbackRequired("message must be a MessageV1")
    try:
        message = MessageV1.model_validate(vars(message), strict=True)
    except ValueError as error:
        raise FallbackRequired("invalid message fields") from error
    _check_dictionary(message.user_id, message.dictionary_version, dictionary)
    reverse = {phrase: code for code, phrase in dictionary.aliases.items()} if dictionary else {}
    parts = ["AC1"]
    for name in FIELDS:
        value = getattr(message, name)
        if name in ENUMS:
            parts.append(ENUMS[name][value])
        else:
            if name in CONTENT_FIELDS:
                value = _substitute(value, reverse)
            parts.append(quote(json.dumps(value, ensure_ascii=False, separators=(",", ":")), safe=""))
    return "|".join(parts)


def decode_message(payload: str, dictionary: UserDictionary | None = None) -> MessageV1:
    if not isinstance(payload, str):
        raise FallbackRequired("wire payload must be a string")
    parts = payload.split("|")
    if parts[0] != "AC1":
        raise FallbackRequired("unknown protocol version")
    if len(parts) != len(FIELDS) + 1:
        raise FallbackRequired("missing or extra wire fields")
    values = {}
    try:
        for name, value in zip(FIELDS, parts[1:]):
            if name in ENUMS:
                values[name] = {code: text for text, code in ENUMS[name].items()}[value]
            else:
                text = unquote(value, errors="strict")
                if quote(text, safe="") != value:
                    raise FallbackRequired("malformed or noncanonical escaping")
                values[name] = json.loads(text, object_pairs_hook=_unique_object)
        _check_dictionary(values["user_id"], values["dictionary_version"], dictionary)
        for name in CONTENT_FIELDS:
            values[name] = _expand(values[name], dictionary)
        return MessageV1.model_validate(values, strict=True)
    except (ValueError, KeyError, TypeError) as error:
        raise FallbackRequired("invalid wire fields, enum, alias, or escaping") from error

"""Exact offline input adapters; labelled NL is fixture syntax, not general prose.

Declared formats never dispatch to another decoder. These functions only restore
message data: they do not interpret tasks, follow evidence, or perform actions.
"""

import json

from agentcodec.ac2 import decode_message_ac2, encode_message_ac2
from agentcodec.benchmark import render_json, render_natural_language
from agentcodec.codec import FIELDS, FallbackRequired, _unique_object, decode_message, encode_message
from agentcodec.schema import MessageV1, UserDictionary

from .contracts import InputFormat


_FORMATS = ("natural_language", "json", "ac1", "ac2")
_LABELS = (
    "ID", "Type", "Task", "Status", "Blocker", "Next step", "Evidence",
    "Constraints", "Approval required", "User", "Dictionary version",
)


def render_input(
    message: MessageV1,
    input_format: InputFormat = "ac1",
    dictionary: UserDictionary | None = None,
) -> str:
    """Reuse existing renderers/codecs; AC2 requires explicit selection."""
    if input_format not in _FORMATS:
        raise FallbackRequired("unknown input format")
    if not isinstance(message, MessageV1):
        raise FallbackRequired("message must be a MessageV1")
    try:
        # Validate raw current fields, including mutated or constructed models.
        message = MessageV1.model_validate(vars(message), strict=True)
        if input_format == "ac1":
            return encode_message(message, dictionary)
        if input_format == "ac2":
            return encode_message_ac2(message, dictionary)
        if dictionary is not None:
            raise FallbackRequired("plain formats do not accept a dictionary")
        if input_format == "json":
            return render_json(message)
        return render_natural_language(message)
    except FallbackRequired:
        raise
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise FallbackRequired("invalid input message") from error


def _parse_labelled_message(payload: str) -> dict:
    """Consume eleven exact labels, JSON values, separators and final period."""
    decoder = json.JSONDecoder(object_pairs_hook=_unique_object)
    values = {}
    position = 0
    for index, (field, label) in enumerate(zip(FIELDS, _LABELS)):
        prefix = ("; " if index else "") + label + ": "
        if not payload.startswith(prefix, position):
            raise FallbackRequired("missing or out-of-order natural language label")
        position += len(prefix)
        values[field], position = decoder.raw_decode(payload, position)
    if payload[position:] != ".":
        raise FallbackRequired("invalid natural language terminator or trailing content")
    return values


def parse_input(
    payload: str,
    input_format: InputFormat,
    expected_user_id: str,
    dictionary: UserDictionary | None = None,
) -> MessageV1:
    """Restore all fields by declared syntax or require fallback without guessing.

    Plain JSON/NL preserve dictionary_version as metadata only; neither accepts a
    selected dictionary nor expands aliases. Codec namespace/version rules remain
    the responsibility of their existing strict decoders.
    """
    if input_format not in _FORMATS:
        raise FallbackRequired("unknown input format")
    if not isinstance(payload, str):
        raise FallbackRequired("input payload must be a string")
    if not isinstance(expected_user_id, str):
        raise FallbackRequired("expected user ID must be a string")
    try:
        if input_format == "ac1":
            message = decode_message(payload, dictionary)
        elif input_format == "ac2":
            message = decode_message_ac2(payload, dictionary)
        else:
            if dictionary is not None:
                raise FallbackRequired("plain formats do not accept a dictionary")
            if input_format == "json":
                values = json.loads(payload, object_pairs_hook=_unique_object)
            else:
                values = _parse_labelled_message(payload)
            message = MessageV1.model_validate(values, strict=True)
        if message.user_id != expected_user_id:
            raise FallbackRequired("message does not match expected user ID")
        return message
    except FallbackRequired:
        raise
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise FallbackRequired("invalid declared input format or message fields") from error

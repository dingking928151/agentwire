"""Strict, reversible encoder and decoder for AgentWire Compact Profile v1."""

from __future__ import annotations

import copy
import json
import math
from typing import Any

from pydantic import ValidationError

from agentcodec.schema import MessageV1, UserDictionary

from .contracts import (
    AgentWireError,
    DictionaryIdentity,
    NegotiationResult,
    ReceiverCapabilities,
    dictionary_identity_for,
)

_PREFIX = "AWP1|"
_MESSAGE_TYPE_TO_CODE = {"delegate": "D", "status": "S", "result": "R"}
_MESSAGE_TYPE_FROM_CODE = {code: name for name, code in _MESSAGE_TYPE_TO_CODE.items()}
_STATUS_TO_CODE = {
    "pending": "P",
    "running": "R",
    "blocked": "B",
    "completed": "C",
    "failed": "F",
    "cancelled": "X",
}
_STATUS_FROM_CODE = {code: name for name, code in _STATUS_TO_CODE.items()}


class _DuplicateObjectKey(ValueError):
    """Internal signal used to reject ambiguous JSON objects."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateObjectKey
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _contains_surrogate(value: Any) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if any("\ud800" <= character <= "\udfff" for character in current):
                return True
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, dict):
            for key, item in current.items():
                pending.append(key)
                pending.append(item)
    return False


def _snapshot_message(message: MessageV1) -> MessageV1:
    if not isinstance(message, MessageV1):
        raise AgentWireError("invalid_message")
    try:
        return MessageV1.model_validate(
            copy.deepcopy(message.model_dump(mode="python")), strict=True
        )
    except (ValidationError, TypeError, ValueError, AttributeError):
        raise AgentWireError("invalid_message") from None


def _snapshot_dictionary(
    dictionary: UserDictionary,
) -> tuple[UserDictionary, DictionaryIdentity]:
    if not isinstance(dictionary, UserDictionary):
        raise AgentWireError("dictionary_identity_mismatch")
    try:
        snapshot = UserDictionary.model_validate(
            copy.deepcopy(dictionary.model_dump(mode="python")), strict=True
        )
        identity = dictionary_identity_for(snapshot)
    except (ValidationError, TypeError, ValueError, AttributeError):
        raise AgentWireError("dictionary_identity_mismatch") from None
    return snapshot, identity


def _identity_values(identity: DictionaryIdentity) -> list[str]:
    return [identity.user_id, identity.version, identity.sha256]


def _encode_reference(value: str, phrase_to_code: dict[str, str]) -> str | dict[str, str]:
    code = phrase_to_code.get(value)
    return {"$ref": code} if code is not None else value


def _encode_field_value(value: Any, phrase_to_code: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _encode_reference(value, phrase_to_code)
    if isinstance(value, list):
        return [_encode_reference(item, phrase_to_code) for item in value]
    return value


def encode_awp1(message: MessageV1, dictionary: UserDictionary | None = None) -> str:
    """Encode all eleven MessageV1 fields into the fixed AWP1 JSON envelope."""
    snapshot = _snapshot_message(message)
    identity: DictionaryIdentity | None = None
    phrase_to_code: dict[str, str] = {}
    if dictionary is not None:
        dictionary_snapshot, identity = _snapshot_dictionary(dictionary)
        if (
            snapshot.user_id != identity.user_id
            or snapshot.dictionary_version != identity.version
        ):
            raise AgentWireError("dictionary_identity_mismatch")
        phrase_to_code = {
            phrase: code for code, phrase in dictionary_snapshot.aliases.items()
        }

    values: list[Any] = [
        snapshot.message_id,
        _MESSAGE_TYPE_TO_CODE[snapshot.message_type],
        _encode_field_value(snapshot.task, phrase_to_code),
        _STATUS_TO_CODE[snapshot.status],
        _encode_field_value(snapshot.blocker, phrase_to_code),
        _encode_field_value(snapshot.next_step, phrase_to_code),
        _encode_field_value(snapshot.evidence, phrase_to_code),
        _encode_field_value(snapshot.constraints, phrase_to_code),
        snapshot.approval_required,
        snapshot.user_id,
        snapshot.dictionary_version,
    ]
    try:
        return _PREFIX + json.dumps(
            [_identity_values(identity) if identity is not None else None, values],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeEncodeError):
        raise AgentWireError("invalid_message") from None


def _check_receiver(receiver: ReceiverCapabilities) -> None:
    if not isinstance(receiver, ReceiverCapabilities):
        raise AgentWireError("invalid_message")
    if "AWP1" not in receiver.supported_profile_versions:
        raise AgentWireError("unsupported_profile")
    if "MessageV1" not in receiver.supported_schema_versions:
        raise AgentWireError("unsupported_schema")
    if "AWP1" not in receiver.supported_enum_table_versions:
        raise AgentWireError("unsupported_enum_table")


def _parse_payload(payload: str, receiver: ReceiverCapabilities) -> Any:
    if not isinstance(payload, str):
        raise AgentWireError("invalid_envelope")
    try:
        actual_size = len(payload.encode("utf-8"))
    except UnicodeEncodeError:
        raise AgentWireError("invalid_envelope") from None
    if actual_size > receiver.max_message_bytes:
        raise AgentWireError("message_too_large")

    _check_receiver(receiver)
    if not payload.startswith(_PREFIX):
        if payload.startswith("AWP"):
            raise AgentWireError("unsupported_profile")
        raise AgentWireError("invalid_envelope")
    try:
        envelope = json.loads(
            payload[len(_PREFIX) :],
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
            parse_float=_parse_finite_float,
        )
    except (json.JSONDecodeError, ValueError, TypeError, OverflowError, RecursionError):
        raise AgentWireError("invalid_envelope") from None
    if _contains_surrogate(envelope):
        raise AgentWireError("invalid_message")
    return envelope


def _parse_identity(value: Any) -> DictionaryIdentity | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 3:
        raise AgentWireError("invalid_envelope")
    if not all(isinstance(part, str) for part in value):
        raise AgentWireError("invalid_envelope")
    try:
        return DictionaryIdentity.model_validate(
            {"user_id": value[0], "version": value[1], "sha256": value[2]},
            strict=True,
        )
    except ValidationError:
        raise AgentWireError("invalid_envelope") from None


def _decode_reference(value: Any, aliases: dict[str, str]) -> Any:
    if not isinstance(value, dict) or set(value) != {"$ref"}:
        return value
    code = value["$ref"]
    if not isinstance(code, str):
        return value
    if code not in aliases:
        raise AgentWireError("unknown_alias")
    return aliases[code]


def _expand_text_values(values: list[Any], aliases: dict[str, str]) -> list[Any]:
    expanded = list(values)
    for index in (2, 4, 5):
        expanded[index] = _decode_reference(expanded[index], aliases)
    for index in (6, 7):
        current = expanded[index]
        if isinstance(current, list):
            expanded[index] = [_decode_reference(item, aliases) for item in current]
    return expanded


def decode_awp1(
    payload: str,
    receiver: ReceiverCapabilities,
    dictionary: UserDictionary | None = None,
) -> MessageV1:
    """Strictly decode an AWP1 wire payload or raise a stable AgentWireError."""
    if not isinstance(receiver, ReceiverCapabilities):
        raise AgentWireError("invalid_message")
    envelope = _parse_payload(payload, receiver)
    if not isinstance(envelope, list) or len(envelope) != 2:
        raise AgentWireError("invalid_envelope")
    wire_identity = _parse_identity(envelope[0])
    wire_values = envelope[1]
    if not isinstance(wire_values, list) or len(wire_values) != 11:
        raise AgentWireError("invalid_envelope")

    dictionary_snapshot: UserDictionary | None = None
    aliases: dict[str, str] = {}
    if wire_identity is None:
        if dictionary is not None:
            raise AgentWireError("dictionary_identity_mismatch")
    else:
        if dictionary is None:
            raise AgentWireError("dictionary_missing")
        dictionary_snapshot, selected_identity = _snapshot_dictionary(dictionary)
        receiver_identity = receiver.dictionary_identity
        if receiver_identity is None:
            raise AgentWireError("dictionary_missing")
        if (
            wire_identity != selected_identity
            or wire_identity != receiver_identity
        ):
            raise AgentWireError("dictionary_identity_mismatch")
        if not isinstance(wire_values[9], str) or not isinstance(wire_values[10], str):
            raise AgentWireError("invalid_message")
        if (
            wire_values[9] != wire_identity.user_id
            or wire_values[10] != wire_identity.version
        ):
            raise AgentWireError("dictionary_identity_mismatch")
        aliases = dictionary_snapshot.aliases

    expanded = _expand_text_values(wire_values, aliases)
    if not isinstance(expanded[1], str) or expanded[1] not in _MESSAGE_TYPE_FROM_CODE:
        raise AgentWireError("unknown_enum")
    if not isinstance(expanded[3], str) or expanded[3] not in _STATUS_FROM_CODE:
        raise AgentWireError("unknown_enum")

    decoded_values = list(expanded)
    decoded_values[1] = _MESSAGE_TYPE_FROM_CODE[expanded[1]]
    decoded_values[3] = _STATUS_FROM_CODE[expanded[3]]
    data = dict(
        zip(
            (
                "message_id", "message_type", "task", "status", "blocker", "next_step",
                "evidence", "constraints", "approval_required", "user_id", "dictionary_version",
            ),
            decoded_values,
            strict=True,
        )
    )
    try:
        message = MessageV1.model_validate(data, strict=True)
    except (ValidationError, TypeError, ValueError):
        raise AgentWireError("invalid_message") from None
    if message.user_id != receiver.user_id:
        raise AgentWireError("receiver_user_mismatch")
    return message


def negotiate_awp1(
    receiver: ReceiverCapabilities,
    payload: str,
    dictionary: UserDictionary | None = None,
) -> NegotiationResult:
    """Report whether this receiver can decode the wire, without returning a payload."""
    try:
        decode_awp1(payload, receiver, dictionary=dictionary)
    except AgentWireError as error:
        return NegotiationResult(
            status="fallback_required",
            reason_code=error.reason_code,
        )
    return NegotiationResult(status="supported", reason_code=None)

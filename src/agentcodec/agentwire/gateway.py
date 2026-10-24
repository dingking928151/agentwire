"""Local, deterministic AgentWire receive adapters; no dispatch or execution."""

from __future__ import annotations

import json
from typing import get_args

from pydantic import ValidationError

from agentcodec.agentwire.codec import decode_awp1
from agentcodec.agentwire.contracts import (
    AgentWireError,
    BenchmarkCandidate,
    GatewayDelivery,
    GatewayResult,
    ReceiverCapabilities,
    dictionary_identity_for,
)
from agentcodec.codec import FallbackRequired
from agentcodec.evaluation import formats as evaluation_formats
from agentcodec.schema import MessageV1, UserDictionary


_CANDIDATES = frozenset(get_args(BenchmarkCandidate))
_DICTIONARY_CANDIDATES = frozenset(
    {"ac1_dictionary", "ac2_dictionary", "awp1_dictionary"}
)
_FORMATS = {
    "natural_language": "natural_language",
    "json": "json",
    "ac1": "ac1",
    "ac1_dictionary": "ac1",
    "ac2": "ac2",
    "ac2_dictionary": "ac2",
    "awp1": "awp1",
    "awp1_dictionary": "awp1",
}


def _fallback_gateway(reason_code: str) -> GatewayResult:
    return GatewayResult(
        status="fallback_required", message=None, reason_code=reason_code
    )


def _fallback_delivery(
    candidate_id: BenchmarkCandidate, reason_code: str
) -> GatewayDelivery:
    return GatewayDelivery(
        candidate_id=candidate_id,
        decode_status="fallback_required",
        model_context=None,
        pass_through_text=None,
        reason_code=reason_code,
    )


def _canonical_context(message: MessageV1) -> str:
    snapshot = MessageV1.model_validate(vars(message), strict=True)
    return json.dumps(
        snapshot.model_dump(),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _selected_dictionary_reason(
    receiver: ReceiverCapabilities, dictionary: UserDictionary | None
) -> str | None:
    if dictionary is None:
        return "dictionary_missing"
    if not isinstance(dictionary, UserDictionary):
        return "dictionary_identity_mismatch"
    try:
        identity = dictionary_identity_for(dictionary)
    except (ValidationError, TypeError, ValueError, AttributeError, RecursionError):
        return "dictionary_identity_mismatch"
    if receiver.dictionary_identity is None:
        return "dictionary_missing"
    if identity != receiver.dictionary_identity or identity.user_id != receiver.user_id:
        return "dictionary_identity_mismatch"
    return None


def decode_at_gateway(
    payload: str,
    receiver: ReceiverCapabilities,
    dictionary: UserDictionary | None = None,
) -> GatewayResult:
    """Strictly decode only AWP1, returning a complete message or a stable failure."""
    try:
        message = decode_awp1(payload, receiver, dictionary=dictionary)
        return GatewayResult(
            status="decoded", message=message, reason_code=None
        )
    except AgentWireError as error:
        return _fallback_gateway(error.reason_code)
    except (ValidationError, TypeError, ValueError, AttributeError, RecursionError):
        return _fallback_gateway("invalid_message")


def deliver_benchmark_at_gateway(
    candidate_id: BenchmarkCandidate,
    payload: str,
    receiver: ReceiverCapabilities,
    dictionary: UserDictionary | None = None,
) -> GatewayDelivery:
    """Decode one explicitly declared offline benchmark format without guessing."""
    try:
        known_candidate = isinstance(candidate_id, str) and candidate_id in _CANDIDATES
    except TypeError:
        known_candidate = False
    if not known_candidate:
        raise ValueError("unsupported benchmark candidate") from None

    if not isinstance(receiver, ReceiverCapabilities):
        return _fallback_delivery(candidate_id, "invalid_message")

    if candidate_id == "natural_language_freeform":
        if dictionary is not None:
            return _fallback_delivery(candidate_id, "dictionary_identity_mismatch")
        if not isinstance(payload, str):
            return _fallback_delivery(candidate_id, "invalid_message")
        return GatewayDelivery(
            candidate_id=candidate_id,
            decode_status="pass_through",
            model_context=None,
            pass_through_text=payload,
            reason_code=None,
        )

    uses_dictionary = candidate_id in _DICTIONARY_CANDIDATES
    if uses_dictionary:
        dictionary_reason = _selected_dictionary_reason(receiver, dictionary)
        if dictionary_reason is not None:
            return _fallback_delivery(candidate_id, dictionary_reason)
    elif dictionary is not None:
        return _fallback_delivery(candidate_id, "dictionary_identity_mismatch")

    if candidate_id in {"awp1", "awp1_dictionary"}:
        result = decode_at_gateway(payload, receiver, dictionary=dictionary)
        if result.status == "fallback_required":
            return _fallback_delivery(candidate_id, result.reason_code)
        message = result.message
    else:
        declared_format = _FORMATS[candidate_id]
        try:
            message = evaluation_formats.parse_input(
                payload,
                declared_format,
                receiver.user_id,
                dictionary=dictionary,
            )
        except (
            FallbackRequired,
            ValidationError,
            TypeError,
            ValueError,
            KeyError,
            AttributeError,
            RecursionError,
        ):
            # Existing parsers do not expose a stable reason taxonomy. Keep this
            # boundary non-revealing instead of copying parser text or input data.
            return _fallback_delivery(candidate_id, "invalid_message")

    try:
        model_context = _canonical_context(message)
    except (ValidationError, TypeError, ValueError, AttributeError, RecursionError):
        return _fallback_delivery(candidate_id, "invalid_message")
    return GatewayDelivery(
        candidate_id=candidate_id,
        decode_status="decoded",
        model_context=model_context,
        pass_through_text=None,
        reason_code=None,
    )

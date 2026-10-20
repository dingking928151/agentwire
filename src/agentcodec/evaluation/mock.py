"""Deterministic pipeline mock: inspect message data, never execute its contents."""

import json
from typing import Protocol

from agentcodec.codec import (
    FallbackRequired,
    _check_dictionary,
    _unique_object,
    _validated_dictionary,
)

from .contracts import AdapterResponse, EvalError, EvalRequest, MockReceipt, canonical_json, message_sha256
from .formats import parse_input


class OfflineAdapter(Protocol):
    """Local request/response boundary; no provider configuration or oracle."""

    adapter_id: str

    def invoke(self, request: EvalRequest) -> AdapterResponse:
        ...


class DeterministicMockAdapter:
    """Restore the explicitly declared format and emit a read-only receipt."""

    adapter_id = "deterministic-mock-v1"

    def invoke(self, request: EvalRequest) -> AdapterResponse:
        try:
            message = parse_input(
                request.payload, request.input_format, request.expected_user_id, request.dictionary,
            )
        except FallbackRequired:
            return AdapterResponse(
                status="error", raw_output=None,
                error=EvalError(
                    stage="decode", code="decode_rejected",
                    message="Input could not be decoded.", retryable=False,
                ),
            )
        receipt = MockReceipt(
            receipt_version="mock-receipt-v1", operation=request.operation,
            message=message, message_sha256=message_sha256(message), actions=[],
        )
        return AdapterResponse(
            status="ok", raw_output=canonical_json(receipt.model_dump()), error=None,
        )


def validate_receipt(raw_output: str, request: EvalRequest) -> MockReceipt:
    """Validate output independently, without decoding the request as an oracle.

    Plain formats preserve non-null dictionary versions as literal metadata;
    codecs require the exact selected namespace/version, including null when
    no dictionary was selected. Full message fidelity is the scorer's job.
    """
    try:
        if not isinstance(raw_output, str):
            raise ValueError("receipt must be text")
        receipt = MockReceipt.model_validate(
            json.loads(raw_output, object_pairs_hook=_unique_object), strict=True,
        )
        if receipt.operation != request.operation:
            raise ValueError("operation mismatch")
        if receipt.message_sha256 != message_sha256(receipt.message):
            raise ValueError("message hash mismatch")
        if receipt.message.user_id != request.expected_user_id:
            raise ValueError("namespace mismatch")
        if request.input_format in ("ac1", "ac2"):
            dictionary = _validated_dictionary(request.dictionary)
            _check_dictionary(receipt.message.user_id, receipt.message.dictionary_version, dictionary)
        elif request.input_format in ("natural_language", "json"):
            if request.dictionary is not None:
                raise ValueError("plain formats do not accept a dictionary")
        else:
            raise ValueError("unknown input format")
        return receipt
    except (ValueError, TypeError, KeyError, RecursionError):
        raise FallbackRequired("Invalid mock receipt.") from None

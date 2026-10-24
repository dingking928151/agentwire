"""Strict, side-effect-free contracts for AgentWire v1."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    PositiveInt,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from agentcodec.schema import Identifier, MessageV1, UserDictionary


Sha256Hex: TypeAlias = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
BenchmarkCandidate: TypeAlias = Literal[
    "natural_language",
    "natural_language_freeform",
    "json",
    "ac1",
    "ac1_dictionary",
    "ac2",
    "ac2_dictionary",
    "awp1",
    "awp1_dictionary",
]
AgentWireReasonCode: TypeAlias = Literal[
    "unsupported_profile",
    "unsupported_schema",
    "unsupported_enum_table",
    "receiver_user_mismatch",
    "message_too_large",
    "invalid_envelope",
    "unknown_enum",
    "dictionary_missing",
    "dictionary_identity_mismatch",
    "unknown_alias",
    "invalid_message",
]

_REASON_CODE_ADAPTER = TypeAdapter(AgentWireReasonCode)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class DictionaryIdentity(_StrictFrozenModel):
    user_id: Identifier
    version: Identifier
    sha256: Sha256Hex


class ReceiverCapabilities(_StrictFrozenModel):
    user_id: Identifier
    supported_profile_versions: tuple[str, ...]
    supported_schema_versions: tuple[str, ...]
    supported_enum_table_versions: tuple[str, ...]
    max_message_bytes: PositiveInt
    dictionary_identity: DictionaryIdentity | None

    @model_validator(mode="after")
    def reject_duplicate_versions(self) -> ReceiverCapabilities:
        for field_name in (
            "supported_profile_versions",
            "supported_schema_versions",
            "supported_enum_table_versions",
        ):
            versions = getattr(self, field_name)
            if len(set(versions)) != len(versions):
                raise ValueError(f"{field_name} must not contain duplicate versions")
        return self


class NegotiationResult(_StrictFrozenModel):
    status: Literal["supported", "fallback_required"]
    reason_code: AgentWireReasonCode | None

    @model_validator(mode="after")
    def enforce_status_reason_pair(self) -> NegotiationResult:
        success = self.status == "supported"
        if success != (self.reason_code is None):
            raise ValueError("negotiation status and reason_code are inconsistent")
        return self


class GatewayResult(_StrictFrozenModel):
    status: Literal["decoded", "fallback_required"]
    message: MessageV1 | None
    reason_code: AgentWireReasonCode | None

    @model_validator(mode="after")
    def enforce_status_payload_pair(self) -> GatewayResult:
        success = self.status == "decoded"
        if success != (self.message is not None and self.reason_code is None):
            raise ValueError("gateway status and result fields are inconsistent")
        if not success and (self.message is not None or self.reason_code is None):
            raise ValueError("fallback result must contain only a stable reason code")
        return self


class GatewayDelivery(_StrictFrozenModel):
    candidate_id: BenchmarkCandidate
    decode_status: Literal["decoded", "fallback_required", "pass_through"]
    model_context: str | None
    pass_through_text: str | None
    reason_code: AgentWireReasonCode | None

    @model_validator(mode="after")
    def enforce_delivery_state(self) -> GatewayDelivery:
        if self.decode_status == "decoded":
            if (
                self.candidate_id == "natural_language_freeform"
                or self.model_context is None
                or not self.model_context
                or self.pass_through_text is not None
                or self.reason_code is not None
            ):
                raise ValueError("decoded delivery fields are inconsistent")
            try:
                decoded_message = MessageV1.model_validate(
                    json.loads(self.model_context), strict=True
                )
                canonical_context = json.dumps(
                    decoded_message.model_dump(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
                raise ValueError(
                    "decoded model_context must be canonical MessageV1 JSON"
                ) from None
            if self.model_context != canonical_context:
                raise ValueError(
                    "decoded model_context must be canonical MessageV1 JSON"
                )
        elif self.decode_status == "fallback_required":
            if (
                self.model_context is not None
                or self.pass_through_text is not None
                or self.reason_code is None
            ):
                raise ValueError("fallback delivery must contain only a stable reason code")
        elif (
            self.candidate_id != "natural_language_freeform"
            or self.model_context is not None
            or self.pass_through_text is None
            or self.reason_code is not None
        ):
            raise ValueError("pass-through delivery fields are inconsistent")
        return self


class AgentWireError(Exception):
    """An error exposing only one stable, non-sensitive reason code."""

    def __init__(self, reason_code: AgentWireReasonCode) -> None:
        try:
            validated = _REASON_CODE_ADAPTER.validate_python(reason_code, strict=True)
        except ValidationError:
            raise ValueError("invalid AgentWire reason code") from None
        self.reason_code: AgentWireReasonCode = validated
        super().__init__("agentwire operation rejected")


def dictionary_identity_for(dictionary: UserDictionary) -> DictionaryIdentity:
    """Hash the full strict, recursively snapshotted UserDictionary content."""
    snapshot = copy.deepcopy(dictionary.model_dump(warnings=False))
    validated = UserDictionary.model_validate(snapshot, strict=True)
    canonical = json.dumps(
        validated.model_dump(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return DictionaryIdentity(
        user_id=validated.user_id,
        version=validated.version,
        sha256=hashlib.sha256(canonical).hexdigest(),
    )

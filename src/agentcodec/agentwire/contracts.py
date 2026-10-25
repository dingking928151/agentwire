"""Strict, side-effect-free contracts for AgentWire v1."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from agentcodec.schema import Identifier, MessageV1, UnicodeText, UserDictionary


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
CostPartLabel: TypeAlias = Literal[
    "setup", "negotiation", "protocol_instructions", "context",
    "dictionary_glossary", "dictionary_transfer", "payload", "response",
    "retry", "fallback", "other",
]
CostEventKind: TypeAlias = Literal[
    "setup", "negotiation", "dictionary_distribution", "request",
    "response", "retry", "fallback",
]
CostSurface: TypeAlias = Literal["transport", "model_input", "model_output"]
CostUnavailableReason: TypeAlias = Literal[
    "tokenizer_unavailable", "profile_unreliable", "surrogate_only",
]
NonnegativeInt = Annotated[int, Field(ge=0)]

COST_PART_LABELS = (
    "setup", "negotiation", "protocol_instructions", "context",
    "dictionary_glossary", "dictionary_transfer", "payload", "response",
    "retry", "fallback", "other",
)
COST_EVENT_KINDS = (
    "setup", "negotiation", "dictionary_distribution", "request",
    "response", "retry", "fallback",
)

_REASON_CODE_ADAPTER = TypeAdapter(AgentWireReasonCode)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
    )


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


class CostPart(_StrictFrozenModel):
    label: CostPartLabel
    text: UnicodeText


class CostEvent(_StrictFrozenModel):
    event_id: Identifier
    kind: CostEventKind
    simulated: Literal[True] = True
    surfaces: tuple[CostSurface, ...]
    parts: Annotated[tuple[CostPart, ...], Field(min_length=1)]
    multiplicity: PositiveInt = 1

    @model_validator(mode="after")
    def require_distinct_surfaces(self) -> CostEvent:
        if not self.surfaces or len(set(self.surfaces)) != len(self.surfaces):
            raise ValueError("cost event surfaces must be nonempty and distinct")
        return self


class CostSummary(_StrictFrozenModel):
    status: Literal["exact_local_text", "cannot_compare"]
    reason_code: CostUnavailableReason | None
    transport_bytes: NonnegativeInt
    transport_tokens: NonnegativeInt | None
    model_input_tokens: NonnegativeInt | None
    model_output_tokens: NonnegativeInt | None
    distinct_text_event_tokens: NonnegativeInt | None
    event_kind_tokens: dict[CostEventKind, NonnegativeInt] | None
    part_label_tokens: dict[CostPartLabel, int] | None

    @model_validator(mode="after")
    def consistent_measurement_state(self) -> CostSummary:
        token_values = (
            self.transport_tokens,
            self.model_input_tokens,
            self.model_output_tokens,
            self.distinct_text_event_tokens,
        )
        if self.status == "cannot_compare":
            if self.reason_code is None or any(value is not None for value in token_values):
                raise ValueError("unknown measurement must not report token totals")
            if self.event_kind_tokens is not None or self.part_label_tokens is not None:
                raise ValueError("unknown measurement must not report token breakdowns")
            return self
        if self.reason_code is not None or any(value is None for value in token_values):
            raise ValueError("exact measurement requires complete token totals")
        if self.event_kind_tokens is None or self.part_label_tokens is None:
            raise ValueError("exact measurement requires token breakdowns")
        if (set(self.event_kind_tokens) != set(COST_EVENT_KINDS)
                or set(self.part_label_tokens) != set(COST_PART_LABELS)):
            raise ValueError("exact measurement breakdowns must be complete")
        if (sum(self.event_kind_tokens.values()) != self.distinct_text_event_tokens
                or sum(self.part_label_tokens.values()) != self.distinct_text_event_tokens):
            raise ValueError("event and part totals must conserve complete event tokens")
        return self


class BreakEvenResult(_StrictFrozenModel):
    status: Literal["finite", "never", "cannot_compare"]
    first_strict_win: PositiveInt | None
    reason_code: Literal["required_cost_unknown"] | None

    @model_validator(mode="after")
    def consistent_break_even_state(self) -> BreakEvenResult:
        if self.status == "finite":
            if self.first_strict_win is None or self.reason_code is not None:
                raise ValueError("finite break-even requires a first strict win")
        elif self.status == "never":
            if self.first_strict_win is not None or self.reason_code is not None:
                raise ValueError("never-winning break-even has no threshold")
        elif self.first_strict_win is not None or self.reason_code is None:
            raise ValueError("incomparable break-even requires an unknown-cost reason")
        return self

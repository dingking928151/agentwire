"""Strict, side-effect-free contracts for adaptive-selection-v1.

No codec, tokenizer, I/O, clock, model mapping, or execution lives here.
Revalidation reads current raw model fields rather than trusting model instances.
"""

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentcodec.codec import FallbackRequired
from agentcodec.schema import Identifier, MessageV1, UnicodeText, UserDictionary


CandidateId = Literal["json", "ac1", "ac2", "ac1_dictionary", "ac2_dictionary"]
ResourceState = Literal["builtin", "ready", "cold", "invalid"]
Exposure = Literal["builtin", "once_per_scope", "per_request_context"]
ComponentKind = Literal["setup", "context", "payload", "retry", "fallback", "other"]
AttemptKind = Literal["setup", "initial", "retry", "fallback", "other"]
Accuracy = Literal["exact_local_text", "surrogate_only", "unknown"]

CANDIDATE_IDS = ("json", "ac1", "ac2", "ac1_dictionary", "ac2_dictionary")
COMPONENT_KINDS = ("setup", "context", "payload", "retry", "fallback", "other")
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_REPEAT_COUNT = 1_000_000
MAX_HISTORY_EVENTS = 100_000

NonnegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(ge=1)]
RepeatCount = Annotated[int, Field(ge=1, le=MAX_REPEAT_COUNT)]
Ratio = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


class _ContractRejected(FallbackRequired):
    """Stable SDK error without input values, paths, or validation details."""

    code = "adaptive_invalid_contract"

    def __init__(self):
        super().__init__("adaptive contract rejected")


def _raw(value: object) -> object:
    """Copy raw fields recursively, including extras and possibly tampered values."""
    if isinstance(value, BaseModel):
        data = dict(vars(value))
        if value.__pydantic_extra__:
            data.update(value.__pydantic_extra__)
        return _raw(data)
    if isinstance(value, dict):
        return {key: _raw(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_raw(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_raw(item) for item in value)
    return value


def _check_json(value: object) -> None:
    if isinstance(value, str):
        if any("\ud800" <= char <= "\udfff" for char in value):
            raise ValueError("invalid Unicode")
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("invalid JSON key")
            _check_json(key)
            _check_json(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_json(item)


def canonical_text(value: object) -> str:
    """Sorted, compact, scalar-Unicode JSON with no appended line feed."""
    try:
        _check_json(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise _ContractRejected() from None


def _sha256(value: object) -> str:
    return hashlib.sha256(canonical_text(value).encode("utf-8")).hexdigest()


def _bounded_text(value: str | None) -> str | None:
    if value is not None and len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("text capacity exceeded")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False,
                              revalidate_instances="always", frozen=True, hide_input_in_errors=True)

    def __init__(self, **data):
        try:
            super().__init__(**data)
        except (ValueError, TypeError, RecursionError):
            raise _ContractRejected() from None

    @classmethod
    def model_validate(cls, obj, **kwargs):
        try:
            return super().model_validate(obj, **kwargs)
        except (ValueError, TypeError, RecursionError):
            raise _ContractRejected() from None

    @classmethod
    def model_validate_json(cls, json_data, **kwargs):
        try:
            return super().model_validate_json(json_data, **kwargs)
        except (ValueError, TypeError, RecursionError):
            raise _ContractRejected() from None

    @model_validator(mode="before")
    @classmethod
    def current_fields(cls, value):
        return _raw(value)


class TokenizerProfile(_StrictModel):
    encoding_name: UnicodeText
    library_version: UnicodeText
    vocabulary_sha256: UnicodeText
    renderer_version: Literal["adaptive-text-v1"] = "adaptive-text-v1"
    accuracy: Accuracy
    mapping_basis: UnicodeText | None


class ResourceIdentity(_StrictModel):
    scope_id: Identifier
    receiver_id: Identifier
    user_id: Identifier
    resource_kind: Identifier
    version: Identifier
    content_hash: UnicodeText
    tokenizer_profile_id: UnicodeText


class ResourceSnapshot(_StrictModel):
    identity: ResourceIdentity
    state: ResourceState
    exposure: Exposure
    epoch: NonnegativeInt
    valid_through_repeat: NonnegativeInt | None
    readiness_basis: UnicodeText | None
    setup_text: UnicodeText | None
    context_text: UnicodeText | None
    independent_setup: bool = False

    @field_validator("setup_text", "context_text")
    @classmethod
    def bounded_text(cls, value):
        return _bounded_text(value)


class HistoricalEvent(_StrictModel):
    event_id: Identifier
    identity: ResourceIdentity
    profile_id: UnicodeText
    tokens: NonnegativeInt
    kind: ComponentKind


class ScopeSnapshot(_StrictModel):
    scope_id: Identifier
    receiver_id: Identifier
    user_id: Identifier
    epoch: NonnegativeInt
    history_complete: bool
    historical_events: Annotated[tuple[HistoricalEvent, ...], Field(max_length=MAX_HISTORY_EVENTS)]
    resources: tuple[ResourceSnapshot, ...]


class DictionarySelection(_StrictModel):
    enabled: bool = False
    dictionary: UserDictionary | None = None
    content_hash: UnicodeText | None = None


class ReceiverCapabilities(_StrictModel):
    """Caller's local declaration, not remote authentication or SDK inference."""

    receiver_id: Identifier
    supported_candidates: tuple[CandidateId, ...]
    candidate_resources: dict[CandidateId, tuple[ResourceIdentity, ...]]
    epoch: NonnegativeInt

    @field_validator("supported_candidates")
    @classmethod
    def unique_candidates(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("duplicate candidate")
        return value


class RepeatDeclaration(_StrictModel):
    unit: Literal["same_message_instance"]
    total_count: RepeatCount
    instance_sha256: UnicodeText


class CostPolicy(_StrictModel):
    repeat_count: RepeatCount = 1
    repeat_declaration: RepeatDeclaration | None = None
    min_savings_tokens: NonnegativeInt = 0
    min_savings_ratio: Ratio = 0.0
    fallback_mode: Literal["record_only"] = "record_only"
    max_retries: Literal[0] = 0
    assumption: Literal["deterministic_clean"] = "deterministic_clean"

    @field_validator("max_retries", mode="before")
    @classmethod
    def strict_zero(cls, value):
        # Literal[0] alone accepts False and 0.0 in Pydantic.
        if type(value) is not int or value != 0:
            raise ValueError("production retries forbidden")
        return value

    @model_validator(mode="after")
    def declared_count(self) -> Self:
        declaration = self.repeat_declaration
        if self.repeat_count > 1 and declaration is None:
            raise ValueError("repeat declaration required")
        if declaration is not None and declaration.total_count != self.repeat_count:
            raise ValueError("repeat count mismatch")
        return self


class TextEnvelope(_StrictModel):
    prefix: UnicodeText = ""
    suffix: UnicodeText = ""
    final_lf: bool = False

    @field_validator("prefix", "suffix")
    @classmethod
    def bounded_text(cls, value):
        return _bounded_text(value)


class SelectionRequest(_StrictModel):
    contract_version: Literal["adaptive-selection-v1"]
    message: MessageV1
    expected_user_id: Identifier
    tokenizer: TokenizerProfile
    capabilities: ReceiverCapabilities
    snapshot: ScopeSnapshot
    original_format: CandidateId = "ac1"
    allowlist: tuple[CandidateId, ...] = ("json", "ac1")
    dictionary: DictionarySelection = Field(default_factory=DictionarySelection)
    policy: CostPolicy = Field(default_factory=CostPolicy)
    envelope: TextEnvelope = Field(default_factory=TextEnvelope)

    @field_validator("allowlist")
    @classmethod
    def explicit_candidates(cls, value):
        if not value or len(value) != len(set(value)):
            raise ValueError("allowlist must be nonempty and unique")
        return value

    @model_validator(mode="after")
    def single_instance(self) -> Self:
        declaration = self.policy.repeat_declaration
        if declaration is not None and declaration.instance_sha256 != instance_id(self.message):
            raise ValueError("repeat instance mismatch")
        if len(canonical_text(self.model_dump()).encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError("request capacity exceeded")
        return self


class CandidatePayload(_StrictModel):
    candidate_id: CandidateId
    declared_format: Literal["json", "ac1", "ac2"]
    payload: UnicodeText | None
    eligibility: Literal["eligible", "unavailable"]
    reason_codes: tuple[UnicodeText, ...]
    required_resources: tuple[ResourceIdentity, ...]

    @model_validator(mode="after")
    def consistent_payload(self) -> Self:
        if self.declared_format != self.candidate_id.split("_")[0]:
            raise ValueError("candidate format mismatch")
        if (self.eligibility == "eligible") != (self.payload is not None):
            raise ValueError("candidate payload mismatch")
        _bounded_text(self.payload)
        return self


class TextComponent(_StrictModel):
    kind: ComponentKind
    text: UnicodeText
    resource_identity: ResourceIdentity | None

    @field_validator("text")
    @classmethod
    def bounded_text(cls, value):
        return _bounded_text(value)


class TextAttempt(_StrictModel):
    event_id: Identifier
    attempt_kind: AttemptKind
    components: tuple[TextComponent, ...]
    simulated: bool
    multiplicity: RepeatCount = 1

    @model_validator(mode="after")
    def bounded_attempt(self) -> Self:
        if sum(len(item.text.encode("utf-8")) for item in self.components) > MAX_TEXT_BYTES:
            raise ValueError("attempt capacity exceeded")
        return self


class MeasuredAttempt(_StrictModel):
    event_id: Identifier
    attempt_kind: AttemptKind
    total_tokens: NonnegativeInt
    component_tokens: tuple[int, ...]
    simulated: bool
    multiplicity: RepeatCount = 1

    @model_validator(mode="after")
    def conserved_tokens(self) -> Self:
        if sum(self.component_tokens) != self.total_tokens:
            raise ValueError("component attribution mismatch")
        return self


class CandidateCost(_StrictModel):
    candidate_id: CandidateId
    remaining_expected_tokens: NonnegativeInt | None
    scope_spent_tokens: NonnegativeInt | None
    scope_lifecycle_expected_tokens: NonnegativeInt | None
    attempts: tuple[MeasuredAttempt, ...]
    component_totals: dict[UnicodeText, int] | None
    excluded: tuple[UnicodeText, ...]
    reason_codes: tuple[UnicodeText, ...]
    new_resource_count: NonnegativeInt

    @model_validator(mode="after")
    def consistent_cost(self) -> Self:
        totals = self.component_totals
        if totals is not None:
            if set(totals) != set(COMPONENT_KINDS) or totals["retry"] != 0 or totals["fallback"] != 0:
                raise ValueError("invalid production component totals")
            if self.remaining_expected_tokens is not None and sum(totals.values()) != self.remaining_expected_tokens:
                raise ValueError("component total mismatch")
        spent, remaining, lifecycle = (self.scope_spent_tokens, self.remaining_expected_tokens,
                                       self.scope_lifecycle_expected_tokens)
        if lifecycle is not None and (spent is None or remaining is None or lifecycle != spent + remaining):
            raise ValueError("lifecycle total mismatch")
        return self


class BreakEvenResult(_StrictModel):
    kind: Literal["finite", "infinity", "interval", "not_applicable"]
    first_strict_win: PositiveInt | None
    winning_interval: tuple[PositiveInt, PositiveInt | None] | None
    assumptions: tuple[UnicodeText, ...]
    reason_codes: tuple[UnicodeText, ...]

    @model_validator(mode="after")
    def consistent_interval(self) -> Self:
        first, interval = self.first_strict_win, self.winning_interval
        if self.kind in ("infinity", "not_applicable"):
            if first is not None or interval is not None:
                raise ValueError("no winning interval")
        elif first is None:
            raise ValueError("first strict win required")
        if self.kind == "interval" and interval is None:
            raise ValueError("winning interval required")
        if interval is not None and (interval[0] != first or
                                     (interval[1] is not None and interval[1] < interval[0])):
            raise ValueError("invalid winning interval")
        return self


class SelectionDecision(_StrictModel):
    contract_version: Literal["adaptive-selection-v1"]
    status: Literal["selected", "preserved_without_comparison", "fallback_required"]
    comparison_status: Literal["exact_local_text", "surrogate_only", "cannot_compare"]
    reason_codes: tuple[UnicodeText, ...]
    selected_candidate: CandidateId | None
    declared_format: UnicodeText | None
    payload: UnicodeText | None
    candidates: tuple[CandidatePayload, ...]
    costs: tuple[CandidateCost, ...]
    minimum_candidate: CandidateId | None
    policy_retained: bool
    baseline_candidate: CandidateId | None
    savings_tokens: int | None
    savings_ratio: Ratio | None
    opportunity_cost_tokens: NonnegativeInt | None
    opportunity_cost_upper_bound: NonnegativeInt | None
    breakeven: dict[CandidateId, BreakEvenResult]
    metadata_selection: dict[UnicodeText, UnicodeText | None]
    proposed_resources: tuple[ResourceSnapshot, ...]
    snapshot_epoch: NonnegativeInt
    decision_id: UnicodeText
    profile_id: UnicodeText
    policy_version: Literal["adaptive-policy-v1"]
    renderer_version: Literal["adaptive-text-v1"]
    assumptions: tuple[UnicodeText, ...]
    excluded: tuple[UnicodeText, ...]

    @model_validator(mode="after")
    def consistent_decision(self) -> Self:
        candidates = {item.candidate_id: item for item in self.candidates}
        if len(self.candidates) != len(CANDIDATE_IDS) or set(candidates) != set(CANDIDATE_IDS):
            raise ValueError("all five candidates required exactly once")
        if len({item.candidate_id for item in self.costs}) != len(self.costs):
            raise ValueError("duplicate candidate costs")
        metadata = self.metadata_selection
        if (set(metadata) != {"selection_status", "source_dictionary_version", "transport_dictionary_version"}
                or metadata["selection_status"] != "unchanged"
                or metadata["source_dictionary_version"] != metadata["transport_dictionary_version"]):
            raise ValueError("metadata must remain unchanged")
        if self.status == "fallback_required":
            if any(value is not None for value in (self.payload, self.selected_candidate, self.declared_format)):
                raise ValueError("failure cannot expose payload")
        else:
            candidate = candidates.get(self.selected_candidate)
            if (candidate is None or candidate.eligibility != "eligible" or self.payload is None
                    or self.payload != candidate.payload or self.declared_format != candidate.declared_format):
                raise ValueError("selected payload mismatch")
        if self.status == "selected" and self.comparison_status != "exact_local_text":
            raise ValueError("selection requires exact comparison")
        if self.status == "preserved_without_comparison" and self.comparison_status == "exact_local_text":
            raise ValueError("preservation is not exact selection")
        if self.comparison_status != "exact_local_text":
            if any(value is not None for value in (self.minimum_candidate, self.savings_tokens,
                    self.savings_ratio, self.opportunity_cost_tokens, self.opportunity_cost_upper_bound)):
                raise ValueError("unknown comparison cannot report savings")
            if self.policy_retained or any(item.remaining_expected_tokens is not None or
                    item.scope_lifecycle_expected_tokens is not None or item.component_totals is not None
                    for item in self.costs):
                raise ValueError("unknown comparison cannot report costs")
        return self


def profile_id(profile: TokenizerProfile) -> str:
    """Hash a newly validated profile, including renderer and mapping evidence."""
    profile = TokenizerProfile.model_validate(profile)
    return _sha256(profile.model_dump())


def instance_id(message: MessageV1) -> str:
    """Hash all eleven current strict fields, never just a free-form template."""
    try:
        message = MessageV1.model_validate(_raw(message), strict=True)
    except (ValueError, TypeError, RecursionError):
        raise _ContractRejected() from None
    return _sha256(message.model_dump())


def freeze_request(request: SelectionRequest) -> SelectionRequest:
    """Revalidate and detach the full message, dictionary, policy and snapshot.

    Nested legacy MessageV1/UserDictionary types retain their public schemas;
    their lists/maps are newly allocated, never shared with caller-owned data.
    """
    return SelectionRequest.model_validate(request)

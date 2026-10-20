"""Required, fail-closed records and canonical bytes for offline-eval-v1."""

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentcodec.schema import Identifier, MessageV1, UnicodeText, UserDictionary


InputFormat = Literal["natural_language", "json", "ac1", "ac2"]
Condition = Literal["natural_language", "json", "ac1", "ac2", "ac1_dictionary", "ac2_dictionary"]

_MESSAGE_FIELDS = frozenset(MessageV1.model_fields)
_HARD_FIELDS = frozenset(("task", "blocker", "next_step", "evidence", "constraints", "approval_required"))


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class MetadataSelection(_StrictModel):
    """Record dictionary selection without overwriting a source version."""

    selection_status: Literal["no_dictionary", "applied_to_null_version", "preserved_non_null_version"]
    selected_dictionary_user_id: Identifier | None
    selected_dictionary_version: Identifier | None
    source_dictionary_version: Identifier | None
    transport_dictionary_version: Identifier | None

    @model_validator(mode="after")
    def consistent_selection(self) -> Self:
        if self.selection_status == "no_dictionary":
            if self.selected_dictionary_user_id is not None or self.selected_dictionary_version is not None:
                raise ValueError("no dictionary must have null selected identity")
            if self.transport_dictionary_version != self.source_dictionary_version:
                raise ValueError("no dictionary must preserve source version")
        else:
            if self.selected_dictionary_user_id is None or self.selected_dictionary_version is None:
                raise ValueError("selected dictionary identity is required")
            if self.selection_status == "applied_to_null_version":
                if self.source_dictionary_version is not None:
                    raise ValueError("dictionary selection applies only to a null source version")
                if self.transport_dictionary_version != self.selected_dictionary_version:
                    raise ValueError("transport version must equal selected version")
            else:
                if self.source_dictionary_version is None:
                    raise ValueError("preserved source version must be non-null")
                if self.transport_dictionary_version != self.source_dictionary_version:
                    raise ValueError("transport version must preserve source version")
        return self


class EvalError(_StrictModel):
    stage: Literal["render", "decode", "adapter", "response", "policy"]
    code: UnicodeText
    message: UnicodeText
    retryable: bool


class EvalPolicy(_StrictModel):
    fallback_mode: Literal["record_only", "local_original_json"] = "record_only"
    max_retries: Annotated[int, Field(ge=0, le=1)] = 0


class EvalCase(_StrictModel):
    """Runner-private oracle; never supplied to an adapter."""

    scenario: UnicodeText
    source_message: MessageV1
    transport_message: MessageV1
    condition: Condition
    dictionary: UserDictionary | None
    metadata_selection: MetadataSelection
    policy: EvalPolicy


class EvalRequest(_StrictModel):
    """Adapter input intentionally excludes source, transport, and scoring oracles."""

    contract_version: Literal["offline-eval-v1"]
    case_id: UnicodeText
    attempt_index: Annotated[int, Field(ge=0)]
    input_format: InputFormat
    payload: UnicodeText
    expected_user_id: UnicodeText
    dictionary: UserDictionary | None
    operation: Literal["inspect_message_v1"]


class AdapterResponse(_StrictModel):
    status: Literal["ok", "error"]
    raw_output: UnicodeText | None
    error: EvalError | None

    @model_validator(mode="after")
    def exclusive_result(self) -> Self:
        if self.status == "ok":
            if self.raw_output is None or self.error is not None:
                raise ValueError("ok requires raw output and no error")
        elif self.raw_output is not None or self.error is None:
            raise ValueError("error requires an error and no raw output")
        return self


class MockReceipt(_StrictModel):
    receipt_version: Literal["mock-receipt-v1"]
    operation: Literal["inspect_message_v1"]
    message: MessageV1
    message_sha256: UnicodeText
    actions: Annotated[list[UnicodeText], Field(max_length=0)]


class AttemptScore(_StrictModel):
    decode_success: bool
    field_equal: dict[UnicodeText, bool]
    hard_field_equal: dict[UnicodeText, bool]
    unrecovered_fields: list[UnicodeText]
    full_message_equal: bool
    hard_constraint_case_pass: bool
    mock_task_completed: bool
    fallback_required: bool

    @field_validator("field_equal")
    @classmethod
    def exact_message_fields(cls, value: dict[str, bool]) -> dict[str, bool]:
        if value.keys() != _MESSAGE_FIELDS:
            raise ValueError("field_equal requires exactly the eleven message fields")
        return value

    @field_validator("hard_field_equal")
    @classmethod
    def exact_hard_fields(cls, value: dict[str, bool]) -> dict[str, bool]:
        if value.keys() != _HARD_FIELDS:
            raise ValueError("hard_field_equal requires exactly the six hard fields")
        return value


class AttemptRecord(_StrictModel):
    record_version: Literal["offline-eval-v1"]
    record_id: UnicodeText
    scenario: UnicodeText
    source_message_id: UnicodeText
    condition: Condition
    metadata_selection: MetadataSelection
    request: EvalRequest
    request_sha256: UnicodeText
    adapter_id: Literal["deterministic-mock-v1"]
    response: AdapterResponse | None
    decoded_message: MessageV1 | None
    decode_status: Literal["ok", "error", "not_attempted"]
    receipt_status: Literal["valid", "invalid", "absent"]
    error: EvalError | None
    transition: Literal["initial", "retry", "fallback_json"]
    fallback_required: bool
    fallback_performed: bool
    retry_performed: bool
    attempt_score: AttemptScore


def _check_json_text(value: object) -> None:
    """Reject non-scalar strings and key coercion before JSON encoding."""
    if isinstance(value, str):
        if any("\ud800" <= character <= "\udfff" for character in value):
            raise ValueError("JSON text must contain only Unicode scalar values")
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            _check_json_text(key)
            _check_json_text(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_json_text(item)


def canonical_json(value: object) -> str:
    """Return compact, sorted, scalar-Unicode JSON without a trailing LF."""
    _check_json_text(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_text(text: str) -> str:
    """Hash the exact UTF-8 bytes; never append a record separator."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def message_sha256(message: MessageV1) -> str:
    """Integrity hash only: semantic fidelity still requires field comparisons."""
    return sha256_text(canonical_json(message.model_dump()))

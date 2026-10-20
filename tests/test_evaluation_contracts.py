"""Offline contracts reject answer leakage and preserve exact local data."""

import hashlib
import importlib

import pytest
from pydantic import ValidationError

from agentcodec.schema import MessageV1


FIELDS = (
    "message_id", "message_type", "task", "status", "blocker", "next_step",
    "evidence", "constraints", "approval_required", "user_id", "dictionary_version",
)
HARD_FIELDS = ("task", "blocker", "next_step", "evidence", "constraints", "approval_required")


@pytest.fixture
def contracts():
    # A missing contract module must fail a test, not prevent collection.
    return importlib.import_module("agentcodec.evaluation.contracts")


def message_values(**changes):
    return dict(
        message_id="local-1", message_type="delegate", task="检查，不要发布 ",
        status="pending", blocker=None, next_step="", evidence=["b", "a", "b"],
        constraints=[], approval_required=False, user_id="demo-user", dictionary_version=None,
    ) | changes


def selection_values(**changes):
    return dict(
        selection_status="no_dictionary", selected_dictionary_user_id=None,
        selected_dictionary_version=None, source_dictionary_version=None,
        transport_dictionary_version=None,
    ) | changes


def request_values(**changes):
    return dict(
        contract_version="offline-eval-v1", case_id="diverse/local-1/json", attempt_index=0,
        input_format="json", payload="", expected_user_id="demo-user", dictionary=None,
        operation="inspect_message_v1",
    ) | changes


def score_values(**changes):
    return dict(
        decode_success=False, field_equal=dict.fromkeys(FIELDS, False),
        hard_field_equal=dict.fromkeys(HARD_FIELDS, False), unrecovered_fields=list(FIELDS),
        full_message_equal=False, hard_constraint_case_pass=False, mock_task_completed=False,
        fallback_required=True,
    ) | changes


def error_values(**changes):
    return dict(stage="render", code="render_rejected", message="render rejected", retryable=False) | changes


def record_values(**changes):
    return dict(
        record_version="offline-eval-v1", record_id="diverse/local-1/json/0", scenario="diverse",
        source_message_id="local-1", condition="json", metadata_selection=selection_values(),
        request=request_values(), request_sha256="0" * 64, adapter_id="deterministic-mock-v1",
        response=None, decoded_message=None, decode_status="not_attempted", receipt_status="absent",
        error=error_values(), transition="initial", fallback_required=True, fallback_performed=False,
        retry_performed=False, attempt_score=score_values(),
    ) | changes


def model_examples():
    return {
        "MetadataSelection": selection_values(),
        "EvalError": error_values(),
        "EvalCase": dict(scenario="diverse", source_message=message_values(),
                         transport_message=message_values(), condition="json", dictionary=None,
                         metadata_selection=selection_values(), policy={}),
        "EvalRequest": request_values(),
        "AdapterResponse": dict(status="ok", raw_output="", error=None),
        "MockReceipt": dict(receipt_version="mock-receipt-v1", operation="inspect_message_v1",
                            message=message_values(), message_sha256="0" * 64, actions=[]),
        "AttemptRecord": record_values(),
        "AttemptScore": score_values(),
    }


def test_request_rejects_oracle_and_unknown_fields(contracts):
    for field in ("source_message", "transport_message", "expected_receipt", "score", "provider", "key"):
        with pytest.raises(ValidationError):
            contracts.EvalRequest(**request_values(**{field: message_values()}))
    assert contracts.EvalRequest(**request_values()).payload == ""


def test_strict_false_is_not_zero(contracts):
    with pytest.raises(ValidationError):
        contracts.MockReceipt(**(model_examples()["MockReceipt"] | {
            "message": message_values(approval_required=0),
        }))


@pytest.mark.parametrize("name,data", list(model_examples().items()))
def test_models_require_every_field_and_forbid_extras(contracts, name, data):
    model = getattr(contracts, name)
    expected = data
    if name == "EvalCase":
        expected = data | {"policy": {"fallback_mode": "record_only", "max_retries": 0}}
    assert model(**data).model_dump() == expected
    for field in data:
        incomplete = data.copy()
        del incomplete[field]
        with pytest.raises(ValidationError) as error:
            model(**incomplete)
        assert any(item["loc"] == (field,) and item["type"] == "missing" for item in error.value.errors())
    with pytest.raises(ValidationError):
        model(**(data | {"unknown": False}))


def test_policy_defaults_and_bounded_strict_retries(contracts):
    assert contracts.EvalPolicy().model_dump() == {"fallback_mode": "record_only", "max_retries": 0}
    assert contracts.EvalPolicy(fallback_mode="local_original_json", max_retries=1).max_retries == 1
    for changes in ({"max_retries": 2}, {"max_retries": -1}, {"max_retries": False},
                    {"max_retries": "1"}, {"max_retries": 1.0}, {"fallback_mode": "online"},
                    {"unknown": None}):
        with pytest.raises(ValidationError):
            contracts.EvalPolicy(**changes)


def test_metadata_selection_no_dictionary_is_explicit(contracts):
    assert contracts.MetadataSelection(**selection_values()).model_dump() == selection_values()
    existing = selection_values(source_dictionary_version="legacy-v0", transport_dictionary_version="legacy-v0")
    assert contracts.MetadataSelection(**existing).model_dump() == existing


def test_metadata_selection_applies_only_to_null_source_version(contracts):
    values = selection_values(selection_status="applied_to_null_version",
                              selected_dictionary_user_id="demo-user", selected_dictionary_version="demo-v1",
                              transport_dictionary_version="demo-v1")
    assert contracts.MetadataSelection(**values).model_dump() == values


def test_metadata_selection_preserves_non_null_source_version(contracts):
    values = selection_values(selection_status="preserved_non_null_version",
                              selected_dictionary_user_id="demo-user", selected_dictionary_version="demo-v1",
                              source_dictionary_version="legacy-v0", transport_dictionary_version="legacy-v0")
    assert contracts.MetadataSelection(**values).model_dump() == values


@pytest.mark.parametrize("changes", [
    {"selection_status": "unknown"}, {"selected_dictionary_user_id": "demo-user"},
    {"selected_dictionary_version": "demo-v1"}, {"transport_dictionary_version": "demo-v1"},
    {"source_dictionary_version": "legacy-v0"},
    {"selection_status": "applied_to_null_version"},
    {"selection_status": "preserved_non_null_version"},
])
def test_metadata_selection_rejects_invalid_no_dictionary_states(contracts, changes):
    with pytest.raises(ValidationError):
        contracts.MetadataSelection(**selection_values(**changes))


@pytest.mark.parametrize("status,source,transport", [
    ("applied_to_null_version", None, "demo-v1"),
    ("preserved_non_null_version", "legacy-v0", "legacy-v0"),
])
@pytest.mark.parametrize("changes", [
    {"selected_dictionary_user_id": None}, {"selected_dictionary_user_id": ""},
    {"selected_dictionary_version": None}, {"selected_dictionary_version": ""},
    {"transport_dictionary_version": None}, {"transport_dictionary_version": "wrong"},
    {"source_dictionary_version": "wrong"}, {"extra": None},
])
def test_metadata_selection_rejects_missing_identity_and_wrong_versions(contracts, status, source, transport, changes):
    values = selection_values(selection_status=status, selected_dictionary_user_id="demo-user",
                              selected_dictionary_version="demo-v1", source_dictionary_version=source,
                              transport_dictionary_version=transport) | changes
    with pytest.raises(ValidationError):
        contracts.MetadataSelection(**values)


@pytest.mark.parametrize("name", ["EvalCase", "AttemptRecord"])
def test_metadata_selection_object_cannot_be_null(contracts, name):
    with pytest.raises(ValidationError):
        getattr(contracts, name)(**(model_examples()[name] | {"metadata_selection": None}))
    assert isinstance(getattr(contracts, name)(**model_examples()[name]).metadata_selection,
                      contracts.MetadataSelection)


@pytest.mark.parametrize("changes", [
    {"status": "unknown"}, {"status": "ok", "raw_output": None, "error": None},
    {"status": "ok", "raw_output": "", "error": error_values()},
    {"status": "error", "raw_output": "", "error": error_values()},
    {"status": "error", "raw_output": None, "error": None},
])
def test_response_requires_exclusive_success_or_error(contracts, changes):
    with pytest.raises(ValidationError):
        contracts.AdapterResponse(**changes)
    assert contracts.AdapterResponse(status="error", raw_output=None, error=error_values()).error.stage == "render"


@pytest.mark.parametrize("name,field,value", [
    ("EvalRequest", "attempt_index", False), ("EvalRequest", "attempt_index", "0"),
    ("EvalRequest", "attempt_index", -1), ("EvalRequest", "payload", b"text"),
    ("EvalRequest", "input_format", "auto"), ("EvalRequest", "contract_version", "v2"),
    ("EvalRequest", "operation", "execute"), ("EvalError", "stage", "network"),
    ("EvalError", "retryable", 0), ("AttemptRecord", "fallback_required", 1),
    ("AttemptRecord", "condition", "auto"), ("AttemptRecord", "decode_status", "success"),
    ("AttemptRecord", "receipt_status", "ok"), ("AttemptRecord", "transition", "loop"),
    ("AttemptRecord", "record_version", "v2"), ("AttemptRecord", "adapter_id", "online"),
    ("MockReceipt", "receipt_version", "v2"), ("MockReceipt", "operation", "execute"),
    ("MockReceipt", "actions", ["publish"]), ("MockReceipt", "actions", ()),
    ("AttemptScore", "decode_success", 0), ("AttemptScore", "unrecovered_fields", ()),
])
def test_contracts_reject_coercion_and_invalid_literals(contracts, name, field, value):
    with pytest.raises(ValidationError):
        getattr(contracts, name)(**(model_examples()[name] | {field: value}))


@pytest.mark.parametrize("adapter_id", ["deterministic-mock-v1", "scripted-mock-test-v1"])
def test_attempt_record_accepts_only_fixed_mock_adapter_ids(contracts, adapter_id):
    record = contracts.AttemptRecord(**record_values(adapter_id=adapter_id))
    assert record.adapter_id == adapter_id
    assert record.model_dump()["adapter_id"] == adapter_id


@pytest.mark.parametrize("adapter_id", [
    "", "mock-v1", "scripted-mock-v1", "scripted-mock-test-v2", "unknown",
    " deterministic-mock-v1", "scripted-mock-test-v1 ", "SCRIPTED-MOCK-TEST-V1",
])
def test_attempt_record_rejects_unapproved_adapter_ids(contracts, adapter_id):
    with pytest.raises(ValidationError) as error:
        contracts.AttemptRecord(**record_values(adapter_id=adapter_id))
    assert any(item["loc"] == ("adapter_id",) and item["type"] == "literal_error"
               for item in error.value.errors())


@pytest.mark.parametrize("field,names", [("field_equal", FIELDS), ("hard_field_equal", HARD_FIELDS)])
def test_score_maps_require_exact_fields_and_strict_booleans(contracts, field, names):
    for invalid in (dict.fromkeys(names[:-1], False), dict.fromkeys((*names, "other"), False),
                    dict.fromkeys((*names[:-1], "other"), False), dict.fromkeys(names, 0)):
        with pytest.raises(ValidationError):
            contracts.AttemptScore(**score_values(**{field: invalid}))


@pytest.mark.parametrize("name,field", [
    ("EvalError", "code"), ("EvalError", "message"), ("EvalRequest", "payload"),
    ("EvalRequest", "case_id"), ("AdapterResponse", "raw_output"),
    ("MetadataSelection", "source_dictionary_version"), ("AttemptRecord", "request_sha256"),
])
@pytest.mark.parametrize("invalid", ["\ud800", "\udfff", float("nan"), float("inf"), float("-inf")])
def test_models_reject_non_scalar_text_and_nonfinite_values(contracts, name, field, invalid):
    with pytest.raises(ValidationError):
        getattr(contracts, name)(**(model_examples()[name] | {field: invalid}))


def test_all_input_formats_and_conditions_are_explicit(contracts):
    for input_format in ("natural_language", "json", "ac1", "ac2"):
        assert contracts.EvalRequest(**request_values(input_format=input_format)).input_format == input_format
    for condition in ("natural_language", "json", "ac1", "ac2", "ac1_dictionary", "ac2_dictionary"):
        assert contracts.EvalCase(**(model_examples()["EvalCase"] | {"condition": condition})).condition == condition


def test_render_failure_keeps_explicit_empty_request_and_absent_response(contracts):
    record = contracts.AttemptRecord(**record_values())
    assert record.request.payload == ""
    assert record.response is None
    assert record.decoded_message is None
    assert record.decode_status == "not_attempted"
    assert record.error.stage == "render"
    assert record.attempt_score.mock_task_completed is False
    assert record.attempt_score.field_equal == dict.fromkeys(FIELDS, False)


def test_canonical_bytes_keep_unicode_and_order(contracts):
    assert contracts.canonical_json({"z": False, "a": "检查"}) == '{"a":"检查","z":false}'
    assert contracts.canonical_json({"z": ["b", "a", "b"], "a": {"z": 1, "a": 2}}) == (
        '{"a":{"a":2,"z":1},"z":["b","a","b"]}'
    )
    assert contracts.canonical_json([None, "", False, [], " e\u0301\u2028\n "]) == (
        '[null,"",false,[]," e\u0301\u2028\\n "]'
    )


@pytest.mark.parametrize("invalid", [
    float("nan"), float("inf"), float("-inf"), {"x": [float("nan")]},
    "\ud800", {"x": ["\udfff"]}, {"\ud800": "value"}, {1: "coerced key"},
])
def test_canonical_json_rejects_invalid_json_values(contracts, invalid):
    with pytest.raises((TypeError, ValueError)):
        contracts.canonical_json(invalid)


def test_sha256_uses_exact_utf8_without_line_feed(contracts):
    assert contracts.sha256_text("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert contracts.sha256_text("检查") == hashlib.sha256(b"\xe6\xa3\x80\xe6\x9f\xa5").hexdigest()
    message = MessageV1(**message_values())
    expected = ('{"approval_required":false,"blocker":null,"constraints":[],"dictionary_version":null,'
                '"evidence":["b","a","b"],"message_id":"local-1","message_type":"delegate",'
                '"next_step":"","status":"pending","task":"检查，不要发布 ","user_id":"demo-user"}')
    assert contracts.message_sha256(message) == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert contracts.message_sha256(message) != hashlib.sha256((expected + "\n").encode("utf-8")).hexdigest()
    assert contracts.message_sha256(MessageV1(**message_values(blocker=""))) != contracts.message_sha256(message)

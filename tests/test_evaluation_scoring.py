"""Offline scoring invariants, using only inline synthetic messages."""

import json

import pytest

from agentcodec.evaluation.contracts import (
    AdapterResponse, EvalError, EvalRequest, canonical_json, message_sha256,
)
from agentcodec.evaluation.mock import DeterministicMockAdapter
from agentcodec.evaluation.scoring import HARD_FIELDS, aggregate_scores, score_attempt
from agentcodec.schema import MessageV1


FIELDS = (
    "message_id", "message_type", "task", "status", "blocker", "next_step",
    "evidence", "constraints", "approval_required", "user_id", "dictionary_version",
)
HARD = ("task", "blocker", "next_step", "evidence", "constraints", "approval_required")


def message(**changes):
    data = dict(
        message_id="synthetic-1", message_type="delegate", task="Do not publish. 不要发布。",
        status="pending", blocker=None, next_step="Review on 2026-11-01; do not send.",
        evidence=["inline evidence", "inline evidence", "tail "],
        constraints=["Budget <= $25.", "No purchase.", "No purchase."],
        approval_required=True, user_id="synthetic-user", dictionary_version=None,
    )
    data.update(changes)
    return MessageV1.model_validate(data, strict=True)


def request(expected, **changes):
    data = dict(
        contract_version="offline-eval-v1", case_id="synthetic/1/json", attempt_index=0,
        input_format="json", payload=canonical_json(expected.model_dump()),
        expected_user_id="synthetic-user", dictionary=None, operation="inspect_message_v1",
    )
    data.update(changes)
    return EvalRequest(**data)


def response_for(recovered):
    # Recompute a legitimate integrity hash: only oracle comparison detects tampering.
    return AdapterResponse(status="ok", error=None, raw_output=canonical_json(dict(
        receipt_version="mock-receipt-v1", operation="inspect_message_v1",
        message=recovered.model_dump(), message_sha256=message_sha256(recovered), actions=[],
    )))


def test_valid_but_budget_changed_is_not_completed():
    expected = message()
    changed = message(constraints=["Budget <= $250.", "No purchase.", "No purchase."])
    score = score_attempt(expected, response_for(changed), request(expected))
    assert score.decode_success is True
    assert score.field_equal["constraints"] is False
    assert score.hard_field_equal["constraints"] is False
    assert score.full_message_equal is False
    assert score.hard_constraint_case_pass is False
    assert score.mock_task_completed is False
    assert score.fallback_required is True
    assert score.unrecovered_fields == []


def test_unrecovered_is_not_dropped_from_denominator():
    expected = message()
    req = request(expected)
    good = score_attempt(expected, response_for(expected), req)
    bad = score_attempt(expected, None, req)
    report = aggregate_scores([good, bad])
    assert report["logical_case_count"] == 2
    assert report["decode_success_count"] == 1
    assert report["decode_success_denominator"] == 2
    assert report["decode_success_rate"] == 0.5
    assert report["full_message_equal_count"] == 1
    assert report["full_message_equal_denominator"] == 2
    assert report["full_message_equal_rate"] == 0.5
    assert report["field_equal_counts"] == dict.fromkeys(FIELDS, 1)
    assert report["field_equal_denominators"] == dict.fromkeys(FIELDS, 2)
    assert report["field_equal_rates"] == dict.fromkeys(FIELDS, 0.5)
    assert report["equal_field_total"] == 11
    assert report["field_fidelity_denominator"] == 22
    assert report["field_fidelity_rate"] == 0.5
    assert report["unrecovered_field_count"] == 11
    assert report["unrecovered_field_denominator"] == 22
    assert report["unrecovered_field_rate"] == 0.5
    assert report["equal_hard_total"] == 6
    assert report["hard_field_preservation_denominator"] == 12
    assert report["hard_field_preservation_rate"] == 0.5
    assert report["hard_constraint_case_pass_count"] == 1
    assert report["hard_constraint_case_pass_denominator"] == 2
    assert report["hard_constraint_case_pass_rate"] == 0.5
    assert report["changed_hard_field_count"] == 0
    assert report["unrecovered_hard_field_count"] == 6
    assert report["mock_task_completed_count"] == 1
    assert report["mock_task_completed_denominator"] == 2
    assert report["mock_task_completed_rate"] == 0.5
    assert report["fallback_required_case_count"] == 1
    assert report["fallback_required_case_denominator"] == 2
    assert report["fallback_required_case_rate"] == 0.5


@pytest.mark.parametrize("status", ["pending", "completed", "failed", "cancelled"])
def test_correct_read_only_receipt_completes_regardless_of_source_status(status):
    expected = message(status=status)
    req = request(expected)
    score = score_attempt(expected, DeterministicMockAdapter().invoke(req), req)
    assert HARD_FIELDS == HARD
    assert score.decode_success is True
    assert score.field_equal == dict.fromkeys(FIELDS, True)
    assert score.hard_field_equal == dict.fromkeys(HARD, True)
    assert score.full_message_equal is True
    assert score.hard_constraint_case_pass is True
    assert score.mock_task_completed is True
    assert score.fallback_required is False
    assert score.unrecovered_fields == []


@pytest.mark.parametrize(("field", "value"), [
    ("task", "Do publish. 不要发布。"),
    ("task", "Do not publish. 发布。"),
    ("task", "Do not publish. 要发布。"),
    ("next_step", "Review on 2026-11-02; do not send."),
    ("approval_required", False),
    ("evidence", ["inline evidence", "tail "]),
    ("evidence", ["tail ", "inline evidence", "inline evidence"]),
    ("evidence", ["inline evidence", "inline evidence", "tail"]),
    ("constraints", ["Budget <= $25.", "No purchase."]),
    ("blocker", ""),
    ("next_step", None),
    ("message_id", "other-id"),
    ("message_type", "result"),
    ("status", "completed"),
    ("dictionary_version", "literal-v1"),
])
def test_valid_content_changes_are_detected_field_by_field(field, value):
    expected = message()
    score = score_attempt(expected, response_for(message(**{field: value})), request(expected))
    assert score.decode_success is True
    assert score.field_equal == {name: name != field for name in FIELDS}
    assert score.hard_field_equal == {name: name != field for name in HARD}
    assert score.hard_constraint_case_pass is (field not in HARD)
    assert score.full_message_equal is False
    assert score.mock_task_completed is False
    assert score.fallback_required is True
    assert score.unrecovered_fields == []


def test_empty_to_null_is_not_equal():
    expected = message(blocker="")
    score = score_attempt(expected, response_for(message()), request(expected))
    assert score.decode_success is True
    assert score.field_equal["blocker"] is False
    assert score.mock_task_completed is False


def test_type_equality_does_not_coerce_integer_oracle_to_boolean():
    expected = message().model_copy(update={"approval_required": 1})
    score = score_attempt(expected, response_for(message()), request(message()))
    assert score.field_equal["approval_required"] is False
    assert score.hard_constraint_case_pass is False
    assert score.mock_task_completed is False


@pytest.mark.parametrize("damage", [
    "malformed", "empty", "hash", "operation", "actions", "missing", "extra",
    "duplicate", "namespace", "version", "boolean_type",
])
def test_invalid_receipt_is_unrecovered_even_when_source_status_completed(damage):
    expected = message(status="completed")
    req = request(expected)
    data = json.loads(response_for(expected).raw_output)
    if damage == "hash":
        data["message_sha256"] = "0" * 64
    elif damage == "operation":
        data["operation"] = "execute"
    elif damage == "actions":
        data["actions"] = ["publish"]
    elif damage == "missing":
        del data["message"]["blocker"]
    elif damage == "extra":
        data["unexpected"] = True
    elif damage == "boolean_type":
        data["message"]["approval_required"] = 1
    elif damage in ("namespace", "version"):
        changed = message(status="completed", **(
            {"user_id": "other-user"} if damage == "namespace" else {"dictionary_version": "v2"}
        ))
        data = json.loads(response_for(changed).raw_output)
        if damage == "version":
            req = request(expected, input_format="ac1")
    raw = canonical_json(data)
    if damage == "malformed":
        raw = "{"
    elif damage == "empty":
        raw = ""
    elif damage == "duplicate":
        raw = raw[:-1] + ',"actions":[]}'
    score = score_attempt(expected, AdapterResponse(status="ok", raw_output=raw, error=None), req)
    assert score.decode_success is False
    assert score.field_equal == dict.fromkeys(FIELDS, False)
    assert score.hard_field_equal == dict.fromkeys(HARD, False)
    assert score.unrecovered_fields == list(FIELDS)
    assert score.full_message_equal is False
    assert score.hard_constraint_case_pass is False
    assert score.mock_task_completed is False
    assert score.fallback_required is True


@pytest.mark.parametrize(("stage", "retryable", "fallback"), [
    ("render", False, True), ("render", True, True),
    ("decode", False, True), ("decode", True, True),
    ("response", False, True), ("response", True, True),
    ("adapter", False, True), ("adapter", True, False),
    ("policy", False, True),
])
def test_error_fallback_only_defers_retryable_adapter_errors(stage, retryable, fallback):
    expected = message()
    response = AdapterResponse(status="error", raw_output=None, error=EvalError(
        stage=stage, code="synthetic_error", message="Synthetic error.", retryable=retryable,
    ))
    score = score_attempt(expected, response, request(expected))
    assert score.decode_success is False
    assert score.field_equal == dict.fromkeys(FIELDS, False)
    assert score.hard_field_equal == dict.fromkeys(HARD, False)
    assert score.unrecovered_fields == list(FIELDS)
    assert score.mock_task_completed is False
    assert score.fallback_required is fallback


def test_changed_and_unrecovered_hard_fields_do_not_overlap():
    expected = message()
    req = request(expected)
    scores = [
        score_attempt(expected, response_for(message(approval_required=False)), req),
        score_attempt(expected, None, req),
    ]
    report = aggregate_scores(scores)
    assert report["changed_hard_field_count"] == 1
    assert report["unrecovered_hard_field_count"] == 6
    assert report["changed_hard_field_denominator"] == 12
    assert report["unrecovered_hard_field_denominator"] == 12
    assert report["changed_hard_field_rate"] == 1 / 12
    assert report["unrecovered_hard_field_rate"] == 0.5
    assert report["equal_hard_total"] == 5
    assert report["equal_field_total"] == 10
    assert report["hard_constraint_case_pass_count"] == 0
    assert report["decode_success_count"] == 1
    assert report["mock_task_completed_count"] == 0


def test_empty_scores_have_zero_denominators_and_null_rates():
    report = aggregate_scores([])
    assert report["logical_case_count"] == 0
    for key, value in report.items():
        if key in ("field_equal_counts", "field_equal_denominators"):
            assert value == dict.fromkeys(FIELDS, 0)
        elif key == "field_equal_rates":
            assert value == dict.fromkeys(FIELDS, None)
        elif key.endswith("_rate"):
            assert value is None
        else:
            assert value == 0
    assert report["field_fidelity_denominator"] == 0
    assert report["hard_field_preservation_denominator"] == 0
    assert report["mock_task_completed_denominator"] == 0


def test_aggregation_does_not_guess_recovery_or_merge_initial_with_final():
    expected = message()
    req = request(expected)
    transient = AdapterResponse(status="error", raw_output=None, error=EvalError(
        stage="adapter", code="temporary", message="Temporary error.", retryable=True,
    ))
    first = score_attempt(expected, transient, req)
    final = score_attempt(expected, response_for(expected), request(expected, attempt_index=1))
    initial_report = aggregate_scores([first])
    final_report = aggregate_scores([final])
    assert initial_report["mock_task_completed_rate"] == 0.0
    assert final_report["mock_task_completed_rate"] == 1.0
    assert initial_report["fallback_required_case_count"] == 0
    assert final_report["fallback_required_case_count"] == 0
    for report in (initial_report, final_report):
        assert not any("retry" in key or "retried" in key or "recovered_case" in key
                       or "performed" in key or "total_score" in key for key in report)
    # The runner, not this scorer, marks the terminal attempt after budget exhaustion.
    terminal = first.model_copy(update={"fallback_required": True})
    assert aggregate_scores([terminal])["fallback_required_case_count"] == 1

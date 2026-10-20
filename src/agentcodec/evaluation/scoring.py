"""Pure offline pipeline scores; never a semantic-safety or model-quality score."""

from agentcodec.codec import FallbackRequired
from agentcodec.schema import MessageV1

from .contracts import AdapterResponse, AttemptScore, EvalRequest
from .mock import validate_receipt


HARD_FIELDS = ("task", "blocker", "next_step", "evidence", "constraints", "approval_required")
_MESSAGE_FIELDS = tuple(MessageV1.model_fields)


def _strict_equal(expected: object, recovered: object) -> bool:
    """Preserve types, list order, duplicates, whitespace, and null/empty distinctions."""
    if type(expected) is not type(recovered):
        return False
    if isinstance(expected, list):
        return len(expected) == len(recovered) and all(
            _strict_equal(left, right) for left, right in zip(expected, recovered)
        )
    return expected == recovered


def score_attempt(
    expected: MessageV1, response: AdapterResponse | None, request: EvalRequest,
) -> AttemptScore:
    """Validate the receipt, then compare every field with the private oracle.

    Exact hard-field equality is a conservative proxy, not semantic safety.
    Retry budget exhaustion and recovery transitions belong to the runner.
    """
    recovered = None
    fallback_required = True
    if response is not None:
        if response.status == "error":
            fallback_required = not (
                response.error.stage == "adapter" and response.error.retryable
            )
        else:
            try:
                recovered = validate_receipt(response.raw_output, request).message
            except FallbackRequired:
                pass

    field_equal = {
        name: recovered is not None and _strict_equal(
            getattr(expected, name), getattr(recovered, name),
        )
        for name in _MESSAGE_FIELDS
    }
    hard_field_equal = {name: field_equal[name] for name in HARD_FIELDS}
    full_message_equal = all(field_equal.values())
    if recovered is not None:
        fallback_required = not full_message_equal
    return AttemptScore(
        decode_success=recovered is not None,
        field_equal=field_equal,
        hard_field_equal=hard_field_equal,
        unrecovered_fields=list(_MESSAGE_FIELDS) if recovered is None else [],
        full_message_equal=full_message_equal,
        hard_constraint_case_pass=all(hard_field_equal.values()),
        mock_task_completed=full_message_equal,
        fallback_required=fallback_required,
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def aggregate_scores(scores: list[AttemptScore]) -> dict[str, object]:
    """Aggregate one selected attempt per logical case, including failed cases.

    Call separately for initial and final selections. This function cannot infer
    retries or fallback performed/recovered events from attempt scores.
    """
    case_count = len(scores)
    field_denominator = len(_MESSAGE_FIELDS) * case_count
    hard_denominator = len(HARD_FIELDS) * case_count
    field_counts = {
        name: sum(score.field_equal[name] for score in scores) for name in _MESSAGE_FIELDS
    }
    result: dict[str, object] = {
        "logical_case_count": case_count,
        "field_equal_counts": field_counts,
        "field_equal_denominators": dict.fromkeys(_MESSAGE_FIELDS, case_count),
        "field_equal_rates": {
            name: _rate(count, case_count) for name, count in field_counts.items()
        },
    }
    # Each metric retains its own numerator, denominator, and rate. No total score.
    metrics = (
        ("decode_success_count", "decode_success", sum(s.decode_success for s in scores), case_count),
        ("full_message_equal_count", "full_message_equal",
         sum(s.full_message_equal for s in scores), case_count),
        ("equal_field_total", "field_fidelity", sum(field_counts.values()), field_denominator),
        ("unrecovered_field_count", "unrecovered_field",
         sum(len(s.unrecovered_fields) for s in scores), field_denominator),
        ("equal_hard_total", "hard_field_preservation",
         sum(sum(s.hard_field_equal.values()) for s in scores), hard_denominator),
        ("hard_constraint_case_pass_count", "hard_constraint_case_pass",
         sum(s.hard_constraint_case_pass for s in scores), case_count),
        ("changed_hard_field_count", "changed_hard_field",
         sum(not s.hard_field_equal[name] and name not in s.unrecovered_fields
             for s in scores for name in HARD_FIELDS), hard_denominator),
        ("unrecovered_hard_field_count", "unrecovered_hard_field",
         sum(name in s.unrecovered_fields for s in scores for name in HARD_FIELDS), hard_denominator),
        ("mock_task_completed_count", "mock_task_completed",
         sum(s.mock_task_completed for s in scores), case_count),
        ("fallback_required_case_count", "fallback_required_case",
         sum(s.fallback_required for s in scores), case_count),
    )
    for count_key, metric, numerator, denominator in metrics:
        result[count_key] = numerator
        result[f"{metric}_denominator"] = denominator
        result[f"{metric}_rate"] = _rate(numerator, denominator)
    return result

"""Remaining local text costs under an explicit deterministic-clean assumption.

Resource texts are caller-declared complete exposures, including the entire
selected dictionary and synchronization metadata, not locally matched entries.
Each resource setup_text is one independent initialization request. Contexts
are concatenated in dependency-union order before the enveloped payload in one
request; no implicit separators, acknowledgements, or file-read events exist.
Only whole measured attempts contribute to cost. Component deltas explain that
same cost; they are never added a second time.
"""

from agentcodec.codec import FallbackRequired

from . import meter
from .contracts import (
    COMPONENT_KINDS, BreakEvenResult, CandidateCost, CandidatePayload,
    ResourceSnapshot, SelectionRequest, TextAttempt, TextComponent, freeze_request,
)
from .resources import required_resources, resource_cost_reason, validate_snapshot


_EXCLUDED = ("local_cpu", "output_direction")
_CLEAN_REASONS = ("deterministic_clean", "no_model_response", "no_network_ack")
_LINEAR_ASSUMPTIONS = ("deterministic_clean", "stationary_costs", "resources_remain_valid")


class _CostRejected(FallbackRequired):
    code = "adaptive_invalid_cost"

    def __init__(self):
        super().__init__("adaptive cost configuration rejected")


def _attempts(candidate: CandidatePayload, request: SelectionRequest,
              resources: tuple[ResourceSnapshot, ...]) -> tuple[TextAttempt, ...]:
    setups = []
    contexts = []
    for index, resource in enumerate(resources):
        if resource.state == "cold" and (resource.exposure == "once_per_scope"
                or resource.independent_setup):
            setups.append(TextAttempt(event_id=f"setup-{index}", attempt_kind="setup",
                components=(TextComponent(kind="setup", text=resource.setup_text,
                                          resource_identity=resource.identity),),
                simulated=False))
        if resource.exposure == "per_request_context":
            contexts.append(TextComponent(kind="context", text=resource.context_text,
                                          resource_identity=resource.identity))
    envelope = request.envelope
    pieces = contexts + [
        TextComponent(kind="other", text=envelope.prefix, resource_identity=None),
        TextComponent(kind="payload", text=candidate.payload, resource_identity=None),
        TextComponent(kind="other", text=envelope.suffix, resource_identity=None),
        TextComponent(kind="other", text="\n" if envelope.final_lf else "", resource_identity=None),
    ]
    initial = TextAttempt(event_id="initial", attempt_kind="initial", components=tuple(pieces),
                          simulated=False, multiplicity=request.policy.repeat_count)
    return (*setups, initial)


def price_candidate(candidate: CandidatePayload, request: SelectionRequest) -> CandidateCost:
    """Price one fixed candidate N times; no sending or readiness transition.

    Configuration conflicts still fail closed before cost insufficiency is
    considered. An unknown complete cost has no partial attempt/component total.
    Incomplete history can leave remaining exact while cumulative costs are null.
    """
    request = freeze_request(request)
    snapshot = validate_snapshot(request)
    candidate = CandidatePayload.model_validate(candidate)
    resources = required_resources(candidate, snapshot)
    spent = (sum(event.tokens for event in snapshot.historical_events)
             if snapshot.history_complete else None)
    reasons = list(_CLEAN_REASONS)
    if not snapshot.history_complete:
        reasons.append("history_incomplete")
    new_count = sum(resource.state == "cold" for resource in resources)

    def unknown(extra: tuple[str, ...]) -> CandidateCost:
        return CandidateCost(candidate_id=candidate.candidate_id, remaining_expected_tokens=None,
            scope_spent_tokens=spent, scope_lifecycle_expected_tokens=None, attempts=(),
            component_totals=None, excluded=_EXCLUDED,
            reason_codes=tuple(dict.fromkeys((*reasons, *extra))), new_resource_count=new_count)

    if candidate.eligibility != "eligible":
        return unknown(candidate.reason_codes)
    missing = tuple(reason for resource in resources if (reason := resource_cost_reason(
        resource, repeat_count=request.policy.repeat_count)) is not None)
    if missing:
        return unknown(missing)
    attempts = _attempts(candidate, request, resources)
    try:
        measured = meter.measure_attempts(attempts, request.tokenizer)
    except meter.MeasurementUnavailable as error:
        return unknown((error.reason_code,))
    # Each total is per attempt, and multiplicity is applied exactly once.
    remaining = sum(item.total_tokens * item.multiplicity for item in measured)
    totals = dict.fromkeys(COMPONENT_KINDS, 0)
    for attempt, result in zip(attempts, measured, strict=True):
        for component, tokens in zip(attempt.components, result.component_tokens, strict=True):
            totals[component.kind] += tokens * result.multiplicity
    return CandidateCost(candidate_id=candidate.candidate_id, remaining_expected_tokens=remaining,
        scope_spent_tokens=spent,
        scope_lifecycle_expected_tokens=None if spent is None else spent + remaining,
        attempts=measured, component_totals=totals, excluded=_EXCLUDED,
        reason_codes=tuple(reasons), new_resource_count=new_count)


def compare_costs(cost: CandidateCost, baseline: CandidateCost) -> tuple[int | None, float | None]:
    """Return baseline minus remaining cost, never savings against sunk history."""
    cost = CandidateCost.model_validate(cost)
    baseline = CandidateCost.model_validate(baseline)
    current, reference = cost.remaining_expected_tokens, baseline.remaining_expected_tokens
    if current is None or reference is None:
        return None, None
    savings = reference - current
    return savings, None if reference == 0 else savings / reference


def break_even(setup_a: int, variable_a: int, setup_b: int, variable_b: int) -> BreakEvenResult:
    """Solve strict wins on positive integers for caller-proven stationary costs.

    This is a conditional algebraic result, not a promise of resource validity
    outside the declared snapshot. Event schedules lacking one constant V must
    not be coerced to these coefficients; use break_even_for_costs to reject them.
    """
    if any(type(term) is not int or term < 0 for term in (setup_a, variable_a, setup_b, variable_b)):
        raise _CostRejected()
    delta_setup = setup_a - setup_b
    delta_variable = variable_b - variable_a
    first = None
    interval = None
    kind = "infinity"
    if delta_variable > 0:
        first = max(1, delta_setup // delta_variable + 1)
        kind, interval = "finite", (first, None)
    elif delta_variable == 0:
        if delta_setup < 0:
            first, kind, interval = 1, "finite", (1, None)
    else:
        # n * (-delta_variable) < -delta_setup: equality is excluded.
        last = (-delta_setup - 1) // (-delta_variable)
        if last >= 1:
            first, kind, interval = 1, "interval", (1, last)
    return BreakEvenResult(kind=kind, first_strict_win=first, winning_interval=interval,
                          assumptions=_LINEAR_ASSUMPTIONS, reason_codes=())


def _linear_terms(cost: CandidateCost) -> tuple[int, int] | None:
    """Recognize only a one-initial-request stationary ledger, conservatively."""
    if cost.remaining_expected_tokens is None:
        return None
    initials = [attempt for attempt in cost.attempts if attempt.attempt_kind == "initial"]
    if len(initials) != 1:
        return None
    setup = 0
    for attempt in cost.attempts:
        if attempt.simulated:
            return None
        if attempt.attempt_kind == "setup" and attempt.multiplicity == 1:
            setup += attempt.total_tokens
        elif attempt.attempt_kind != "initial":
            return None
    initial = initials[0]
    if setup + initial.total_tokens * initial.multiplicity != cost.remaining_expected_tokens:
        return None
    return setup, initial.total_tokens


def break_even_for_costs(cost: CandidateCost, baseline: CandidateCost) -> BreakEvenResult:
    """Guard the four-integer solver against unknown or nonstationary ledgers.

    The result remains conditional on resources remaining valid, not an infinite
    extension of N's finite coverage. No offline event simulation is performed.
    """
    cost = CandidateCost.model_validate(cost)
    baseline = CandidateCost.model_validate(baseline)
    terms_a, terms_b = _linear_terms(cost), _linear_terms(baseline)
    if terms_a is None or terms_b is None:
        unknown = cost.remaining_expected_tokens is None or baseline.remaining_expected_tokens is None
        return BreakEvenResult(kind="not_applicable", first_strict_win=None, winning_interval=None,
            assumptions=("deterministic_clean",),
            reason_codes=("required_cost_unknown" if unknown else "nonstationary_costs",))
    return break_even(*terms_a, *terms_b)

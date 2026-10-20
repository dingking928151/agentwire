"""One-message, offline encoding advice; never send, recover or commit readiness.

Only complete exact comparisons can select a new format. Configuration,
round-trip integrity and capacity errors reject the entire decision, whereas
missing measurement evidence may preserve a strictly validated legal original.
"""

from fractions import Fraction
import hashlib

from agentcodec.codec import FallbackRequired

from .candidates import build_candidates, validate_explicit_selection
from .contracts import (
    CANDIDATE_IDS, MAX_HISTORY_EVENTS, CandidateCost, CandidatePayload,
    SelectionDecision, SelectionRequest, canonical_text, freeze_request, profile_id,
)
from .costs import break_even_for_costs, compare_costs, price_candidate
from .resources import propose_resources, required_resources, validate_snapshot


_EXCLUDED = ("local_cpu", "output_direction")
_ASSUMPTIONS = ("deterministic_clean", "record_only", "no_send", "no_retry",
                "no_automatic_fallback", "no_model_response", "no_network_ack",
                "local_ordinary_text_only")
_REJECTION_CODES = frozenset({
    "adaptive_invalid_contract", "adaptive_invalid_selection", "adaptive_candidate_integrity",
    "adaptive_invalid_resource", "adaptive_stale_epoch", "adaptive_invalid_cost", "adaptive_invalid_event",
})
_UNKNOWN_CODES = frozenset({"tokenizer_unavailable", "profile_unreliable", "surrogate_only",
                            "required_cost_unknown", "resource_validity_unknown"})


class _CapacityRejected(FallbackRequired):
    code = "adaptive_invalid_event"

    def __init__(self):
        super().__init__("adaptive text event rejected")


def _unavailable(reason: str) -> tuple[CandidatePayload, ...]:
    return tuple(CandidatePayload(candidate_id=key, declared_format=key.split("_")[0],
        payload=None, eligibility="unavailable", reason_codes=(reason,), required_resources=())
        for key in CANDIDATE_IDS)


def _unknown_cost(candidate: CandidatePayload, *, reasons: tuple[str, ...],
                  cost: CandidateCost | None = None) -> CandidateCost:
    return CandidateCost(candidate_id=candidate.candidate_id, remaining_expected_tokens=None,
        scope_spent_tokens=None if cost is None else cost.scope_spent_tokens,
        scope_lifecycle_expected_tokens=None, attempts=(), component_totals=None,
        excluded=_EXCLUDED, reason_codes=reasons,
        new_resource_count=0 if cost is None else cost.new_resource_count)


def _capacity_preflight(candidates: tuple[CandidatePayload, ...], request: SelectionRequest) -> None:
    # Run for EVERY required candidate before counting ANY, even when its resource
    # text or tokenizer is unknown. Schema-valid N is not a priceability promise.
    for candidate in candidates:
        if candidate.eligibility != "eligible":
            continue
        resources = required_resources(candidate, request.snapshot)
        setups = sum(item.state == "cold" and (item.exposure == "once_per_scope"
                     or item.independent_setup) for item in resources)
        if request.policy.repeat_count + setups > MAX_HISTORY_EVENTS:
            raise _CapacityRejected()


def _bounded_break_even(cost: CandidateCost, baseline: CandidateCost,
                        candidate: CandidatePayload, original: CandidatePayload,
                        request: SelectionRequest):
    result = break_even_for_costs(cost, baseline)
    if result.winning_interval is None:
        return result
    # The algebraic solver is conditional. Do not extend its interval beyond
    # either candidate's declared validity or expanded-event capacity.
    upper = MAX_HISTORY_EVENTS
    for payload in (candidate, original):
        resources = required_resources(payload, request.snapshot)
        setups = sum(item.state == "cold" and (item.exposure == "once_per_scope"
                     or item.independent_setup) for item in resources)
        upper = min(upper, MAX_HISTORY_EVENTS - setups,
                    *(item.valid_through_repeat for item in resources))
    lower, end = result.winning_interval
    if end is not None:
        upper = min(upper, end)
    if lower > upper:
        return result.model_copy(update=dict(kind="infinity", first_strict_win=None,
            winning_interval=None, reason_codes=("no_win_within_validity",)))
    return result.model_copy(update=dict(kind="interval", winning_interval=(lower, upper),
        assumptions=(*result.assumptions, "snapshot_repeat_coverage_bound")))


def select_encoding(request: SelectionRequest) -> SelectionDecision:
    """Return one deterministic payload proposal, not an execution or recovery.

    Revalidate and detach current fields first. Stable fail-closed reason codes
    are separate from measurement-unknown preservation; no exception text or
    partial recovered payload is exposed by rejection.
    """
    frozen = None
    base = dict(contract_version="adaptive-selection-v1", status="fallback_required",
        comparison_status="cannot_compare", reason_codes=(), selected_candidate=None,
        declared_format=None, payload=None, candidates=_unavailable("adaptive_invalid_contract"),
        costs=(), minimum_candidate=None, policy_retained=False, baseline_candidate=None,
        savings_tokens=None, savings_ratio=None, opportunity_cost_tokens=None,
        opportunity_cost_upper_bound=None, breakeven={},
        metadata_selection=dict(selection_status="unchanged", source_dictionary_version=None,
                                transport_dictionary_version=None),
        proposed_resources=(), snapshot_epoch=0,
        decision_id=hashlib.sha256(b"adaptive_invalid_contract").hexdigest(), profile_id="",
        policy_version="adaptive-policy-v1", renderer_version="adaptive-text-v1",
        assumptions=_ASSUMPTIONS, excluded=_EXCLUDED)
    try:
        frozen = freeze_request(request)
        # Identity uses the complete frozen input, not a normalized/deduplicated
        # projection, and is independent of time, filesystem paths or randomness.
        base["decision_id"] = hashlib.sha256(canonical_text(frozen.model_dump()).encode("utf-8")).hexdigest()
        base["profile_id"] = profile_id(frozen.tokenizer)
        version = frozen.message.dictionary_version
        base["metadata_selection"] = dict(selection_status="unchanged",
            source_dictionary_version=version, transport_dictionary_version=version)
        base["snapshot_epoch"] = frozen.snapshot.epoch
        validate_explicit_selection(frozen)
        snapshot = validate_snapshot(frozen)
        normalized = frozen.model_copy(update={"snapshot": snapshot})
        candidates = build_candidates(normalized)
        eligible = tuple(item for item in candidates if item.eligibility == "eligible")
        base["candidates"] = candidates
        if not eligible:
            return SelectionDecision(**(base | {"reason_codes": ("no_eligible_candidate",)}))
        _capacity_preflight(candidates, normalized)
        # Do not short-circuit on unknown costs: a later candidate can still
        # contain a hard capacity/configuration rejection, which must win.
        priced = {item.candidate_id: price_candidate(item, normalized) for item in eligible}
        original = next((item for item in eligible if item.candidate_id == frozen.original_format), None)
        baseline = None if original is None else priced[original.candidate_id]
        base["baseline_candidate"] = None if original is None else original.candidate_id
        unknown = tuple(cost for cost in priced.values() if cost.remaining_expected_tokens is None)
        if unknown:
            reasons = tuple(dict.fromkeys(reason for cost in unknown for reason in cost.reason_codes
                                           if reason in _UNKNOWN_CODES)) or ("required_cost_unknown",)
            # No partial exact table may masquerade as an allowlist-wide ranking.
            costs = tuple(_unknown_cost(item, reasons=(priced[item.candidate_id].reason_codes
                if item.candidate_id in priced else item.reason_codes), cost=priced.get(item.candidate_id))
                for item in candidates)
            update = dict(reason_codes=reasons, costs=costs,
                comparison_status="surrogate_only" if frozen.tokenizer.accuracy == "surrogate_only" else "cannot_compare")
            if original is not None:
                update.update(status="preserved_without_comparison", selected_candidate=original.candidate_id,
                              declared_format=original.declared_format, payload=original.payload)
            return SelectionDecision(**(base | update))

        def ranking(item: CandidatePayload):
            cost = priced[item.candidate_id]
            return (cost.remaining_expected_tokens, item.candidate_id != frozen.original_format,
                    cost.new_resource_count != 0, CANDIDATE_IDS.index(item.candidate_id))

        minimum = min(eligible, key=ranking)
        selected = minimum
        retained = False
        opportunity = bound = 0
        if original is not None:
            difference = baseline.remaining_expected_tokens - priced[minimum.candidate_id].remaining_expected_tokens
            threshold = max(Fraction(frozen.policy.min_savings_tokens),
                Fraction(str(frozen.policy.min_savings_ratio)) * baseline.remaining_expected_tokens)
            if difference > 0 and difference <= threshold:
                selected, retained = original, True
                opportunity, bound = difference, threshold.numerator // threshold.denominator
        selected_cost = priced[selected.candidate_id]
        savings = (None, None) if baseline is None else compare_costs(selected_cost, baseline)
        proposals = propose_resources(required_resources(selected, snapshot))
        reasons = ("policy_retained",) if retained else ()
        if not snapshot.history_complete:
            reasons += ("history_incomplete",)
        return SelectionDecision(**(base | dict(status="selected", comparison_status="exact_local_text",
            reason_codes=reasons, selected_candidate=selected.candidate_id,
            declared_format=selected.declared_format, payload=selected.payload,
            costs=tuple(priced.get(item.candidate_id) or _unknown_cost(item, reasons=item.reason_codes)
                        for item in candidates),
            minimum_candidate=minimum.candidate_id, policy_retained=retained,
            savings_tokens=savings[0], savings_ratio=savings[1], opportunity_cost_tokens=opportunity,
            opportunity_cost_upper_bound=bound,
            breakeven={} if baseline is None else {item.candidate_id: _bounded_break_even(
                priced[item.candidate_id], baseline, item, original, normalized) for item in eligible},
            proposed_resources=proposals, assumptions=(*_ASSUMPTIONS,
                *(("initialization_proposed_not_ready",) if proposals else ())))))
    except FallbackRequired as error:
        # Catching this boundary means rejection, NEVER unknown-cost preservation.
        code = getattr(error, "code", "adaptive_candidate_integrity")
        if code not in _REJECTION_CODES:
            code = "adaptive_candidate_integrity"
        return SelectionDecision(**(base | dict(status="fallback_required", comparison_status="cannot_compare",
            reason_codes=(code,), candidates=_unavailable(code), costs=(), baseline_candidate=None)))

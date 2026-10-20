"""Deterministic offline scripts, never a production execution/recovery path.

Decisions, outcomes and every resource state/epoch are supplied before replay.
A setup's success is represented ONLY by the subsequent declared snapshot; this
module neither confirms initialization nor derives ready state from an outcome.
Initial/retry use the fixed transport; an explicitly scripted fallback uses the
retained source JSON in its own domain. No selector is invoked here.

Diagnostics compare finite prefixes against the legal original format in the
TRANSPORT domain, under a clean initial-only counterfactual with the declared
initial response. They are not workflow optimization or implementation regret.
Unknown necessary costs propagate null; no tokenizer estimate is substituted.
"""

from fractions import Fraction
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from agentcodec.codec import FallbackRequired
from agentcodec.evaluation.formats import render_input
from agentcodec.schema import Identifier, MessageV1, UnicodeText

from . import meter
from .candidates import build_candidates
from .contracts import (
    MAX_HISTORY_EVENTS, MAX_REPEAT_COUNT, MAX_TEXT_BYTES, CandidateId, CostPolicy,
    NonnegativeInt, ScopeSnapshot, SelectionRequest, TextAttempt, TextComponent,
    _StrictModel, _bounded_text, _raw, canonical_text,
)
from .resources import required_resources, resource_cost_reason, validate_snapshot


class _OfflineRejected(FallbackRequired):
    code = "adaptive_invalid_offline_scenario"

    def __init__(self):
        super().__init__("adaptive offline scenario rejected")


class OfflineCase(_StrictModel):
    source_message: MessageV1
    request: SelectionRequest
    metadata_authorized: bool

    @model_validator(mode="after")
    def same_domain_or_authorized_metadata(self) -> Self:
        source, transport = self.source_message.model_dump(), self.request.message.model_dump()
        different = {key for key in source if source[key] != transport[key]}
        if different and (different != {"dictionary_version"} or not self.metadata_authorized):
            raise _OfflineRejected()
        return self


class ScheduledSnapshot(_StrictModel):
    case_index: NonnegativeInt
    repeat_index: NonnegativeInt
    stage: Literal["setup", "initial", "retry", "fallback"]
    snapshot: ScopeSnapshot


class OfflineOutcome(_StrictModel):
    case_index: NonnegativeInt
    repeat_index: NonnegativeInt
    stage: Literal["pre_send_reject", "initial", "retry", "fallback"]
    success: bool
    response_text: UnicodeText | None

    @model_validator(mode="after")
    def valid_rejection(self) -> Self:
        _bounded_text(self.response_text)
        if self.stage == "pre_send_reject" and (self.success or self.response_text is not None):
            raise _OfflineRejected()
        return self


class OfflinePath(_StrictModel):
    path_id: Identifier
    probability_numerator: NonnegativeInt = 1
    probability_denominator: Annotated[int, Field(ge=1)] = 1
    outcomes: Annotated[tuple[OfflineOutcome, ...], Field(max_length=MAX_HISTORY_EVENTS)]


class OfflineScenario(_StrictModel):
    scenario_id: Identifier
    cases: Annotated[tuple[OfflineCase, ...], Field(max_length=10_000)]
    whole_corpus_repeats: Annotated[int, Field(ge=0, le=MAX_REPEAT_COUNT)]
    fixed_decisions: Annotated[tuple[CandidateId, ...], Field(max_length=MAX_HISTORY_EVENTS)]
    resource_schedule: Annotated[tuple[ScheduledSnapshot, ...], Field(max_length=MAX_HISTORY_EVENTS)]
    paths: Annotated[tuple[OfflinePath, ...], Field(min_length=1, max_length=MAX_HISTORY_EVENTS)]
    remainder_policy: Literal["continue_declared", "stop_group"]

    @model_validator(mode="before")
    @classmethod
    def current_fields(cls, value):
        # Check explicit probability declarations before raw-field revalidation
        # fills defaults. model_dump is an explicit, complete serialized script.
        paths = value.get("paths", ()) if isinstance(value, dict) else getattr(value, "paths", ())
        if isinstance(paths, (tuple, list)) and len(paths) > 1:
            for path in paths:
                fields = path.keys() if isinstance(path, dict) else getattr(path, "model_fields_set", ())
                if not {"probability_numerator", "probability_denominator"} <= set(fields):
                    raise _OfflineRejected()
        return _raw(value)

    @model_validator(mode="after")
    def complete_bounded_script(self) -> Self:
        size = len(self.cases) * self.whole_corpus_repeats
        if size > MAX_HISTORY_EVENTS or len(self.fixed_decisions) != size:
            raise _OfflineRejected()
        boundaries = {(item.request.snapshot.scope_id, item.request.expected_user_id,
                       item.request.capabilities.receiver_id,
                       canonical_text(item.request.tokenizer.model_dump())) for item in self.cases}
        if len(boundaries) > 1:
            raise _OfflineRejected()
        if sum((Fraction(p.probability_numerator, p.probability_denominator) for p in self.paths),
               Fraction()) != 1 or len({p.path_id for p in self.paths}) != len(self.paths):
            raise _OfflineRejected()
        positions = {(c, r) for r in range(self.whole_corpus_repeats) for c in range(len(self.cases))}
        scheduled = {}
        for item in self.resource_schedule:
            key = (item.case_index, item.repeat_index, item.stage)
            if key in scheduled or key[:2] not in positions:
                raise _OfflineRejected()
            scheduled[key] = item.snapshot
        needed = {(c, r, stage) for c, r in positions for stage in ("setup", "initial")}
        expanded = 0
        for path in self.paths:
            grouped = _group_outcomes(path.outcomes)
            if set(grouped) != positions:
                raise _OfflineRejected()
            for (c, r), outcomes in grouped.items():
                stages = tuple(o.stage for o in outcomes)
                allowed = (("pre_send_reject",), ("initial",), ("initial", "retry"),
                           ("initial", "fallback"), ("initial", "retry", "fallback"))
                if stages not in allowed or any(o.success for o in outcomes[:-1]):
                    raise _OfflineRejected()
                needed.update((c, r, o.stage) for o in outcomes if o.stage in ("retry", "fallback"))
            # Even a stopped suffix must be bounded and complete before replay.
            expanded += len(path.outcomes)
            for c, r in positions:
                snap = scheduled.get((c, r, "setup"))
                if snap is not None:
                    expanded += sum(x.state == "cold" and (x.exposure == "once_per_scope"
                                    or x.independent_setup) for x in snap.resources)
        if set(scheduled) != needed or expanded + len(scheduled) > MAX_HISTORY_EVENTS:
            raise _OfflineRejected()
        if len(canonical_text(self.model_dump()).encode("utf-8")) > MAX_TEXT_BYTES:
            raise _OfflineRejected()
        return self


class OfflineResult(_StrictModel):
    paths: tuple[dict[str, object], ...]
    expected_tokens_numerator: int | None
    expected_tokens_denominator: int | None
    reason_codes: tuple[UnicodeText, ...]


def _group_outcomes(outcomes):
    grouped = {}
    previous = (-1, -1)
    for item in outcomes:
        position = (item.repeat_index, item.case_index)
        if position < previous:
            raise _OfflineRejected()
        previous = position
        grouped.setdefault((item.case_index, item.repeat_index), []).append(item)
    return grouped


def _request_at(case, snapshot):
    # Epoch is taken solely from the predeclared schedule, never a wall clock or
    # a simulated send. All identity/resource conflicts still fail closed.
    if snapshot.scope_id != case.request.snapshot.scope_id:
        raise _OfflineRejected()
    data = case.request.model_dump() | {"snapshot": snapshot, "policy": CostPolicy(),
        "capabilities": case.request.capabilities.model_copy(update={"epoch": snapshot.epoch})}
    request = SelectionRequest(**data)
    normalized = validate_snapshot(request)
    return request.model_copy(update={"snapshot": normalized})


def _setups(candidate, request):
    return tuple(resource for resource in required_resources(candidate, request.snapshot)
                 if resource.state == "cold" and (resource.exposure == "once_per_scope"
                                                  or resource.independent_setup))


def _resource_reason(resources, *, sending=False):
    reasons = []
    for item in resources:
        reason = resource_cost_reason(item, repeat_count=1)
        if reason:
            reasons.append(reason)
        if sending and item.state == "cold" and (item.exposure == "once_per_scope" or item.independent_setup):
            reasons.append("resource_not_ready")
    return tuple(dict.fromkeys(reasons))


def _setup_attempt(resource, event_id):
    if resource.setup_text is None:
        return None
    return TextAttempt(event_id=event_id, attempt_kind="setup", simulated=True,
        components=(TextComponent(kind="setup", text=resource.setup_text,
                                  resource_identity=resource.identity),))


def _payload_attempt(candidate, request, outcome, event_id, *, source=None):
    resources = required_resources(candidate, request.snapshot)
    if any(item.exposure == "per_request_context" and item.context_text is None for item in resources):
        return None
    envelope = request.envelope
    payload = candidate.payload if source is None else render_input(source, "json", None)
    components = [TextComponent(kind="context", text=item.context_text, resource_identity=item.identity)
                  for item in resources if item.exposure == "per_request_context"]
    components.extend((TextComponent(kind="other", text=envelope.prefix, resource_identity=None),
        TextComponent(kind="payload", text=payload, resource_identity=None),
        TextComponent(kind="other", text=envelope.suffix, resource_identity=None),
        TextComponent(kind="other", text="\n" if envelope.final_lf else "", resource_identity=None),
        TextComponent(kind="other", text=outcome.response_text or "", resource_identity=None)))
    return TextAttempt(event_id=event_id, attempt_kind=outcome.stage, simulated=True,
                       components=tuple(components))


def _measure(attempt, request, reasons=()):
    if reasons or attempt is None:
        return None, None, reasons or ("required_cost_unknown",)
    try:
        measured = meter.measure_attempt(attempt, request.tokenizer)
    except meter.MeasurementUnavailable as error:
        return None, None, (error.reason_code,)
    components = dict.fromkeys(("setup", "context", "payload", "other"), 0)
    for piece, tokens in zip(attempt.components, measured.component_tokens, strict=True):
        components[piece.kind] += tokens
    return measured.total_tokens, components, ()


def _clean_cost(candidate, setup_request, initial_request, initial_outcome):
    """Diagnostic only: no decisions or snapshots are changed by these counts."""
    resources = required_resources(candidate, initial_request.snapshot)
    # Setup is a distinct event. Its missing text cannot erase a known request
    # variable cost; retain validity, readiness and context checks independently.
    reasons = _resource_reason(tuple(item.model_copy(update={"setup_text": ""}) for item in resources))
    setup_total = 0
    setup_known = True
    unknown_reasons = []
    for index, resource in enumerate(_setups(candidate, setup_request)):
        tokens, _, unknown = _measure(_setup_attempt(resource, f"reference-setup-{index}"),
                                     setup_request, _resource_reason((resource,)))
        unknown_reasons.extend(unknown)
        if tokens is None:
            setup_known = False
        else:
            setup_total += tokens
    attempt = _payload_attempt(candidate, initial_request, initial_outcome, "reference-initial")
    tokens, _, unknown = _measure(attempt, initial_request, reasons)
    unknown_reasons.extend(unknown)
    total = setup_total + tokens if setup_known and tokens is not None else None
    return total, tokens, tuple(dict.fromkeys(unknown_reasons))


def _references(setup_request, initial_request, outcomes):
    initial = outcomes[0]
    if initial.stage == "pre_send_reject":
        initial = OfflineOutcome(case_index=initial.case_index, repeat_index=initial.repeat_index,
                                 stage="initial", success=True, response_text=None)
    costs, variables, reasons = {}, {}, []
    # Same-domain candidates only. Source is never a selector/candidate input.
    for candidate in build_candidates(initial_request):
        if candidate.eligibility != "eligible":
            continue
        total, variable, unknown = _clean_cost(candidate, setup_request, initial_request, initial)
        costs[candidate.candidate_id] = total
        variables[candidate.candidate_id] = variable
        reasons.extend(unknown)
    baseline = initial_request.original_format
    minimum = min(costs.values()) if costs and all(value is not None for value in costs.values()) else None
    if minimum is None and not reasons:
        reasons.append("required_cost_unknown")
    return minimum, costs.get(baseline), variables.get(baseline), tuple(dict.fromkeys(reasons))


def _intervals(flags):
    intervals = []
    start = None
    for position, win in enumerate((*flags, False), 1):
        if win and start is None:
            start = position
        elif not win and start is not None:
            intervals.append((start, position - 1))
            start = None
    return tuple(intervals)


def _replay(scenario, path, requests):
    ledger, statuses, reasons = [], [], []
    stopped = False
    grouped = _group_outcomes(path.outcomes)
    actual = baseline_total = minimum_total = setup_total = operating_delta = 0
    planned_setup = planned_savings = 0
    used_clean_savings = 0
    actual_known = True
    minimum_known = baseline_known = setup_known = recovery_known = True
    used_clean_known = full_declaration_known = True
    flags = []
    setup_events = []
    uses = []

    for r in range(scenario.whole_corpus_repeats):
        for c, case in enumerate(scenario.cases):
            fixed = scenario.fixed_decisions[r * len(scenario.cases) + c]
            setup_request = requests[c, r, "setup"]
            initial_request = requests[c, r, "initial"]
            outcomes = grouped[c, r]
            candidates = {x.candidate_id: x for x in build_candidates(initial_request)}
            chosen = candidates[fixed]
            minimum, baseline, baseline_variable, unknown = _references(
                setup_request, initial_request, outcomes)
            reasons.extend(unknown)
            chosen_clean, chosen_variable, unknown = (None, None, ())
            if chosen.eligibility == "eligible":
                chosen_clean, chosen_variable, unknown = _clean_cost(
                    chosen, setup_request, initial_request,
                    outcomes[0] if outcomes[0].stage == "initial" else OfflineOutcome(
                        case_index=c, repeat_index=r, stage="initial", success=True, response_text=None))
            reasons.extend(unknown)
            if chosen_clean is None or baseline_variable is None:
                full_declaration_known = False
            else:
                planned_setup += chosen_clean - chosen_variable
                planned_savings += max(0, baseline_variable - chosen_variable)

            status = dict(case_index=c, repeat_index=r, fixed_candidate=fixed,
                sdk_repeat_count=case.request.policy.repeat_count, reuse_unit="whole_corpus",
                source_dictionary_version=case.source_message.dictionary_version,
                transport_dictionary_version=case.request.message.dictionary_version,
                status="not_executed", reason_codes=())
            statuses.append(status)
            if stopped:
                continue
            start = len(ledger)
            local_reasons = []
            used_resources = []

            def record(attempt, req, stage, candidate_id, *, resource=None, cost_reasons=(), success=None):
                nonlocal actual_known, actual, setup_total, setup_known
                tokens, components, unknown = _measure(attempt, req, cost_reasons)
                local_reasons.extend(unknown)
                if tokens is None:
                    actual_known = False
                    if stage == "setup":
                        setup_known = False
                else:
                    actual += tokens
                    if stage == "setup":
                        setup_total += tokens
                event = dict(event_id=f"r{r}-c{c}-{stage}-{len(ledger)}", case_index=c, repeat_index=r,
                    attempt_kind=stage, candidate_id=candidate_id,
                    domain="source" if stage == "fallback" else "transport", simulated=True,
                    success=success, total_tokens=tokens, component_tokens=components,
                    measurement_basis="unknown" if tokens is None else "locally_measured",
                    outcome_basis="configured", shared_setup_uses=None, display_tokens_per_use=None)
                ledger.append(event)
                if resource is not None:
                    setup_events.append((len(ledger) - 1, resource.identity, req.snapshot.epoch))

            rejected = outcomes[0].stage == "pre_send_reject" or chosen.eligibility != "eligible"
            if rejected:
                status["status"] = "rejected"
                local_reasons.extend(("configured_pre_send_reject",) if outcomes[0].stage == "pre_send_reject"
                                     else chosen.reason_codes)
            else:
                for index, resource in enumerate(_setups(chosen, setup_request)):
                    record(_setup_attempt(resource, f"r{r}-c{c}-setup-{index}"), setup_request,
                           "setup", fixed, resource=resource,
                           cost_reasons=_resource_reason((resource,)))
                for outcome in outcomes:
                    req = requests[c, r, outcome.stage]
                    stage_candidates = {x.candidate_id: x for x in build_candidates(req)}
                    candidate = stage_candidates["json" if outcome.stage == "fallback" else fixed]
                    # JSON fallback is separately authorized by its explicit script,
                    # not by transport allowlist. Receiver capability still applies.
                    if outcome.stage == "fallback" and candidate.eligibility != "eligible":
                        fallback_req = SelectionRequest(**(req.model_dump() | {"allowlist": ("json",)}))
                        candidate = next(x for x in build_candidates(fallback_req) if x.candidate_id == "json")
                    resources = required_resources(candidate, req.snapshot)
                    missing = _resource_reason(resources, sending=True)
                    if candidate.eligibility != "eligible" or "resource_not_ready" in missing or "resource_invalid" in missing:
                        rejected = True
                        local_reasons.extend(candidate.reason_codes if candidate.eligibility != "eligible" else missing)
                        break
                    attempt = _payload_attempt(candidate, req, outcome, f"r{r}-c{c}-{outcome.stage}",
                        source=case.source_message if outcome.stage == "fallback" else None)
                    record(attempt, req, outcome.stage, candidate.candidate_id,
                           cost_reasons=missing, success=outcome.success)
                    used_resources.extend((item.identity, req.snapshot.epoch) for item in resources)
                    if outcome.success:
                        status["status"] = "simulated_success"
                        break
                    status["status"] = "simulated_failure"
                if rejected:
                    status["status"] = "rejected"
            uses.append((len(ledger), set(used_resources)))
            status["reason_codes"] = tuple(dict.fromkeys(local_reasons))
            reasons.extend(local_reasons)
            if status["status"] != "simulated_success" and scenario.remainder_policy == "stop_group":
                stopped = True
            # Only positions with actual simulated events contribute reference
            # cost. Rejected/unexecuted suffixes cannot invalidate prefix facts.
            if len(ledger) > start:
                if minimum is None:
                    minimum_known = False
                else:
                    minimum_total += minimum
                if baseline is None:
                    baseline_known = False
                else:
                    baseline_total += baseline
            request_tokens = [event["total_tokens"] for event in ledger[start:] if event["attempt_kind"] != "setup"]
            if request_tokens:
                if any(value is None for value in request_tokens) or baseline_variable is None:
                    recovery_known = False
                else:
                    operating_delta += baseline_variable - sum(request_tokens)
            if any(event["attempt_kind"] == "initial" for event in ledger[start:]):
                if baseline_variable is None or chosen_variable is None:
                    used_clean_known = False
                else:
                    used_clean_savings += max(0, baseline_variable - chosen_variable)
            # Once cumulative inputs become unknown, retain already evaluated
            # intervals and bound them by the last exactly known prefix.
            if actual_known and baseline_known:
                flags.append(actual < baseline_total)

    # Display-only S/M, with one logical use even if that case retries. Bind a
    # use to the latest setup for its exact identity at or before its epoch.
    assigned = dict.fromkeys((index for index, _, _ in setup_events), 0)
    for before_index, use in uses:
        assigned_here = set()
        for identity, epoch in use:
            applicable = [index for index, target, setup_epoch in setup_events
                          if index < before_index and target == identity and setup_epoch <= epoch]
            if applicable:
                assigned_here.add(applicable[-1])
        for index in assigned_here:
            assigned[index] += 1
    for index, count in assigned.items():
        event = ledger[index]
        event["shared_setup_uses"] = count
        if count and event["total_tokens"] is not None:
            share = Fraction(event["total_tokens"], count)
            event["display_tokens_per_use"] = dict(numerator=share.numerator, denominator=share.denominator)
    # Later fault overhead must offset earlier operating savings. Bound only
    # the net recovered setup, not each position's signed contribution.
    recovered_setup = min(setup_total, max(0, operating_delta)) if setup_known and recovery_known else None
    unamortized = setup_total - recovered_setup if recovered_setup is not None else None
    planned_unamortized = max(0, planned_setup - planned_savings) if full_declaration_known else None
    # Isolate missing logical uses under the clean declaration. Retry/fallback
    # overhead belongs to realized cost/regret, not to a reuse-count shortfall.
    used_clean_unamortized = max(0, setup_total - used_clean_savings) if setup_known and used_clean_known else None
    shortfall = (None if used_clean_unamortized is None or planned_unamortized is None
                 else max(0, used_clean_unamortized - planned_unamortized))
    regret = actual - minimum_total if actual_known and minimum_known else None
    baseline_comparable = actual_known and baseline_known
    if any(value is None for value in (unamortized, shortfall, regret)) or not baseline_comparable:
        reasons.append("cannot_compare")
    intervals = _intervals(flags)
    return dict(path_id=path.path_id, attempts=tuple(ledger), case_statuses=tuple(statuses),
        unamortized_setup_tokens=unamortized,
        simulated_realized_regret=regret,
        repeat_shortfall_loss_tokens=shortfall,
        winning_intervals=dict(intervals=intervals, first_strict_win=intervals[0][0] if intervals else None,
            valid_through_position=len(flags), basis="fixed_event_sequence", baseline_domain="transport",
            baseline="legal_original_format", comparison_status="exact_local_text" if baseline_comparable else "cannot_compare")), (
        actual if actual_known else None), tuple(dict.fromkeys(reasons))


def simulate_scenario(scenario: OfflineScenario) -> OfflineResult:
    """Replay a complete finite script; all output events are explicitly simulated.

    whole_corpus_repeats is independent of SDK same-instance repeat_count. Each
    logical position simulates at most one initial, one identical-payload retry
    and one source JSON fallback. Dry-run performs no tokenization or setup.
    """
    scenario = OfflineScenario.model_validate(scenario)
    # Validate ALL declared snapshots before any metering, including unused
    # stopped suffixes and zero-probability paths. No conflict is masked by cost.
    requests = {(item.case_index, item.repeat_index, item.stage):
                _request_at(scenario.cases[item.case_index], item.snapshot)
                for item in scenario.resource_schedule}
    results, costs, reasons = [], [], []
    for path in scenario.paths:
        result, cost, unknown = _replay(scenario, path, requests)
        results.append(result)
        costs.append(cost)
        reasons.extend(unknown)
    weighted = tuple((path, cost) for path, cost in zip(scenario.paths, costs, strict=True)
                     if path.probability_numerator > 0)
    if any(cost is None for _, cost in weighted):
        expected = None
        reasons.append("cannot_compare")
    else:
        expected = sum((Fraction(path.probability_numerator, path.probability_denominator) * cost
                        for path, cost in weighted), Fraction())
    if any(result["winning_intervals"]["comparison_status"] == "cannot_compare" for result in results):
        reasons.append("cannot_compare")
    return OfflineResult(paths=tuple(results),
        expected_tokens_numerator=None if expected is None else expected.numerator,
        expected_tokens_denominator=None if expected is None else expected.denominator,
        reason_codes=tuple(dict.fromkeys(reasons)))

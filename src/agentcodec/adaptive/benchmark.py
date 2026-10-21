"""Reproducible local diagnostics, not model/provider or workflow optimization.

Configuration uses the existing fixed OfflineScenario scripts. Scenario IDs
explicitly declare their role with clean-, policy-, fixed-, ablation- or
negative- prefixes; untagged scenarios are diagnostics, never acceptance cases.
The tag is a declaration, not evidence: clean scripts must ALSO pass the full
independent audit. Every scenario has its own denominator; heterogeneous cases
must be split into separate bounded configurations by the caller.

Only the controlled manifest contains input/resource/profile identities. Public
reports and records are constructed by allowlisted projection, not by removing
fields from SDK decisions. No raw text, dictionary, payload, content hash,
caller scenario/path identifier, or decision ID is projected into them.
"""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from agentcodec.codec import FallbackRequired
from agentcodec.evaluation.formats import parse_input, render_input
from agentcodec.schema import Identifier, MessageV1, UserDictionary

from . import meter
from .candidates import build_candidates
from .contracts import (
    MAX_TEXT_BYTES, NonnegativeInt, SelectionRequest, TextAttempt, TextComponent,
    _StrictModel, canonical_text,
)
from .offline import OfflineScenario, simulate_scenario
from .resources import required_resources, resource_cost_reason, validate_snapshot
from .selector import select_encoding


DISCLAIMER = ("Local ordinary-text tokenizer diagnostics only. Not evidence of model understanding, "
    "business success, provider performance, actual billing savings, or global corpus routing optimality. "
    "All attempts are configured offline simulations; no messages are sent.")
_HARD_FIELDS = ("task", "blocker", "next_step", "evidence", "constraints", "approval_required")
_EXCLUDED = ("local_cpu", "output_direction", "provider_billing", "business_execution")
_ASSUMPTIONS = ("local_ordinary_text_only", "deterministic_clean_sdk", "predeclared_fixed_decisions",
    "no_failure_time_rerouting", "no_model_response_unless_configured", "no_network_ack",
    "source_json_reference_excluded_from_transport_minimum", "sdk_and_whole_corpus_units_distinct")


class _BenchmarkRejected(FallbackRequired):
    code = "adaptive_invalid_benchmark"

    def __init__(self):
        super().__init__("adaptive benchmark rejected")


class BenchmarkInput(_StrictModel):
    corpus_id: Identifier
    source_path: Identifier
    source_sha256: Identifier
    source_count: Annotated[int, Field(ge=1, le=10_000)]
    dictionary_path: Identifier | None
    dictionary_sha256: Identifier | None
    dictionary_source: Literal["local_file", "legacy_demo", "none"]

    @model_validator(mode="after")
    def provenance(self) -> Self:
        hashes = (self.source_sha256, self.dictionary_sha256)
        if any(h is not None and (len(h) != 64 or any(c not in "0123456789abcdef" for c in h)) for h in hashes):
            raise _BenchmarkRejected()
        if self.dictionary_source == "local_file":
            if self.dictionary_path is None or self.dictionary_sha256 is None:
                raise _BenchmarkRejected()
        elif self.dictionary_path is not None or (self.dictionary_source == "none" and self.dictionary_sha256 is not None):
            raise _BenchmarkRejected()
        elif self.dictionary_source == "legacy_demo" and self.dictionary_sha256 is None:
            raise _BenchmarkRejected()
        return self


class AdaptiveBenchmarkConfig(_StrictModel):
    scenarios: Annotated[tuple[OfflineScenario, ...], Field(min_length=1)]
    code_revision: Identifier
    input_manifest: Annotated[tuple[BenchmarkInput, ...], Field(min_length=1)]
    scenario_corpora: dict[str, str]

    @model_validator(mode="after")
    def complete_mapping(self) -> Self:
        ids = [s.scenario_id for s in self.scenarios]
        corpora = [i.corpus_id for i in self.input_manifest]
        if (len(set(ids)) != len(ids) or len(set(corpora)) != len(corpora)
                or set(self.scenario_corpora) != set(ids)
                or set(self.scenario_corpora.values()) - set(corpora)):
            raise _BenchmarkRejected()
        if len(canonical_text(self.model_dump()).encode()) > MAX_TEXT_BYTES:
            raise _BenchmarkRejected()
        return self


class AdaptiveBenchmarkArtifacts(_StrictModel):
    manifest: dict[str, object]
    report: dict[str, object]
    records: tuple[dict[str, object], ...]


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _BenchmarkRejected()
        result[key] = value
    return result


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_TEXT_BYTES + 1)
    if len(data) > MAX_TEXT_BYTES:
        raise _BenchmarkRejected()
    return data


def _inputs(config):
    """Bind every script's full source order and explicit dictionary to its manifest."""
    loaded = {}
    for item in config.input_manifest:
        raw = _read(item.source_path)
        if _digest(raw) != item.source_sha256:
            raise _BenchmarkRejected()
        messages = tuple(MessageV1.model_validate(json.loads(line, object_pairs_hook=_unique), strict=True)
                         for line in raw.decode("utf-8").split("\n") if line.strip())
        if len(messages) != item.source_count or len({m.message_id for m in messages}) != len(messages):
            raise _BenchmarkRejected()
        dictionary = None
        if item.dictionary_source == "local_file":
            data = _read(item.dictionary_path)
            if _digest(data) != item.dictionary_sha256:
                raise _BenchmarkRejected()
            dictionary = UserDictionary.model_validate(json.loads(data, object_pairs_hook=_unique), strict=True)
        elif item.dictionary_source == "legacy_demo":
            # Explicit opt-in only. Never dump this object into an artifact.
            from agentcodec.benchmark import DEMO_DICTIONARY
            dictionary = UserDictionary.model_validate(vars(DEMO_DICTIONARY), strict=True).model_copy(deep=True)
            if _digest(canonical_text(dictionary.model_dump()).encode()) != item.dictionary_sha256:
                raise _BenchmarkRejected()
        loaded[item.corpus_id] = messages, dictionary
    for scenario in config.scenarios:
        messages, dictionary = loaded[config.scenario_corpora[scenario.scenario_id]]
        if tuple(c.source_message for c in scenario.cases) != messages:
            raise _BenchmarkRejected()
        for case in scenario.cases:
            selection = case.request.dictionary
            if selection.dictionary is not None and selection.dictionary != dictionary:
                raise _BenchmarkRejected()
    return loaded


def _domain(case):
    req = case.request
    return dict(metadata_selection="authorized_transport" if case.source_message != req.message else "unchanged",
        dictionary_version_present=req.message.dictionary_version is not None,
        encoding_name=req.tokenizer.encoding_name if req.tokenizer.encoding_name in ("o200k_base", "cl100k_base") else "unknown",
        original_format=req.original_format,
        allowlist=list(req.allowlist), supported_candidates=list(req.capabilities.supported_candidates),
        accuracy=req.tokenizer.accuracy,
        resources=[dict(state=r.state, exposure=r.exposure, independent_setup=r.independent_setup,
            setup_known=r.setup_text is not None, context_known=r.context_text is not None,
            valid_through_repeat=r.valid_through_repeat) for r in req.snapshot.resources],
        history_complete=req.snapshot.history_complete,
        min_savings_tokens=req.policy.min_savings_tokens, min_savings_ratio=req.policy.min_savings_ratio)


def _group_key(case):
    # Private equality key may use identities; it is NEVER a public hash.
    req = case.request
    return canonical_text(dict(domain=_domain(case), tokenizer=req.tokenizer.model_dump(),
        scope=(req.snapshot.scope_id, req.snapshot.receiver_id, req.snapshot.user_id),
        metadata=(case.source_message.dictionary_version, req.message.dictionary_version),
        dependencies=req.capabilities.model_dump(), dictionary=req.dictionary.model_dump(),
        resources=[r.model_dump() for r in req.snapshot.resources],
        history=[h.model_dump() for h in req.snapshot.historical_events],
        envelope=req.envelope.model_dump(), repeat_count=req.policy.repeat_count))


def _request_at(case, snapshot):
    return SelectionRequest(**(case.request.model_dump() | dict(snapshot=snapshot,
        capabilities=case.request.capabilities.model_copy(update={"epoch": snapshot.epoch}))))


def _audit(candidate, req):
    """Independent full-text enumeration: no selector costs/price_candidate helpers.

    Construct whole requests directly, then count all requests as one bounded
    batch. Independently count each full text again, and compare both totals and
    signed component ledgers. Unknown necessary costs invalidate the minimum.
    """
    resources = required_resources(candidate, req.snapshot)
    if any(resource_cost_reason(r, repeat_count=req.policy.repeat_count) for r in resources):
        return None, None
    attempts = []
    for i, resource in enumerate(resources):
        if resource.state == "cold" and (resource.exposure == "once_per_scope" or resource.independent_setup):
            attempts.append(TextAttempt(event_id=f"audit-setup-{i}", attempt_kind="setup", simulated=False,
                components=(TextComponent(kind="setup", text=resource.setup_text, resource_identity=resource.identity),)))
    env = req.envelope
    components = tuple(TextComponent(kind="context", text=r.context_text, resource_identity=r.identity)
                       for r in resources if r.exposure == "per_request_context")
    components += (TextComponent(kind="other", text=env.prefix, resource_identity=None),
        TextComponent(kind="payload", text=candidate.payload, resource_identity=None),
        TextComponent(kind="other", text=env.suffix + ("\n" if env.final_lf else ""), resource_identity=None))
    attempts.append(TextAttempt(event_id="audit-initial", attempt_kind="initial", simulated=False,
        multiplicity=req.policy.repeat_count, components=components))
    try:
        measured = meter.measure_attempts(tuple(attempts), req.tokenizer)
        # A second whole-request pass, not a sum of separately tokenized fields.
        with meter._get_encoding(req.tokenizer) as encoding:
            independent = [len(encoding.encode_ordinary("".join(c.text for c in a.components))) for a in attempts]
        differences = sum(abs(m.total_tokens - n) + abs(sum(m.component_tokens) - n)
                          for m, n in zip(measured, independent, strict=True))
        return sum(n * a.multiplicity for n, a in zip(independent, attempts, strict=True)), differences
    except meter.MeasurementUnavailable:
        return None, None


def _bare_count(text, profile):
    try:
        with meter._get_encoding(profile) as encoding:
            return len(encoding.encode_ordinary(text))
    except meter.MeasurementUnavailable:
        return None


def _role(scenario):
    for prefix, role in (("clean-", "clean_acceptance"), ("policy-", "policy_diagnostic"),
                         ("fixed-", "fixed_reference"), ("ablation-", "payload_only_ablation"),
                         ("negative-", "negative_expected")):
        if scenario.scenario_id.startswith(prefix):
            return role
    return "diagnostic_only"


def _record(scenario_index, c, r, case, req, fixed, role):
    decision = select_encoding(req)
    candidates = build_candidates(req)
    costs, differences = {}, []
    full = hard = False
    candidate_full = candidate_hard = 0
    chosen = next(x for x in candidates if x.candidate_id == fixed)
    for candidate in candidates:
        if candidate.eligibility == "eligible":
            costs[candidate.candidate_id], difference = _audit(candidate, req)
            differences.append(difference)
            dictionary = req.dictionary.dictionary if candidate.candidate_id.endswith("_dictionary") else None
            decoded = parse_input(candidate.payload, candidate.declared_format, req.expected_user_id, dictionary).model_dump()
            original = req.message.model_dump()
            candidate_full += original == decoded
            candidate_hard += all(original[k] == decoded[k] for k in _HARD_FIELDS)
    known = bool(costs) and all(x is not None for x in costs.values())
    minimum = min(costs.values()) if known else None
    selected = costs.get(fixed)
    regret = selected - minimum if selected is not None and minimum is not None else None
    if chosen.eligibility == "eligible":
        dictionary = req.dictionary.dictionary if fixed.endswith("_dictionary") else None
        restored = parse_input(chosen.payload, chosen.declared_format, req.expected_user_id, dictionary)
        original, decoded = req.message.model_dump(), restored.model_dump()
        full = original == decoded
        hard = all(original[k] == decoded[k] for k in _HARD_FIELDS)
    legal = chosen.eligibility == "eligible" and fixed in req.allowlist and fixed in req.capabilities.supported_candidates
    baseline = costs.get(req.original_format)
    source_reference = _bare_count(render_input(case.source_message, "json", None), req.tokenizer)
    spent = sum(h.tokens for h in validate_snapshot(req).historical_events) if req.snapshot.history_complete else None
    savings = baseline - selected if known and baseline is not None and selected is not None else None
    return dict(version="adaptive-benchmark-record-v1", scenario_index=scenario_index,
        case_index=c, repeat_index=r, fixed_candidate=fixed, selected_candidate=decision.selected_candidate,
        declaration_matches_selector=fixed == decision.selected_candidate,
        comparison_status=decision.comparison_status, status=decision.status,
        policy_retained=decision.policy_retained, unavailable_count=sum(x.eligibility == "unavailable" for x in candidates),
        full_equal=full, hard_equal=hard, eligibility_violation_count=0 if legal else 1,
        candidate_full_equal_count=candidate_full, candidate_hard_equal_count=candidate_hard,
        candidate_costs=costs, minimum_tokens=minimum, sdk_total_tokens=selected,
        sdk_repeat_count=req.policy.repeat_count, sdk_reuse_unit="same_message_instance",
        scope_spent_tokens=spent, scope_lifecycle_expected_tokens=None if spent is None or selected is None else spent + selected,
        predicted_regret=regret, savings_tokens=savings,
        opportunity_cost_tokens=decision.opportunity_cost_tokens,
        opportunity_cost_upper_bound=decision.opportunity_cost_upper_bound,
        recount_difference=None if any(d is None for d in differences) else sum(differences),
        breakeven={key: dict(kind=value.kind, first_strict_win=value.first_strict_win,
            winning_interval=value.winning_interval) for key, value in decision.breakeven.items()},
        source_json_reference=dict(tokens=source_reference, boundary="bare_source_json_reference",
            included_in_transport_minimum=False),
        payload_only_ablation_tokens=_bare_count(chosen.payload, req.tokenizer)
            if role == "payload_only_ablation" and chosen.payload is not None else None)


def _sum_known(values):
    return None if any(v is None for v in values) else sum(values)


def _weighted(scenario, paths, key):
    values = [(Fraction(p.probability_numerator, p.probability_denominator), result[key])
              for p, result in zip(scenario.paths, paths, strict=True) if p.probability_numerator]
    if any(v is None for _, v in values):
        return None
    value = sum((weight * v for weight, v in values), Fraction())
    return value.numerator if value.denominator == 1 else dict(numerator=value.numerator, denominator=value.denominator)


def _snapshot_manifest(snapshot):
    return dict(epoch=snapshot.epoch, scope_id=snapshot.scope_id, receiver_id=snapshot.receiver_id,
        user_id=snapshot.user_id, history_complete=snapshot.history_complete,
        historical_events=[h.model_dump() for h in snapshot.historical_events], resources=[
            {k: v for k, v in resource.model_dump().items() if k not in ("setup_text", "context_text", "readiness_basis")}
            | dict(readiness_basis_declared=resource.readiness_basis is not None,
                setup_utf8_bytes=None if resource.setup_text is None else len(resource.setup_text.encode()),
                setup_sha256=None if resource.setup_text is None else _digest(resource.setup_text.encode()),
                context_sha256=None if resource.context_text is None else _digest(resource.context_text.encode()),
                context_utf8_bytes=None if resource.context_text is None else len(resource.context_text.encode()))
            for resource in snapshot.resources])


def _run(config):
    _inputs(config)
    corpus_labels = {item.corpus_id: item.corpus_id if item.corpus_id in ("diverse", "repetitive") else str(i)
                     for i, item in enumerate(config.input_manifest)}
    records, summaries, manifest_scenarios = [], {}, []
    clean_results = []
    for index, scenario in enumerate(config.scenarios):
        if len({_group_key(c) for c in scenario.cases}) > 1:
            raise _BenchmarkRejected()
        role = _role(scenario)
        schedule = {(s.case_index, s.repeat_index, s.stage): s.snapshot for s in scenario.resource_schedule}
        local = []
        for r in range(scenario.whole_corpus_repeats):
            for c, case in enumerate(scenario.cases):
                req = _request_at(case, schedule[c, r, "setup"])
                local.append(_record(index, c, r, case, req,
                    scenario.fixed_decisions[r * len(scenario.cases) + c], role))
        # All choices and all resource/failure scripts exist before replay. The
        # simulator never calls a selector, including after a scripted failure.
        result = simulate_scenario(scenario)
        records.extend(local)
        statuses = {}
        for path in result.paths:
            for status in path["case_statuses"]:
                key = status["status"]
                statuses[key] = statuses.get(key, 0) + 1
        statuses.update(unavailable=sum(x["unavailable_count"] for x in local),
            cannot_compare=sum(x["comparison_status"] != "exact_local_text" for x in local),
            policy_retained=sum(x["policy_retained"] for x in local))
        per_path_totals = []
        for p, path in enumerate(result.paths):
            attempts = path["attempts"]
            per_path_totals.append(dict(path_index=p, attempts=len(attempts),
                total_tokens=_sum_known([a["total_tokens"] for a in attempts]),
                attempt_kind_tokens={kind: _sum_known([a["total_tokens"] for a in attempts if a["attempt_kind"] == kind])
                    for kind in ("setup", "initial", "retry", "fallback", "other")},
                ledger_equal=all(a["total_tokens"] is not None and a["component_tokens"] is not None
                    and sum(a["component_tokens"].values()) == a["total_tokens"] for a in attempts)))
        simulated = None
        lifecycle = None
        initial_request = (_request_at(scenario.cases[0], schedule[0, 0, "setup"])
                           if scenario.whole_corpus_repeats else scenario.cases[0].request)
        initial_snapshot = validate_snapshot(initial_request)
        spent = (sum(h.tokens for h in initial_snapshot.historical_events)
                 if initial_snapshot.history_complete else None)
        if result.expected_tokens_denominator is not None:
            value = Fraction(result.expected_tokens_numerator, result.expected_tokens_denominator)
            simulated = value.numerator if value.denominator == 1 else dict(numerator=value.numerator, denominator=value.denominator)
            if spent is not None:
                cumulative = value + spent
                lifecycle = (cumulative.numerator if cumulative.denominator == 1 else
                    dict(numerator=cumulative.numerator, denominator=cumulative.denominator))
        predicted = _sum_known([x["predicted_regret"] for x in local])
        clean_script = all(o.stage == "initial" and o.success and o.response_text in (None, "")
                           for p in scenario.paths for o in p.outcomes)
        passed = (bool(local) and clean_script and all(x["full_equal"] and x["hard_equal"]
            and x["eligibility_violation_count"] == 0 and x["predicted_regret"] == 0
            and x["recount_difference"] == 0 and x["declaration_matches_selector"]
            and x["candidate_full_equal_count"] == len(x["candidate_costs"])
            and x["candidate_hard_equal_count"] == len(x["candidate_costs"])
            and x["comparison_status"] == "exact_local_text" and not x["policy_retained"] for x in local)
            and statuses.get("simulated_success", 0) == len(local) * len(scenario.paths)
            and all(x["ledger_equal"] for x in per_path_totals)
            and all(c.request.policy.min_savings_tokens == 0 and c.request.policy.min_savings_ratio == 0
                    for c in scenario.cases))
        if role == "clean_acceptance" and scenario.whole_corpus_repeats > 0:
            clean_results.append(passed)
        domain = _domain(scenario.cases[0]) if scenario.cases else {}
        summaries[str(index)] = dict(corpus_id=corpus_labels[config.scenario_corpora[scenario.scenario_id]],
            comparison_domain=domain, reuse_unit="whole_corpus", whole_corpus_repeats=scenario.whole_corpus_repeats,
            logical_case_count=len(local), full_equal_count=sum(x["full_equal"] for x in local),
            hard_equal_count=sum(x["hard_equal"] for x in local),
            eligibility_violation_count=sum(x["eligibility_violation_count"] for x in local),
            predicted_regret=predicted, predicted_regret_unit="sum_of_declared_sdk_decisions",
            simulated_realized_regret=_weighted(scenario, result.paths, "simulated_realized_regret"),
            repeat_shortfall_loss_tokens=_weighted(scenario, result.paths, "repeat_shortfall_loss_tokens"),
            cost_totals=dict(simulated_tokens=simulated, paths=per_path_totals,
                scope_spent_tokens=spent, scope_lifecycle_expected_tokens=lifecycle),
            status_counts=statuses, status_count_unit="path_logical_positions_and_sdk_decisions_separate",
            acceptance_role=role, clean_acceptance_passed=passed if role == "clean_acceptance" else None,
            selector_claim=role in ("clean_acceptance", "policy_diagnostic"),
            dry_run=scenario.whole_corpus_repeats == 0)
        manifest_scenarios.append(dict(scenario_index=index,
            corpus_id=corpus_labels[config.scenario_corpora[scenario.scenario_id]], acceptance_role=role,
            profiles=[scenario.cases[0].request.tokenizer.model_dump()] if scenario.cases else [],
            renderer_version="adaptive-text-v1", policy_version="adaptive-policy-v1", comparison_domain=domain,
            metadata_domains=[dict(source_dictionary_version=c.source_message.dictionary_version,
                transport_dictionary_version=c.request.message.dictionary_version, metadata_authorized=c.metadata_authorized)
                for c in scenario.cases],
            reuse_unit="whole_corpus", whole_corpus_repeats=scenario.whole_corpus_repeats,
            sdk_repeat_counts=[c.request.policy.repeat_count for c in scenario.cases],
            fixed_decisions=list(scenario.fixed_decisions),
            envelopes=[dict(prefix_utf8_bytes=len(c.request.envelope.prefix.encode()),
                suffix_utf8_bytes=len(c.request.envelope.suffix.encode()), final_lf=c.request.envelope.final_lf,
                prefix_sha256=_digest(c.request.envelope.prefix.encode()), suffix_sha256=_digest(c.request.envelope.suffix.encode()))
                for c in scenario.cases],
            resource_schedule=[dict(case_index=s.case_index, repeat_index=s.repeat_index, stage=s.stage,
                snapshot=_snapshot_manifest(s.snapshot)) for s in scenario.resource_schedule],
            paths=[dict(path_index=p, probability_numerator=path.probability_numerator,
                probability_denominator=path.probability_denominator, outcomes=[dict(case_index=o.case_index,
                    repeat_index=o.repeat_index, stage=o.stage, success=o.success,
                    response_known=o.response_text is not None,
                    response_sha256=None if o.response_text is None else _digest(o.response_text.encode()),
                    response_utf8_bytes=None if o.response_text is None else len(o.response_text.encode()))
                    for o in path.outcomes]) for p, path in enumerate(scenario.paths)],
            remainder_policy=scenario.remainder_policy, assumptions=list(_ASSUMPTIONS), excluded=list(_EXCLUDED)))
    # Read and compare again before releasing any artifact. No input mutation,
    # resequencing, auto-dictionary discovery, or cached manifest trust.
    _inputs(config)
    return AdaptiveBenchmarkArtifacts(manifest=dict(version="adaptive-benchmark-manifest-v1",
        code_revision=config.code_revision, input_manifest=[i.model_dump() for i in config.input_manifest],
        scenarios=manifest_scenarios, assumptions=list(_ASSUMPTIONS), excluded=list(_EXCLUDED)),
        report=dict(version="adaptive-benchmark-v1", disclaimer=DISCLAIMER,
            acceptance_passed=bool(clean_results) and all(clean_results), clean_scenario_count=len(clean_results),
            scenarios=summaries, reference_only_formats=["natural_language", "drixl"]), records=tuple(records))


def run_adaptive_benchmark(config: AdaptiveBenchmarkConfig) -> AdaptiveBenchmarkArtifacts:
    """Return deterministic data only. No output files, downloads, or execution.

    Never stringify dependency errors: parsers can embed input text in chained
    exceptions. Invalid benchmark configuration fails closed with a stable code.
    """
    try:
        return _run(AdaptiveBenchmarkConfig.model_validate(config))
    except (FallbackRequired, OSError, ValueError, TypeError, KeyError, RecursionError):
        raise _BenchmarkRejected() from None

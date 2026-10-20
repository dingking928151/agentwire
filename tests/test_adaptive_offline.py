"""Synthetic-only fixed-script replay; no corpus or dictionary fixtures."""

from contextlib import contextmanager
import hashlib
import importlib
import json
import socket
import subprocess

import pytest

from agentcodec.adaptive import meter
from agentcodec.adaptive.contracts import (
    CANDIDATE_IDS, CostPolicy, ResourceIdentity, ResourceSnapshot, ScopeSnapshot,
    SelectionRequest, TextEnvelope,
)
from agentcodec.codec import FallbackRequired
from agentcodec.schema import MessageV1


MESSAGE = dict(message_id="synthetic", message_type="delegate", task="do not spend $20 ",
    status="pending", blocker=None, next_step="", evidence=["b", "a", "b"], constraints=[],
    approval_required=False, user_id="user", dictionary_version=None)
PROFILE = dict(encoding_name="cl100k_base", library_version="synthetic",
    vocabulary_sha256="0" * 64, renderer_version="adaptive-text-v1",
    accuracy="exact_local_text", mapping_basis=None)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def resource(**changes):
    identity = ResourceIdentity(scope_id="scope", receiver_id="receiver", user_id="user",
        resource_kind="schema", version="v1", content_hash="1" * 64,
        tokenizer_profile_id=digest(PROFILE))
    return ResourceSnapshot(**(dict(identity=identity, state="cold", exposure="once_per_scope",
        epoch=0, valid_through_repeat=50, readiness_basis="declared confirmation",
        setup_text="SETUP", context_text="CTX") | changes))


def request(*resources, dependencies=None, count=1, **changes):
    policy = dict(repeat_count=count)
    if count > 1:
        policy["repeat_declaration"] = dict(unit="same_message_instance", total_count=count,
                                           instance_sha256=digest(MESSAGE))
    return SelectionRequest(**(dict(contract_version="adaptive-selection-v1", message=MESSAGE,
        expected_user_id="user", tokenizer=PROFILE, original_format="json", allowlist=CANDIDATE_IDS,
        capabilities=dict(receiver_id="receiver", epoch=0, supported_candidates=CANDIDATE_IDS,
                          candidate_resources=dependencies or {}),
        snapshot=dict(scope_id="scope", receiver_id="receiver", user_id="user", epoch=0,
                      history_complete=True, historical_events=(), resources=resources),
        policy=policy, envelope=dict(prefix="<", suffix=">", final_lf=True)) | changes))


@pytest.fixture
def api():
    # Missing module is a per-test feature failure, not a collection/environment error.
    class API:
        def __getattr__(self, name):
            return getattr(importlib.import_module("agentcodec.adaptive.offline"), name)
    return API()


@pytest.fixture(autouse=True)
def no_external_or_selection(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline replay must not execute, send, select, or commit readiness")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    selector = importlib.import_module("agentcodec.adaptive.selector")
    resources = importlib.import_module("agentcodec.adaptive.resources")
    monkeypatch.setattr(selector, "select_encoding", forbidden)
    monkeypatch.setattr(resources, "confirm_initialization", forbidden)


def counter(monkeypatch, *, json_tokens=20, ac1_tokens=8, setup=12, context=3):
    # Only the tokenizer boundary is replaced. Real rendering, validation and meter stay in use.
    # Enveloped request: < + payload + > + LF = payload + 3; responses add 2.
    class Encoding:
        def encode_ordinary(self, text):
            if text == "":
                n = 0
            elif text == "SETUP":
                n = setup
            elif text == "CTX":
                n = context
            else:
                n = 0
                if text.startswith("CTX"):
                    n += context
                    text = text[3:]
                if text.startswith("<"):
                    n += 1
                    text = text[1:]
                if text.startswith(("AC1|", "AC2|")):
                    n += ac1_tokens if text.startswith("AC1|") else 30
                    # The payload ends at our synthetic envelope; message contains no >.
                    text = text[text.index(">"):] if ">" in text else ""
                elif text.startswith("{"):
                    n += json_tokens
                    text = text[text.index(">"):] if ">" in text else ""
                elif text:
                    raise AssertionError("unexpected synthetic text boundary")
                n += (1 if text.startswith(">") else 0) + (1 if "\n" in text else 0)
                n += 2 if text.endswith("OK") else 0
            return [0] * n
    @contextmanager
    def encoding(profile):
        yield Encoding()
    monkeypatch.setattr(meter, "_get_encoding", encoding)


def case(api, req=None, source=None, authorized=False):
    req = request() if req is None else req
    return api.OfflineCase(source_message=req.message if source is None else source,
                           request=req, metadata_authorized=authorized)


def outcome(api, stage="initial", success=True, *, c=0, r=0, response="OK"):
    return api.OfflineOutcome(case_index=c, repeat_index=r, stage=stage,
                              success=success, response_text=response)


def scenario(api, cases, scripts, *, repeats=1, decisions=None, schedule=None, policy="continue_declared",
             probabilities=None):
    if schedule is None:
        schedule = tuple(api.ScheduledSnapshot(case_index=c, repeat_index=r, stage=stage,
                           snapshot=item.request.snapshot)
            for r in range(repeats) for c, item in enumerate(cases)
            for stage in ("setup", "initial", "retry", "fallback")
            if stage in ("setup", "initial") or any(
                o.case_index == c and o.repeat_index == r and o.stage == stage
                for script in scripts for o in script))
    paths = tuple(api.OfflinePath(path_id=f"path-{i}", outcomes=script,
                  **({} if probabilities is None else dict(probability_numerator=probabilities[i][0],
                       probability_denominator=probabilities[i][1]))) for i, script in enumerate(scripts))
    return api.OfflineScenario(scenario_id="synthetic", cases=cases, whole_corpus_repeats=repeats,
        fixed_decisions=("ac1",) * (len(cases) * repeats) if decisions is None else decisions,
        resource_schedule=schedule, paths=paths, remainder_policy=policy)


def totals(path):
    return [a["total_tokens"] for a in path["attempts"]]


@pytest.mark.parametrize("stages,want,status", [
    ((("pre_send_reject", False),), [], "rejected"),
    ((("initial", True),), [13], "simulated_success"),
    ((("initial", False),), [13], "simulated_failure"),
    ((("initial", False), ("retry", True)), [13, 13], "simulated_success"),
    ((("initial", False), ("retry", False)), [13, 13], "simulated_failure"),
    ((("initial", False), ("retry", False), ("fallback", True)), [13, 13, 25], "simulated_success"),
    ((("initial", False), ("retry", False), ("fallback", False)), [13, 13, 25], "simulated_failure"),
])
def test_fixed_failure_paths_charge_all_attempts(api, monkeypatch, stages, want, status):
    # Catches erased failed W, recursive retry, omitted response/envelope, and double-counted response.
    counter(monkeypatch)
    script = tuple(outcome(api, s, ok, response=None if s == "pre_send_reject" else "OK") for s, ok in stages)
    result = api.simulate_scenario(scenario(api, (case(api),), (script,)))
    path = result.paths[0]
    assert set(path) == {"path_id", "attempts", "case_statuses", "unamortized_setup_tokens",
                         "simulated_realized_regret", "repeat_shortfall_loss_tokens", "winning_intervals"}
    assert totals(path) == want
    assert result.expected_tokens_numerator == sum(want) and result.expected_tokens_denominator == 1
    assert [s["status"] for s in path["case_statuses"]] == [status]
    assert all(a["simulated"] is True for a in path["attempts"])
    assert [a["domain"] for a in path["attempts"]] == [
        "source" if stage == "fallback" else "transport" for stage, _ in stages if stage != "pre_send_reject"]
    for a in path["attempts"]:
        assert sum(a["component_tokens"].values()) == a["total_tokens"]
        assert a["measurement_basis"] == "locally_measured"
        assert a["outcome_basis"] == "configured"
    assert "do not spend" not in json.dumps(result.model_dump())


def test_every_retry_and_source_fallback_reexposes_context(api, monkeypatch):
    # Catches treating ready context as free, changing retry payload, or using transport for source fallback.
    counter(monkeypatch)
    item = resource(state="ready", exposure="per_request_context")
    req = request(item, dependencies={"ac1": (item.identity,), "json": (item.identity,)})
    script = (outcome(api, success=False), outcome(api, "retry", False), outcome(api, "fallback"))
    path = api.simulate_scenario(scenario(api, (case(api, req),), (script,))).paths[0]
    assert totals(path) == [16, 16, 28]
    assert [a["component_tokens"]["context"] for a in path["attempts"]] == [3, 3, 3]
    assert [a["candidate_id"] for a in path["attempts"]] == ["ac1", "ac1", "json"]


@pytest.mark.parametrize("policy,want", [("continue_declared", ["simulated_failure", "rejected"]),
                                         ("stop_group", ["simulated_failure", "not_executed"])])
def test_continue_does_not_replan_later_message(api, monkeypatch, policy, want):
    # Catches failure-time selection or pretending that the stopped suffix was sent successfully.
    counter(monkeypatch)
    invalid = resource(state="invalid")
    second = request(invalid, dependencies={"ac1": (invalid.identity,)})
    cases = (case(api), case(api, second))
    script = (outcome(api, success=False), outcome(api, c=1))
    obj = scenario(api, cases, (script,), policy=policy)
    before = obj.model_dump()
    path = api.simulate_scenario(obj).paths[0]
    assert [s["status"] for s in path["case_statuses"]] == want
    assert totals(path) == [13]
    assert [s["fixed_candidate"] for s in path["case_statuses"]] == ["ac1", "ac1"]
    assert obj.model_dump() == before
    assert api.simulate_scenario(obj).model_dump() == api.simulate_scenario(obj).model_dump()


@pytest.mark.parametrize("ready,want,status", [(True, [12, 13, 13], "simulated_success"),
                                              (False, [12, 12], "rejected")])
def test_setup_readiness_only_comes_from_declared_snapshots(api, monkeypatch, ready, want, status):
    # Catches inferred readiness after failed initialization, duplicate successful shared setup, and M=0 division.
    counter(monkeypatch)
    cold = resource()
    req = request(cold, dependencies={"ac1": (cold.identity,)})
    hot = cold.model_copy(update={"state": "ready", "epoch": 1})
    hot_snapshot = req.snapshot.model_copy(update={"epoch": 1, "resources": (hot,)})
    snapshots = []
    for c in range(2):
        for stage in ("setup", "initial"):
            snap = hot_snapshot if ready and (c == 1 or stage == "initial") else req.snapshot
            snapshots.append(api.ScheduledSnapshot(case_index=c, repeat_index=0, stage=stage, snapshot=snap))
    obj = scenario(api, (case(api, req), case(api, req)),
        ((outcome(api), outcome(api, c=1)),), schedule=tuple(snapshots))
    path = api.simulate_scenario(obj).paths[0]
    assert totals(path) == want
    assert [s["status"] for s in path["case_statuses"]] == [status, status]
    setups = [a for a in path["attempts"] if a["attempt_kind"] == "setup"]
    assert setups[0]["shared_setup_uses"] == (2 if ready else 0)
    assert setups[0]["display_tokens_per_use"] == ({"numerator": 6, "denominator": 1} if ready else None)
    assert req.snapshot.resources[0].state == "cold"
    if not ready:
        assert path["unamortized_setup_tokens"] == 24


def test_probability_paths_and_unknown_required_cost(api, monkeypatch):
    # Catches float-weighted expectation, unknown-as-zero, and conflation of configured outcomes with measurements.
    counter(monkeypatch)
    scripts = ((outcome(api),), (outcome(api, success=False), outcome(api, "retry")))
    obj = scenario(api, (case(api),), scripts, probabilities=((1, 3), (2, 3)))
    result = api.simulate_scenario(obj)
    assert (result.expected_tokens_numerator, result.expected_tokens_denominator) == (65, 3)
    unknown = resource(setup_text=None)
    req = request(unknown, dependencies={"ac1": (unknown.identity,)})
    result = api.simulate_scenario(scenario(api, (case(api, req),), ((outcome(api),),)))
    assert result.expected_tokens_numerator is result.expected_tokens_denominator is None
    assert "cannot_compare" in result.reason_codes and "required_cost_unknown" in result.reason_codes
    path = result.paths[0]
    assert path["simulated_realized_regret"] is None
    assert path["attempts"][0]["total_tokens"] is None
    assert path["attempts"][0]["measurement_basis"] == "unknown"
    assert path["attempts"][0]["outcome_basis"] == "configured"


@pytest.mark.parametrize("probabilities", [((1, 2), (1, 3)), ((-1, 1), (2, 1)), ((1, 0), (1, 1)),
                                          ((True, 1), (0, 1)), ((1.0, 1), (0, 1))])
def test_invalid_probability_rejected(api, probabilities):
    # Catches incomplete probability mass, negative mass, zero denominator, and coercion.
    with pytest.raises(FallbackRequired):
        scenario(api, (case(api),), ((outcome(api),), (outcome(api),)), probabilities=probabilities)


def test_omitted_probabilities_only_for_single_path(api):
    with pytest.raises(FallbackRequired):
        scenario(api, (case(api),), ((outcome(api),), (outcome(api),)))


@pytest.mark.parametrize("repeats", [0, 1, 10, 50])
def test_dry_run_and_reuse_units(api, monkeypatch, repeats):
    # Catches multiplying corpus passes by SDK N and any setup/metering on dry-run.
    counter(monkeypatch)
    req = request(count=10)
    script = tuple(outcome(api, r=r) for r in range(repeats))
    if repeats == 0:
        def forbidden(*args):
            pytest.fail("dry run must not initialize a tokenizer")
        monkeypatch.setattr(meter, "_get_encoding", forbidden)
    obj = scenario(api, (case(api, req),), (script,), repeats=repeats)
    path = api.simulate_scenario(obj).paths[0]
    assert len(path["attempts"]) == repeats
    assert (api.simulate_scenario(obj).expected_tokens_numerator,
            api.simulate_scenario(obj).expected_tokens_denominator) == (13 * repeats, 1)
    assert all(s["sdk_repeat_count"] == 10 and s["reuse_unit"] == "whole_corpus"
               for s in path["case_statuses"])
    if repeats == 0:
        assert path["unamortized_setup_tokens"] == path["repeat_shortfall_loss_tokens"] == 0
        assert path["winning_intervals"]["intervals"] == ()
    with pytest.raises(FallbackRequired):
        CostPolicy(repeat_count=0)


def test_nonstationary_break_even_and_early_stop(api, monkeypatch):
    # Catches an infinite straight-line threshold and hiding unrecovered setup after early stop.
    counter(monkeypatch, setup=18)
    cold = resource()
    hot = cold.model_copy(update={"state": "ready"})
    req = request(cold, dependencies={"ac1": (cold.identity,)})
    hot_snapshot = req.snapshot.model_copy(update={"resources": (hot,)})
    schedule = tuple(api.ScheduledSnapshot(case_index=0, repeat_index=r, stage=stage,
                        snapshot=req.snapshot if stage == "setup" and r in (0, 2) else hot_snapshot)
        for r in range(4) for stage in ("setup", "initial"))
    # Initial costs 11, JSON clean reference 23. Setup 18 paid at pass 1 and 3.
    scripts = (tuple(outcome(api, r=r, response=None) for r in range(4)),)
    obj = scenario(api, (case(api, req),), scripts, repeats=4, schedule=schedule)
    path = api.simulate_scenario(obj).paths[0]
    assert totals(path) == [18, 11, 11, 18, 11, 11]
    wins = path["winning_intervals"]
    assert wins["intervals"] == ((2, 2), (4, 4))
    assert wins["first_strict_win"] == 2 and wins["valid_through_position"] == 4
    assert wins["basis"] == "fixed_event_sequence" and wins["baseline_domain"] == "transport"
    stopped = scenario(api, (case(api, req),),
        ((outcome(api, success=False, response=None), *scripts[0][1:]),), repeats=4,
        schedule=schedule, policy="stop_group")
    path = api.simulate_scenario(stopped).paths[0]
    assert [s["status"] for s in path["case_statuses"]] == [
        "simulated_failure", "not_executed", "not_executed", "not_executed"]
    assert path["unamortized_setup_tokens"] == 6  # 18 setup minus 12 first-request operating saving.
    assert path["repeat_shortfall_loss_tokens"] == 6


def test_early_stop_reports_positive_setup_shortfall(api, monkeypatch):
    counter(monkeypatch, setup=30)
    cold = resource()
    hot = cold.model_copy(update={"state": "ready"})
    req = request(cold, dependencies={"ac1": (cold.identity,)})
    schedule = tuple(api.ScheduledSnapshot(case_index=0, repeat_index=r, stage=stage,
        snapshot=req.snapshot if r == 0 and stage == "setup" else req.snapshot.model_copy(update={"resources": (hot,)}))
        for r in range(4) for stage in ("setup", "initial"))
    obj = scenario(api, (case(api, req),),
        ((outcome(api, success=False, response=None), *(outcome(api, r=r, response=None) for r in range(1, 4))),),
        repeats=4, schedule=schedule, policy="stop_group")
    path = api.simulate_scenario(obj).paths[0]
    assert path["unamortized_setup_tokens"] == 18
    assert path["repeat_shortfall_loss_tokens"] == 18
    assert path["simulated_realized_regret"] == 18


@pytest.mark.parametrize("difference", ["task", "evidence", "approval_required", "dictionary_version"])
def test_metadata_authorization_is_narrow(api, difference):
    changed = MESSAGE | {difference: {"task": "different", "evidence": [], "approval_required": True,
                                      "dictionary_version": "v1"}[difference]}
    source = MessageV1(**changed)
    if difference == "dictionary_version":
        with pytest.raises(FallbackRequired):
            case(api, source=source)
        assert case(api, source=source, authorized=True).source_message.dictionary_version == "v1"
    else:
        with pytest.raises(FallbackRequired):
            case(api, source=source, authorized=True)


def test_source_json_fallback_is_separately_metered_domain(api, monkeypatch):
    # Catches passing source to selector or replacing transport metadata during recovery.
    counter(monkeypatch)
    source = MessageV1(**(MESSAGE | {"dictionary_version": "v1"}))
    obj = scenario(api, (case(api, source=source, authorized=True),),
                   ((outcome(api, success=False), outcome(api, "fallback")),))
    path = api.simulate_scenario(obj).paths[0]
    assert totals(path) == [13, 25]
    assert [a["domain"] for a in path["attempts"]] == ["transport", "source"]
    assert path["case_statuses"][0]["source_dictionary_version"] == "v1"
    assert path["case_statuses"][0]["transport_dictionary_version"] is None


@pytest.mark.parametrize("bad", ["missing_decision", "missing_snapshot", "duplicate_snapshot", "missing_outcome",
    "duplicate_outcome", "retry_after_success", "fallback_then_retry", "reject_response", "reject_success",
    "out_of_range", "bool_repeat", "negative_repeat", "too_many_repeats", "too_many_cases", "expanded_limit",
    "unknown_field", "surrogate", "mutated_message"])
def test_strict_bounded_complete_scripts(api, bad):
    # Catches accepting dynamic/incomplete scripts, illegal stage transitions, and capacity/coercion bypasses.
    obj = scenario(api, (case(api),), ((outcome(api),),))
    data = obj.model_dump()
    if bad == "missing_decision":
        data["fixed_decisions"] = ()
    elif bad == "missing_snapshot":
        data["resource_schedule"] = data["resource_schedule"][:1]
    elif bad == "duplicate_snapshot":
        data["resource_schedule"] += (data["resource_schedule"][0],)
    elif bad == "missing_outcome":
        data["paths"][0]["outcomes"] = ()
    elif bad == "duplicate_outcome":
        data["paths"][0]["outcomes"] *= 2
    elif bad == "retry_after_success":
        data["paths"][0]["outcomes"] += (outcome(api, "retry").model_dump(),)
    elif bad == "fallback_then_retry":
        data["paths"][0]["outcomes"] = (outcome(api, success=False).model_dump(),
            outcome(api, "fallback", False).model_dump(), outcome(api, "retry").model_dump())
    elif bad in ("reject_response", "reject_success"):
        data["paths"][0]["outcomes"] = (dict(case_index=0, repeat_index=0, stage="pre_send_reject",
            success=bad == "reject_success", response_text="OK" if bad == "reject_response" else None),)
    elif bad == "out_of_range":
        data["paths"][0]["outcomes"][0]["case_index"] = 1
    elif bad in ("bool_repeat", "negative_repeat", "too_many_repeats"):
        data["whole_corpus_repeats"] = {"bool_repeat": True, "negative_repeat": -1,
                                        "too_many_repeats": 1_000_001}[bad]
    elif bad == "too_many_cases":
        data["cases"] *= 10_001
    elif bad == "expanded_limit":
        data["whole_corpus_repeats"] = 100_001
    elif bad == "unknown_field":
        data["callback"] = lambda: "json"
    elif bad == "surrogate":
        data["paths"][0]["outcomes"][0]["response_text"] = "\ud800"
    else:
        obj.cases[0].request.message.evidence.append(False)
    with pytest.raises(FallbackRequired):
        api.simulate_scenario(obj if bad == "mutated_message" else api.OfflineScenario.model_validate(data))


def test_scheduled_epoch_conflict_rejected_before_metering(api, monkeypatch):
    counter(monkeypatch)
    item = resource(state="ready")
    req = request(item, dependencies={"ac1": (item.identity,)})
    bad = req.snapshot.model_copy(update={"epoch": 1})
    schedule = tuple(api.ScheduledSnapshot(case_index=0, repeat_index=0, stage=stage, snapshot=bad)
                     for stage in ("setup", "initial"))
    with pytest.raises(FallbackRequired):
        api.simulate_scenario(scenario(api, (case(api, req),), ((outcome(api),),), schedule=schedule))


def test_reinitialization_assigns_setup_only_to_later_uses(api, monkeypatch):
    # Catches retroactively assigning earlier usage to a future setup at the same declared epoch.
    counter(monkeypatch, setup=18)
    cold = resource()
    hot = cold.model_copy(update={"state": "ready"})
    req = request(cold, dependencies={"ac1": (cold.identity,)})
    schedule = tuple(api.ScheduledSnapshot(case_index=0, repeat_index=r, stage=stage,
        snapshot=req.snapshot if stage == "setup" and r in (0, 2)
        else req.snapshot.model_copy(update={"resources": (hot,)}))
        for r in range(4) for stage in ("setup", "initial"))
    obj = scenario(api, (case(api, req),), (tuple(outcome(api, r=r) for r in range(4)),),
                   repeats=4, schedule=schedule)
    path = api.simulate_scenario(obj).paths[0]
    setups = [event for event in path["attempts"] if event["attempt_kind"] == "setup"]
    assert [event["shared_setup_uses"] for event in setups] == [2, 2]
    assert [event["display_tokens_per_use"] for event in setups] == [
        {"numerator": 9, "denominator": 1}, {"numerator": 9, "denominator": 1}]


def test_different_profile_or_scope_cannot_be_aggregated(api, monkeypatch):
    # Catches reporting a single expected-token optimum across incomparable tokenizers or scopes.
    counter(monkeypatch)
    first = case(api)
    for other in (request(tokenizer=PROFILE | {"encoding_name": "o200k_base"}),
                  request(snapshot=first.request.snapshot.model_copy(update={"scope_id": "other"}))):
        with pytest.raises(FallbackRequired):
            api.simulate_scenario(scenario(api, (first, case(api, other)),
                ((outcome(api), outcome(api, c=1)),)))


@pytest.mark.parametrize("kind", ["unknown", "surrogate", "missing_tokenizer"])
def test_unmeasurable_attempt_never_has_fabricated_tokens(api, monkeypatch, kind):
    # Catches char estimates, falsely measured labels, or zero regret for required unknown text.
    def missing(profile):
        raise meter.MeasurementUnavailable({"unknown": "profile_unreliable", "surrogate": "surrogate_only",
                                            "missing_tokenizer": "tokenizer_unavailable"}[kind])
    monkeypatch.setattr(meter, "_get_encoding", missing)
    obj = scenario(api, (case(api),), ((outcome(api),),))
    result = api.simulate_scenario(obj)
    assert result.expected_tokens_numerator is result.expected_tokens_denominator is None
    assert "cannot_compare" in result.reason_codes
    path = result.paths[0]
    assert path["attempts"][0]["total_tokens"] is None
    assert path["attempts"][0]["measurement_basis"] == "unknown"
    assert path["simulated_realized_regret"] is path["unamortized_setup_tokens"] is None


def test_fallback_is_scripted_even_when_not_in_transport_allowlist(api, monkeypatch):
    # Catches confusing source-domain recovery authorization with transport selector eligibility.
    counter(monkeypatch)
    req = request(allowlist=("ac1",), original_format="ac1")
    obj = scenario(api, (case(api, req),), ((outcome(api, success=False), outcome(api, "fallback")),))
    path = api.simulate_scenario(obj).paths[0]
    assert totals(path) == [13, 25]
    assert [s["status"] for s in path["case_statuses"]] == ["simulated_success"]


def test_retry_snapshot_cannot_silently_reinitialize_or_change_fixed_format(api, monkeypatch):
    # Catches performing another setup during retry or selecting a cheaper legal JSON instead.
    counter(monkeypatch)
    cold = resource()
    hot = cold.model_copy(update={"state": "ready"})
    req = request(hot, dependencies={"ac1": (cold.identity,)})
    schedule = tuple(api.ScheduledSnapshot(case_index=0, repeat_index=0, stage=stage,
        snapshot=req.snapshot if stage != "retry" else req.snapshot.model_copy(update={"resources": (cold,)}))
        for stage in ("setup", "initial", "retry"))
    obj = scenario(api, (case(api, req),), ((outcome(api, success=False), outcome(api, "retry")),),
                   schedule=schedule)
    path = api.simulate_scenario(obj).paths[0]
    assert totals(path) == [13]
    assert path["case_statuses"][0]["status"] == "rejected"
    assert "resource_not_ready" in path["case_statuses"][0]["reason_codes"]


def test_fixed_order_uses_repeat_outer_case_inner(api, monkeypatch):
    # Catches flattening fixed decisions in case-major order instead of repeat-major order.
    counter(monkeypatch)
    scripts = (tuple(outcome(api, c=c, r=r) for r in range(2) for c in range(2)),)
    obj = scenario(api, (case(api), case(api)), scripts, repeats=2,
                   decisions=("json", "ac1", "ac2", "json"))
    path = api.simulate_scenario(obj).paths[0]
    assert [event["candidate_id"] for event in path["attempts"]] == ["json", "ac1", "ac2", "json"]
    assert totals(path) == [25, 13, 35, 25]


def test_failed_fallback_json_prerequisite_does_not_erase_initial(api, monkeypatch):
    counter(monkeypatch)
    item = resource(state="invalid")
    req = request(item, dependencies={"json": (item.identity,)}, original_format="ac1")
    obj = scenario(api, (case(api, req),), ((outcome(api, success=False), outcome(api, "fallback")),))
    path = api.simulate_scenario(obj).paths[0]
    assert totals(path) == [13]
    assert path["case_statuses"][0]["status"] == "rejected"


def test_scope_schedule_cannot_change_the_case_comparison_domain(api, monkeypatch):
    counter(monkeypatch)
    req = request()
    snapshot = req.snapshot.model_copy(update={"scope_id": "other"})
    schedule = tuple(api.ScheduledSnapshot(case_index=0, repeat_index=0, stage=stage, snapshot=snapshot)
                     for stage in ("setup", "initial"))
    with pytest.raises(FallbackRequired):
        api.simulate_scenario(scenario(api, (case(api, req),), ((outcome(api),),), schedule=schedule))


def test_fallback_meters_source_not_transport_json(api, monkeypatch):
    # Catches rendering request.message during fallback despite a source-domain ledger label.
    # This oracle assigns distinct costs to the two JSON metadata domains.
    class Encoding:
        def encode_ordinary(self, text):
            if not text:
                return []
            if text == "<":
                return [0]
            if text.startswith("<AC1|"):
                base = 8
            elif text.startswith("<{"):
                base = 27 if '"dictionary_version":"v1"' in text else 20
            elif text.startswith("<AC2|"):
                base = 30
            else:
                raise AssertionError("unexpected synthetic request")
            return [0] * (1 + base + (1 if ">" in text else 0) +
                          (1 if "\n" in text else 0) + (2 if text.endswith("OK") else 0))
    @contextmanager
    def encoding(profile):
        yield Encoding()
    monkeypatch.setattr(meter, "_get_encoding", encoding)
    dictionary = dict(user_id="user", version="v1", aliases={"x": MESSAGE["task"]})
    req = request(message=MESSAGE | {"dictionary_version": "v1"},
        dictionary=dict(enabled=True, dictionary=dictionary, content_hash=digest(dictionary)))
    obj = scenario(api, (case(api, req, source=MessageV1(**MESSAGE), authorized=True),),
        ((outcome(api, success=False), outcome(api, "retry", False), outcome(api, "fallback")),),
        decisions=("ac1_dictionary",))
    path = api.simulate_scenario(obj).paths[0]
    assert totals(path) == [13, 13, 25]  # Transport JSON would incorrectly cost 32.
    assert path["case_statuses"][0]["transport_dictionary_version"] == "v1"
    assert path["case_statuses"][0]["source_dictionary_version"] is None


def test_zero_probability_unknown_path_does_not_poison_exact_expectation(api, monkeypatch):
    # Catches treating 0 * unknown as an unknown required term in the finite expectation.
    counter(monkeypatch)
    hot = resource(state="ready", exposure="per_request_context")
    req = request(hot, dependencies={"ac1": (hot.identity,)})
    missing = req.snapshot.model_copy(update={"resources": (hot.model_copy(update={"context_text": None}),)})
    schedule = tuple(api.ScheduledSnapshot(case_index=0, repeat_index=0, stage=stage,
                     snapshot=missing if stage == "retry" else req.snapshot)
                     for stage in ("setup", "initial", "retry"))
    obj = scenario(api, (case(api, req),),
        ((outcome(api),), (outcome(api, success=False), outcome(api, "retry"))),
        probabilities=((1, 1), (0, 1)), schedule=schedule)
    result = api.simulate_scenario(obj)
    assert (result.expected_tokens_numerator, result.expected_tokens_denominator) == (16, 1)
    assert result.paths[1]["attempts"][1]["total_tokens"] is None
    assert result.paths[1]["attempts"][1]["measurement_basis"] == "unknown"


def test_failure_overhead_is_not_reported_as_reuse_shortfall(api, monkeypatch):
    # Catches labeling fault overhead as a reuse shortage when all declared positions were attempted.
    counter(monkeypatch, setup=30)
    cold = resource()
    hot = cold.model_copy(update={"state": "ready"})
    req = request(cold, dependencies={"ac1": (cold.identity,)})
    schedule = tuple(api.ScheduledSnapshot(case_index=0, repeat_index=r, stage=stage,
        snapshot=req.snapshot if r == 0 and stage == "setup"
        else req.snapshot.model_copy(update={"resources": (hot,)}))
        for r in range(4) for stage in ("setup", "initial", "retry"))
    scripts = (tuple(o for r in range(4) for o in (outcome(api, r=r, success=False),
                                                 outcome(api, "retry", r=r))),)
    obj = scenario(api, (case(api, req),), scripts, repeats=4, schedule=schedule)
    path = api.simulate_scenario(obj).paths[0]
    assert [s["status"] for s in path["case_statuses"]] == ["simulated_success"] * 4
    assert path["unamortized_setup_tokens"] == 30
    assert path["repeat_shortfall_loss_tokens"] == 0
    assert path["simulated_realized_regret"] == 70

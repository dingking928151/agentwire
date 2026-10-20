"""Synthetic complete-cost tests; no repository corpus/dictionary contents."""

from contextlib import contextmanager
import hashlib
import http.client
import importlib
from itertools import product
import json
import os
import socket
import subprocess
import urllib.request

import pytest

from agentcodec.adaptive import meter
from agentcodec.adaptive.candidates import build_candidates
from agentcodec.adaptive.contracts import (
    CandidateCost, CandidatePayload, HistoricalEvent, MeasuredAttempt,
    ResourceIdentity, ResourceSnapshot, ScopeSnapshot, SelectionRequest,
)
from agentcodec.codec import FallbackRequired


@pytest.fixture
def api():
    # Missing cost behavior fails in test bodies rather than at collection.
    class Costs:
        def __getattr__(self, name):
            return getattr(importlib.import_module("agentcodec.adaptive.costs"), name)
    return Costs()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append("external I/O")
        raise AssertionError("external I/O forbidden")
    for target, name in ((socket, "socket"), (socket, "create_connection"),
                         (urllib.request, "urlopen"), (http.client.HTTPConnection, "connect"),
                         (subprocess, "Popen"), (os, "system")):
        monkeypatch.setattr(target, name, forbidden)
    yield
    assert calls == []


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


PROFILE = dict(encoding_name="cl100k_base", library_version="synthetic",
               vocabulary_sha256="0" * 64, renderer_version="adaptive-text-v1",
               accuracy="exact_local_text", mapping_basis=None)
MESSAGE = dict(message_id="synthetic", message_type="delegate", task="do not publish",
               status="pending", blocker=None, next_step="", evidence=[], constraints=[],
               approval_required=False, user_id="user", dictionary_version=None)


def resource(**changes):
    identity = ResourceIdentity(scope_id="scope", receiver_id="receiver", user_id="user",
        resource_kind="schema", version="v1", content_hash="1" * 64,
        tokenizer_profile_id=digest(PROFILE))
    return ResourceSnapshot(**(dict(identity=identity, state="cold", exposure="once_per_scope",
        epoch=0, valid_through_repeat=10, readiness_basis=None, setup_text="sync",
        context_text=None) | changes))


def request(*resources, count=3, history=(), complete=True, message=None, **changes):
    message = MESSAGE if message is None else message
    return SelectionRequest(**(dict(contract_version="adaptive-selection-v1", message=message,
        expected_user_id="user", tokenizer=PROFILE,
        capabilities=dict(receiver_id="receiver", supported_candidates=("json", "ac1", "ac2_dictionary"),
            candidate_resources={"ac1": tuple(r.identity for r in resources)}, epoch=0),
        snapshot=ScopeSnapshot(scope_id="scope", receiver_id="receiver", user_id="user", epoch=0,
            history_complete=complete, historical_events=history, resources=resources),
        policy=dict(repeat_count=count, repeat_declaration=dict(unit="same_message_instance",
            total_count=count, instance_sha256=digest(message)))) | changes))


def candidate(*resources, payload="body", candidate_id="ac1", **changes):
    return CandidatePayload(**(dict(candidate_id=candidate_id,
        declared_format=candidate_id.split("_")[0], payload=payload, eligibility="eligible",
        reason_codes=(), required_resources=tuple(r.identity for r in resources)) | changes))


def history(resource, tokens=11):
    return HistoricalEvent(event_id="historical", identity=resource.identity,
        profile_id=digest(PROFILE), tokens=tokens, kind="setup")


def counter(monkeypatch, counts=None):
    # Only local counting is replaced; real attempt construction/measurement runs.
    seen = []
    if counts is not None:
        counts = {"": 0, **counts}
    class Encoding:
        def encode_ordinary(self, text):
            seen.append(text)
            return [0] * (len(text) if counts is None else counts[text])
    @contextmanager
    def get_encoding(profile):
        yield Encoding()
    monkeypatch.setattr(meter, "_get_encoding", get_encoding)
    return seen


def test_complete_cost_boundary_and_sunk_history(api, monkeypatch):
    # Catches free cold setup, repeated setup, history in marginal rankings, or missing history as zero.
    counter(monkeypatch, {"sync": 7, "body": 4, "longer": 5})
    cold = resource()
    event = history(cold)
    req = request(cold, history=(event, event))
    priced = api.price_candidate(candidate(cold), req)
    baseline = api.price_candidate(candidate(payload="longer", candidate_id="json"), req)
    assert priced.remaining_expected_tokens == 19
    assert priced.scope_spent_tokens == 11 and priced.scope_lifecycle_expected_tokens == 30
    assert baseline.remaining_expected_tokens == 15
    assert api.compare_costs(priced, baseline) == (-4, -4 / 15)
    assert priced.new_resource_count == 1
    without_history = request(cold)
    assert api.compare_costs(api.price_candidate(candidate(cold), without_history),
        api.price_candidate(candidate(payload="longer", candidate_id="json"), without_history)) == (-4, -4 / 15)
    unknown = api.price_candidate(candidate(cold), request(cold, complete=False, history=(event,)))
    assert unknown.remaining_expected_tokens == 19
    assert unknown.scope_spent_tokens is None and unknown.scope_lifecycle_expected_tokens is None
    assert "history_incomplete" in unknown.reason_codes
    assert [(a.attempt_kind, a.total_tokens, a.multiplicity) for a in priced.attempts] == [
        ("setup", 7, 1), ("initial", 4, 3)]
    assert "deterministic_clean" in priced.reason_codes
    assert "no_model_response" in priced.reason_codes and "no_network_ack" in priced.reason_codes
    assert {"local_cpu", "output_direction"}.issubset(priced.excluded)


@pytest.mark.parametrize("exposure,state,independent,expected,setup_count,context_count", [
    ("builtin", "builtin", False, 12, 0, 0),
    ("once_per_scope", "cold", False, 16, 1, 0),
    ("once_per_scope", "ready", False, 12, 0, 0),
    ("per_request_context", "cold", False, 21, 0, 9),
    ("per_request_context", "ready", False, 21, 0, 9),
    ("per_request_context", "cold", True, 25, 1, 9),
    ("per_request_context", "ready", True, 21, 0, 9),
])
def test_exposure_charges_by_model_not_readiness(api, monkeypatch, exposure, state, independent,
                                               expected, setup_count, context_count):
    # Catches ready-as-free context and duplicate first-context setup.
    counter(monkeypatch)
    item = resource(exposure=exposure, state=state, independent_setup=independent,
                    readiness_basis="caller confirmed", context_text="ctx")
    result = api.price_candidate(candidate(item), request(item))
    assert result.remaining_expected_tokens == expected
    assert sum(a.attempt_kind == "setup" for a in result.attempts) == setup_count
    assert result.component_totals["context"] == context_count
    assert result.component_totals["retry"] == result.component_totals["fallback"] == 0


@pytest.mark.parametrize("exposure", ["once_per_scope", "per_request_context"])
def test_exposure_union_and_full_dictionary(api, monkeypatch, tmp_path, exposure):
    # Catches charging shared schema twice, selecting only matching aliases, or losing envelope/metadata/LF.
    seen = counter(monkeypatch)
    dictionary = dict(user_id="user", version="v1", aliases={"x": "do not publish", "y": "unmatched phrase"})
    full_dictionary = json.dumps(dictionary, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    schema = resource(exposure=exposure, setup_text="schema;", context_text="schema;")
    dictionary_resource = resource(identity=schema.identity.model_copy(update={
        "resource_kind": "dictionary", "content_hash": digest(dictionary)}), exposure=exposure,
        setup_text=full_dictionary, context_text=full_dictionary)
    message = MESSAGE | {"dictionary_version": "v1"}
    deps = (schema.identity, schema.identity, dictionary_resource.identity)
    req = request(schema, schema, dictionary_resource, message=message,
        dictionary=dict(enabled=True, dictionary=dictionary, content_hash=digest(dictionary)),
        allowlist=("json", "ac2_dictionary"),
        capabilities=dict(receiver_id="receiver", epoch=0,
            supported_candidates=("json", "ac2_dictionary"), candidate_resources={"ac2_dictionary": deps}),
        envelope=dict(prefix="<request>", suffix="</request>", final_lf=True))
    chosen = next(c for c in build_candidates(req) if c.candidate_id == "ac2_dictionary")
    # A local read is not a text attempt; only declared setup/context is exposed.
    local_file = tmp_path / "synthetic.txt"
    local_file.write_text("not exposed", encoding="utf-8")
    assert local_file.read_text(encoding="utf-8") == "not exposed"
    before = set(tmp_path.iterdir())
    priced = api.price_candidate(chosen, req)
    assert set(tmp_path.iterdir()) == before
    payload_request = "<request>" + chosen.payload + "</request>\n"
    exposure_text = "schema;" + full_dictionary
    expected = (len(exposure_text) + 3 * len(payload_request) if exposure == "once_per_scope"
                else 3 * len(exposure_text + payload_request))
    assert priced.remaining_expected_tokens == expected
    assert priced.new_resource_count == 2
    assert sum(a.total_tokens * a.multiplicity for a in priced.attempts) == expected
    assert "not exposed" not in seen
    assert any("unmatched phrase" in text for text in seen)
    assert payload_request in seen or exposure_text + payload_request in seen
    assert req.message.dictionary_version == "v1" and all(r.state == "cold" for r in req.snapshot.resources)


def test_break_even_strict_equality_and_reversing_advantage(api):
    # Catches equality as a win, float rounding, zero-slope mistakes, and reversed advantages extrapolated forever.
    cases = [
        ((10, 2, 0, 4), "finite", 6, (6, None)),
        ((9, 2, 0, 4), "finite", 5, (5, None)),
        ((0, 2, 0, 4), "finite", 1, (1, None)),
        ((0, 2, 1, 2), "finite", 1, (1, None)),
        ((1, 2, 1, 2), "infinity", None, None),
        ((2, 2, 1, 2), "infinity", None, None),
        ((0, 4, 10, 2), "interval", 1, (1, 4)),
        ((0, 4, 9, 2), "interval", 1, (1, 4)),
        ((0, 4, 2, 2), "infinity", None, None),
        ((10, 4, 0, 2), "infinity", None, None),
        ((10**30, 0, 0, 1), "finite", 10**30 + 1, (10**30 + 1, None)),
    ]
    for arguments, kind, first, interval in cases:
        result = api.break_even(*arguments)
        assert (result.kind, result.first_strict_win, result.winning_interval) == (kind, first, interval)
        assert "stationary_costs" in result.assumptions
        assert "resources_remain_valid" in result.assumptions


def test_break_even_matches_independent_integer_enumeration(api):
    # The oracle directly compares costs at every integer, not the implementation's inequality/floor formula.
    for sa, va, sb, vb in product(range(5), repeat=4):
        wins = [n for n in range(1, 16) if sa + n * va < sb + n * vb]
        result = api.break_even(sa, va, sb, vb)
        assert result.first_strict_win == (wins[0] if wins else None)
        if not wins:
            assert result.kind == "infinity" and result.winning_interval is None
        else:
            lower, upper = result.winning_interval
            assert [n for n in range(1, 16) if n >= lower and (upper is None or n <= upper)] == wins
            if wins[-1] < 15:
                assert result.kind == "interval" and upper == wins[-1]
            else:
                assert result.kind == "finite" and upper is None


@pytest.mark.parametrize("changes,reason", [
    ({"valid_through_repeat": 2}, "resource_validity_unknown"),
    ({"valid_through_repeat": None}, "resource_validity_unknown"),
    ({"setup_text": None}, "required_cost_unknown"),
    ({"exposure": "per_request_context", "context_text": None}, "required_cost_unknown"),
    ({"state": "ready", "readiness_basis": None}, "required_cost_unknown"),
    ({"state": "invalid"}, "resource_invalid"),
])
def test_validity_must_cover_all_n(api, monkeypatch, changes, reason):
    # Catches partial-N extrapolation and unknown exposure text/readiness converted to zero.
    def forbidden(*args):
        pytest.fail("unknown necessary resource cost must not be measured")
    monkeypatch.setattr(meter, "_get_encoding", forbidden)
    item = resource(**changes)
    priced = api.price_candidate(candidate(item), request(item))
    assert priced.remaining_expected_tokens is None and priced.scope_lifecycle_expected_tokens is None
    assert priced.scope_spent_tokens == 0
    assert priced.component_totals is None and priced.attempts == ()
    assert reason in priced.reason_codes
    be = api.break_even_for_costs(priced, priced)
    assert be.kind == "not_applicable" and be.first_strict_win is None


def test_no_double_sum_and_no_free_unknown_history(api, monkeypatch):
    # Catches summing attempts plus components, clipping signed deltas, or charging declared suffix text twice.
    seen = counter(monkeypatch, {"sync": 7, "ctx": 5, "ctx<": 3, "ctx<body": 9,
                                "ctx<bodyACK": 8, "ctx<bodyACK\n": 6})
    item = resource(exposure="per_request_context", context_text="ctx")
    req = request(item, complete=False, envelope=dict(prefix="<", suffix="ACK", final_lf=True))
    priced = api.price_candidate(candidate(item), req)
    assert priced.remaining_expected_tokens == 18
    assert priced.scope_spent_tokens is None and priced.scope_lifecycle_expected_tokens is None
    assert priced.component_totals == dict(setup=0, context=15, payload=18, retry=0, fallback=0, other=-15)
    assert sum(priced.component_totals.values()) == 18
    assert [(a.total_tokens, a.multiplicity) for a in priced.attempts] == [(6, 3)]
    assert "ACK" not in seen and "sync" not in seen


def test_unknown_measurement_and_unavailable_never_invent_savings(api, monkeypatch):
    # Catches converting a meter failure or a non-sent candidate into a zero-cost winner.
    def unavailable(profile):
        raise meter.MeasurementUnavailable("tokenizer_unavailable")
    monkeypatch.setattr(meter, "_get_encoding", unavailable)
    priced = api.price_candidate(candidate(), request())
    assert priced.remaining_expected_tokens is None and priced.component_totals is None
    assert "tokenizer_unavailable" in priced.reason_codes
    assert api.compare_costs(priced, priced) == (None, None)
    unavailable_candidate = candidate(payload=None, eligibility="unavailable", reason_codes=("candidate_not_allowed",))
    result = api.price_candidate(unavailable_candidate, request())
    assert result.remaining_expected_tokens is None and result.attempts == ()
    assert "candidate_not_allowed" in result.reason_codes


def test_compare_costs_zero_baseline_and_positive_savings(api, monkeypatch):
    # Catches wrong subtraction, integer division, null-as-zero, or division by zero.
    counter(monkeypatch)
    req = request(count=1)
    zero = api.price_candidate(candidate(payload=""), req)
    cheap = api.price_candidate(candidate(payload="xx"), req)
    expensive = api.price_candidate(candidate(payload="xxxx"), req)
    assert api.compare_costs(cheap, expensive) == (2, 0.5)
    assert api.compare_costs(expensive, cheap) == (-2, -1.0)
    assert api.compare_costs(cheap, cheap) == (0, 0.0)
    assert api.compare_costs(cheap, zero) == (-2, None)
    assert api.compare_costs(zero, zero) == (0, None)


def test_nonstationary_costs_have_no_permanent_threshold(api):
    # Catches turning different full request costs or recurring setup into a permanent linear threshold.
    def priced(attempts, remaining):
        return CandidateCost(candidate_id="ac1", remaining_expected_tokens=remaining,
            scope_spent_tokens=0, scope_lifecycle_expected_tokens=remaining, attempts=attempts,
            component_totals=None, excluded=(), reason_codes=(), new_resource_count=0)
    def measured(event, kind, total, multiplicity=1):
        return MeasuredAttempt(event_id=event, attempt_kind=kind, total_tokens=total,
            component_tokens=(total,), simulated=False, multiplicity=multiplicity)
    stationary = priced((measured("setup", "setup", 10), measured("initial", "initial", 2, 3)), 16)
    baseline = priced((measured("initial", "initial", 4, 3),), 12)
    assert api.break_even_for_costs(stationary, baseline).first_strict_win == 6
    for attempts, remaining in [
        ((measured("first", "initial", 2), measured("later", "initial", 5, 2)), 12),
        ((measured("setup", "setup", 1, 3), measured("first", "initial", 2, 3)), 9),
        ((measured("first", "initial", 2), measured("retry", "retry", 2)), 4),
    ]:
        result = api.break_even_for_costs(priced(attempts, remaining), baseline)
        assert result.kind == "not_applicable"
        assert "nonstationary_costs" in result.reason_codes


@pytest.mark.parametrize("value", [True, 1.5, "1", -1, None])
def test_break_even_rejects_non_integer_or_negative_terms(api, value):
    # Catches silent coercion/invalid costs in any coefficient.
    for position in range(4):
        terms = [1, 1, 1, 1]
        terms[position] = value
        with pytest.raises(FallbackRequired):
            api.break_even(*terms)


def test_configuration_conflicts_precede_unavailable_measurement(api, monkeypatch):
    # Catches bypassing identity/history validation just because measurement is unavailable.
    def forbidden(*args):
        pytest.fail("configuration must be rejected before measurement")
    monkeypatch.setattr(meter, "_get_encoding", forbidden)
    item = resource()
    event = history(item)
    conflict = event.model_copy(update={"tokens": 99})
    with pytest.raises(FallbackRequired):
        api.price_candidate(candidate(item), request(item, history=(event, conflict)))


def test_repeated_calls_are_pure_and_compressed(api, monkeypatch):
    # Catches payload expansion into N copies, state mutation, or nondeterministic event IDs.
    counter(monkeypatch)
    item = resource(valid_through_repeat=50_000)
    req = request(item, count=50_000)
    before = req.model_dump()
    result = api.price_candidate(candidate(item), req)
    assert result.remaining_expected_tokens == 200_004
    assert len(result.attempts) == 2 and result.attempts[-1].multiplicity == 50_000
    assert req.model_dump() == before and req.snapshot.resources[0].state == "cold"
    assert api.price_candidate(candidate(item), req) == result

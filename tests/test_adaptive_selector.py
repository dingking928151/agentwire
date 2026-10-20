"""Synthetic-only selector tests with independent cost tables and offline guards."""

from contextlib import contextmanager
import hashlib
import http.client
import importlib
from importlib.metadata import version
import json
import os
import socket
import subprocess
import urllib.request

import pytest

from agentcodec.adaptive import meter
from agentcodec.adaptive.candidates import build_candidates
from agentcodec.adaptive.contracts import (
    CANDIDATE_IDS, ResourceIdentity, ResourceSnapshot, SelectionRequest,
)
from agentcodec.benchmark import _VOCAB_HASHES


MESSAGE = dict(message_id="synthetic", message_type="delegate", task="do not publish $20 on 2026-10-21 ",
    status="pending", blocker=None, next_step="", evidence=["b", "a", "b"], constraints=[],
    approval_required=False, user_id="user", dictionary_version=None)
DICTIONARY = dict(user_id="user", version="v1", aliases={"x": MESSAGE["task"], "y": "unused phrase"})
PROFILE = dict(encoding_name="cl100k_base", library_version="synthetic",
    vocabulary_sha256="0" * 64, renderer_version="adaptive-text-v1",
    accuracy="exact_local_text", mapping_basis=None)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def request(*resources, count=1, domain=False, dependencies=None, **changes):
    message = MESSAGE | {"dictionary_version": "v1" if domain else None}
    policy = dict(repeat_count=count)
    if count > 1:
        policy["repeat_declaration"] = dict(unit="same_message_instance", total_count=count,
                                           instance_sha256=digest(message))
    return SelectionRequest(**(dict(contract_version="adaptive-selection-v1", message=message,
        expected_user_id="user", tokenizer=PROFILE, original_format="ac1",
        allowlist=CANDIDATE_IDS,
        dictionary=dict(enabled=domain, dictionary=DICTIONARY if domain else None,
                        content_hash=digest(DICTIONARY) if domain else None),
        capabilities=dict(receiver_id="receiver", epoch=0, supported_candidates=CANDIDATE_IDS,
                          candidate_resources=dependencies or {}),
        snapshot=dict(scope_id="scope", receiver_id="receiver", user_id="user", epoch=0,
                      history_complete=True, historical_events=(), resources=resources),
        policy=policy) | changes))


def resource(*, profile=PROFILE, **changes):
    identity = ResourceIdentity(scope_id="scope", receiver_id="receiver", user_id="user",
        resource_kind="schema", version="v1", content_hash="1" * 64,
        tokenizer_profile_id=digest(profile))
    return ResourceSnapshot(**(dict(identity=identity, state="cold", exposure="once_per_scope",
        epoch=0, valid_through_repeat=10, readiness_basis="caller confirmed",
        setup_text="setup;", context_text="ctx;") | changes))


@pytest.fixture
def select():
    # Missing behavior is an expected failure in each test, not a collection error.
    return lambda req: importlib.import_module("agentcodec.adaptive.selector").select_encoding(req)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append("external operation")
        raise AssertionError("external operation forbidden")
    for target, name in ((socket, "socket"), (socket, "create_connection"),
                         (urllib.request, "urlopen"), (http.client.HTTPConnection, "connect"),
                         (subprocess, "Popen"), (os, "system")):
        monkeypatch.setattr(target, name, forbidden)
    yield
    assert calls == []


def counter(monkeypatch, req, table, *, setup=7, context=3):
    # Replace only local tokenization; rendering, strict decoding, attempts and pricing stay real.
    counts = {"": 0, "setup;": setup, "ctx;": context}
    for item in build_candidates(req):
        if item.eligibility == "eligible":
            counts[item.payload] = table[item.candidate_id]
            counts[item.payload + "\n"] = table[item.candidate_id] + 1
            counts["ctx;" + item.payload] = context + table[item.candidate_id]
    class Encoding:
        def encode_ordinary(self, text):
            return [0] * counts[text]
    @contextmanager
    def encoding(profile):
        yield Encoding()
    monkeypatch.setattr(meter, "_get_encoding", encoding)


def by_id(items):
    return {item.candidate_id: item for item in items}


@pytest.mark.parametrize("domain", [False, True])
@pytest.mark.parametrize("state,exposure,setup,variable_extra", [
    ("builtin", "builtin", 0, 0), ("cold", "once_per_scope", 7, 0),
    ("ready", "once_per_scope", 0, 0), ("ready", "per_request_context", 0, 3),
])
@pytest.mark.parametrize("count", [1, 3, 10])
def test_default_minimum_matches_independent_candidate_enumeration(
        select, monkeypatch, domain, state, exposure, setup, variable_extra, count):
    # Catches payload-only ranking, omitted setup/context/N, and cross-metadata candidates.
    item = resource(state=state, exposure=exposure)
    ids = ("json", "ac1_dictionary", "ac2_dictionary") if domain else ("json", "ac1", "ac2")
    req = request(item, domain=domain, count=count,
        dependencies={key: (item.identity,) for key in ids[1:]}, original_format=ids[1])
    table = dict(zip(ids, (10, 8, 6)))
    counter(monkeypatch, req, table)
    result = select(req)
    expected = {ids[0]: 10 * count, ids[1]: setup + count * (8 + variable_extra),
                ids[2]: setup + count * (6 + variable_extra)}
    costs = by_id(result.costs)
    assert result.status == "selected" and result.comparison_status == "exact_local_text"
    assert {key: costs[key].remaining_expected_tokens for key in ids} == expected
    assert costs[result.selected_candidate].remaining_expected_tokens == min(expected.values())
    assert costs[result.selected_candidate].remaining_expected_tokens - min(expected.values()) == 0
    assert result.minimum_candidate == result.selected_candidate
    assert result.opportunity_cost_tokens == result.opportunity_cost_upper_bound == 0
    assert result.baseline_candidate == ids[1]
    assert result.savings_tokens == expected[ids[1]] - min(expected.values())
    assert result.metadata_selection == dict(selection_status="unchanged",
        source_dictionary_version="v1" if domain else None, transport_dictionary_version="v1" if domain else None)
    assert tuple(c.candidate_id for c in result.candidates) == CANDIDATE_IDS
    assert {c.candidate_id for c in result.candidates if c.eligibility == "eligible"} == set(ids)


@pytest.mark.parametrize("original,deps,want", [
    ("ac1", True, "ac1"),  # incumbent beats fewer resources
    ("ac1_dictionary", True, "ac2"),  # fewer resources beats fixed order
    ("ac1_dictionary", False, "json"),  # final fixed order
])
def test_ties_incumbent_resource_then_fixed_order(select, monkeypatch, original, deps, want):
    # Catches reordered tie priorities and counting resources rather than new resources.
    item = resource()
    req = request(item, original_format=original,
        dependencies={"json": (item.identity,), "ac1": (item.identity,)} if deps else {})
    counter(monkeypatch, req, dict(json=10, ac1=10, ac2=10), setup=0)
    result = select(req)
    assert result.selected_candidate == result.minimum_candidate == want
    assert result.policy_retained is False
    if original == "ac1_dictionary":
        assert result.baseline_candidate is None
        assert result.savings_tokens is result.savings_ratio is None


@pytest.mark.parametrize("saving,tokens,ratio,retained,bound", [
    (28, 29, 0.0, True, 29), (29, 29, 0.0, True, 29), (30, 29, 0.0, False, 0),
    (28, 0, 0.29, True, 29), (29, 0, 0.29, True, 29), (30, 0, 0.29, False, 0),
    (29, 30, 0.29, True, 30), (1, 0, 0.015, True, 1),
])
def test_explicit_threshold_requires_strictly_greater(select, monkeypatch, saving, tokens, ratio, retained, bound):
    # Catches >= switching and binary-float 0.29*100 equality drift.
    req = request(policy=dict(min_savings_tokens=tokens, min_savings_ratio=ratio))
    counter(monkeypatch, req, dict(json=100-saving, ac1=100, ac2=120))
    result = select(req)
    assert result.minimum_candidate == "json"
    assert result.selected_candidate == ("ac1" if retained else "json")
    assert result.policy_retained is retained
    assert result.opportunity_cost_tokens == (saving if retained else 0)
    assert result.opportunity_cost_upper_bound == bound
    assert result.savings_tokens == (0 if retained else saving)
    assert result.savings_ratio == (0.0 if retained else saving / 100)


def test_threshold_without_legal_incumbent_selects_minimum(select, monkeypatch):
    # Catches policy retaining an unavailable original or inventing a JSON denominator.
    req = request(original_format="ac1_dictionary", policy=dict(min_savings_tokens=1000))
    counter(monkeypatch, req, dict(json=10, ac1=8, ac2=6))
    result = select(req)
    assert result.selected_candidate == result.minimum_candidate == "ac2"
    assert result.baseline_candidate is None and result.savings_tokens is result.savings_ratio is None
    assert not result.policy_retained
    assert result.opportunity_cost_tokens == result.opportunity_cost_upper_bound == 0


@pytest.mark.parametrize("conflict", ["owner", "hash", "version", "schema"])
def test_bad_dictionary_precedes_unavailable_tokenizer_even_json(select, monkeypatch, conflict):
    # Catches measurement-first preservation masking explicit dictionary corruption.
    req = request(domain=True, original_format="json", tokenizer=PROFILE | {"encoding_name": "unknown"})
    if conflict == "owner":
        req.dictionary.dictionary.user_id = "other"
    elif conflict == "hash":
        object.__setattr__(req.dictionary, "content_hash", "wrong")
    elif conflict == "version":
        req.dictionary.dictionary.version = "v2"
    else:
        req.dictionary.dictionary.aliases["z"] = False
    def forbidden(*args):
        pytest.fail("configuration rejection must precede tokenization")
    monkeypatch.setattr(meter, "_get_encoding", forbidden)
    result = select(req)
    assert result.status == "fallback_required" and result.payload is None
    assert result.comparison_status == "cannot_compare"
    assert result.reason_codes == (("adaptive_invalid_contract",) if conflict == "schema"
                                   else ("adaptive_invalid_selection",))
    assert result.selected_candidate is result.minimum_candidate is None


@pytest.mark.parametrize("case,reason", [("unknown", "tokenizer_unavailable"),
    ("corrupt", "tokenizer_unavailable"), ("surrogate", "surrogate_only"),
    ("unreliable", "profile_unreliable"), ("cost", "required_cost_unknown")])
@pytest.mark.parametrize("legal", ["yes", "not_allowed", "not_supported", "wrong_domain", "invalid"])
def test_cannot_compare_preserves_only_legal_original(select, monkeypatch, tmp_path, case, reason, legal):
    # Catches silent omission of a necessary unknown competitor and illegal format preservation.
    profile = PROFILE | dict(library_version=version("tiktoken"), vocabulary_sha256=_VOCAB_HASHES["cl100k_base"])
    if case == "unknown":
        profile["encoding_name"] = "unknown"
    elif case == "surrogate":
        profile["accuracy"] = "surrogate_only"
    elif case == "unreliable":
        profile["accuracy"] = "unknown"
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    if case == "corrupt":
        name = hashlib.sha1(b"https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken").hexdigest()
        (tmp_path / name).write_bytes(b"synthetic corrupt cache")
    item = resource(profile=profile, setup_text=None)
    changes = dict(tokenizer=profile, original_format="json",
                   dependencies={"ac1": (item.identity,), "ac2": (item.identity,)})
    if legal == "not_allowed":
        changes["allowlist"] = ("ac1", "ac2")
    elif legal == "not_supported":
        changes["capabilities"] = dict(receiver_id="receiver", epoch=0,
            supported_candidates=("ac1", "ac2"), candidate_resources={"ac1": (item.identity,)})
    elif legal == "wrong_domain":
        changes.update(original_format="ac1_dictionary")
    elif legal == "invalid":
        invalid = resource(profile=profile, state="invalid", identity=item.identity.model_copy(update={"resource_kind": "protocol"}))
        changes["dependencies"]["json"] = (invalid.identity,)
    resources = (item, invalid) if legal == "invalid" else (item,)
    if case != "cost":
        # No resource insufficiency should hide the tokenizer reason in these cases.
        resources = tuple(r.model_copy(update={"setup_text": "setup;"}) for r in resources)
    req = request(*resources, **changes)
    if case == "cost":
        counter(monkeypatch, req, dict(json=10, ac1=8, ac2=6))
    result = select(req)
    assert result.status == ("preserved_without_comparison" if legal == "yes" else "fallback_required")
    assert result.comparison_status == ("surrogate_only" if case == "surrogate" else "cannot_compare")
    assert reason in result.reason_codes
    assert (result.payload is not None) == (legal == "yes")
    assert result.selected_candidate == ("json" if legal == "yes" else None)
    assert result.minimum_candidate is result.savings_tokens is result.savings_ratio is None
    assert result.opportunity_cost_tokens is result.opportunity_cost_upper_bound is None
    assert not result.policy_retained and result.proposed_resources == ()
    assert all(c.remaining_expected_tokens is c.scope_lifecycle_expected_tokens is c.component_totals is None
               and c.attempts == () for c in result.costs)


@pytest.mark.parametrize("bad", ["declaration", "variable", "zero", "over_limit", "messages", "workflow", "epoch"])
def test_same_instance_n_and_insufficient_epoch_coverage(select, monkeypatch, bad):
    # Catches trusting mutated instances, wrong repeat unit, and stale epoch as measurement unknown.
    req = request(count=3)
    if bad == "declaration":
        object.__setattr__(req.policy, "repeat_declaration", None)
    elif bad == "variable":
        req.message.task += "changed"
    elif bad == "zero":
        object.__setattr__(req.policy, "repeat_count", 0)
    elif bad == "over_limit":
        object.__setattr__(req.policy, "repeat_count", 1_000_001)
    elif bad == "messages":
        object.__setattr__(req, "message", [req.message])
    elif bad == "workflow":
        object.__setattr__(req, "workflow", [])
    else:
        object.__setattr__(req.capabilities, "epoch", 1)
    result = select(req)
    assert result.status == "fallback_required" and result.payload is None
    assert result.reason_codes == (("adaptive_stale_epoch",) if bad == "epoch" else ("adaptive_invalid_contract",))


@pytest.mark.parametrize("coverage", [None, 2])
def test_insufficient_repeat_coverage_is_cannot_compare(select, monkeypatch, coverage):
    # Catches extrapolating an exact subset of N while silently ignoring a necessary candidate.
    item = resource(valid_through_repeat=coverage)
    req = request(item, count=3, dependencies={"ac2": (item.identity,)})
    counter(monkeypatch, req, dict(json=10, ac1=8, ac2=6))
    result = select(req)
    assert result.status == "preserved_without_comparison" and result.selected_candidate == "ac1"
    assert result.reason_codes == ("resource_validity_unknown",)
    assert result.minimum_candidate is result.savings_tokens is None


@pytest.mark.parametrize("count,cold", [(100_001, False), (100_000, True), (1_000_000, False)])
@pytest.mark.parametrize("unknown", [False, True])
def test_expanded_event_capacity_rejects_not_preserves(select, monkeypatch, count, cold, unknown):
    # Catches broad FallbackRequired catches and unknown-resource paths masking hard capacity rejection.
    item = resource(valid_through_repeat=1_000_000, setup_text=None if unknown else "setup;")
    req = request(*(item,) if cold else (), count=count,
        dependencies={"ac2": (item.identity,)} if cold else {})
    def forbidden(*args):
        pytest.fail("over-capacity request must reject before counting any candidate")
    monkeypatch.setattr(meter, "_get_encoding", forbidden)
    result = select(req)
    assert result.status == "fallback_required" and result.payload is None
    assert result.reason_codes == ("adaptive_invalid_event",)
    assert result.minimum_candidate is result.savings_tokens is None


def test_unavailable_candidates_do_not_block_exact_selection(select, monkeypatch):
    # Catches treating a disabled candidate's unknown resource costs as a required comparison.
    item = resource(setup_text=None)
    req = request(item, allowlist=("json",), dependencies={"ac2": (item.identity,)})
    counter(monkeypatch, req, dict(json=10))
    result = select(req)
    assert result.status == "selected" and result.selected_candidate == "json"
    assert result.baseline_candidate is None and result.savings_tokens is None


def test_no_send_retry_fallback_or_state_commit(select, monkeypatch):
    # Catches execution, implicit recovery, mutable proposals and hidden state/time in decisions.
    item = resource()
    req = request(item, domain=True, original_format="json", dependencies={"ac2_dictionary": (item.identity,)})
    counter(monkeypatch, req, dict(json=100, ac1_dictionary=80, ac2_dictionary=10))
    before = req.model_dump_json()
    def forbidden(*args, **kwargs):
        pytest.fail("resource confirmation or business execution is not selector work")
    from agentcodec.adaptive import resources
    monkeypatch.setattr(resources, "confirm_initialization", forbidden)
    first = select(req)
    second = select(req)
    assert first.status == "selected" and first.selected_candidate == "ac2_dictionary"
    assert first.model_dump_json() == second.model_dump_json()
    assert req.model_dump_json() == before
    assert first.proposed_resources == (item,) and first.proposed_resources[0] is not item
    assert first.proposed_resources[0].state == req.snapshot.resources[0].state == "cold"
    assert "initialization_proposed_not_ready" in first.assumptions
    assert all(c.component_totals["retry"] == c.component_totals["fallback"] == 0
               for c in first.costs if c.component_totals is not None)
    assert all(a.attempt_kind in ("setup", "initial") and not a.simulated
               for c in first.costs for a in c.attempts)
    assert first.decision_id == digest(req.model_dump())
    assert first.profile_id == digest(PROFILE)
    identity_request = request()
    counter(monkeypatch, identity_request, dict(json=100, ac1=80, ac2=10))
    identity_decision = select(identity_request)
    for changes in ({"message": MESSAGE | {"task": "different"}},
                    {"policy": dict(min_savings_tokens=1)},
                    {"tokenizer": PROFILE | {"mapping_basis": "new basis"}},
                    {"snapshot": identity_request.snapshot.model_dump() | {"history_complete": False}},
                    {"envelope": dict(final_lf=True)},
                    {"allowlist": ("json", "ac1")},
                    {"original_format": "json"}):
        changed = SelectionRequest(**(identity_request.model_dump() | changes))
        counter(monkeypatch, changed, dict(json=100, ac1=80, ac2=10))
        result = select(changed)
        assert result.status == "selected"
        assert result.decision_id == digest(changed.model_dump())
        assert result.decision_id != identity_decision.decision_id


def test_break_even_is_strict_and_coverage_bounded(select, monkeypatch):
    # Catches break-even using only payload or promising unbounded resource validity.
    item = resource()
    req = request(item, count=3, original_format="json", dependencies={"ac2": (item.identity,)})
    counter(monkeypatch, req, dict(json=10, ac1=20, ac2=6), setup=8)
    result = select(req)
    assert result.selected_candidate == "ac2"
    be = result.breakeven["ac2"]
    assert be.first_strict_win == 3 and be.winning_interval == (3, 10)
    assert "resources_remain_valid" in be.assumptions


def test_no_eligible_candidate_rejects_without_format_fallback(select):
    # Catches inventing an automatic JSON fallback outside the allowlist.
    result = select(request(allowlist=("ac1_dictionary",)))
    assert result.status == "fallback_required" and result.payload is None
    assert "no_eligible_candidate" in result.reason_codes


def test_lazy_package_entry_point(select, monkeypatch):
    # Catches missing public entry or eager initialization on package import/reload.
    # Resolve sys.modules rather than a parent attribute left stale by a fresh-import guard.
    package = importlib.import_module("agentcodec.adaptive")
    def forbidden(*args):
        pytest.fail("import must not initialize a tokenizer")
    monkeypatch.setattr(meter, "_get_encoding", forbidden)
    importlib.reload(package)
    entry = package.select_encoding
    req = request()
    counter(monkeypatch, req, dict(json=10, ac1=8, ac2=6))
    assert entry(req) == select(req)

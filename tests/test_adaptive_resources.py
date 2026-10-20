"""Synthetic resource tests; no repository corpus or dictionary is inspected."""

import hashlib
import importlib
import json

import pytest

from agentcodec.adaptive.contracts import (
    CandidatePayload, HistoricalEvent, ResourceIdentity, ResourceSnapshot,
    ScopeSnapshot, SelectionRequest,
)
from agentcodec.codec import FallbackRequired


@pytest.fixture
def api():
    # Keep a missing feature failure inside tests, not in collection.
    class Resources:
        def __getattr__(self, name):
            return getattr(importlib.import_module("agentcodec.adaptive.resources"), name)

    return Resources()


def digest(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


PROFILE = dict(encoding_name="cl100k_base", library_version="synthetic",
               vocabulary_sha256="0" * 64, renderer_version="adaptive-text-v1",
               accuracy="exact_local_text", mapping_basis=None)
PROFILE_ID = digest(PROFILE)
MESSAGE = dict(message_id="synthetic", message_type="delegate", task="do not publish",
               status="pending", blocker=None, next_step="", evidence=[], constraints=[],
               approval_required=False, user_id="user", dictionary_version=None)


def identity(**changes):
    return ResourceIdentity(**(dict(scope_id="scope", receiver_id="receiver", user_id="user",
        resource_kind="schema", version="v1", content_hash="1" * 64,
        tokenizer_profile_id=PROFILE_ID) | changes))


def resource(**changes):
    return ResourceSnapshot(**(dict(identity=identity(), state="cold", exposure="once_per_scope",
        epoch=0, valid_through_repeat=3, readiness_basis=None,
        setup_text="synthetic full schema", context_text=None) | changes))


def event(**changes):
    return HistoricalEvent(**(dict(event_id="setup-1", identity=identity(), profile_id=PROFILE_ID,
                                   tokens=7, kind="setup") | changes))


def snapshot(**changes):
    return ScopeSnapshot(**(dict(scope_id="scope", receiver_id="receiver", user_id="user", epoch=0,
        history_complete=True, historical_events=(), resources=(resource(),)) | changes))


def request(*, snap=None, dependencies=None, count=1, **changes):
    policy = {} if count == 1 else dict(repeat_count=count, repeat_declaration=dict(
        unit="same_message_instance", total_count=count, instance_sha256=digest(MESSAGE)))
    capabilities = dict(receiver_id="receiver", supported_candidates=("json", "ac1"), epoch=0,
                        candidate_resources={"ac1": (identity(),) if dependencies is None else dependencies})
    return SelectionRequest(**(dict(contract_version="adaptive-selection-v1", message=MESSAGE,
        expected_user_id="user", tokenizer=PROFILE, capabilities=capabilities,
        snapshot=snapshot() if snap is None else snap, policy=policy) | changes))


def candidate(*dependencies):
    return CandidatePayload(candidate_id="ac1", declared_format="ac1", payload="synthetic",
        eligibility="eligible", reason_codes=(), required_resources=dependencies)


def test_selection_proposal_never_mutates_ready(api):
    # Catches promoting cold resources merely by selection/proposal.
    original = snapshot()
    validated = api.validate_snapshot(request(snap=original))
    needed = api.required_resources(candidate(identity(), identity()), validated)
    proposed = api.propose_resources(needed)
    assert len(proposed) == 1
    assert proposed[0].state == "cold"
    assert proposed[0].readiness_basis is None
    assert original.resources[0].state == "cold" and original.epoch == 0
    assert validated is not original and proposed[0] is not needed[0]


def test_proposal_only_contains_resources_needing_initialization(api):
    cold = resource()
    ready = resource(identity=identity(resource_kind="protocol"), state="ready", readiness_basis="imported")
    builtin = resource(identity=identity(resource_kind="decoder"), state="builtin", exposure="builtin",
                       readiness_basis="caller built in")
    invalid = resource(identity=identity(resource_kind="dictionary"), state="invalid")
    assert api.propose_resources((ready, cold, builtin, invalid, cold)) == (cold,)


def test_invalid_builtin_is_unavailable_not_an_identity_conflict(api):
    # Invalid resources are never initialized or priced, regardless of exposure.
    invalid = resource(state="invalid", exposure="builtin")
    validated = api.validate_snapshot(request(snap=snapshot(resources=(invalid,))))
    assert validated.resources[0].state == "invalid"
    assert api.resource_cost_reason(invalid, repeat_count=1) == "resource_invalid"
    assert api.propose_resources((invalid,)) == ()


def test_confirm_requires_epoch_and_success(api):
    # Catches implicit initialization, lost updates, or mutation of an old snapshot.
    original = snapshot()
    failed = api.confirm_initialization(original, expected_epoch=0, event=event(), succeeded=False)
    assert failed == original and failed is not original
    confirmed = api.confirm_initialization(original, expected_epoch=0, event=event(), succeeded=True)
    assert confirmed.epoch == 1
    assert confirmed.resources[0].state == "ready"
    assert confirmed.resources[0].epoch == 1
    assert confirmed.resources[0].readiness_basis
    assert confirmed.historical_events == (event(),)
    assert original.resources[0].state == "cold" and original.historical_events == ()
    with pytest.raises(FallbackRequired):
        api.confirm_initialization(confirmed, expected_epoch=0, event=event(), succeeded=True)
    repeated = api.confirm_initialization(confirmed, expected_epoch=1, event=event(), succeeded=True)
    assert repeated == confirmed and repeated.epoch == 1


@pytest.mark.parametrize("field,value", [
    ("scope_id", "other-scope"), ("receiver_id", "other-receiver"), ("user_id", "other-user"),
    ("tokenizer_profile_id", "other-profile"), ("version", "v2"), ("content_hash", "2" * 64),
])
def test_resource_identity_rejects_conflicts(api, field, value):
    # Every identity coordinate must match the requested dependency exactly.
    bad = identity(**{field: value})
    with pytest.raises(FallbackRequired):
        api.validate_snapshot(request(snap=snapshot(resources=(resource(identity=bad),))))
    with pytest.raises(FallbackRequired):
        api.required_resources(candidate(bad), snapshot())


@pytest.mark.parametrize("changes", [
    {"scope_id": "other"}, {"receiver_id": "other"}, {"user_id": "other"}, {"epoch": 1},
])
def test_snapshot_binding_rejects_conflicts(api, changes):
    with pytest.raises(FallbackRequired):
        api.validate_snapshot(request(snap=snapshot(**changes)))


def test_capability_epoch_and_receiver_conflicts(api):
    for changes in ({"epoch": 1}, {"receiver_id": "other"}):
        capabilities = dict(receiver_id="receiver", supported_candidates=("ac1",),
                            candidate_resources={"ac1": (identity(),)}, epoch=0) | changes
        with pytest.raises(FallbackRequired):
            api.validate_snapshot(request(capabilities=capabilities))
    with pytest.raises(FallbackRequired):
        api.validate_snapshot(request(snap=snapshot(resources=(resource(epoch=1),))))


def test_same_version_different_hash_is_rejected_even_if_unused(api):
    conflicting = resource(identity=identity(content_hash="2" * 64))
    with pytest.raises(FallbackRequired):
        api.validate_snapshot(request(snap=snapshot(resources=(resource(), conflicting))))
    with pytest.raises(FallbackRequired):
        api.propose_resources((resource(), conflicting))
    with pytest.raises(FallbackRequired):
        api.validate_snapshot(request(snap=snapshot(historical_events=(
            event(identity=identity(content_hash="2" * 64)),))))


def test_resource_union_deduplicates_but_never_hides_conflicting_snapshots(api):
    other = resource(identity=identity(resource_kind="protocol"))
    snap = snapshot(resources=(resource(), other, resource()))
    validated = api.validate_snapshot(request(snap=snap))
    assert len(validated.resources) == 2
    needed = api.required_resources(candidate(identity(), other.identity, identity()), validated)
    assert tuple(item.identity.resource_kind for item in needed) == ("schema", "protocol")
    for conflicting in (resource(state="invalid"), resource(setup_text="different")):
        with pytest.raises(FallbackRequired):
            api.required_resources(candidate(identity()), snapshot(resources=(resource(), conflicting)))
    assert api.required_resources(candidate(), validated) == ()


def test_history_dedup_conflict_and_incomplete(api):
    # Deduplication is by ID plus full content, not ID alone or text alone.
    ready = resource(state="ready", readiness_basis="caller-confirmed import")
    first, second = event(), event(event_id="setup-2", tokens=11)
    snap = snapshot(resources=(ready,), historical_events=(first, first, second))
    validated = api.validate_snapshot(request(snap=snap))
    assert tuple(item.tokens for item in validated.historical_events) == (7, 11)
    assert sum(item.tokens for item in validated.historical_events) == 18
    assert len(snap.historical_events) == 3
    for changed in (event(tokens=8), event(kind="other"), event(identity=identity(version="v2"))):
        with pytest.raises(FallbackRequired):
            api.validate_snapshot(request(snap=snapshot(historical_events=(first, changed))))
    incomplete = api.validate_snapshot(request(snap=snapshot(resources=(ready,), history_complete=False)))
    assert incomplete.history_complete is False and incomplete.historical_events == ()
    assert api.resource_cost_reason(incomplete.resources[0], repeat_count=1) is None
    unconfirmed = resource(state="ready", readiness_basis=None)
    assert api.resource_cost_reason(unconfirmed, repeat_count=1) == "required_cost_unknown"


@pytest.mark.parametrize("changes", [
    {"scope_id": "other-scope"}, {"receiver_id": "other-receiver"}, {"user_id": "other-user"},
    {"tokenizer_profile_id": "other-profile"},
])
def test_foreign_history_is_rejected_not_charged_to_scope(api, changes):
    foreign = event(identity=identity(**changes))
    with pytest.raises(FallbackRequired):
        api.validate_snapshot(request(snap=snapshot(historical_events=(foreign,))))
    assert api.validate_snapshot(request()).historical_events == ()


def test_history_profile_and_hash_identity_are_checked(api):
    with pytest.raises(FallbackRequired):
        api.validate_snapshot(request(snap=snapshot(historical_events=(event(profile_id="other"),))))
    # Earlier versions in this scope are legitimate sunk history, not current resources.
    old = event(identity=identity(version="v0"))
    assert api.validate_snapshot(request(snap=snapshot(historical_events=(old,)))).historical_events == (old,)


def test_ready_per_request_context_still_requires_exposure(api):
    ready = resource(state="ready", exposure="per_request_context", readiness_basis="caller-confirmed",
                     context_text="complete synthetic context")
    assert api.resource_cost_reason(ready, repeat_count=3) is None
    assert api.resource_cost_reason(ready.model_copy(update={"context_text": None}), repeat_count=1) == "required_cost_unknown"
    assert api.resource_cost_reason(ready.model_copy(update={"context_text": ""}), repeat_count=1) is None
    # A separate initialization request needs its own text, unlike ordinary first context.
    cold = resource(exposure="per_request_context", context_text="full context", setup_text=None)
    assert api.resource_cost_reason(cold, repeat_count=1) is None
    assert api.resource_cost_reason(cold.model_copy(update={"independent_setup": True}), repeat_count=1) == "required_cost_unknown"


@pytest.mark.parametrize("coverage", [None, 0, 1, 2])
def test_repeat_validity_unknown_is_not_free_setup(api, coverage):
    for state in ("cold", "ready", "builtin"):
        item = resource(state=state, exposure="builtin" if state == "builtin" else "once_per_scope",
                        readiness_basis="caller-confirmed", valid_through_repeat=coverage)
        assert api.resource_cost_reason(item, repeat_count=3) == "resource_validity_unknown"
    # Unknown measurement conditions must not masquerade as explicit identity errors.
    validated = api.validate_snapshot(request(count=3, snap=snapshot(resources=(resource(valid_through_repeat=coverage),))))
    assert validated.resources[0].valid_through_repeat == coverage


def test_builtin_requires_callers_basis_and_no_text_setup(api):
    builtin = resource(state="builtin", exposure="builtin", setup_text=None, readiness_basis="caller decoder built in")
    assert api.resource_cost_reason(builtin, repeat_count=1) is None
    assert api.resource_cost_reason(builtin.model_copy(update={"readiness_basis": None}), repeat_count=1) == "required_cost_unknown"
    for item in (resource(state="builtin"), resource(exposure="builtin")):
        with pytest.raises(FallbackRequired):
            api.validate_snapshot(request(snap=snapshot(resources=(item,))))
    cold = resource(setup_text=None)
    assert api.resource_cost_reason(cold, repeat_count=1) == "required_cost_unknown"


@pytest.mark.parametrize("epoch", [True, False, 0.0, "0", -1])
def test_confirmation_rejects_noninteger_epoch(api, epoch):
    with pytest.raises(FallbackRequired):
        api.confirm_initialization(snapshot(), expected_epoch=epoch, event=event(), succeeded=True)


@pytest.mark.parametrize("success", [None, 0, 1, "true"])
def test_confirmation_requires_explicit_boolean(api, success):
    with pytest.raises(FallbackRequired):
        api.confirm_initialization(snapshot(), expected_epoch=0, event=event(), succeeded=success)


def test_confirmation_validates_event_and_preserves_other_resources(api):
    other = resource(identity=identity(resource_kind="protocol"), state="ready", readiness_basis="imported")
    original = snapshot(resources=(resource(), other))
    confirmed = api.confirm_initialization(original, expected_epoch=0, event=event(), succeeded=True)
    assert confirmed.resources[1].state == "ready" and confirmed.resources[1].epoch == 1
    assert confirmed.resources[1].readiness_basis == "imported"
    assert confirmed.resources[0].valid_through_repeat == 3
    # It is usable with the next epoch, not an internally inconsistent result.
    capabilities = dict(receiver_id="receiver", supported_candidates=("ac1",),
                        candidate_resources={"ac1": (identity(),)}, epoch=1)
    assert api.validate_snapshot(request(snap=confirmed, capabilities=capabilities)).epoch == 1
    for bad in (event(kind="payload"), event(profile_id="other"), event(identity=identity(version="v2"))):
        with pytest.raises(FallbackRequired):
            api.confirm_initialization(original, expected_epoch=0, event=bad, succeeded=True)
    with pytest.raises(FallbackRequired):
        api.confirm_initialization(snapshot(resources=(resource(state="invalid"),)),
                                   expected_epoch=0, event=event(), succeeded=True)
    with pytest.raises(FallbackRequired):
        api.confirm_initialization(snapshot(historical_events=(event(tokens=9),)),
                                   expected_epoch=0, event=event(), succeeded=True)


def test_mutated_models_are_revalidated_without_leaking_values(api):
    item = resource()
    object.__setattr__(item, "epoch", "private marker")
    for operation in (lambda: api.propose_resources((item,)),
                      lambda: api.resource_cost_reason(item, repeat_count=1)):
        with pytest.raises(FallbackRequired) as error:
            operation()
        assert "private marker" not in str(error.value)
    req = request()
    req.capabilities.candidate_resources["ac1"] = (identity(user_id="other"),)
    with pytest.raises(FallbackRequired):
        api.validate_snapshot(req)


def test_dictionary_resources_bind_explicit_version_and_full_hash(api):
    dictionary = dict(user_id="user", version="v1", aliases={"a": "synthetic full phrase"})
    selected_hash = digest(dictionary)
    ident = identity(resource_kind="dictionary", content_hash=selected_hash)
    snap = snapshot(resources=(resource(identity=ident),))
    message = MESSAGE | {"dictionary_version": "v1"}
    req = request(snap=snap, dependencies=(ident,), message=message,
                  dictionary=dict(enabled=True, dictionary=dictionary, content_hash=selected_hash))
    assert api.validate_snapshot(req).resources[0].identity == ident
    for bad in (ident.model_copy(update={"version": "v2"}),
                ident.model_copy(update={"content_hash": "wrong"})):
        snap = snapshot(resources=(resource(identity=bad),))
        with pytest.raises(FallbackRequired):
            api.validate_snapshot(request(snap=snap, dependencies=(bad,), message=message,
                dictionary=dict(enabled=True, dictionary=dictionary, content_hash=selected_hash)))

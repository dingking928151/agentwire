"""Synthetic-only boundary tests: each assertion protects a public contract."""

import hashlib
import importlib
import json
import sys

import pytest

from agentcodec.codec import FallbackRequired
from agentcodec.schema import MessageV1, UserDictionary


IDS = ("json", "ac1", "ac2", "ac1_dictionary", "ac2_dictionary")
KINDS = ("setup", "context", "payload", "retry", "fallback", "other")


@pytest.fixture
def c():
    # Missing implementation must fail inside a test, not break collection.
    class Contracts:
        def __getattr__(self, name):
            return getattr(importlib.import_module("agentcodec.adaptive.contracts"), name)

    return Contracts()


def message_values(**changes):
    return dict(message_id="synthetic-1", message_type="delegate", task="检查，不发布 ",
                status="pending", blocker=None, next_step="", evidence=["b", "a", "b"],
                constraints=[], approval_required=False, user_id="test-user",
                dictionary_version=None) | changes


def digest(value):
    # Independent standard-library oracle, not a helper from production.
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def profile_values(**changes):
    return dict(encoding_name="cl100k_base", library_version="synthetic-version",
                vocabulary_sha256="0" * 64, accuracy="exact_local_text", mapping_basis=None) | changes


def identity_values(**changes):
    return dict(scope_id="scope", receiver_id="receiver", user_id="test-user", resource_kind="schema",
                version="v1", content_hash="1" * 64, tokenizer_profile_id="2" * 64) | changes


def resource_values(**changes):
    return dict(identity=identity_values(), state="cold", exposure="once_per_scope", epoch=0,
                valid_through_repeat=None, readiness_basis=None, setup_text="", context_text=None) | changes


def snapshot_values(**changes):
    return dict(scope_id="scope", receiver_id="receiver", user_id="test-user", epoch=0,
                history_complete=True, historical_events=(), resources=()) | changes


def capabilities_values(**changes):
    return dict(receiver_id="receiver", supported_candidates=("json", "ac1"),
                candidate_resources={"json": (), "ac1": ()}, epoch=0) | changes


def request_values(**changes):
    return dict(contract_version="adaptive-selection-v1", message=message_values(),
                expected_user_id="test-user", tokenizer=profile_values(),
                capabilities=capabilities_values(), snapshot=snapshot_values()) | changes


def candidate_values(candidate_id="json", **changes):
    return dict(candidate_id=candidate_id, declared_format=candidate_id.split("_")[0], payload=None,
                eligibility="unavailable", reason_codes=("not_enabled",), required_resources=()) | changes


def cost_values(candidate_id="json", **changes):
    return dict(candidate_id=candidate_id, remaining_expected_tokens=None, scope_spent_tokens=None,
                scope_lifecycle_expected_tokens=None, attempts=(), component_totals=None,
                excluded=(), reason_codes=("required_cost_unknown",), new_resource_count=0) | changes


def decision_values(**changes):
    return dict(contract_version="adaptive-selection-v1", status="fallback_required",
                comparison_status="cannot_compare", reason_codes=("invalid_contract",),
                selected_candidate=None, declared_format=None, payload=None,
                candidates=tuple(candidate_values(i) for i in IDS),
                costs=tuple(cost_values(i) for i in IDS), minimum_candidate=None, policy_retained=False,
                baseline_candidate=None, savings_tokens=None, savings_ratio=None,
                opportunity_cost_tokens=None, opportunity_cost_upper_bound=None, breakeven={},
                metadata_selection=dict(selection_status="unchanged", source_dictionary_version=None,
                                        transport_dictionary_version=None),
                proposed_resources=(), snapshot_epoch=0, decision_id="3" * 64, profile_id="2" * 64,
                policy_version="adaptive-policy-v1", renderer_version="adaptive-text-v1",
                assumptions=(), excluded=()) | changes


def examples():
    return {
        "TokenizerProfile": profile_values(), "ResourceIdentity": identity_values(),
        "ResourceSnapshot": resource_values(),
        "HistoricalEvent": dict(event_id="event", identity=identity_values(), profile_id="2" * 64,
                                tokens=0, kind="setup"),
        "ScopeSnapshot": snapshot_values(), "DictionarySelection": {},
        "ReceiverCapabilities": capabilities_values(),
        "RepeatDeclaration": dict(unit="same_message_instance", total_count=1,
                                  instance_sha256=digest(message_values())),
        "CostPolicy": {}, "TextEnvelope": {}, "SelectionRequest": request_values(),
        "CandidatePayload": candidate_values(),
        "TextComponent": dict(kind="payload", text="", resource_identity=None),
        "TextAttempt": dict(event_id="event", attempt_kind="initial", components=(), simulated=False),
        "MeasuredAttempt": dict(event_id="event", attempt_kind="initial", total_tokens=1,
                                component_tokens=(-1, 2), simulated=False),
        "CandidateCost": cost_values(),
        "BreakEvenResult": dict(kind="infinity", first_strict_win=None, winning_interval=None,
                                assumptions=(), reason_codes=()),
        "SelectionDecision": decision_values(),
    }


def test_defaults_are_opt_in_and_record_only(c):
    request = c.SelectionRequest(**request_values())
    assert request.allowlist == ("json", "ac1")
    assert request.original_format == "ac1"
    assert request.dictionary.model_dump() == dict(enabled=False, dictionary=None, content_hash=None)
    assert request.policy.model_dump() == dict(repeat_count=1, repeat_declaration=None,
        min_savings_tokens=0, min_savings_ratio=0.0, fallback_mode="record_only", max_retries=0,
        assumption="deterministic_clean")
    assert request.envelope.model_dump() == dict(prefix="", suffix="", final_lf=False)
    assert request.tokenizer.renderer_version == "adaptive-text-v1"
    assert request.message.blocker is None and request.message.next_step == ""
    assert request.message.approval_required is False and request.message.constraints == []
    assert c.ResourceSnapshot(**resource_values()).independent_setup is False


def test_repeat_declaration_pins_full_instance(c):
    declaration = dict(unit="same_message_instance", total_count=2,
                       instance_sha256=digest(message_values()))
    policy = dict(repeat_count=2, repeat_declaration=declaration)
    assert c.SelectionRequest(**request_values(policy=policy)).policy.repeat_count == 2
    for changed in (message_values(message_id="synthetic-2"), message_values(task="检查，不发布"),
                    message_values(evidence=["a", "b", "b"]), message_values(blocker=""),
                    message_values(approval_required=True), message_values(dictionary_version="v1")):
        with pytest.raises(FallbackRequired):
            c.SelectionRequest(**request_values(message=changed, policy=policy))
    for bad in (dict(repeat_count=2), dict(repeat_count=1, repeat_declaration=declaration),
                dict(repeat_count=1, repeat_declaration=declaration | {
                    "total_count": 1, "instance_sha256": "f" * 64})):
        with pytest.raises(FallbackRequired):
            c.SelectionRequest(**request_values(policy=bad))
    maximum = dict(repeat_count=1_000_000, repeat_declaration=declaration | {"total_count": 1_000_000})
    assert c.SelectionRequest(**request_values(policy=maximum)).policy.repeat_count == 1_000_000


@pytest.mark.parametrize("count", [True, False, 1.0, "1", 0, -1, 1_000_001])
def test_reject_coercion_multi_message_and_limits(c, count):
    with pytest.raises(FallbackRequired) as error:
        c.SelectionRequest(**request_values(policy={"repeat_count": count}))
    assert error.value.code == "adaptive_invalid_contract"
    assert str(error.value) == "adaptive contract rejected"


def test_reject_multi_message_extra_fields_and_duplicate_candidates(c):
    for changes in ({"message": [message_values()]}, {"messages": [message_values()]},
                    {"workflow": []}, {"global_optimization": True}, {"future_reuses": 1},
                    {"allowlist": ()}, {"allowlist": ("json", "json")},
                    {"allowlist": ["json", "ac1"]}, {"allowlist": ("auto",)},
                    {"tokenizer": profile_values(renderer_version="v2")},
                    {"policy": {"max_retries": False}}, {"policy": {"max_retries": 0.0}},
                    {"policy": {"max_retries": 1}}, {"policy": {"fallback_mode": "local_json"}},
                    {"policy": {"min_savings_tokens": True}}, {"policy": {"min_savings_tokens": -1}},
                    {"policy": {"min_savings_ratio": float("nan")}},
                    {"policy": {"min_savings_ratio": float("inf")}},
                    {"policy": {"min_savings_ratio": 1.01}},
                    {"policy": {"min_savings_ratio": -0.01}}):
        with pytest.raises(FallbackRequired):
            c.SelectionRequest(**request_values(**changes))


@pytest.mark.parametrize("name,data", list(examples().items()))
def test_required_fields_and_unknown_fields(c, name, data):
    model = getattr(c, name)
    instance = model(**data)
    for field, definition in model.model_fields.items():
        if definition.is_required():
            incomplete = instance.model_dump()
            del incomplete[field]
            with pytest.raises(FallbackRequired):
                model(**incomplete)
    with pytest.raises(FallbackRequired):
        model(**(data | {"unknown": False}))


def test_every_message_field_is_required(c):
    for field in message_values():
        incomplete = message_values()
        del incomplete[field]
        with pytest.raises(FallbackRequired):
            c.SelectionRequest(**request_values(message=incomplete))


def test_mutated_models_are_revalidated_and_frozen(c):
    message = MessageV1(**message_values(dictionary_version="v1"))
    dictionary = UserDictionary(user_id="test-user", version="v1", aliases={"code": "unused phrase"})
    selection = c.DictionarySelection(enabled=True, dictionary=dictionary,
                                      content_hash=digest(dictionary.model_dump()))
    request = c.SelectionRequest(**request_values(message=message, dictionary=selection))
    frozen = c.freeze_request(request)
    message.evidence.append("changed")
    dictionary.aliases["extra"] = "extra phrase"
    request.message.constraints.append("changed after request")
    request.dictionary.dictionary.aliases["later"] = "later phrase"
    assert frozen.message.evidence == ["b", "a", "b"]
    assert frozen.message.constraints == []
    assert frozen.dictionary.dictionary.aliases == {"code": "unused phrase"}
    assert frozen.message is not request.message
    request.message.approval_required = "false"
    with pytest.raises(FallbackRequired):
        c.freeze_request(request)
    with pytest.raises(FallbackRequired):
        c.SelectionRequest.model_validate(request)
    broken = MessageV1(**message_values())
    broken.evidence.append(False)
    with pytest.raises(FallbackRequired):
        c.SelectionRequest(**request_values(message=broken))
    bad_dictionary = UserDictionary(user_id="test-user", version="v1", aliases={"a": "one"})
    bad_dictionary.aliases["b"] = "one"
    with pytest.raises(FallbackRequired):
        c.DictionarySelection(enabled=True, dictionary=bad_dictionary)


def test_canonical_identities_preserve_exact_unicode_and_all_fields(c):
    assert c.canonical_text({"z": False, "a": "检查"}) == '{"a":"检查","z":false}'
    assert c.canonical_text([None, "", False, [], " e\u0301\n "]) == '[null,"",false,[]," é\\n "]'
    message = MessageV1(**message_values())
    assert c.instance_id(message) == digest(message_values())
    profile = c.TokenizerProfile(**profile_values())
    assert c.profile_id(profile) == digest(profile_values(renderer_version="adaptive-text-v1"))
    assert c.instance_id(MessageV1(**message_values(task="检查，不发布"))) != c.instance_id(message)
    for bad in ({1: "key"}, "\ud800", {"x": ["\udfff"]}, float("nan"), float("inf")):
        with pytest.raises(FallbackRequired):
            c.canonical_text(bad)
    message.approval_required = 0
    with pytest.raises(FallbackRequired):
        c.instance_id(message)


def test_capacity_limits_reject_not_truncate(c):
    limit = 8 * 1024 * 1024
    assert len(c.TextComponent(kind="context", text="a" * limit, resource_identity=None).text) == limit
    for text in ("a" * (limit + 1), "界" * (limit // 3 + 1), "\ud800"):
        with pytest.raises(FallbackRequired):
            c.TextComponent(kind="context", text=text, resource_identity=None)
    with pytest.raises(FallbackRequired):
        c.SelectionRequest(**request_values(message=message_values(task="a" * limit)))
    event = c.HistoricalEvent(event_id="e", identity=identity_values(), profile_id="2" * 64,
                              tokens=0, kind="setup")
    assert len(c.ScopeSnapshot(**snapshot_values(historical_events=(event,) * 100_000)).historical_events) <= 100_000
    with pytest.raises(FallbackRequired):
        c.ScopeSnapshot(**snapshot_values(historical_events=(event,) * 100_001))
    for multiplicity in (0, -1, False, 1.0, 1_000_001):
        with pytest.raises(FallbackRequired):
            c.TextAttempt(event_id="e", attempt_kind="initial", components=(), simulated=False,
                          multiplicity=multiplicity)


def test_decision_invariants_and_json_safe_infinity(c):
    values = examples()["BreakEvenResult"]
    result = c.BreakEvenResult(**values)
    assert json.loads(result.model_dump_json())["kind"] == "infinity"
    assert "Infinity" not in result.model_dump_json()
    assert c.SelectionDecision(**decision_values()).payload is None
    for changes in ({"payload": "partial"}, {"selected_candidate": "json"},
                    {"declared_format": "json"}, {"savings_tokens": 0}, {"savings_ratio": 0.0},
                    {"opportunity_cost_tokens": 0}, {"minimum_candidate": "json"},
                    {"savings_ratio": float("nan")}, {"unknown": None},
                    {"candidates": ()},
                    {"metadata_selection": {"selection_status": "unchanged", "source_dictionary_version": None,
                                             "transport_dictionary_version": "v1"}},
                    {"metadata_selection": {"selection_status": "unchanged", "source_dictionary_version": None,
                                             "transport_dictionary_version": None, "extra": None}}):
        with pytest.raises(FallbackRequired):
            c.SelectionDecision(**decision_values(**changes))
    with pytest.raises(FallbackRequired):
        c.BreakEvenResult(**(values | {"first_strict_win": float("inf")}))
    with pytest.raises(FallbackRequired):
        c.BreakEvenResult(**(values | {"first_strict_win": 1}))


def test_attempt_and_component_contracts_allow_signed_attribution_not_negative_total(c):
    attempt = c.MeasuredAttempt(event_id="e", attempt_kind="initial", total_tokens=1,
                                component_tokens=(-2, 3), simulated=False, multiplicity=2)
    assert attempt.component_tokens == (-2, 3) and attempt.total_tokens == 1 and attempt.multiplicity == 2
    for changes in ({"total_tokens": -1}, {"total_tokens": False}, {"component_tokens": (-2, 2)},
                    {"component_tokens": (True, 0)}):
        with pytest.raises(FallbackRequired):
            c.MeasuredAttempt(**(attempt.model_dump() | changes))
    totals = dict.fromkeys(KINDS, 0) | {"payload": 2}
    assert c.CandidateCost(**cost_values(remaining_expected_tokens=2, scope_spent_tokens=0,
        scope_lifecycle_expected_tokens=2, component_totals=totals)).component_totals["retry"] == 0
    for invalid in (totals | {"extra": 0}, {"payload": 2}, totals | {"retry": 1}):
        with pytest.raises(FallbackRequired):
            c.CandidateCost(**cost_values(component_totals=invalid))


def test_import_is_inert(monkeypatch):
    # Real fresh imports with sentinels at the external side-effect boundaries.
    import os
    import socket
    import subprocess
    import tiktoken
    from collections.abc import MutableMapping

    def forbidden(*args, **kwargs):
        raise AssertionError("import attempted an external side effect")

    def guarded_getenv(key, default=None):
        if key == "PYDANTIC_DISABLE_PLUGINS":
            return "__all__"
        forbidden()

    class NoCredentials(MutableMapping):
        def __getitem__(self, key):
            forbidden()
        def __setitem__(self, key, value):
            forbidden()
        def __delitem__(self, key):
            forbidden()
        def __iter__(self):
            forbidden()
        def __len__(self):
            forbidden()

    with monkeypatch.context() as guard:
        for name in tuple(sys.modules):
            if name == "agentcodec.adaptive" or name.startswith("agentcodec.adaptive."):
                guard.delitem(sys.modules, name)
        guard.setattr(os, "environ", NoCredentials())
        guard.setattr(os, "getenv", guarded_getenv)
        guard.setattr(os, "system", forbidden)
        guard.setattr(socket, "socket", forbidden)
        guard.setattr(socket, "create_connection", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(subprocess, "run", forbidden)
        guard.setattr(tiktoken, "get_encoding", forbidden)
        guard.setattr(tiktoken, "encoding_for_model", forbidden)
        module = importlib.import_module("agentcodec.adaptive")
        assert module is not None
        importlib.import_module("agentcodec.adaptive.contracts")
        assert not any(name in sys.modules for name in ("agentcodec.adaptive.meter",
            "agentcodec.adaptive.offline", "agentcodec.adaptive.benchmark"))


@pytest.mark.parametrize("coverage", [0, 2_000_000])
def test_coverage_is_not_sdk_repeat_limit(c, coverage):
    # Removing a needless SDK cap must not alter mathematical or snapshot bounds.
    assert c.ResourceSnapshot(**resource_values(valid_through_repeat=coverage)).valid_through_repeat == coverage


def test_break_even_is_not_sdk_repeat_limit(c):
    result = c.BreakEvenResult(kind="finite", first_strict_win=2_000_001,
        winning_interval=(2_000_001, None), assumptions=(), reason_codes=())
    assert result.first_strict_win == 2_000_001


def test_preserved_and_selected_decisions_require_matching_eligible_payload(c):
    candidates = tuple(candidate_values(i, **(dict(eligibility="eligible", payload="") if i == "json" else {}))
                       for i in IDS)
    preserved = decision_values(status="preserved_without_comparison", selected_candidate="json",
                                declared_format="json", payload="", candidates=candidates)
    assert c.SelectionDecision(**preserved).payload == ""
    assert c.SelectionDecision(**(preserved | {"comparison_status": "surrogate_only"})).savings_tokens is None
    for changes in ({"payload": "different"}, {"selected_candidate": "ac1"}, {"declared_format": "ac1"},
                    {"comparison_status": "exact_local_text"}, {"policy_retained": True},
                    {"costs": (cost_values(remaining_expected_tokens=1),)}):
        with pytest.raises(FallbackRequired):
            c.SelectionDecision(**(preserved | changes))
    exact_costs = (cost_values(remaining_expected_tokens=0, scope_spent_tokens=0,
                              scope_lifecycle_expected_tokens=0, component_totals=dict.fromkeys(KINDS, 0)),)
    selected = preserved | dict(status="selected", comparison_status="exact_local_text", costs=exact_costs,
        minimum_candidate="json", baseline_candidate="json", savings_tokens=0, savings_ratio=None,
        opportunity_cost_tokens=0, opportunity_cost_upper_bound=0)
    assert c.SelectionDecision(**selected).selected_candidate == "json"


def test_revalidation_detaches_capability_maps_and_rejects_raw_tampering(c):
    request = c.SelectionRequest(**request_values())
    frozen = c.freeze_request(request)
    request.capabilities.candidate_resources["json"] = ("not an identity",)
    assert frozen.capabilities.candidate_resources == {"json": (), "ac1": ()}
    with pytest.raises(FallbackRequired):
        c.freeze_request(request)
    profile = c.TokenizerProfile(**profile_values(encoding_name="unknown", accuracy="unknown"))
    assert profile.encoding_name == "unknown"  # Insufficient metering is not malformed configuration.
    object.__setattr__(profile, "accuracy", "invented")
    with pytest.raises(FallbackRequired):
        c.profile_id(profile)
    message = MessageV1(**message_values())
    del message.__dict__["blocker"]
    with pytest.raises(FallbackRequired):
        c.SelectionRequest(**request_values(message=message))

"""Synthetic-only tests for the public AgentWire v1 contracts."""

import hashlib
import importlib
import json
from typing import get_args

import pytest
from pydantic import ValidationError

from agentcodec.schema import MessageV1, UserDictionary


@pytest.fixture
def contracts():
    # Missing feature must fail inside each test, not during collection/setup.
    try:
        return importlib.import_module("agentcodec.agentwire.contracts")
    except ModuleNotFoundError as error:
        if error.name in {"agentcodec.agentwire", "agentcodec.agentwire.contracts"}:
            class MissingContracts:
                def __getattr__(self, name):
                    pytest.fail(f"AgentWire contract API is not implemented: {name}", pytrace=False)

            return MissingContracts()
        raise


def dictionary(**changes):
    values = dict(
        user_id="user-测试",
        version="v1",
        aliases={"z-code": "雪とé", "alpha": "first"},
    )
    return UserDictionary(**(values | changes))


def identity_values(**changes):
    return dict(user_id="user-测试", version="v1", sha256="a" * 64) | changes


def capability_values(**changes):
    return dict(
        user_id="user-测试",
        supported_profile_versions=("AWP1",),
        supported_schema_versions=("MessageV1",),
        supported_enum_table_versions=("AWP1",),
        max_message_bytes=4096,
        dictionary_identity=None,
    ) | changes


def message():
    return MessageV1(
        message_id="synthetic-1",
        message_type="delegate",
        task="检查，不发布",
        status="pending",
        blocker=None,
        next_step="",
        evidence=["evidence-a", "evidence-a"],
        constraints=["保留 原文"],
        approval_required=False,
        user_id="user-测试",
        dictionary_version=None,
    )


def test_dictionary_identity_hashes_full_canonical_snapshot(contracts):
    value = dictionary()
    identity = contracts.dictionary_identity_for(value)
    canonical = '{"aliases":{"alpha":"first","z-code":"雪とé"},"user_id":"user-测试","version":"v1"}'.encode("utf-8")

    assert identity == contracts.DictionaryIdentity(
        user_id="user-测试",
        version="v1",
        sha256=hashlib.sha256(canonical).hexdigest(),
    )
    assert identity.sha256 == hashlib.sha256(canonical).hexdigest()
    assert contracts.dictionary_identity_for(
        dictionary(aliases={"z-code": "雪とé", "alpha": "first", "third": "third phrase"})
    ).sha256 != identity.sha256
    assert contracts.dictionary_identity_for(
        dictionary(aliases={"z-code": "changed", "alpha": "first"})
    ).sha256 != identity.sha256
    with pytest.raises(ValidationError):
        contracts.DictionaryIdentity(**(identity_values() | {"unknown": "extra"}))
    with pytest.raises(ValidationError):
        contracts.DictionaryIdentity(**(identity_values() | {"sha256": "A" * 64}))
    frozen_identity = contracts.DictionaryIdentity(**identity_values())
    with pytest.raises(ValidationError):
        frozen_identity.version = "v2"
    with pytest.raises(ValidationError):
        contracts.DictionaryIdentity(**(identity_values() | {"version": 1}))


def test_mutated_dictionary_is_rejected_before_hashing(contracts):
    value = dictionary()
    value.aliases["alpha"] = False

    with pytest.raises(ValidationError):
        contracts.dictionary_identity_for(value)


def test_capability_contract_rejects_duplicate_or_invalid_versions(contracts):
    capability = contracts.ReceiverCapabilities(**capability_values())
    assert capability.supported_profile_versions == ("AWP1",)
    assert capability.max_message_bytes == 4096

    with pytest.raises(ValidationError):
        capability.max_message_bytes = 1
    with pytest.raises(ValidationError):
        contracts.ReceiverCapabilities(**(capability_values() | {"unexpected": True}))

    for field, duplicate in (
        ("supported_profile_versions", ("AWP1", "AWP1")),
        ("supported_schema_versions", ("MessageV1", "MessageV1")),
        ("supported_enum_table_versions", ("AWP1", "AWP1")),
    ):
        with pytest.raises(ValidationError):
            contracts.ReceiverCapabilities(**(capability_values() | {field: duplicate}))
    with pytest.raises(ValidationError):
        contracts.ReceiverCapabilities(**(capability_values() | {
            "supported_profile_versions": ["AWP1"],
        }))
    with pytest.raises(ValidationError):
        contracts.ReceiverCapabilities(**(capability_values() | {
            "supported_profile_versions": (1,),
        }))

    for bad_capacity in (0, -1, True, 1.0, "4096"):
        with pytest.raises(ValidationError):
            contracts.ReceiverCapabilities(**(capability_values() | {"max_message_bytes": bad_capacity}))

    # Capability declarations are explicit; unknown versions are retained, never upgraded or guessed.
    unknown = contracts.ReceiverCapabilities(**(capability_values() | {
        "supported_profile_versions": ("AWP9",),
        "supported_schema_versions": ("MessageV9",),
        "supported_enum_table_versions": ("AWP9",),
    }))
    assert unknown.supported_profile_versions == ("AWP9",)
    assert unknown.supported_schema_versions == ("MessageV9",)
    assert unknown.supported_enum_table_versions == ("AWP9",)


def test_results_enforce_success_failure_invariants(contracts):
    contracts.NegotiationResult(status="supported", reason_code=None)
    contracts.NegotiationResult(status="fallback_required", reason_code="unsupported_profile")
    contracts.GatewayResult(status="decoded", message=message(), reason_code=None)
    contracts.GatewayResult(status="fallback_required", message=None, reason_code="invalid_message")

    for values in (
        dict(status="supported", reason_code="unsupported_profile"),
        dict(status="fallback_required", reason_code=None),
        dict(status="other", reason_code=None),
    ):
        with pytest.raises(ValidationError):
            contracts.NegotiationResult(**values)

    for values in (
        dict(status="decoded", message=None, reason_code=None),
        dict(status="decoded", message=message(), reason_code="invalid_message"),
        dict(status="fallback_required", message=message(), reason_code="invalid_message"),
        dict(status="fallback_required", message=None, reason_code=None),
        dict(status="decoded", message=message(), reason_code="not_a_stable_code"),
    ):
        with pytest.raises(ValidationError):
            contracts.GatewayResult(**values)


def test_gateway_delivery_enforces_decoded_fallback_and_pass_through_states(contracts):
    valid_message = message()
    canonical_context = json.dumps(
        valid_message.model_dump(), ensure_ascii=False, separators=(",", ":")
    )
    decoded = contracts.GatewayDelivery(
        candidate_id="awp1",
        decode_status="decoded",
        model_context=canonical_context,
        pass_through_text=None,
        reason_code=None,
    )
    assert decoded.model_context == canonical_context

    noncanonical_contexts = (
        "not JSON",
        "{}",
        json.dumps(
            valid_message.model_dump() | {"status": "not-a-message-status"},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        json.dumps(valid_message.model_dump(), ensure_ascii=False, separators=(", ", ": ")),
        json.dumps(
            dict(reversed(list(valid_message.model_dump().items()))),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    for context in noncanonical_contexts:
        with pytest.raises(ValidationError):
            contracts.GatewayDelivery(
                candidate_id="awp1",
                decode_status="decoded",
                model_context=context,
                pass_through_text=None,
                reason_code=None,
            )

    fallback = contracts.GatewayDelivery(
        candidate_id="json",
        decode_status="fallback_required",
        model_context=None,
        pass_through_text=None,
        reason_code="invalid_envelope",
    )
    assert fallback.reason_code == "invalid_envelope"

    pass_through = contracts.GatewayDelivery(
        candidate_id="natural_language_freeform",
        decode_status="pass_through",
        model_context=None,
        pass_through_text="",
        reason_code=None,
    )
    assert pass_through.pass_through_text == ""
    assert pass_through.pass_through_text is not None

    invalid = (
        dict(candidate_id="natural_language_freeform", decode_status="decoded",
             model_context="{}", pass_through_text=None, reason_code=None),
        dict(candidate_id="awp1", decode_status="pass_through",
             model_context=None, pass_through_text="raw", reason_code=None),
        dict(candidate_id="natural_language_freeform", decode_status="pass_through",
             model_context="{}", pass_through_text="raw", reason_code=None),
        dict(candidate_id="natural_language_freeform", decode_status="pass_through",
             model_context=None, pass_through_text=None, reason_code=None),
        dict(candidate_id="json", decode_status="fallback_required",
             model_context=None, pass_through_text="raw", reason_code="invalid_message"),
        dict(candidate_id="json", decode_status="fallback_required",
             model_context="{}", pass_through_text=None, reason_code="invalid_message"),
        dict(candidate_id="json", decode_status="fallback_required",
             model_context=None, pass_through_text=None, reason_code=None),
        dict(candidate_id="json", decode_status="decoded",
             model_context="{}", pass_through_text=None, reason_code="invalid_message"),
    )
    for values in invalid:
        with pytest.raises(ValidationError):
            contracts.GatewayDelivery(**values)


def test_public_exports_and_agentwire_error_are_stable_and_nonrevealing(contracts):
    try:
        package = importlib.import_module("agentcodec.agentwire")
    except ModuleNotFoundError as error:
        if error.name == "agentcodec.agentwire":
            pytest.fail("AgentWire public package is not implemented", pytrace=False)
        raise
    required = {
        "DictionaryIdentity", "BenchmarkCandidate", "ReceiverCapabilities", "NegotiationResult",
        "GatewayResult", "GatewayDelivery", "AgentWireError", "dictionary_identity_for",
        "AgentWireReasonCode",
    }
    assert required <= set(package.__all__)
    assert set(get_args(contracts.BenchmarkCandidate)) == {
        "natural_language", "natural_language_freeform", "json", "ac1", "ac1_dictionary",
        "ac2", "ac2_dictionary", "awp1", "awp1_dictionary",
    }
    assert set(get_args(contracts.AgentWireReasonCode)) == {
        "unsupported_profile", "unsupported_schema", "unsupported_enum_table",
        "receiver_user_mismatch", "message_too_large", "invalid_envelope", "unknown_enum",
        "dictionary_missing", "dictionary_identity_mismatch", "unknown_alias", "invalid_message",
    }

    error = contracts.AgentWireError("invalid_message")
    assert error.reason_code == "invalid_message"
    assert str(error) == "agentwire operation rejected"
    sentinel = "SENSITIVE_SENTINEL_TEST"
    with pytest.raises((ValidationError, ValueError, TypeError)) as exc_info:
        contracts.AgentWireError(sentinel)
    assert sentinel not in str(exc_info.value)

    with pytest.raises(ValidationError):
        contracts.DictionaryIdentity(**identity_values(sha256="g" * 64))

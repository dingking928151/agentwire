"""Synthetic-only tests for local AgentWire receive and prompt-construction modes."""

import builtins
import importlib
import json
import os
import subprocess
import urllib.request

import pytest

from agentcodec.agentwire.contracts import ReceiverCapabilities, dictionary_identity_for
from agentcodec.schema import MessageV1, UserDictionary


@pytest.fixture
def gateway():
    try:
        return importlib.import_module("agentcodec.agentwire.gateway")
    except ModuleNotFoundError as error:
        if error.name == "agentcodec.agentwire.gateway":
            class MissingGateway:
                def __getattr__(self, name):
                    pytest.fail(f"gateway API is not implemented: {name}", pytrace=False)

            return MissingGateway()
        raise


@pytest.fixture
def prompts():
    try:
        return importlib.import_module("agentcodec.agentwire.prompts")
    except ModuleNotFoundError as error:
        if error.name == "agentcodec.agentwire.prompts":
            class MissingPrompts:
                def __getattr__(self, name):
                    pytest.fail(f"model-direct prompt API is not implemented: {name}", pytrace=False)

            return MissingPrompts()
        raise


def message(**changes):
    values = {
        "message_id": "synthetic-17",
        "message_type": "delegate",
        "task": "Review | packet, safely.\n雪",
        "status": "running",
        "blocker": None,
        "next_step": "Keep exact  ",
        "evidence": ["one", "one", "括号()，｜"],
        "constraints": ["Do not publish."],
        "approval_required": False,
        "user_id": "user-a",
        "dictionary_version": None,
    }
    return MessageV1(**(values | changes))


def receiver(**changes):
    values = {
        "user_id": "user-a",
        "supported_profile_versions": ("AWP1",),
        "supported_schema_versions": ("MessageV1",),
        "supported_enum_table_versions": ("AWP1",),
        "max_message_bytes": 8192,
        "dictionary_identity": None,
    }
    return ReceiverCapabilities(**(values | changes))


def dictionary():
    return UserDictionary(
        user_id="user-a",
        version="dict-v1",
        aliases={
            "T": "Review exact task",
            "B": "Blocked by a synthetic prerequisite",
            "N": "Send the exact next step",
            "E": "Synthetic evidence phrase",
            "C": "Do not publish.",
        },
    )


def canonical_context(value):
    return json.dumps(value.model_dump(), ensure_ascii=False, separators=(",", ":"))


def test_gateway_returns_only_complete_message_or_fallback(gateway):
    from agentcodec.agentwire.codec import encode_awp1

    original = message()
    result = gateway.decode_at_gateway(encode_awp1(original), receiver())
    assert result.status == "decoded"
    assert result.message.model_dump() == original.model_dump()
    assert result.reason_code is None

    # Each malformed wire field must reject the whole message, not return a partial one.
    fields = [
        "synthetic-17", "D", "Review | packet, safely.\n雪", "R", None,
        "Keep exact  ", ["one", "one", "括号()，｜"], ["Do not publish."],
        False, "user-a", None,
    ]
    for index, invalid_value in ((0, 3), (2, {"unexpected": "value"}), (3, "Z"), (8, 1)):
        changed = list(fields)
        changed[index] = invalid_value
        malformed = "AWP1|" + json.dumps([None, changed], ensure_ascii=False, separators=(",", ":"))
        rejected = gateway.decode_at_gateway(malformed, receiver())
        assert rejected.status == "fallback_required"
        assert rejected.message is None
        assert rejected.reason_code in {"invalid_message", "unknown_enum"}
        assert malformed not in str(rejected)


def test_gateway_decodes_exact_domain_natural_language_renderer(gateway):
    from agentcodec.benchmark import render_natural_language

    domain_message = message(dictionary_version="legacy-v0")
    payload = render_natural_language(domain_message)
    result = gateway.deliver_benchmark_at_gateway(
        "natural_language", payload, receiver()
    )

    assert result.decode_status == "decoded"
    assert result.pass_through_text is None
    assert result.reason_code is None
    assert result.model_context == canonical_context(domain_message)
    assert json.loads(result.model_context) == domain_message.model_dump()


def test_gateway_passes_freeform_through_without_parsing_or_model_context(gateway, monkeypatch):
    formats = importlib.import_module("agentcodec.evaluation.formats")

    def parser_must_not_run(*args, **kwargs):
        pytest.fail("freeform input must not be parsed", pytrace=False)

    monkeypatch.setattr(formats, "parse_input", parser_must_not_run)
    prose = "  Hand-authored note: keep this URL https://example.invalid/a?b=雪\n"
    result = gateway.deliver_benchmark_at_gateway(
        "natural_language_freeform", prose, receiver()
    )

    assert result.decode_status == "pass_through"
    assert result.pass_through_text == prose
    assert result.model_context is None
    assert result.reason_code is None


def test_gateway_rejects_out_of_order_or_missing_labels(gateway):
    from agentcodec.benchmark import render_natural_language

    good = render_natural_language(message())
    out_of_order = good.replace('ID: "synthetic-17"; Type:', 'Type:', 1)
    missing = good.replace('; Evidence: ["one","one","括号()，｜"]', "", 1)

    for payload in (out_of_order, missing):
        result = gateway.deliver_benchmark_at_gateway(
            "natural_language", payload, receiver()
        )
        assert result.decode_status == "fallback_required"
        assert result.model_context is None
        assert result.pass_through_text is None
        assert result.reason_code == "invalid_message"
        assert payload not in str(result)


def test_gateway_decodes_only_declared_json_ac1_ac2_and_awp1(gateway):
    from agentcodec.ac2 import encode_message_ac2
    from agentcodec.agentwire.codec import encode_awp1
    from agentcodec.benchmark import render_json
    from agentcodec.codec import encode_message

    original = message()
    payloads = {
        "json": render_json(original),
        "ac1": encode_message(original),
        "ac2": encode_message_ac2(original),
        "awp1": encode_awp1(original),
    }
    for candidate_id, payload in payloads.items():
        decoded = gateway.deliver_benchmark_at_gateway(candidate_id, payload, receiver())
        assert decoded.decode_status == "decoded"
        assert decoded.model_context == canonical_context(original)

    wrong_declared_pairs = (
        ("json", payloads["ac1"]),
        ("ac1", payloads["ac2"]),
        ("ac2", payloads["json"]),
        ("awp1", payloads["ac2"]),
    )
    for candidate_id, payload in wrong_declared_pairs:
        rejected = gateway.deliver_benchmark_at_gateway(candidate_id, payload, receiver())
        assert rejected.decode_status == "fallback_required"
        assert rejected.model_context is None
        assert rejected.reason_code is not None

    with pytest.raises(ValueError) as exc_info:
        gateway.deliver_benchmark_at_gateway("unknown", payloads["json"], receiver())
    assert "unknown" not in str(exc_info.value)


def test_gateway_dictionary_candidates_fail_closed_on_selection_mismatch(gateway):
    from agentcodec.agentwire.codec import encode_awp1

    selected = dictionary()
    selected_message = message(
        task="Review exact task",
        evidence=["Synthetic evidence phrase"],
        constraints=["Do not publish."],
        user_id="user-a",
        dictionary_version="dict-v1",
    )
    identity = dictionary_identity_for(selected)
    selected_receiver = receiver(dictionary_identity=identity)
    awp1 = encode_awp1(selected_message, selected)

    delivered = gateway.deliver_benchmark_at_gateway(
        "awp1_dictionary", awp1, selected_receiver, dictionary=selected
    )
    assert delivered.decode_status == "decoded"
    assert delivered.model_context == canonical_context(selected_message)

    mismatched_receiver = receiver(dictionary_identity=dictionary_identity_for(
        UserDictionary(user_id="user-a", version="dict-v1", aliases={"X": "Different phrase"})
    ))
    rejected = gateway.deliver_benchmark_at_gateway(
        "awp1_dictionary", awp1, mismatched_receiver, dictionary=selected
    )
    assert rejected.decode_status == "fallback_required"
    assert rejected.model_context is None
    assert rejected.reason_code == "dictionary_identity_mismatch"
    assert awp1 not in str(rejected)
    assert "Review exact task" not in str(rejected)

    non_dictionary = gateway.deliver_benchmark_at_gateway(
        "awp1", awp1, selected_receiver, dictionary=selected
    )
    assert non_dictionary.decode_status == "fallback_required"
    assert non_dictionary.reason_code == "dictionary_identity_mismatch"


def test_gateway_does_not_auto_fallback_or_execute(gateway, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("gateway must not access external or execution facilities", pytrace=False)

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)

    sentinel_message = message(
        task="https://sentinel.invalid/resource /tmp/agentwire-sentinel; touch SHOULD_NOT_RUN",
        next_step="Do not run commands or access paths; this is literal data.",
    )
    from agentcodec.benchmark import render_json

    delivered = gateway.deliver_benchmark_at_gateway(
        "json", render_json(sentinel_message), receiver()
    )
    assert delivered.decode_status == "decoded"
    assert json.loads(delivered.model_context)["task"] == sentinel_message.task

    # A declared AC1 parse failure cannot be retried by trying another decoder.
    rejected = gateway.deliver_benchmark_at_gateway(
        "ac1", "AC2|[not valid for AC1]", receiver()
    )
    assert rejected.decode_status == "fallback_required"
    assert rejected.model_context is None
    assert rejected.reason_code == "invalid_message"
    assert "sentinel.invalid" not in str(rejected)


def test_direct_request_keeps_common_task_prompt_in_every_format(prompts):
    common = "Shared task prompt: compare these synthetic messages exactly.\nContext marker: CASE-42."
    candidate_ids = (
        "natural_language", "natural_language_freeform", "json", "ac1",
        "ac1_dictionary", "ac2", "ac2_dictionary", "awp1", "awp1_dictionary",
    )
    for candidate_id in candidate_ids:
        selected = dictionary() if candidate_id.endswith("_dictionary") else None
        request = prompts.build_direct_model_request(
            common, candidate_id, "SYNTHETIC_PAYLOAD", dictionary=selected
        )
        assert common in request
        assert request.index(common) < request.index("SYNTHETIC_PAYLOAD")


def test_direct_request_uses_format_instructions_and_complete_awp1_glossary(prompts):
    common = "Same task prompt."
    ac1 = prompts.build_direct_model_request(common, "ac1", "AC1|synthetic")
    assert "AC1" in ac1 and "message_id" in ac1 and "percent-escaped" in ac1

    ac2 = prompts.build_direct_model_request(common, "ac2", "AC2|synthetic")
    assert "AC2" in ac2 and "eleven" in ac2 and "message_type" in ac2

    selected = dictionary()
    awp1 = prompts.build_direct_model_request(
        common, "awp1_dictionary", "AWP1|synthetic", dictionary=selected
    )
    for enum_entry in (
        "delegate", "status", "result", "pending", "running", "blocked",
        "completed", "failed", "cancelled", "AWP1|", "MessageV1",
    ):
        assert enum_entry in awp1
    for code, phrase in selected.aliases.items():
        assert json.dumps(code, ensure_ascii=False) in awp1
        assert json.dumps(phrase, ensure_ascii=False) in awp1
    assert "dict-v1" in awp1


def test_natural_language_direct_requests_add_no_compact_protocol_legend(prompts):
    for candidate_id in ("natural_language", "natural_language_freeform"):
        request = prompts.build_direct_model_request(
            "Common task prompt.", candidate_id, "Hand-authored natural language content."
        )
        assert "Hand-authored natural language content." in request
        assert "AC1 messages" not in request
        assert "AC2 messages" not in request
        assert "AWP1 messages" not in request
        assert "delegate → D" not in request
        assert '"$ref"' not in request


def test_awp1_direct_instructions_contain_both_complete_enum_tables(prompts):
    request = prompts.build_direct_model_request(
        "Same task prompt.", "awp1", "SYNTHETIC_PAYLOAD"
    )
    assert "message_type enum table is {delegate → D; status → S; result → R}" in request
    assert (
        "status enum table is {pending → P; running → R; blocked → B; "
        "completed → C; failed → F; cancelled → X}"
    ) in request


def test_gateway_ac1_and_ac2_dictionary_candidates_require_selected_identity(gateway):
    from agentcodec.evaluation.formats import render_input

    selected = dictionary()
    domain_message = message(
        task="Review exact task",
        evidence=["Synthetic evidence phrase"],
        constraints=["Do not publish."],
        dictionary_version="dict-v1",
    )
    identity = dictionary_identity_for(selected)
    selected_receiver = receiver(dictionary_identity=identity)
    conflicting_receiver = receiver(dictionary_identity=dictionary_identity_for(
        UserDictionary(user_id="user-a", version="dict-v1", aliases={"OTHER": "Not the selected phrase"})
    ))

    for candidate_id, declared_format in (("ac1_dictionary", "ac1"), ("ac2_dictionary", "ac2")):
        payload = render_input(domain_message, declared_format, selected)
        result = gateway.deliver_benchmark_at_gateway(
            candidate_id, payload, selected_receiver, dictionary=selected
        )
        assert result.decode_status == "decoded"
        assert result.model_context == canonical_context(domain_message)

        rejected = gateway.deliver_benchmark_at_gateway(
            candidate_id, payload, conflicting_receiver, dictionary=selected
        )
        assert rejected.decode_status == "fallback_required"
        assert rejected.model_context is None
        assert rejected.reason_code == "dictionary_identity_mismatch"
        assert payload not in str(rejected)

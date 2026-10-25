"""Synthetic-only tests for complete local AgentWire text-cost accounting."""

import importlib

import pytest
from pydantic import ValidationError

from agentcodec.adaptive import meter
from agentcodec.adaptive.contracts import MeasuredAttempt, TokenizerProfile
from agentcodec.codec import FallbackRequired
from agentcodec.agentwire import contracts


@pytest.fixture
def api():
    try:
        return importlib.import_module("agentcodec.agentwire.costs")
    except ModuleNotFoundError as error:
        if error.name == "agentcodec.agentwire.costs":
            class MissingCosts:
                def __getattr__(self, name):
                    pytest.fail(f"AgentWire cost API is not implemented: {name}", pytrace=False)

            return MissingCosts()
        raise


def _contract(name, **fields):
    try:
        model = getattr(contracts, name)
    except AttributeError:
        pytest.fail(f"AgentWire shared cost contract is missing: {name}", pytrace=False)
    return model(**fields)


def part(label, text):
    return _contract("CostPart", label=label, text=text)


def event(event_id, kind, surfaces, *parts, multiplicity=1):
    return _contract(
        "CostEvent",
        event_id=event_id,
        kind=kind,
        surfaces=surfaces,
        parts=tuple(parts),
        multiplicity=multiplicity,
    )


def profile():
    return TokenizerProfile(
        encoding_name="synthetic-local-profile",
        library_version="0.0.synthetic",
        vocabulary_sha256="0" * 64,
        accuracy="exact_local_text",
        mapping_basis=None,
    )


def stub_meter(monkeypatch, results_by_id):
    """Replace only the meter boundary; keep AgentWire mapping and aggregation real."""
    calls = []

    def measure_attempts(attempts, tokenizer_profile):
        calls.append((attempts, tokenizer_profile))
        measured = []
        for attempt in attempts:
            total, deltas = results_by_id[attempt.event_id]
            measured.append(MeasuredAttempt(
                event_id=attempt.event_id,
                attempt_kind=attempt.attempt_kind,
                total_tokens=total,
                component_tokens=deltas,
                simulated=attempt.simulated,
                multiplicity=attempt.multiplicity,
            ))
        return tuple(measured)

    monkeypatch.setattr(meter, "measure_attempts", measure_attempts)
    return calls


def test_complete_request_response_and_setup_are_counted_once(api, monkeypatch):
    setup = event("protocol-setup-1", "setup", ("transport",), part("setup", "grammar"))
    request = event(
        "request-1", "request", ("transport", "model_input"),
        part("context", "system\n"), part("payload", "AWP1|synthetic"),
    )
    response = event("response-1", "response", ("model_output",), part("response", "ack"))
    calls = stub_meter(monkeypatch, {
        "protocol-setup-1": (4, (4,)),
        "request-1": (11, (5, 6)),
        "response-1": (3, (3,)),
    })

    result = api.measure_cost_events((setup, request, response), profile())

    assert result.status == "exact_local_text"
    assert result.distinct_text_event_tokens == 18
    assert result.transport_tokens == 15
    assert result.model_input_tokens == 11
    assert result.model_output_tokens == 3
    assert result.transport_bytes == len(b"grammar") + len("system\nAWP1|synthetic".encode("utf-8"))
    assert result.event_kind_tokens["setup"] == 4
    assert result.event_kind_tokens["request"] == 11
    assert result.event_kind_tokens["response"] == 3
    assert result.part_label_tokens["setup"] == 4
    assert result.part_label_tokens["context"] == 5
    assert result.part_label_tokens["payload"] == 6
    assert result.part_label_tokens["response"] == 3
    attempts = calls[0][0]
    assert tuple("".join(component.text for component in item.components) for item in attempts) == (
        "grammar", "system\nAWP1|synthetic", "ack",
    )
    assert len(attempts) == 3


def test_cost_events_convert_to_existing_text_attempts_without_heuristics(api, monkeypatch):
    cases = (
        ("setup", "setup", "setup"),
        ("negotiation", "negotiation", "setup"),
        ("dictionary_transfer", "dictionary_distribution", "setup"),
        ("protocol_instructions", "request", "context"),
        ("context", "request", "context"),
        ("dictionary_glossary", "request", "context"),
        ("payload", "request", "payload"),
        ("response", "response", "other"),
        ("retry", "retry", "retry"),
        ("fallback", "fallback", "fallback"),
        ("other", "response", "other"),
    )
    events = tuple(
        event(f"event-{index}", kind, ("transport",), part(label, f"text-{label}"), multiplicity=2)
        for index, (label, kind, _) in enumerate(cases)
    )
    counts = {f"event-{index}": (2, (2,)) for index in range(len(cases))}
    calls = stub_meter(monkeypatch, counts)

    api.measure_cost_events(events, profile())

    attempts = calls[0][0]
    assert tuple(item.attempt_kind for item in attempts) == (
        "setup", "setup", "setup", "initial", "initial", "initial", "initial",
        "other", "retry", "fallback", "other",
    )
    assert tuple(item.simulated for item in attempts) == (True,) * len(cases)
    assert tuple(item.multiplicity for item in attempts) == (2,) * len(cases)
    assert tuple(
        (component.kind, component.text, component.resource_identity)
        for item in attempts for component in item.components
    ) == tuple(
        (component_kind, f"text-{label}", None)
        for label, _, component_kind in cases
    )


def test_protocol_and_dictionary_setup_histories_are_independent_events(api, monkeypatch):
    protocol = event("protocol-history-1", "setup", ("transport",), part("setup", "profile v1"))
    dictionary = event(
        "dictionary-history-1", "dictionary_distribution", ("transport",),
        part("dictionary_transfer", "dictionary v1"),
    )
    calls = stub_meter(monkeypatch, {
        "protocol-history-1": (4, (4,)),
        "dictionary-history-1": (7, (7,)),
    })

    result = api.measure_cost_events((protocol, dictionary), profile())

    assert len(calls[0][0]) == 2
    assert tuple(item.event_id for item in calls[0][0]) == (
        "protocol-history-1", "dictionary-history-1",
    )
    assert tuple(item.attempt_kind for item in calls[0][0]) == ("setup", "setup")
    assert result.distinct_text_event_tokens == 11
    assert result.event_kind_tokens["setup"] == 4
    assert result.event_kind_tokens["dictionary_distribution"] == 7
    assert result.part_label_tokens["setup"] == 4
    assert result.part_label_tokens["dictionary_transfer"] == 7


def test_direct_request_can_have_two_surfaces_without_double_total(api, monkeypatch):
    request = event(
        "direct-request-1", "request", ("transport", "model_input"),
        part("context", "shared prompt"), part("payload", "body"),
    )
    stub_meter(monkeypatch, {"direct-request-1": (8, (3, 5))})

    result = api.measure_cost_events((request,), profile())

    assert result.transport_tokens == 8
    assert result.model_input_tokens == 8
    assert result.distinct_text_event_tokens == 8


def test_same_id_with_changed_text_or_surface_is_rejected(api, monkeypatch):
    original = event("same-event", "request", ("transport",), part("payload", "literal text"))
    changed_text = event("same-event", "request", ("transport",), part("payload", "other text"))
    changed_surface = event("same-event", "request", ("model_input",), part("payload", "literal text"))
    calls = stub_meter(monkeypatch, {"same-event": (2, (2,))})

    with pytest.raises(FallbackRequired):
        api.measure_cost_events((original, changed_text), profile())
    with pytest.raises(FallbackRequired):
        api.measure_cost_events((original, changed_surface), profile())
    assert calls == []


def test_cost_event_rejects_missing_parts_but_allows_explicit_empty_text(api, monkeypatch):
    with pytest.raises(ValidationError):
        event("missing-parts", "request", ("transport",))

    explicit_empty = event(
        "explicit-empty-text", "request", ("transport",), part("payload", ""),
    )
    calls = stub_meter(monkeypatch, {"explicit-empty-text": (0, (0,))})
    result = api.measure_cost_events((explicit_empty,), profile())

    assert result.status == "exact_local_text"
    assert result.distinct_text_event_tokens == 0
    assert len(calls[0][0]) == 1
    assert len(calls[0][0][0].components) == 1
    assert calls[0][0][0].components[0].text == ""


def test_break_even_is_reported_separately_for_each_baseline(api):
    labelled = api.calculate_break_even(30, 5, 100, 20)
    freeform = api.calculate_break_even(30, 5, 0, 20)
    equal_cost = api.calculate_break_even(10, 20, 10, 20)
    delayed = api.calculate_break_even(100, 1, 0, 20)
    never = api.calculate_break_even(10, 21, 0, 20)

    assert (labelled.status, labelled.first_strict_win) == ("finite", 1)
    assert (freeform.status, freeform.first_strict_win) == ("finite", 3)
    assert (equal_cost.status, equal_cost.first_strict_win) == ("never", None)
    assert (delayed.status, delayed.first_strict_win) == ("finite", 6)
    assert (never.status, never.first_strict_win) == ("never", None)


def test_break_even_unknown_cost_returns_cannot_compare_without_threshold(api):
    result = api.calculate_break_even(None, 5, 0, 20)

    assert result.status == "cannot_compare"
    assert result.first_strict_win is None
    assert result.reason_code == "required_cost_unknown"


def test_negative_costs_and_zero_reuse_are_rejected(api):
    with pytest.raises((ValueError, FallbackRequired)):
        api.calculate_break_even(-1, 0, 0, 0)
    with pytest.raises(ValidationError):
        event("zero-reuse", "request", ("transport",), part("payload", "x"), multiplicity=0)


def test_missing_tokenizer_returns_cannot_compare_not_zero(api, monkeypatch):
    request = event("unknown-request", "request", ("transport", "model_input"), part("payload", "x"))
    calls = stub_meter(monkeypatch, {"unknown-request": (1, (1,))})

    result = api.measure_cost_events((request,), None)

    assert result.status == "cannot_compare"
    assert result.reason_code is not None
    assert result.distinct_text_event_tokens is None
    assert result.transport_tokens is None
    assert result.model_input_tokens is None
    assert result.model_output_tokens is None
    assert result.transport_bytes == 1
    assert calls == []


def test_measurement_unavailable_response_and_signed_part_deltas_keep_their_meaning(api, monkeypatch):
    response = event("synthetic-response", "response", ("model_output",), part("response", "fixture"))

    def unavailable(attempts, tokenizer_profile):
        raise meter.MeasurementUnavailable("tokenizer_unavailable")

    monkeypatch.setattr(meter, "measure_attempts", unavailable)
    unknown = api.measure_cost_events((response,), profile())
    assert unknown.status == "cannot_compare"
    assert unknown.reason_code == "tokenizer_unavailable"
    assert unknown.model_output_tokens is None
    assert unknown.distinct_text_event_tokens is None

    repeated = event(
        "repeat-request", "request", ("transport", "model_input"),
        part("context", "prefix"), part("payload", "suffix"), multiplicity=3,
    )
    calls = stub_meter(monkeypatch, {"repeat-request": (4, (5, -1))})
    counted = api.measure_cost_events((repeated, repeated), profile())

    assert len(calls) == 1
    assert len(calls[0][0]) == 1
    assert calls[0][0][0].multiplicity == 3
    assert counted.distinct_text_event_tokens == 12
    assert counted.transport_tokens == 12
    assert counted.model_input_tokens == 12
    assert counted.part_label_tokens["context"] == 15
    assert counted.part_label_tokens["payload"] == -3

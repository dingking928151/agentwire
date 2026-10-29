"""Synthetic-only tests for paired AgentWire benchmark domains and reuse."""

import importlib
import json
from pathlib import Path

import pytest

from agentcodec.adaptive import meter
from agentcodec.adaptive.contracts import MeasuredAttempt, TokenizerProfile
from agentcodec.agentwire import contracts
from agentcodec.agentwire.contracts import ReceiverCapabilities, dictionary_identity_for
from agentcodec.schema import MessageV1, UserDictionary


PROFILE_ID = "synthetic-byte-count-v1"
FIXTURE_DIR = Path(__file__).parents[1] / "bench" / "agentwire"
ALL_CANDIDATES = (
    "natural_language", "natural_language_freeform", "json", "ac1",
    "ac1_dictionary", "ac2", "ac2_dictionary", "awp1", "awp1_dictionary",
)
SOURCE_ALLOWLIST = {
    "natural_language", "natural_language_freeform", "json", "ac1", "ac2", "awp1",
}
SELECTED_ALLOWLIST = {
    "natural_language", "json", "awp1", "ac1_dictionary", "ac2_dictionary",
    "awp1_dictionary",
}


@pytest.fixture
def api():
    try:
        return importlib.import_module("agentcodec.agentwire.benchmark")
    except ModuleNotFoundError as error:
        if error.name == "agentcodec.agentwire.benchmark":
            class MissingBenchmark:
                def __getattr__(self, name):
                    pytest.fail(f"AgentWire benchmark API is not implemented: {name}", pytrace=False)

            return MissingBenchmark()
        raise


def _fixture_records():
    return [
        json.loads(line)
        for line in (FIXTURE_DIR / "exchanges.v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]


def _cases(api, records=None):
    records = _fixture_records() if records is None else records
    return tuple(
        api.BenchmarkCase(
            case_id=record["case_id"],
            task_prompt=record["task_prompt"],
            source_message=MessageV1.model_validate(record["source_message"], strict=True),
            freeform_text=record["freeform_text"],
            freeform_annotations={key: tuple(value) for key, value in record["freeform_annotations"].items()},
            synthetic_response_fixture=record["synthetic_response_fixture"],
        )
        for record in records
    )


def _dictionary():
    return UserDictionary.model_validate(
        json.loads((FIXTURE_DIR / "dictionary.v1.json").read_text(encoding="utf-8")),
        strict=True,
    )


def _profile():
    return TokenizerProfile(
        encoding_name="synthetic-byte-count",
        library_version="0.0.synthetic",
        vocabulary_sha256="0" * 64,
        accuracy="exact_local_text",
        mapping_basis="test-only deterministic UTF-8 byte meter",
    )


def _event(event_id, kind, label, text, surfaces=("transport",)):
    return contracts.CostEvent(
        event_id=event_id,
        kind=kind,
        surfaces=surfaces,
        parts=(contracts.CostPart(label=label, text=text),),
    )


def _scenario(
    api,
    cases,
    *,
    domain="source",
    unit="same_message_instance",
    states=None,
    dictionary_exposure=None,
    dictionary_history=(),
    dictionary=None,
    scope_id="synthetic-scope-a",
    receiver_user="synthetic-user",
    corpus_id="synthetic-corpus-v1",
):
    selected = domain == "selected_dictionary_transport"
    identity = dictionary_identity_for(dictionary) if selected and dictionary is not None else None
    receiver = ReceiverCapabilities(
        user_id=receiver_user,
        supported_profile_versions=("AWP1",),
        supported_schema_versions=("MessageV1",),
        supported_enum_table_versions=("AWP1",),
        max_message_bytes=2_000_000,
        dictionary_identity=identity,
    )
    supplied = {} if states is None else dict(states)
    protocol_states = [
        supplied.get(
            candidate,
            api.ProtocolState(
                candidate_id=candidate,
                profile_id=PROFILE_ID,
                state="out_of_band_preinstalled",
            ),
        )
        for candidate in ALL_CANDIDATES
    ]
    protocol_states.extend(
        state for state in supplied.values()
        if isinstance(state, api.ProtocolState) and state not in protocol_states
    )
    if dictionary_exposure is None:
        dictionary_exposure = api.DictionaryExposure(
            state="out_of_band_preinstalled" if selected else "not_applicable"
        )
    return api.BenchmarkScenario(
        scenario_id=f"private-{domain}-scenario",
        scope_id=scope_id,
        corpus_id=corpus_id,
        metadata_domain=domain,
        receiver_capabilities=receiver,
        protocol_states=tuple(protocol_states),
        dictionary_candidate_exposure=dictionary_exposure,
        dictionary_history=tuple(dictionary_history),
        cases=tuple(cases),
        reuse=api.ReuseScenario(unit=unit, total_uses=(1, 10, 50)),
    )


def _byte_meter(monkeypatch):
    observed = []

    def measure_attempts(attempts, tokenizer_profile):
        results = []
        for attempt in attempts:
            observed.append((attempt, tokenizer_profile))
            deltas = tuple(len(component.text.encode("utf-8")) for component in attempt.components)
            results.append(MeasuredAttempt(
                event_id=attempt.event_id,
                attempt_kind=attempt.attempt_kind,
                total_tokens=sum(deltas),
                component_tokens=deltas,
                simulated=attempt.simulated,
                multiplicity=attempt.multiplicity,
            ))
        return tuple(results)

    monkeypatch.setattr(meter, "measure_attempts", measure_attempts)
    return observed


def _run(api, scenarios, monkeypatch, dictionary=None, profiles=None):
    _byte_meter(monkeypatch)
    return api.run_agentwire_benchmark(
        tuple(scenarios),
        {PROFILE_ID: _profile()} if profiles is None else profiles,
        dictionary=dictionary,
    )


def _domain_report(report, domain):
    return next(item for item in report.scenarios if item.metadata_domain == domain)


def _row(scenario_report, *, candidate, mode="gateway", total_uses=1):
    return next(
        row for row in scenario_report.rows
        if row.candidate_id == candidate
        and row.receiver_mode == mode
        and row.total_uses == total_uses
    )


def test_candidate_allowlists_are_domain_specific(api, monkeypatch):
    dictionary = _dictionary()
    case = _cases(api, _fixture_records()[:1])
    source = _scenario(api, case)
    selected = _scenario(
        api, case, domain="selected_dictionary_transport", dictionary=dictionary,
    )

    report = _run(api, (source, selected), monkeypatch, dictionary=dictionary)
    source_rows = _domain_report(report, "source").rows
    selected_rows = _domain_report(report, "selected_dictionary_transport").rows
    source_candidates = {row.candidate_id for row in source_rows if row.metric_status != "not_applicable"}
    selected_candidates = {
        row.candidate_id for row in selected_rows if row.metric_status != "not_applicable"
    }

    assert source_candidates == SOURCE_ALLOWLIST
    assert selected_candidates == SELECTED_ALLOWLIST
    selected_by_id = {row.candidate_id: row for row in selected_rows if row.receiver_mode == "gateway"}
    assert selected_by_id["natural_language_freeform"].metric_status == "not_applicable"
    assert selected_by_id["ac1"].metric_status == "not_applicable"
    assert selected_by_id["ac2"].metric_status == "not_applicable"
    assert selected_by_id["ac1_dictionary"].metric_status != "not_applicable"
    assert selected_by_id["ac2_dictionary"].metric_status != "not_applicable"
    assert selected_by_id["awp1"].candidate_id in SELECTED_ALLOWLIST
    assert selected_by_id["natural_language_freeform"].transport_bytes is None
    assert selected_by_id["natural_language_freeform"].vs_natural_language_labelled.status == "not_applicable"
    assert selected_by_id["natural_language_freeform"].vs_natural_language_freeform.status == "not_applicable"
    assert selected_by_id["ac1"].vs_natural_language_labelled.status == "not_applicable"
    source_labelled = _row(_domain_report(report, "source"), candidate="natural_language")
    selected_labelled = _row(_domain_report(report, "selected_dictionary_transport"), candidate="natural_language")
    assert source_labelled.transport_bytes != selected_labelled.transport_bytes


def test_natural_language_labelled_baseline_uses_same_domain_message(api, monkeypatch):
    import agentcodec.agentwire.benchmark as benchmark_module
    from agentcodec.benchmark import render_natural_language as real_renderer

    seen = []

    def record_real_renderer(message):
        seen.append(message)
        return real_renderer(message)

    monkeypatch.setattr(benchmark_module, "render_natural_language", record_real_renderer)
    dictionary = _dictionary()
    one_case = _cases(api, _fixture_records()[:1])
    source = _scenario(api, one_case)
    selected = _scenario(
        api, one_case, domain="selected_dictionary_transport", dictionary=dictionary,
    )
    _run(api, (source, selected), monkeypatch, dictionary=dictionary)

    assert len(seen) == 2
    assert seen[0].model_dump() == one_case[0].source_message.model_dump()
    expected_transport = api.prepare_transport_message(one_case[0].source_message, dictionary)
    assert seen[1].model_dump() == expected_transport.model_dump()
    assert seen[1].dictionary_version == dictionary.version


def test_human_freeform_is_paired_and_manually_annotated(api):
    records = _fixture_records()
    assert len(records) == 20
    assert sum(record["case_id"].startswith("delegation-") for record in records) == 10
    assert sum(record["case_id"].startswith("workflow-") for record in records) == 10
    cases = _cases(api, records)
    for case, record in zip(cases, records, strict=True):
        assert case.case_id == record["case_id"]
        assert case.task_prompt == record["task_prompt"]
        assert case.source_message.model_dump() == record["source_message"]
        assert case.freeform_text == record["freeform_text"]
        assert case.freeform_text != __import__("agentcodec.benchmark", fromlist=["render_natural_language"]).render_natural_language(case.source_message)
        assert case.freeform_annotations["key_semantics"]
        assert case.freeform_annotations["hard_constraints"]
        assert case.synthetic_response_fixture


def test_prepare_transport_message_preserves_source_and_only_changes_selected_copy(api):
    source = MessageV1(
        message_id="transport-copy-check",
        message_type="delegate",
        task="Synthetic transport test.",
        status="pending",
        blocker=None,
        next_step="Keep source unchanged.",
        evidence=["synthetic-evidence"],
        constraints=["No external calls."],
        approval_required=False,
        user_id="synthetic-user",
        dictionary_version=None,
    )
    before = source.model_dump()
    selected = api.prepare_transport_message(source, _dictionary())

    assert source.model_dump() == before
    differences = {
        key for key in before
        if before[key] != selected.model_dump()[key]
    }
    assert differences == {"dictionary_version"}
    assert selected.dictionary_version == "synthetic-dictionary-v1"
    assert selected is not source


def test_non_null_conflicting_dictionary_version_rejects_domain(api):
    source = MessageV1(
        message_id="conflicting-version-check",
        message_type="delegate",
        task="Synthetic conflicting metadata.",
        status="pending",
        blocker=None,
        next_step="Reject the mismatch.",
        evidence=[],
        constraints=[],
        approval_required=False,
        user_id="synthetic-user",
        dictionary_version="different-synthetic-version",
    )
    with pytest.raises(ValueError):
        api.prepare_transport_message(source, _dictionary())
    assert source.dictionary_version == "different-synthetic-version"


def test_gateway_decodes_structured_and_labelled_conditions_to_same_domain_message(api, monkeypatch):
    dictionary = _dictionary()
    one_case = _cases(api, _fixture_records()[:1])
    scenarios = (
        _scenario(api, one_case),
        _scenario(api, one_case, domain="selected_dictionary_transport", dictionary=dictionary),
    )
    report = _run(api, scenarios, monkeypatch, dictionary=dictionary)

    source_report = _domain_report(report, "source")
    for candidate in ("natural_language", "json", "ac1", "ac2", "awp1"):
        row = _row(source_report, candidate=candidate)
        assert row.strict_decode_status == "decoded"
        assert row.full_field_equal_count == 1
        assert row.hard_field_equal_count == 1
    selected_report = _domain_report(report, "selected_dictionary_transport")
    for candidate in ("natural_language", "json", "awp1", "ac1_dictionary", "ac2_dictionary", "awp1_dictionary"):
        row = _row(selected_report, candidate=candidate)
        assert row.strict_decode_status == "decoded"
        assert row.full_field_equal_count == 1
        assert row.hard_field_equal_count == 1


def test_gateway_freeform_is_transport_only_and_equality_metrics_are_not_applicable(api, monkeypatch):
    import agentcodec.agentwire.benchmark as benchmark_module

    cases = _cases(api, _fixture_records()[:1])
    deliveries = []
    real_delivery = benchmark_module.deliver_benchmark_at_gateway

    def record_delivery(candidate_id, payload, receiver, dictionary=None):
        delivery = real_delivery(candidate_id, payload, receiver, dictionary=dictionary)
        if candidate_id == "natural_language_freeform":
            deliveries.append((payload, delivery))
        return delivery

    monkeypatch.setattr(benchmark_module, "deliver_benchmark_at_gateway", record_delivery)
    source_scenario = _scenario(api, cases)
    report = _run(api, (source_scenario,), monkeypatch)
    row = _row(_domain_report(report, "source"), candidate="natural_language_freeform")

    assert len(deliveries) == 1
    assert deliveries[0][0] == cases[0].freeform_text
    assert deliveries[0][1].decode_status == "pass_through"
    assert deliveries[0][1].pass_through_text == cases[0].freeform_text
    assert deliveries[0][1].model_context is None
    assert row.transport_bytes == len(cases[0].freeform_text.encode("utf-8"))
    assert row.transport_tokens == row.transport_bytes
    assert row.strict_decode_status == "not_applicable"
    assert row.full_field_equal_count is None
    assert row.hard_field_equal_count is None
    assert row.model_input_tokens is None
    assert row.synthetic_response_tokens is None
    assert row.distinct_text_event_tokens is None
    for field in (
        "candidate_protocol_exposure_tokens", "candidate_protocol_setup_history_tokens",
        "candidate_protocol_setup_tokens", "negotiation_tokens", "dictionary_exposure_tokens",
        "dictionary_history_tokens", "dictionary_setup_tokens", "request_tokens",
        "response_tokens", "retry_tokens", "fallback_tokens", "per_use_cost_tokens",
        "amortized_cost_tokens", "lifecycle_cost_tokens",
    ):
        assert getattr(row, field) is None
    assert row.metric_surface == "transport_only"


def test_gateway_freeform_comparison_uses_transport_delta_and_break_even(api, monkeypatch):
    cases = (_cases(api, _fixture_records()[:1])[0].model_copy(update={
        "freeform_text": "A manually written transport baseline. " * 100,
    }),)
    setup_text = "synthetic gateway protocol setup " * 1000
    scenario = _scenario(api, cases, states={"awp1": api.ProtocolState(
        candidate_id="awp1", profile_id=PROFILE_ID,
        state="cold_text_setup", setup_text=setup_text,
    )})
    report = _run(api, (scenario,), monkeypatch)
    domain = _domain_report(report, "source")
    candidate = _row(domain, candidate="awp1", mode="gateway", total_uses=10)
    baseline = _row(domain, candidate="natural_language_freeform", mode="gateway", total_uses=10)
    candidate_one = _row(domain, candidate="awp1", mode="gateway", total_uses=1)
    baseline_one = _row(domain, candidate="natural_language_freeform", mode="gateway", total_uses=1)
    candidate_per_use = (candidate.transport_tokens - candidate_one.transport_tokens) // 9
    baseline_per_use = baseline_one.transport_tokens
    candidate_setup = candidate_one.transport_tokens - candidate_per_use
    expected = api.calculate_break_even(candidate_setup, candidate_per_use, 0, baseline_per_use)

    assert candidate.vs_natural_language_freeform.cost_delta_tokens == (
        candidate.transport_tokens - baseline.transport_tokens
    )
    assert candidate.vs_natural_language_freeform.status == expected.status == "finite"
    assert candidate.vs_natural_language_freeform.break_even_status == expected.status
    assert candidate.vs_natural_language_freeform.first_strict_win == expected.first_strict_win
    assert baseline.metric_surface == "transport_only"
    assert baseline.model_input_tokens is None
    assert baseline.synthetic_response_tokens is None
    assert baseline.lifecycle_cost_tokens is None


def test_gateway_freeform_comparison_fails_closed_for_unknown_transport_cost(api, monkeypatch):
    cases = _cases(api, _fixture_records()[:1])
    scenario = _scenario(api, cases, states={"awp1": api.ProtocolState(
        candidate_id="awp1", profile_id=PROFILE_ID,
        state="unknown_history", history_complete=False,
    )})
    report = _run(api, (scenario,), monkeypatch)
    row = _row(_domain_report(report, "source"), candidate="awp1", mode="gateway")

    assert row.metric_status == "cannot_compare"
    assert row.transport_tokens is None
    assert row.vs_natural_language_freeform.status == "cannot_compare"
    assert row.vs_natural_language_freeform.cost_delta_tokens is None
    assert row.vs_natural_language_freeform.break_even_status == "cannot_compare"
    assert row.vs_natural_language_freeform.first_strict_win is None


def test_benchmark_runner_only_measures_requested_receiver_modes(api, monkeypatch):
    cases = _cases(api, _fixture_records()[:1])
    _byte_meter(monkeypatch)

    def forbidden_direct_request(*args, **kwargs):
        pytest.fail("unselected model_direct mode performed benchmark work")

    monkeypatch.setattr(api, "build_direct_model_request", forbidden_direct_request)
    report = api.run_agentwire_benchmark(
        (_scenario(api, cases),), {PROFILE_ID: _profile()}, modes=("gateway",),
    )

    assert report.scenarios
    assert all(row.receiver_mode == "gateway" for scenario in report.scenarios for row in scenario.rows)


def test_reuse_counts_do_not_retokenize_invariant_event_text(api, monkeypatch):
    observed = _byte_meter(monkeypatch)
    cases = _cases(api, _fixture_records()[:1])
    api.run_agentwire_benchmark((_scenario(api, cases),), {PROFILE_ID: _profile()}, modes=("gateway",))

    signatures = [
        (profile.encoding_name, attempt.attempt_kind,
         tuple((component.kind, component.text, component.resource_identity)
               for component in attempt.components))
        for attempt, profile in observed
    ]
    assert len(signatures) == len(set(signatures))


def test_direct_mode_counts_common_prompt_context_and_response_fixture_for_freeform(api, monkeypatch):
    import agentcodec.agentwire.benchmark as benchmark_module

    cases = _cases(api, _fixture_records()[:1])
    seen_requests = []
    real_builder = benchmark_module.build_direct_model_request

    def recording_builder(context_prefix, candidate_id, wire_payload, dictionary=None):
        result = real_builder(context_prefix, candidate_id, wire_payload, dictionary=dictionary)
        seen_requests.append((context_prefix, candidate_id, wire_payload, result))
        return result

    monkeypatch.setattr(benchmark_module, "build_direct_model_request", recording_builder)
    observed = _byte_meter(monkeypatch)
    report = api.run_agentwire_benchmark(
        (_scenario(api, cases),), {PROFILE_ID: _profile()}, dictionary=None,
    )
    row = _row(_domain_report(report, "source"), candidate="natural_language_freeform", mode="model_direct")
    matching = [item for item in seen_requests if item[1] == "natural_language_freeform"]

    assert len(matching) == 1
    prompt, _, payload, complete_request = matching[0]
    assert prompt == cases[0].task_prompt
    assert payload == cases[0].freeform_text
    assert prompt in complete_request and payload in complete_request
    assert row.model_input_tokens == len(complete_request.encode("utf-8"))
    assert row.synthetic_response_tokens == len(cases[0].synthetic_response_fixture.encode("utf-8"))
    measured_texts = ["".join(part.text for part in attempt.components) for attempt, _ in observed]
    assert complete_request in measured_texts
    assert cases[0].synthetic_response_fixture in measured_texts
    assert row.vs_natural_language_labelled.status in {"finite", "never", "cannot_compare"}
    assert row.vs_natural_language_freeform.status in {"finite", "never", "cannot_compare"}


def test_protocol_state_is_candidate_scoped_and_dictionary_history_is_separate(api, monkeypatch):
    cases = _cases(api, _fixture_records()[:1])
    history = _event("synthetic-dictionary-history", "dictionary_distribution", "dictionary_transfer", "synthetic glossary delivered")
    states = {
        "json": api.ProtocolState(
            candidate_id="json", profile_id=PROFILE_ID, state="cold_text_setup",
            setup_text="Synthetic JSON protocol setup.",
        ),
        "ac1": api.ProtocolState(
            candidate_id="ac1", profile_id=PROFILE_ID, state="ready_history",
            history=(_event("synthetic-ac1-ready", "setup", "setup", "complete AC1 ready history"),),
        ),
        "ac2": api.ProtocolState(
            candidate_id="ac2", profile_id=PROFILE_ID, state="unknown_history",
            history_complete=False,
        ),
    }
    source = _scenario(api, cases, states=states)
    dictionary = _dictionary()
    selected = _scenario(
        api, cases, domain="selected_dictionary_transport", dictionary=dictionary,
        dictionary_exposure=api.DictionaryExposure(state="ready_history"),
        dictionary_history=(history,),
    )
    report = _run(api, (source, selected), monkeypatch, dictionary=dictionary)
    source_rows = {row.candidate_id: row for row in _domain_report(report, "source").rows if row.receiver_mode == "gateway"}
    selected_rows = {row.candidate_id: row for row in _domain_report(report, "selected_dictionary_transport").rows if row.receiver_mode == "gateway"}

    assert source_rows["json"].candidate_protocol_setup_tokens == len("Synthetic JSON protocol setup.".encode())
    assert source_rows["ac1"].candidate_protocol_setup_history_tokens == len("complete AC1 ready history".encode())
    assert source_rows["ac1"].candidate_protocol_setup_tokens == 0
    assert source_rows["ac2"].candidate_protocol_setup_history_tokens is None
    assert source_rows["ac2"].candidate_protocol_setup_tokens is None
    assert source_rows["ac2"].lifecycle_cost_tokens is None
    assert selected_rows["natural_language"].dictionary_history_tokens == 0
    assert selected_rows["ac1_dictionary"].dictionary_history_tokens == len("synthetic glossary delivered".encode())


def test_same_instance_and_whole_corpus_reuse_have_distinct_denominators(api, monkeypatch):
    records = _fixture_records()
    all_cases = _cases(api, records)
    one_case = (all_cases[0],)
    same_message = _scenario(api, one_case, unit="same_message_instance", scope_id="scope-one-instance")
    whole_corpus = _scenario(api, all_cases, unit="whole_corpus_pass", scope_id="scope-whole-corpus")
    report = _run(api, (same_message, whole_corpus), monkeypatch)

    single_rows = [row for row in _domain_report(report, "source").rows if row.candidate_id == "natural_language" and row.receiver_mode == "gateway" and row.reuse_unit == "same_message_instance"]
    corpus_report = next(item for item in report.scenarios if item.reuse_unit == "whole_corpus_pass")
    corpus_rows = [row for row in corpus_report.rows if row.candidate_id == "natural_language" and row.receiver_mode == "gateway"]
    assert {row.case_count for row in single_rows} == {1}
    assert {row.total_uses for row in single_rows} == {1, 10, 50}
    single_by_uses = {row.total_uses: row for row in single_rows}
    assert single_by_uses[10].event_count == 10 * single_by_uses[1].event_count
    assert {row.case_count for row in corpus_rows} == {20}
    assert {row.total_uses for row in corpus_rows} == {1, 10, 50}
    corpus_by_uses = {row.total_uses: row for row in corpus_rows}
    assert corpus_by_uses[10].event_count == 10 * corpus_by_uses[1].event_count
    assert all(row.lifecycle_cost_tokens == row.amortized_cost_tokens * row.total_uses for row in corpus_rows)


def test_scope_mismatch_never_amortizes_across_users_receivers_domains_or_dictionaries(api, monkeypatch):
    dictionary = _dictionary()
    cases = _cases(api, _fixture_records()[:1])
    setup = _event("same-local-history-id", "setup", "setup", "complete synthetic profile history")
    first = _scenario(api, cases, states={"json": api.ProtocolState(candidate_id="json", profile_id=PROFILE_ID, state="ready_history", history=(setup,))}, scope_id="scope-a")
    second = _scenario(api, cases, states={"json": api.ProtocolState(candidate_id="json", profile_id=PROFILE_ID, state="ready_history", history=(setup,))}, scope_id="scope-b")
    selected = _scenario(api, cases, domain="selected_dictionary_transport", dictionary=dictionary, scope_id="scope-a")
    report = _run(api, (first, second, selected), monkeypatch, dictionary=dictionary)

    histories = [
        _row(item, candidate="json", total_uses=1).candidate_protocol_setup_history_tokens
        for item in report.scenarios
        if item.metadata_domain == "source" and item.reuse_unit == "same_message_instance"
    ]
    assert histories == [len("complete synthetic profile history".encode())] * 2
    assert report.scenarios[0] != report.scenarios[1]
    assert _domain_report(report, "selected_dictionary_transport").metadata_domain != "source"


def test_unknown_history_tokenizer_and_reuse_never_become_zero_cost(api, monkeypatch):
    cases = _cases(api, _fixture_records()[:1])
    unknown = _scenario(
        api, cases,
        states={"json": api.ProtocolState(candidate_id="json", profile_id=PROFILE_ID, state="unknown_history", history_complete=False)},
    )
    report = _run(api, (unknown,), monkeypatch, profiles={})
    row = _row(_domain_report(report, "source"), candidate="json")
    assert row.metric_status == "cannot_compare"
    assert row.transport_tokens is None
    assert row.candidate_protocol_setup_history_tokens is None
    assert row.lifecycle_cost_tokens is None
    assert row.vs_natural_language_labelled.cost_delta_tokens is None
    assert row.vs_natural_language_labelled.status == "cannot_compare"


def test_break_even_comparisons_are_separate_and_labelled_is_not_real_freeform(api, monkeypatch):
    cases = _cases(api, _fixture_records()[:1])
    report = _run(api, (_scenario(api, cases),), monkeypatch)
    row = _row(_domain_report(report, "source"), candidate="awp1", mode="model_direct")

    assert hasattr(row, "vs_natural_language_labelled")
    assert hasattr(row, "vs_natural_language_freeform")
    assert row.vs_natural_language_labelled is not row.vs_natural_language_freeform
    assert row.vs_natural_language_labelled.cost_delta_tokens is not None
    assert row.vs_natural_language_freeform.cost_delta_tokens is not None
    assert "real natural language" not in row.labelled_baseline_description.lower()


def test_report_is_deterministic_and_redacts_payload_user_scope_and_content_hashes(api, monkeypatch):
    cases = _cases(api, _fixture_records()[:1])
    scenario = _scenario(api, cases, scope_id="secret-synthetic-scope")
    first = _run(api, (scenario,), monkeypatch)
    second = _run(api, (scenario,), monkeypatch)
    serialized = first.model_dump_json()

    assert first == second
    assert "secret-synthetic-scope" not in serialized
    assert "synthetic-user" not in serialized
    assert cases[0].task_prompt not in serialized
    assert cases[0].freeform_text not in serialized
    assert cases[0].source_message.task not in serialized
    assert "input_content_hash" not in serialized
    assert "payload_sha256" not in serialized
    assert first.version == "agentwire-benchmark-v1"


def test_unknown_freeform_equality_metrics_are_null_not_zero(api, monkeypatch):
    cases = _cases(api, _fixture_records()[:1])
    report = _run(api, (_scenario(api, cases),), monkeypatch)
    source_report = _domain_report(report, "source")
    direct = _row(source_report, candidate="natural_language_freeform", mode="model_direct")
    gateway = _row(source_report, candidate="natural_language_freeform", mode="gateway")

    for row in (direct, gateway):
        assert row.strict_decode_status == "not_applicable"
        assert row.full_field_equal_count is None
        assert row.hard_field_equal_count is None
    assert gateway.metric_status == "transport_only"
    assert gateway.model_input_tokens is None
    assert gateway.synthetic_response_tokens is None
    assert direct.model_input_tokens is not None
    assert direct.synthetic_response_tokens is not None


def test_direct_format_and_glossary_text_are_charged_per_request_but_cold_setup_once(api, monkeypatch):
    import agentcodec.agentwire.benchmark as benchmark_module

    dictionary = _dictionary()
    cases = _cases(api, _fixture_records()[:1])
    states = {
        "awp1_dictionary": api.ProtocolState(
            candidate_id="awp1_dictionary", profile_id=PROFILE_ID,
            state="cold_text_setup", setup_text="Synthetic AWP1 instructions delivered once.",
        ),
    }
    scenario = _scenario(
        api, cases, domain="selected_dictionary_transport", dictionary=dictionary,
        states=states,
        dictionary_exposure=api.DictionaryExposure(
            state="cold_text_setup", setup_text="Synthetic dictionary transferred once."
        ),
    )
    built = []
    real_builder = benchmark_module.build_direct_model_request

    def record_builder(context_prefix, candidate_id, wire_payload, dictionary=None):
        text = real_builder(context_prefix, candidate_id, wire_payload, dictionary=dictionary)
        if candidate_id == "awp1_dictionary":
            built.append(text)
        return text

    monkeypatch.setattr(benchmark_module, "build_direct_model_request", record_builder)
    report = _run(api, (scenario,), monkeypatch, dictionary=dictionary)
    selected_report = _domain_report(report, "selected_dictionary_transport")
    rows = {
        uses: _row(selected_report, candidate="awp1_dictionary", mode="model_direct", total_uses=uses)
        for uses in (1, 10, 50)
    }

    assert len(built) == 1
    assert "Format-specific instructions:" in built[0]
    assert "Selected dictionary glossary" in built[0]
    for phrase in dictionary.aliases.values():
        assert json.dumps(phrase, ensure_ascii=False) in built[0]
    assert rows[1].candidate_protocol_setup_tokens == len("Synthetic AWP1 instructions delivered once.".encode())
    assert rows[10].candidate_protocol_setup_tokens == rows[1].candidate_protocol_setup_tokens
    assert rows[1].dictionary_setup_tokens == len("Synthetic dictionary transferred once.".encode())
    assert rows[10].dictionary_setup_tokens == rows[1].dictionary_setup_tokens
    assert rows[10].dictionary_exposure_tokens == 10 * rows[1].dictionary_exposure_tokens
    assert rows[10].candidate_protocol_exposure_tokens == 10 * rows[1].candidate_protocol_exposure_tokens
    assert rows[10].lifecycle_cost_tokens - rows[1].lifecycle_cost_tokens == 9 * rows[1].per_use_cost_tokens


def test_unknown_negotiation_and_unknown_dictionary_history_do_not_become_zero(api, monkeypatch):
    cases = _cases(api, _fixture_records()[:1])
    dictionary = _dictionary()
    source = _scenario(
        api, cases,
        states={"json": api.ProtocolState(
            candidate_id="json", profile_id=PROFILE_ID,
            state="out_of_band_preinstalled", negotiation_required=True,
        ), "ac1": api.ProtocolState(
            candidate_id="ac1", profile_id=PROFILE_ID,
            state="out_of_band_preinstalled", negotiation_required=True,
            negotiation_text="Full synthetic capability negotiation text.",
        )},
    )
    selected = _scenario(
        api, cases, domain="selected_dictionary_transport", dictionary=dictionary,
        dictionary_exposure=api.DictionaryExposure(
            state="unknown_history", history_complete=False,
        ),
    )
    report = _run(api, (source, selected), monkeypatch, dictionary=dictionary)
    json_row = _row(_domain_report(report, "source"), candidate="json")
    ac1_row = _row(_domain_report(report, "source"), candidate="ac1")
    dictionary_row = _row(
        _domain_report(report, "selected_dictionary_transport"), candidate="ac1_dictionary",
    )

    assert json_row.metric_status == "cannot_compare"
    assert json_row.negotiation_tokens is None
    assert json_row.lifecycle_cost_tokens is None
    assert ac1_row.negotiation_tokens == len("Full synthetic capability negotiation text.".encode())
    assert dictionary_row.metric_status == "cannot_compare"
    assert dictionary_row.dictionary_history_tokens is None
    assert dictionary_row.dictionary_setup_tokens is None
    assert dictionary_row.lifecycle_cost_tokens is None


def test_each_retry_and_fallback_history_event_is_counted_without_real_fallback(api, monkeypatch):
    cases = _cases(api, _fixture_records()[:1])
    history = (
        _event("synthetic-retry-1", "retry", "retry", "synthetic retry attempt"),
        _event("synthetic-fallback-1", "fallback", "fallback", "synthetic fallback record"),
    )
    scenario = _scenario(
        api, cases,
        states={"json": api.ProtocolState(
            candidate_id="json", profile_id=PROFILE_ID,
            state="ready_history", history=history,
        )},
    )
    report = _run(api, (scenario,), monkeypatch)
    row = _row(_domain_report(report, "source"), candidate="json")

    assert row.retry_tokens == len("synthetic retry attempt".encode())
    assert row.fallback_tokens == len("synthetic fallback record".encode())
    assert row.event_count >= 4
    assert row.strict_decode_status == "decoded"
    assert "model" not in report.notes[1].lower() or "no model" in report.notes[1].lower()


def test_unknown_direct_request_text_returns_null_not_zero(api, monkeypatch):
    import agentcodec.agentwire.benchmark as benchmark_module

    cases = _cases(api, _fixture_records()[:1])
    real_builder = benchmark_module.build_direct_model_request

    def unknown_freeform_request(context_prefix, candidate_id, wire_payload, dictionary=None):
        if candidate_id == "natural_language_freeform":
            raise ValueError("synthetic request text unavailable")
        return real_builder(context_prefix, candidate_id, wire_payload, dictionary=dictionary)

    monkeypatch.setattr(benchmark_module, "build_direct_model_request", unknown_freeform_request)
    report = _run(api, (_scenario(api, cases),), monkeypatch)
    direct = _row(_domain_report(report, "source"), candidate="natural_language_freeform", mode="model_direct")
    gateway = _row(_domain_report(report, "source"), candidate="natural_language_freeform", mode="gateway")

    assert direct.metric_status == "cannot_compare"
    assert direct.transport_bytes is None
    assert direct.model_input_tokens is None
    assert direct.synthetic_response_tokens is None
    assert direct.request_tokens is None
    assert direct.lifecycle_cost_tokens is None
    assert gateway.metric_status == "transport_only"
    assert gateway.transport_bytes == len(cases[0].freeform_text.encode("utf-8"))


def test_unknown_reuse_unit_is_not_assumed_to_be_one_or_a_corpus_pass(api, monkeypatch):
    cases = _cases(api, _fixture_records()[:1])
    scenario = _scenario(api, cases, unit="unknown-repeat-semantics")
    report = _run(api, (scenario,), monkeypatch)
    row = _row(_domain_report(report, "source"), candidate="json")

    assert row.metric_status == "cannot_compare"
    assert row.per_use_cost_tokens is None
    assert row.amortized_cost_tokens is None
    assert row.lifecycle_cost_tokens is None
    assert row.vs_natural_language_labelled.status == "cannot_compare"


def test_no_amortization_crosses_user_domain_profile_or_dictionary_identity(api, monkeypatch):
    records_a = _fixture_records()[:1]
    records_b = _fixture_records()[:1]
    records_b[0]["source_message"]["user_id"] = "synthetic-user-b"
    cases_a = _cases(api, records_a)
    cases_b = _cases(api, records_b)
    history = _event("same-synthetic-history-id", "setup", "setup", "complete isolated profile history")
    state_a = api.ProtocolState(
        candidate_id="json", profile_id=PROFILE_ID, state="ready_history", history=(history,),
    )
    second_profile_id = "synthetic-second-profile"
    state_b = api.ProtocolState(
        candidate_id="json", profile_id=second_profile_id, state="ready_history", history=(history,),
    )
    source_a = _scenario(api, cases_a, states={"json": state_a, "json-profile-b": state_b}, scope_id="shared-synthetic-scope")
    # Use a second receiver/user and independently formed state with the same event ID.
    source_b = _scenario(
        api, cases_b, states={"json": state_a}, scope_id="shared-synthetic-scope",
        receiver_user="synthetic-user-b",
    )
    dictionary = _dictionary()
    selected = _scenario(
        api, cases_a, domain="selected_dictionary_transport", dictionary=dictionary,
        scope_id="shared-synthetic-scope",
    )
    profiles = {
        PROFILE_ID: _profile(),
        second_profile_id: TokenizerProfile(
            encoding_name="synthetic-second-byte-count", library_version="0.0.synthetic-two",
            vocabulary_sha256="1" * 64, accuracy="exact_local_text",
            mapping_basis="test-only deterministic UTF-8 byte meter",
        ),
    }
    report = _run(api, (source_a, source_b, selected), monkeypatch, dictionary=dictionary, profiles=profiles)
    source_groups = [item for item in report.scenarios if item.metadata_domain == "source"]
    assert len(source_groups) == 4
    history_costs = [
        _row(item, candidate="json", mode="gateway").candidate_protocol_setup_history_tokens
        for item in source_groups
        if any(row.candidate_id == "json" and row.metric_status != "not_applicable" for row in item.rows)
    ]
    expected_history = len("complete isolated profile history".encode())
    assert history_costs.count(expected_history) == 3
    assert history_costs.count(None) == 1
    assert len({item.source_hash for item in report.scenarios}) == len(report.scenarios)
    assert len({item.transport_hash for item in report.scenarios}) == len(report.scenarios)

    dictionary_b = UserDictionary(
        user_id=dictionary.user_id, version="synthetic-dictionary-v2",
        aliases={"A": "A second synthetic phrase."},
    )
    selected_b = _scenario(
        api, cases_a, domain="selected_dictionary_transport", dictionary=dictionary_b,
        scope_id="shared-synthetic-scope",
    )
    report_b = _run(api, (selected_b,), monkeypatch, dictionary=dictionary_b, profiles={PROFILE_ID: _profile()})
    selected_profile_a = next(
        item for item in report.scenarios
        if item.metadata_domain == "selected_dictionary_transport"
        and any(row.tokenizer_profile_id == PROFILE_ID for row in item.rows)
    )
    assert report_b.scenarios[0].transport_hash != selected_profile_a.transport_hash


def test_source_domain_rejects_dictionary_history_instead_of_borrowing_setup(api):
    cases = _cases(api, _fixture_records()[:1])
    history = _event("source-domain-dictionary-history", "dictionary_distribution", "dictionary_transfer", "synthetic dictionary text")
    with pytest.raises(ValueError):
        _scenario(
            api, cases,
            dictionary_exposure=api.DictionaryExposure(state="ready_history"),
            dictionary_history=(history,),
        )


def test_gateway_decode_failure_keeps_attempt_cost_but_blocks_lifecycle_comparison(api, monkeypatch):
    import agentcodec.agentwire.benchmark as benchmark_module

    cases = _cases(api, _fixture_records()[:1])
    failed_payload = "{broken synthetic JSON"
    monkeypatch.setattr(benchmark_module, "render_json", lambda message: failed_payload)
    report = _run(api, (_scenario(api, cases),), monkeypatch)
    row = _row(_domain_report(report, "source"), candidate="json", mode="gateway")

    assert row.strict_decode_status == "fallback_required"
    assert row.metric_status == "cannot_compare"
    assert row.case_count == 1
    assert row.event_count == 1
    assert row.transport_bytes == len(failed_payload.encode("utf-8"))
    assert row.transport_tokens == len(failed_payload.encode("utf-8"))
    assert row.request_tokens == len(failed_payload.encode("utf-8"))
    assert row.model_input_tokens is None
    assert row.synthetic_response_tokens is None
    assert row.response_tokens is None
    assert row.retry_tokens is None
    assert row.fallback_tokens is None
    assert row.distinct_text_event_tokens is None
    assert row.per_use_cost_tokens is None
    assert row.amortized_cost_tokens is None
    assert row.lifecycle_cost_tokens is None
    assert row.vs_natural_language_labelled.status == "cannot_compare"
    assert row.vs_natural_language_labelled.cost_delta_tokens is None
    assert row.vs_natural_language_labelled.break_even_status == "cannot_compare"


def test_cold_protocol_setup_with_incomplete_history_is_not_exact(api, monkeypatch):
    cases = _cases(api, _fixture_records()[:1])
    partial_history = _event(
        "synthetic-partial-protocol-history", "setup", "setup",
        "partial synthetic protocol history",
    )
    scenario = _scenario(
        api, cases,
        states={"json": api.ProtocolState(
            candidate_id="json", profile_id=PROFILE_ID,
            state="cold_text_setup", setup_text="Synthetic protocol setup text.",
            history=(partial_history,), history_complete=False,
        )},
    )
    report = _run(api, (scenario,), monkeypatch)
    row = _row(_domain_report(report, "source"), candidate="json", mode="gateway")

    assert row.metric_status == "cannot_compare"
    assert row.candidate_protocol_setup_history_tokens is None
    assert row.candidate_protocol_setup_tokens is None
    assert row.lifecycle_cost_tokens is None
    assert row.amortized_cost_tokens is None
    assert row.vs_natural_language_labelled.status == "cannot_compare"


def test_cold_dictionary_exposure_with_incomplete_history_is_not_exact(api, monkeypatch):
    cases = _cases(api, _fixture_records()[:1])
    dictionary = _dictionary()
    partial_history = _event(
        "synthetic-partial-dictionary-history", "dictionary_distribution",
        "dictionary_transfer", "partial synthetic dictionary history",
    )
    scenario = _scenario(
        api, cases, domain="selected_dictionary_transport", dictionary=dictionary,
        dictionary_exposure=api.DictionaryExposure(
            state="cold_text_setup", setup_text="Synthetic dictionary setup text.",
            history_complete=False,
        ),
        dictionary_history=(partial_history,),
    )
    report = _run(api, (scenario,), monkeypatch, dictionary=dictionary)
    row = _row(
        _domain_report(report, "selected_dictionary_transport"),
        candidate="ac1_dictionary", mode="gateway",
    )

    assert row.metric_status == "cannot_compare"
    assert row.dictionary_history_tokens is None
    assert row.dictionary_setup_tokens is None
    assert row.lifecycle_cost_tokens is None
    assert row.amortized_cost_tokens is None
    assert row.vs_natural_language_labelled.status == "cannot_compare"

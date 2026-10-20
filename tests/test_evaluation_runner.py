"""Runner regressions: fixed identities, independent oracles, and bounded recovery."""

import builtins
from contextlib import contextmanager
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess

import pytest

from agentcodec import benchmark
from agentcodec.benchmark import BenchmarkScenario, DEMO_DICTIONARY, load_tasks
from agentcodec.evaluation.contracts import AdapterResponse, EvalError, EvalPolicy
from agentcodec.evaluation.mock import DeterministicMockAdapter
from agentcodec.schema import MessageV1, UserDictionary


ROOT = Path(__file__).resolve().parents[1]
FORMATS = ("natural_language", "json", "ac1", "ac2")
CONDITIONS = (*FORMATS, "ac1_dictionary", "ac2_dictionary")
HASHES = {
    "diverse": "9284dc0b7a878355c6e8ddc0195cd7adcade9b4966dc3a07aeb58f2f1f2ad7b8",
    "repetitive": "f5c8d2d98837edd5944f10e6fb7a63bd44dfcff6b022aab57502b2197cd239fe",
    "repetitive_dictionary": "9b320df2e125804c07dc53418164633345c4d26ff4e56406e42b3fcd94338d86",
}
DATA = {
    "message_id": "m1", "message_type": "delegate", "task": "Do not publish.",
    "status": "completed", "blocker": None, "next_step": "",
    "evidence": ["local://evidence", "", "local://evidence"],
    "constraints": ["Budget <= $25.", "Do not call external APIs."],
    "approval_required": False, "user_id": "demo-user", "dictionary_version": None,
}


def api():
    return importlib.import_module("agentcodec.evaluation.runner")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def local_scenarios(**changes):
    return {"diverse": BenchmarkScenario(tasks=[MessageV1(**(DATA | changes))],
                                         dictionary=DEMO_DICTIONARY.model_copy(deep=True))}


@pytest.fixture
def real_scenarios():
    paths = {"diverse": ROOT / "bench/tasks.v1.jsonl",
             "repetitive": ROOT / "bench/tasks-repetitive.v1.jsonl",
             "repetitive_dictionary": ROOT / "bench/dictionaries/repeat-v1.json"}
    assert {name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in paths.items()} == HASHES
    dictionary = UserDictionary.model_validate_json(paths["repetitive_dictionary"].read_bytes())
    return {"diverse": BenchmarkScenario(tasks=load_tasks(paths["diverse"]), dictionary=DEMO_DICTIONARY),
            "repetitive": BenchmarkScenario(tasks=load_tasks(paths["repetitive"]), dictionary=dictionary)}


def forbidden(*args, **kwargs):
    pytest.fail("offline runner attempted a forbidden external effect")


@pytest.fixture(autouse=True)
def offline_guards(monkeypatch):
    # Any unexpected I/O/tokenizer path fails before it can do external work.
    for owner, names in (
        (socket, ("socket", "create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr")),
        (subprocess, ("Popen", "run", "call", "check_call", "check_output")),
        (os, ("system", "popen")),
        (benchmark, ("_get_encoding", "count_tokens")),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)
    import tiktoken
    monkeypatch.setattr(tiktoken, "get_encoding", forbidden)
    monkeypatch.setattr(tiktoken, "encoding_for_model", forbidden)


@contextmanager
def no_environment(monkeypatch):
    # Only wrap the operation under test: pytest itself reads its own environment.
    with monkeypatch.context() as guard:
        guard.setattr(os, "getenv", forbidden)
        guard.setattr(type(os.environ), "__getitem__", forbidden)
        guard.setattr(type(os.environ), "__iter__", forbidden)
        yield


class ScriptedMockAdapter:
    adapter_id = "scripted-mock-test-v1"

    def __init__(self, script):
        self.script = script
        self.requests = []

    def invoke(self, request):
        self.requests.append(request.model_copy(deep=True))
        step = self.script.get((request.case_id, request.attempt_index))
        if callable(step):
            return step(request)
        return step if step is not None else DeterministicMockAdapter().invoke(request)


def failure(stage="adapter", retryable=True):
    return AdapterResponse(status="error", raw_output=None,
                           error=EvalError(stage=stage, code="injected_failure",
                                           message="Injected local failure.", retryable=retryable))


def corrupted(request):
    return AdapterResponse(status="ok", raw_output="{", error=None)


def altered(**changes):
    def respond(request):
        response = DeterministicMockAdapter().invoke(request)
        receipt = json.loads(response.raw_output)
        receipt["message"].update(changes)
        receipt["message_sha256"] = digest(receipt["message"])
        return AdapterResponse(status="ok", raw_output=canonical(receipt), error=None)
    return respond


def recovery_case(*, paired=False, mode="local_original_json", retries=1, **changes):
    cases = api().build_cases(local_scenarios(**changes), dictionary_pairs="paired" if paired else "none",
                              policy=EvalPolicy(fallback_mode=mode, max_retries=retries))
    return cases[-1]


def execute(case, script):
    case_id = f"diverse/m1/{case.condition}"
    adapter = ScriptedMockAdapter({(case_id, index): response for index, response in script.items()})
    manifest, records, report = api().run_offline_evaluation([case], adapter, {"diverse": "local-hash"}, "rev-test")
    return adapter, manifest, records, report, report["scenarios"]["diverse"]["conditions"][case.condition]


def assert_clean(condition, count):
    assert condition["logical_case_count"] == count
    assert condition["attempt_count"] == count
    for phase in ("initial", "final"):
        score = condition[phase]
        assert score["mock_task_completed_count"] == count
        assert score["mock_task_completed_denominator"] == count
        assert score["mock_task_completed_rate"] == 1.0
        assert score["field_equal_counts"] == dict.fromkeys(DATA, count)
        assert score["equal_hard_total"] == 6 * count
        assert score["hard_field_preservation_denominator"] == 6 * count
        assert score["changed_hard_field_count"] == score["unrecovered_hard_field_count"] == 0
    for metric in ("fallback_required_case", "fallback_performed_case", "fallback_recovered_case", "retried_case"):
        assert condition[metric + "_count"] == 0
        assert condition[metric + "_denominator"] == count
        assert condition[metric + "_rate"] == 0.0
    assert condition["retry_attempt_count"] == condition["retry_recovered_case_count"] == 0
    assert condition["retry_recovered_case_rate"] is None
    assert condition["fallback_recovered_case_rate"] == 0.0


def test_real_corpora_four_formats_keep_two_groups(real_scenarios, monkeypatch):
    # Catches merged scenarios, duplicate-task collapse, implicit metadata, or lost fields.
    before = {key: value.model_dump() for key, value in real_scenarios.items()}
    with no_environment(monkeypatch):
        cases = api().build_cases(dict(reversed(list(real_scenarios.items()))), FORMATS)
        manifest, records, report = api().run_offline_evaluation(cases, DeterministicMockAdapter(), HASHES, "rev-test")
    assert len(cases) == len(records) == 240
    assert list(report["scenarios"]) == ["diverse", "repetitive"]
    for name in ("diverse", "repetitive"):
        selected = [case for case in cases if case.scenario == name]
        assert len(selected) == 120
        assert len({case.source_message.message_id for case in selected}) == 30
        assert list(report["scenarios"][name]["conditions"]) == list(FORMATS)
        for condition in report["scenarios"][name]["conditions"].values():
            assert_clean(condition, 30)
            assert all(result["final_oracle"] == "transport" for result in condition["case_results"])
    assert [record.source_message_id for record in records[:4]] == ["s01"] * 4
    assert [record.condition for record in records[:4]] == list(FORMATS)
    assert all(record.record_id == record.request.case_id + "/0" for record in records)
    assert all(record.request_sha256 == digest(record.request.model_dump()) for record in records)
    assert all(record.response is not None and record.decoded_message is not None for record in records)
    assert report["acceptance_passed"] is True
    assert report["acceptance_contract"] == "clean-v1"
    assert report["pipeline_only"] is True and report["adapter_kind"] == "mock"
    assert manifest["input_hashes"] == HASHES
    assert manifest["code_revision"] == "rev-test"
    assert manifest["scenario_counts"] == {"diverse": 30, "repetitive": 30}
    assert manifest["run_id"] == digest({key: value for key, value in manifest.items() if key != "run_id"})
    assert before == {key: value.model_dump() for key, value in real_scenarios.items()}


def test_paired_dictionaries_add_only_codec_cases(real_scenarios):
    # Catches adding plain-format pairs, implicitly selecting AC2, or changing sources.
    before = {key: value.model_dump() for key, value in real_scenarios.items()}
    cases = api().build_cases(real_scenarios, tuple(reversed(FORMATS)), "paired")
    assert len(cases) == 360
    manifest, records, report = api().run_offline_evaluation(cases, DeterministicMockAdapter(), HASHES, "rev-test")
    for name, scenario in real_scenarios.items():
        selected = [case for case in cases if case.scenario == name]
        assert len(selected) == 180
        assert [case.condition for case in selected[:6]] == list(CONDITIONS)
        for condition in report["scenarios"][name]["conditions"].values():
            assert_clean(condition, 30)
        for ac1, ac2 in zip(selected[4::6], selected[5::6]):
            assert ac1.dictionary.model_dump() == ac2.dictionary.model_dump() == scenario.dictionary.model_dump()
            assert ac1.source_message.dictionary_version is None
            assert ac1.transport_message.dictionary_version == scenario.dictionary.version
            assert ac1.metadata_selection.model_dump() == ac2.metadata_selection.model_dump()
        snapshot = manifest["dictionaries"][name]
        assert snapshot["snapshot"] == scenario.dictionary.model_dump()
        assert snapshot["sha256"] == digest(scenario.dictionary.model_dump())
    assert len(records) == 360 and report["acceptance_passed"] is True
    assert {case.condition for case in api().build_cases(real_scenarios, dictionary_pairs="paired")} == {"ac1", "ac1_dictionary"}
    assert before == {key: value.model_dump() for key, value in real_scenarios.items()}


@pytest.mark.parametrize("source_version,status,transport_version", [
    (None, "applied_to_null_version", "demo-v1"),
    ("demo-v1", "preserved_non_null_version", "demo-v1"),
    ("legacy-v0", "preserved_non_null_version", "legacy-v0"),
])
def test_metadata_selection_three_states_remain_auditable(source_version, status, transport_version):
    cases = api().build_cases(local_scenarios(dictionary_version=source_version), dictionary_pairs="paired")
    plain, paired = cases
    assert plain.metadata_selection.model_dump() == {
        "selection_status": "no_dictionary", "selected_dictionary_user_id": None,
        "selected_dictionary_version": None, "source_dictionary_version": source_version,
        "transport_dictionary_version": source_version,
    }
    assert paired.metadata_selection.model_dump() == {
        "selection_status": status, "selected_dictionary_user_id": "demo-user",
        "selected_dictionary_version": "demo-v1", "source_dictionary_version": source_version,
        "transport_dictionary_version": transport_version,
    }
    assert paired.source_message.dictionary_version == source_version
    assert paired.transport_message.dictionary_version == transport_version
    records = api().evaluate_case(paired, DeterministicMockAdapter())
    assert records[0].metadata_selection == paired.metadata_selection
    if source_version == "legacy-v0":
        assert records[0].decode_status == "not_attempted"
        assert records[0].fallback_required and records[0].error.stage == "render"
    else:
        assert records[0].attempt_score.mock_task_completed


def test_temporary_error_retries_same_payload_once_and_preserves_initial_score():
    case = recovery_case()
    adapter, _, records, report, condition = execute(case, {0: failure()})
    assert len(adapter.requests) == len(records) == 2
    assert [record.transition for record in records] == ["initial", "retry"]
    assert records[0].request.payload == records[1].request.payload
    assert records[0].request.input_format == records[1].request.input_format == "ac1"
    assert records[0].retry_performed is False and records[1].retry_performed is True
    assert records[0].fallback_required is False
    assert condition["initial"]["mock_task_completed_count"] == 0
    assert condition["final"]["mock_task_completed_count"] == 1
    assert condition["retried_case_count"] == condition["retry_attempt_count"] == condition["retry_recovered_case_count"] == 1
    assert condition["retry_recovered_case_denominator"] == 1
    assert condition["retry_recovered_case_rate"] == 1.0
    assert condition["fallback_performed_case_count"] == 0
    assert report["acceptance_passed"] is False


@pytest.mark.parametrize("first", [failure(stage="decode", retryable=True), failure(retryable=False),
                                   AdapterResponse(status="ok", raw_output="{", error=None)])
def test_nonretryable_or_decode_or_response_failure_falls_back_without_retry(first):
    case = recovery_case()
    adapter, _, records, report, condition = execute(case, {0: first})
    assert len(adapter.requests) == len(records) == 2
    assert [record.transition for record in records] == ["initial", "fallback_json"]
    assert records[0].fallback_required is True and records[0].fallback_performed is False
    assert records[1].fallback_performed is True and records[1].retry_performed is False
    assert records[1].request.input_format == "json" and records[1].request.dictionary is None
    assert records[1].request.payload == canonical(case.source_message.model_dump())
    assert condition["retry_attempt_count"] == 0
    assert condition["fallback_required_case_count"] == condition["fallback_performed_case_count"] == condition["fallback_recovered_case_count"] == 1
    assert condition["initial"]["equal_field_total"] == 0
    assert condition["final"]["equal_field_total"] == 11
    assert condition["case_results"] == [{"case_id": "diverse/m1/ac1", "final_oracle": "source", "terminal_record_id": "diverse/m1/ac1/1"}]
    assert report["acceptance_passed"] is False


def test_persistent_temporary_then_failing_fallback_is_bounded_to_three():
    adapter, _, records, report, condition = execute(recovery_case(), {0: failure(), 1: failure(), 2: failure()})
    assert len(adapter.requests) == len(records) == 3
    assert [record.transition for record in records] == ["initial", "retry", "fallback_json"]
    assert [record.fallback_required for record in records] == [False, True, True]
    assert records[1].attempt_score.fallback_required is True
    assert condition["final"]["mock_task_completed_count"] == 0
    assert condition["retry_attempt_count"] == condition["fallback_performed_case_count"] == 1
    assert condition["retry_recovered_case_count"] == condition["fallback_recovered_case_count"] == 0
    assert report["acceptance_passed"] is False


@pytest.mark.parametrize("first", [failure(), failure(retryable=False), corrupted])
def test_record_only_never_makes_extra_calls_even_with_retry_budget(first):
    adapter, _, records, _, condition = execute(recovery_case(mode="record_only"), {0: first})
    assert len(adapter.requests) == len(records) == 1
    assert records[0].fallback_required is True
    assert records[0].attempt_score.fallback_required is True
    assert condition["fallback_required_case_count"] == 1
    assert condition["retried_case_count"] == condition["fallback_performed_case_count"] == 0


def test_zero_retry_budget_goes_directly_to_fallback():
    adapter, _, records, _, condition = execute(recovery_case(retries=0), {0: failure()})
    assert len(adapter.requests) == 2
    assert [record.transition for record in records] == ["initial", "fallback_json"]
    assert records[0].fallback_required
    assert condition["retry_attempt_count"] == 0


@pytest.mark.parametrize("version", [None, "demo-v1", "legacy-v0"])
def test_dictionary_fallback_uses_source_oracle_without_rewriting_selection(version):
    case = recovery_case(paired=True, dictionary_version=version)
    _, manifest, records, _, condition = execute(case, {0: corrupted})
    assert len(records) == 2
    assert records[-1].decoded_message.dictionary_version == version
    assert records[-1].request.dictionary is None
    assert records[-1].attempt_score.full_message_equal is True
    assert all(record.metadata_selection.model_dump() == case.metadata_selection.model_dump() for record in records)
    assert condition["case_results"][0]["final_oracle"] == "source"
    assert condition["initial"]["mock_task_completed_count"] == 0
    assert condition["final"]["mock_task_completed_count"] == 1
    assert len(manifest["metadata_selection_rules"]) == 3


@pytest.mark.parametrize("changes,field", [({"task": "Do publish."}, "task"),
                                           ({"constraints": ["Budget <= $250.", "Do not call external APIs."]}, "constraints")])
def test_schema_valid_negation_and_budget_mutations_remain_visible(changes, field):
    _, _, records, report, condition = execute(recovery_case(), {0: altered(**changes)})
    assert records[0].decode_status == "ok" and records[0].receipt_status == "valid"
    assert records[0].attempt_score.field_equal[field] is False
    assert records[0].attempt_score.hard_field_equal[field] is False
    assert records[0].fallback_required is True
    assert condition["initial"]["changed_hard_field_count"] == 1
    assert condition["initial"]["mock_task_completed_count"] == 0
    assert condition["final"]["mock_task_completed_count"] == 1
    assert report["acceptance_passed"] is False


def test_wrong_namespace_is_not_automatically_corrected():
    case = recovery_case(mode="record_only")
    _, _, records, _, condition = execute(case, {0: altered(user_id="other-user")})
    assert records[0].receipt_status == "invalid"
    assert records[0].decoded_message is None
    assert records[0].error.stage == "response"
    assert records[0].fallback_required
    assert condition["initial"]["equal_field_total"] == 0


def test_render_failure_does_not_invoke_adapter_and_records_absent_response():
    case = recovery_case(mode="record_only", paired=True, dictionary_version="wrong-version")
    adapter, _, records, _, condition = execute(case, {})
    assert adapter.requests == [] and len(records) == 1
    record = records[0]
    assert record.request.payload == "" and record.response is None
    assert record.decode_status == "not_attempted" and record.receipt_status == "absent"
    assert record.error.stage == "render" and record.error.code == "render_rejected"
    assert record.fallback_required and record.attempt_score.mock_task_completed is False
    assert condition["attempt_count"] == 1


def test_retry_keeps_dictionary_selection_and_request_snapshot():
    case = recovery_case(paired=True)
    _, _, records, _, _ = execute(case, {0: failure(), 1: corrupted})
    assert len(records) == 3
    assert all(record.metadata_selection == case.metadata_selection for record in records)
    assert records[0].request.dictionary == records[1].request.dictionary == case.dictionary
    assert records[2].request.dictionary is None
    assert records[0].request.payload == records[1].request.payload
    assert records[0].request.dictionary is not records[1].request.dictionary


def test_deterministic_core_bytes_and_offline_guards_cover_all_paths(real_scenarios, monkeypatch):
    # Catches hidden revision discovery, machine/time fields, credentials, or mutable state.
    cases = api().build_cases(real_scenarios, FORMATS, "paired")
    before = [case.model_dump() for case in cases]
    with no_environment(monkeypatch), monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(io, "open", forbidden)
        guard.setattr(os, "open", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(Path, "read_bytes", forbidden)
        guard.setattr(Path, "write_bytes", forbidden)
        first = api().run_offline_evaluation(cases, DeterministicMockAdapter(), HASHES, "rev-test")
        second = api().run_offline_evaluation(cases, DeterministicMockAdapter(), HASHES, "rev-test")
        for script in ({0: failure()}, {0: corrupted}, {0: failure(), 1: failure(), 2: failure()}):
            execute(recovery_case(paired=True), script)
    def serialized(result):
        manifest, records, report = result
        return ((canonical(manifest) + "\n").encode(),
                "".join(canonical(record.model_dump()) + "\n" for record in records).encode(),
                (canonical(report) + "\n").encode())
    assert serialized(first) == serialized(second)
    assert [case.model_dump() for case in cases] == before
    assert HASHES == first[0]["input_hashes"]
    assert first[0]["manifest_version"] == "offline-eval-manifest-v1"
    assert first[2]["report_version"] == "offline-eval-report-v1"
    for name, relative in (("diverse", "bench/tasks.v1.jsonl"),
                           ("repetitive", "bench/tasks-repetitive.v1.jsonl"),
                           ("repetitive_dictionary", "bench/dictionaries/repeat-v1.json")):
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == HASHES[name]


@pytest.mark.parametrize("formats,pairs", [((), "none"), (("json", "json"), "none"),
                                           (("unknown",), "none"), (("ac1",), "auto")])
def test_invalid_matrix_configuration_is_rejected(formats, pairs):
    with pytest.raises(ValueError):
        api().build_cases(local_scenarios(), formats, pairs)


def test_duplicate_source_ids_and_empty_matrix_are_rejected():
    scenarios = local_scenarios()
    scenarios["diverse"].tasks *= 2
    with pytest.raises(ValueError):
        api().build_cases(scenarios)
    with pytest.raises(ValueError):
        api().build_cases({})
    with pytest.raises(ValueError):
        api().run_offline_evaluation([], DeterministicMockAdapter(), {}, "rev-test")


def test_duplicate_case_ids_are_rejected_before_adapter_calls():
    case = recovery_case()
    adapter = ScriptedMockAdapter({})
    with pytest.raises(ValueError):
        api().run_offline_evaluation([case, case], adapter, {}, "rev-test")
    assert adapter.requests == []


def test_case_snapshots_are_independent_of_mutable_inputs():
    scenarios = local_scenarios()
    cases = api().build_cases(scenarios, dictionary_pairs="paired")
    scenarios["diverse"].tasks[0].constraints.append("MUTATED")
    scenarios["diverse"].dictionary.aliases.clear()
    assert cases[0].source_message.constraints == DATA["constraints"]
    assert cases[1].dictionary.aliases
    cases[0].transport_message.constraints.append("TRANSPORT MUTATED")
    assert cases[0].source_message.constraints == DATA["constraints"]
    assert cases[1].transport_message.constraints == DATA["constraints"]


def test_invalid_original_json_stops_without_counting_an_uncalled_fallback():
    # Catches claiming a fallback was performed even though strict rendering blocked it.
    case = recovery_case()
    case.source_message.task = 123
    adapter = ScriptedMockAdapter({("diverse/m1/ac1", 0): corrupted})
    records = api().evaluate_case(case, adapter)
    assert len(adapter.requests) == 1
    assert len(records) == 2
    assert records[-1].transition == "fallback_json"
    assert records[-1].decode_status == "not_attempted"
    assert records[-1].error.stage == "render"
    assert records[-1].fallback_performed is False


def test_rendered_corrupt_ac1_uses_original_json_without_retry(monkeypatch):
    module = api()
    real_render = module.render_input
    monkeypatch.setattr(module, "render_input", lambda message, input_format, dictionary:
                        "AC1|broken" if input_format == "ac1" else real_render(message, input_format, dictionary))
    adapter, _, records, _, condition = execute(recovery_case(), {})
    assert len(adapter.requests) == 2
    assert records[0].request.payload == "AC1|broken"
    assert records[0].error.stage == "decode"
    assert records[0].decode_status == "error"
    assert records[1].request.payload == canonical(DATA)
    assert condition["initial"]["equal_field_total"] == 0
    assert condition["final"]["equal_field_total"] == 11
    assert condition["retry_attempt_count"] == 0


def test_response_validation_failure_never_scores_a_partial_message():
    def invalid(request):
        response = DeterministicMockAdapter().invoke(request)
        receipt = json.loads(response.raw_output)
        del receipt["message"]["blocker"]
        return AdapterResponse(status="ok", raw_output=canonical(receipt), error=None)
    _, _, records, _, condition = execute(recovery_case(mode="record_only"), {0: invalid})
    assert records[0].receipt_status == "invalid" and records[0].decoded_message is None
    assert condition["initial"]["equal_field_total"] == 0
    assert condition["initial"]["unrecovered_field_count"] == 11


def test_adapter_cannot_mutate_audit_request_or_oracle():
    def mutate(request):
        response = DeterministicMockAdapter().invoke(request)
        request.payload = "MUTATED BY ADAPTER"
        request.dictionary.aliases.clear()
        return response
    case = recovery_case(paired=True)
    before = case.model_dump()
    _, _, records, _, condition = execute(case, {0: mutate})
    assert len(records) == 1 and condition["initial"]["mock_task_completed_count"] == 1
    assert records[0].request.payload != "MUTATED BY ADAPTER"
    assert records[0].request.dictionary.aliases
    assert records[0].request_sha256 == digest(records[0].request.model_dump())
    assert case.model_dump() == before


def test_mismatched_pair_snapshots_reject_before_calls():
    cases = api().build_cases(local_scenarios(), ("ac1", "ac2"), "paired")
    cases[-1].dictionary.aliases["EXTRA"] = "Another phrase."
    adapter = ScriptedMockAdapter({})
    with pytest.raises(ValueError, match="same dictionary"):
        api().run_offline_evaluation(cases, adapter, {}, "rev-test")
    assert adapter.requests == []

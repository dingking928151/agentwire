"""Reproducible local evaluation; mock receipts measure pipelines, not understanding."""

from typing import Literal

from agentcodec.benchmark import BenchmarkScenario
from agentcodec.codec import FallbackRequired
from agentcodec.schema import MessageV1, UserDictionary

from .contracts import (
    AdapterResponse, AttemptRecord, EvalCase, EvalError, EvalPolicy, EvalRequest,
    InputFormat, MetadataSelection, canonical_json, sha256_text,
)
from .formats import render_input
from .mock import OfflineAdapter, validate_receipt
from .scoring import HARD_FIELDS, aggregate_scores, score_attempt


_FORMATS = ("natural_language", "json", "ac1", "ac2")
_CONDITIONS = (*_FORMATS, "ac1_dictionary", "ac2_dictionary")
_DISCLAIMER = (
    "Deterministic local mock validates the pipeline only, not model understanding, "
    "semantic safety, real business-task completion, API cost, or latency."
)
_SELECTION_RULES = {
    "no_dictionary": "No dictionary selected; preserve the source version.",
    "applied_to_null_version": "Select dictionary metadata on a transport copy only when source version is null.",
    "preserved_non_null_version": "Preserve non-null source version even when it differs from the selected dictionary.",
}


def _scenario_order(names):
    return sorted(names, key=lambda name: (name not in ("diverse", "repetitive"),
                                          {"diverse": 0, "repetitive": 1}.get(name, 2), name))


def _case_id(case: EvalCase) -> str:
    return f"{case.scenario}/{case.source_message.message_id}/{case.condition}"


def build_cases(
    scenarios: dict[str, BenchmarkScenario], formats: tuple[InputFormat, ...] = ("ac1",),
    dictionary_pairs: Literal["none", "paired"] = "none", policy: EvalPolicy | None = None,
) -> list[EvalCase]:
    """Snapshot explicit conditions without token counting or changing source data."""
    if not scenarios or not formats or len(set(formats)) != len(formats):
        raise ValueError("scenarios and unique formats must not be empty")
    if any(input_format not in _FORMATS for input_format in formats):
        raise ValueError("unknown input format")
    if dictionary_pairs not in ("none", "paired"):
        raise ValueError("unknown dictionary pairing mode")
    policy = EvalPolicy.model_validate(vars(policy) if policy is not None else {}, strict=True)
    conditions = [name for name in _FORMATS if name in formats]
    if dictionary_pairs == "paired":
        conditions += [name + "_dictionary" for name in ("ac1", "ac2") if name in formats]
    cases = []
    for name in _scenario_order(scenarios):
        scenario = scenarios[name]
        tasks = [MessageV1.model_validate(task.model_dump(), strict=True) for task in scenario.tasks]
        dictionary = UserDictionary.model_validate(scenario.dictionary.model_dump(), strict=True)
        if not tasks or len({task.message_id for task in tasks}) != len(tasks):
            raise ValueError("scenario requires nonempty tasks with unique message IDs")
        for task in tasks:
            for condition in conditions:
                selected = dictionary.model_copy(deep=True) if condition.endswith("_dictionary") else None
                source = task.model_copy(deep=True)
                transport = task.model_copy(deep=True)
                if selected is None:
                    status = "no_dictionary"
                elif source.dictionary_version is None:
                    status = "applied_to_null_version"
                    transport.dictionary_version = selected.version
                else:
                    status = "preserved_non_null_version"
                selection = MetadataSelection(
                    selection_status=status,
                    selected_dictionary_user_id=selected.user_id if selected else None,
                    selected_dictionary_version=selected.version if selected else None,
                    source_dictionary_version=source.dictionary_version,
                    transport_dictionary_version=transport.dictionary_version,
                )
                cases.append(EvalCase(
                    scenario=name, source_message=source, transport_message=transport,
                    condition=condition, dictionary=selected, metadata_selection=selection,
                    policy=policy.model_copy(deep=True),
                ))
    return cases


def _error(stage, code, message) -> EvalError:
    return EvalError(stage=stage, code=code, message=message, retryable=False)


def _attempt(
    case: EvalCase, adapter: OfflineAdapter, index: int, transition: str,
    previous_request: EvalRequest | None = None,
) -> AttemptRecord:
    fallback = transition == "fallback_json"
    expected = case.source_message if fallback else case.transport_message
    input_format = "json" if fallback else case.condition.removesuffix("_dictionary")
    dictionary = None if fallback else case.dictionary
    error = None
    response = None
    decoded = None
    decode_status, receipt_status = "not_attempted", "absent"
    try:
        if previous_request is not None:
            payload = previous_request.payload
        elif fallback:
            # Validate the original again before any fallback call, retaining all metadata.
            source = MessageV1.model_validate(vars(case.source_message), strict=True)
            payload = canonical_json(source.model_dump())
        else:
            payload = render_input(expected, input_format, dictionary)
    except (ValueError, TypeError, KeyError, RecursionError, FallbackRequired):
        payload = ""
        error = _error("render", "render_rejected", "Input could not be rendered.")
    request = EvalRequest(
        contract_version="offline-eval-v1", case_id=_case_id(case), attempt_index=index,
        input_format=input_format, payload=payload, expected_user_id=case.source_message.user_id,
        dictionary=dictionary.model_copy(deep=True) if dictionary is not None else None,
        operation="inspect_message_v1",
    )
    if error is None:
        try:
            # An adapter may retain or mutate its argument, but never the audit snapshot.
            result = adapter.invoke(request.model_copy(deep=True))
            response = AdapterResponse.model_validate(result.model_dump(), strict=True)
        except (ValueError, TypeError, KeyError, RecursionError, AttributeError):
            response = AdapterResponse(
                status="error", raw_output=None,
                error=_error("adapter", "adapter_rejected", "Adapter returned an invalid response."),
            )
        if response.status == "error":
            error = response.error
            decode_status = "error" if error.stage == "decode" else "not_attempted"
        else:
            try:
                decoded = validate_receipt(response.raw_output, request).message
                decode_status, receipt_status = "ok", "valid"
            except FallbackRequired:
                decode_status, receipt_status = "error", "invalid"
                error = _error("response", "response_rejected", "Mock receipt could not be validated.")
    score = score_attempt(expected, response, request)
    if decoded is not None and not score.full_message_equal:
        error = _error("policy", "oracle_mismatch", "Recovered fields differ from the oracle.")
    retry_allowed = (
        transition == "initial" and case.policy.fallback_mode != "record_only"
        and case.policy.max_retries == 1 and response is not None and response.status == "error"
        and response.error.stage == "adapter" and response.error.retryable
    )
    # The scorer intentionally leaves temporary adapter errors to policy. Once no
    # retry is permitted (including record-only), expose the exhausted requirement.
    required = not score.mock_task_completed and not retry_allowed
    score = score.model_copy(update={"fallback_required": required})
    return AttemptRecord(
        record_version="offline-eval-v1", record_id=f"{request.case_id}/{index}",
        scenario=case.scenario, source_message_id=case.source_message.message_id,
        condition=case.condition, metadata_selection=case.metadata_selection.model_copy(deep=True),
        request=request, request_sha256=sha256_text(canonical_json(request.model_dump())),
        adapter_id=adapter.adapter_id, response=response, decoded_message=decoded,
        decode_status=decode_status, receipt_status=receipt_status, error=error,
        transition=transition, fallback_required=required,
        fallback_performed=fallback and response is not None,
        retry_performed=transition == "retry" and response is not None, attempt_score=score,
    )


def evaluate_case(case: EvalCase, adapter: OfflineAdapter) -> list[AttemptRecord]:
    """At most initial, one same-payload retry, and one original-JSON fallback."""
    initial = _attempt(case, adapter, 0, "initial")
    records = [initial]
    if initial.attempt_score.mock_task_completed or case.policy.fallback_mode == "record_only":
        return records
    if not initial.fallback_required:
        records.append(_attempt(case, adapter, 1, "retry", initial.request))
    if not records[-1].attempt_score.mock_task_completed:
        records.append(_attempt(case, adapter, len(records), "fallback_json"))
    return records


def _condition_report(groups: list[list[AttemptRecord]]) -> dict[str, object]:
    initial = [records[0].attempt_score for records in groups]
    final = [records[-1].attempt_score for records in groups]
    count = len(groups)
    result = {
        "logical_case_count": count, "attempt_count": sum(map(len, groups)),
        "initial": aggregate_scores(initial), "final": aggregate_scores(final),
        "case_results": [
            {"case_id": records[0].request.case_id,
             "final_oracle": "source" if records[-1].transition == "fallback_json" else "transport",
             "terminal_record_id": records[-1].record_id}
            for records in groups
        ],
    }
    retried = sum(any(record.retry_performed for record in records) for records in groups)
    retry_recovered = sum(any(record.retry_performed and record.attempt_score.mock_task_completed
                              for record in records) for records in groups)
    fallback_recovered = sum(any(record.fallback_performed and record.attempt_score.mock_task_completed
                                 for record in records) for records in groups)
    counts = {
        "fallback_required_case": sum(any(record.fallback_required for record in records) for records in groups),
        "fallback_performed_case": sum(any(record.fallback_performed for record in records) for records in groups),
        "fallback_recovered_case": fallback_recovered,
        "retried_case": retried,
    }
    for metric, numerator in counts.items():
        result[metric + "_count"] = numerator
        result[metric + "_denominator"] = count
        result[metric + "_rate"] = numerator / count if count else None
    result["retry_attempt_count"] = sum(record.retry_performed for records in groups for record in records)
    result["retry_recovered_case_count"] = retry_recovered
    result["retry_recovered_case_denominator"] = retried
    result["retry_recovered_case_rate"] = retry_recovered / retried if retried else None
    return result


def run_offline_evaluation(
    cases: list[EvalCase], adapter: OfflineAdapter, input_hashes: dict[str, str], code_revision: str,
) -> tuple[dict[str, object], list[AttemptRecord], dict[str, object]]:
    """Return deterministic artifacts; caller owns loading, revision discovery, and I/O."""
    if not cases:
        raise ValueError("evaluation requires at least one case")
    if adapter.adapter_id not in ("deterministic-mock-v1", "scripted-mock-test-v1"):
        raise ValueError("unsupported offline adapter identity")
    if len({_case_id(case) for case in cases}) != len(cases):
        raise ValueError("duplicate case identity")
    # Source ordering is the supplied corpus line order; conditions always use the
    # contract order even if callers selected formats in a different order.
    ordered = []
    for name in _scenario_order({case.scenario for case in cases}):
        source_ids = list(dict.fromkeys(case.source_message.message_id for case in cases if case.scenario == name))
        positions = {source_id: index for index, source_id in enumerate(source_ids)}
        ordered += sorted((case for case in cases if case.scenario == name),
                          key=lambda case: (positions[case.source_message.message_id], _CONDITIONS.index(case.condition)))
    dictionaries = {}
    for case in ordered:
        if case.dictionary is not None:
            snapshot = case.dictionary.model_dump()
            entry = {"snapshot": snapshot, "sha256": sha256_text(canonical_json(snapshot))}
            if case.scenario in dictionaries and dictionaries[case.scenario] != entry:
                raise ValueError("one scenario must use the same dictionary snapshot")
            dictionaries[case.scenario] = entry
    scenario_counts = {
        name: len({case.source_message.message_id for case in ordered if case.scenario == name})
        for name in _scenario_order({case.scenario for case in ordered})
    }
    manifest = {
        "manifest_version": "offline-eval-manifest-v1", "contract_version": "offline-eval-v1",
        "adapter_id": adapter.adapter_id, "scorer_version": "offline-eval-v1",
        "code_revision": code_revision, "input_hashes": dict(input_hashes),
        "scenario_counts": scenario_counts, "dictionaries": dictionaries,
        "formats": [name for name in _FORMATS if any(case.condition.removesuffix("_dictionary") == name for case in ordered)],
        "conditions": [name for name in _CONDITIONS if any(case.condition == name for case in ordered)],
        "canonicalization": {"encoding": "UTF-8", "ensure_ascii": False, "sort_keys": True,
                             "separators": [",", ":"], "trailing_lf": 1,
                             "unicode_normalization": None, "list_order_and_duplicates": "preserved"},
        "hard_fields": list(HARD_FIELDS), "metadata_selection_rules": dict(_SELECTION_RULES),
        "policies": [dict(value) for value in dict.fromkeys(
            tuple(case.policy.model_dump().items()) for case in ordered)],
        "cases": [{"case_id": _case_id(case), **case.model_dump()} for case in ordered],
        "disclaimer": _DISCLAIMER,
    }
    manifest["run_id"] = sha256_text(canonical_json(manifest))
    grouped = {}
    records = []
    for case in ordered:
        attempts = evaluate_case(case, adapter)
        records.extend(attempts)
        grouped.setdefault(case.scenario, {}).setdefault(case.condition, []).append(attempts)
    report = {
        "report_version": "offline-eval-report-v1", "run_id": manifest["run_id"],
        "adapter_kind": "mock", "pipeline_only": True, "disclaimer": _DISCLAIMER,
        "acceptance_contract": "clean-v1",
        # Clean acceptance must never be inferred from successful recovery alone.
        "acceptance_passed": all(record.attempt_score.mock_task_completed and record.error is None
                                 for record in records),
        "scenarios": {
            name: {"conditions": {condition: _condition_report(grouped[name][condition])
                                  for condition in _CONDITIONS if condition in grouped[name]}}
            for name in _scenario_order(grouped)
        },
    }
    return manifest, records, report

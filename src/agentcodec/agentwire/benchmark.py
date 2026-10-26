"""Paired, synthetic-only AgentWire benchmarks; never contacts a model/provider.

Rows keep source and selected-dictionary transport domains separate. Hashes in
reports identify only opaque benchmark namespaces; they are not message,
payload, dictionary-content, or input-content hashes.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentcodec.ac2 import encode_message_ac2
from agentcodec.adaptive.contracts import TokenizerProfile
from agentcodec.agentwire.codec import encode_awp1
from agentcodec.agentwire.contracts import (
    BenchmarkCandidate,
    CostEvent,
    CostPart,
    ReceiverCapabilities,
    dictionary_identity_for,
)
from agentcodec.agentwire.costs import calculate_break_even, measure_cost_events
from agentcodec.agentwire.gateway import deliver_benchmark_at_gateway
from agentcodec.agentwire.prompts import build_direct_model_request
from agentcodec.benchmark import render_json, render_natural_language
from agentcodec.codec import encode_message
from agentcodec.schema import MessageV1, UserDictionary


MetadataDomain = Literal["source", "selected_dictionary_transport"]
ProtocolStateKind = Literal[
    "out_of_band_preinstalled", "cold_text_setup", "ready_history", "unknown_history"
]
DictionaryExposureKind = Literal[
    "not_applicable", "out_of_band_preinstalled", "cold_text_setup",
    "ready_history", "unknown_history",
]
ReuseUnit = Literal["same_message_instance", "whole_corpus_pass"]
ReceiverMode = Literal["model_direct", "gateway"]
MetricStatus = Literal["exact_local_text", "cannot_compare", "not_applicable", "transport_only"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)


class BenchmarkCase(_FrozenModel):
    case_id: str = Field(min_length=1)
    task_prompt: str
    source_message: MessageV1
    freeform_text: str
    freeform_annotations: dict[str, tuple[str, ...]]
    synthetic_response_fixture: str

    @field_validator("source_message", mode="before")
    @classmethod
    def snapshot_message(cls, value):
        if isinstance(value, MessageV1):
            value = copy.deepcopy(value.model_dump(mode="python"))
        return MessageV1.model_validate(value, strict=True)

    @field_validator("freeform_annotations", mode="before")
    @classmethod
    def snapshot_annotations(cls, value):
        if not isinstance(value, dict):
            raise ValueError("benchmark case rejected")
        try:
            return {key: tuple(items) for key, items in value.items()}
        except (TypeError, ValueError):
            raise ValueError("benchmark case rejected") from None

    @model_validator(mode="after")
    def require_paired_handwritten_freeform(self):
        if (
            not self.case_id
            or not self.task_prompt
            or not self.freeform_text
            or not self.synthetic_response_fixture
            or not self.freeform_annotations.get("key_semantics")
            or not self.freeform_annotations.get("hard_constraints")
        ):
            raise ValueError("benchmark case rejected")
        return self


class ProtocolState(_FrozenModel):
    candidate_id: BenchmarkCandidate
    profile_id: str = Field(min_length=1)
    state: ProtocolStateKind
    setup_text: str | None = None
    history: tuple[CostEvent, ...] = ()
    history_complete: bool = True
    negotiation_required: bool = False
    negotiation_text: str | None = None

    @model_validator(mode="after")
    def consistent_state(self):
        if self.state == "unknown_history" and self.history_complete:
            raise ValueError("unknown protocol history must be incomplete")
        if self.state == "ready_history" and not self.history_complete:
            raise ValueError("ready protocol history must be complete")
        if self.state == "out_of_band_preinstalled" and self.setup_text is not None:
            raise ValueError("preinstalled protocol cannot have transmitted setup text")
        if self.negotiation_text is not None and not self.negotiation_text:
            raise ValueError("empty negotiation text is not evidence of transmission")
        event_ids = [event.event_id for event in self.history]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("protocol history event IDs must be unique")
        return self


class DictionaryExposure(_FrozenModel):
    state: DictionaryExposureKind
    setup_text: str | None = None
    history_complete: bool = True

    @model_validator(mode="after")
    def consistent_exposure(self):
        if self.state == "unknown_history" and self.history_complete:
            raise ValueError("unknown dictionary history must be incomplete")
        if self.state == "ready_history" and not self.history_complete:
            raise ValueError("ready dictionary history must be complete")
        if self.state in {"not_applicable", "out_of_band_preinstalled", "ready_history"} and self.setup_text is not None:
            raise ValueError("selected dictionary exposure state conflicts with setup text")
        return self


class ReuseScenario(_FrozenModel):
    # Keep an unknown unit representable so a report can say cannot_compare,
    # rather than silently treating it as one of the two supported units.
    unit: str = Field(min_length=1)
    total_uses: tuple[int, ...] = (1, 10, 50)

    @field_validator("total_uses")
    @classmethod
    def fixed_use_counts(cls, value):
        if value != (1, 10, 50):
            raise ValueError("benchmark reuse counts must be 1, 10, and 50")
        return value


class BenchmarkScenario(_FrozenModel):
    scenario_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    corpus_id: str = Field(min_length=1)
    metadata_domain: MetadataDomain
    receiver_capabilities: ReceiverCapabilities
    protocol_states: tuple[ProtocolState, ...]
    dictionary_candidate_exposure: DictionaryExposure
    dictionary_history: tuple[CostEvent, ...]
    cases: tuple[BenchmarkCase, ...] = Field(min_length=1)
    reuse: ReuseScenario

    @model_validator(mode="after")
    def enforce_one_scope_and_reuse_unit(self):
        receiver = self.receiver_capabilities
        if any(case.source_message.user_id != receiver.user_id for case in self.cases):
            raise ValueError("benchmark scenario rejected")
        if self.metadata_domain == "source":
            if (
                self.dictionary_candidate_exposure.state != "not_applicable"
                or self.dictionary_history
            ):
                raise ValueError("source domain cannot select dictionary exposure or history")
        elif self.dictionary_candidate_exposure.state == "not_applicable":
            raise ValueError("selected transport requires explicit dictionary exposure")
        if self.reuse.unit == "same_message_instance" and len(self.cases) != 1:
            raise ValueError("same_message_instance requires exactly one case")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("benchmark case IDs must be unique within a corpus")
        state_keys = [(state.candidate_id, state.profile_id) for state in self.protocol_states]
        if len(state_keys) != len(set(state_keys)):
            raise ValueError("protocol state must be unique per candidate and profile")
        history_ids = [event.event_id for event in self.dictionary_history]
        if len(history_ids) != len(set(history_ids)):
            raise ValueError("dictionary history event IDs must be unique")
        return self


class BenchmarkComparison(_FrozenModel):
    status: Literal["finite", "never", "cannot_compare", "not_applicable"]
    cost_delta_tokens: int | None
    break_even_status: Literal["finite", "never", "cannot_compare", "not_applicable"]
    first_strict_win: int | None


class BenchmarkRow(_FrozenModel):
    # Stable, privacy-safe row order. Namespace hashes are not content hashes.
    synthetic_corpus_ordinal: int
    tokenizer_profile_id: str
    receiver_mode: ReceiverMode
    candidate_id: BenchmarkCandidate
    metric_surface: Literal["end_to_end", "transport_and_model", "transport_only", "not_applicable"]
    metric_status: MetricStatus
    dictionary_condition: Literal["none", "selected", "not_applicable"]
    dictionary_candidate_exposure: DictionaryExposureKind
    reuse_unit: str
    total_uses: int
    case_count: int
    event_count: int | None
    source_hash: str
    transport_hash: str
    transport_bytes: int | None
    transport_tokens: int | None
    model_input_tokens: int | None
    synthetic_response_tokens: int | None
    distinct_text_event_tokens: int | None
    candidate_protocol_exposure_tokens: int | None
    candidate_protocol_setup_history_tokens: int | None
    candidate_protocol_setup_tokens: int | None
    negotiation_tokens: int | None
    dictionary_exposure_tokens: int | None
    dictionary_history_tokens: int | None
    dictionary_setup_tokens: int | None
    request_tokens: int | None
    response_tokens: int | None
    retry_tokens: int | None
    fallback_tokens: int | None
    per_use_cost_tokens: int | None
    amortized_cost_tokens: float | None
    lifecycle_cost_tokens: int | None
    strict_decode_status: Literal["decoded", "fallback_required", "not_applicable"]
    strict_decode_success_count: int | None
    full_field_equal_count: int | None
    hard_field_equal_count: int | None
    vs_natural_language_labelled: BenchmarkComparison
    vs_natural_language_freeform: BenchmarkComparison
    labelled_baseline_description: str = "labelled-field control only"


class ScenarioReport(_FrozenModel):
    metadata_domain: MetadataDomain
    reuse_unit: str
    source_hash: str
    transport_hash: str
    hash_basis: str = "opaque namespace identifiers only; not message or payload content"
    rows: tuple[BenchmarkRow, ...]


class AgentWireBenchmarkReport(_FrozenModel):
    version: Literal["agentwire-benchmark-v1"] = "agentwire-benchmark-v1"
    scenarios: tuple[ScenarioReport, ...]
    notes: tuple[str, ...] = (
        "Tokenizer counts are local text estimates, not provider billing.",
        "No model or provider was contacted; response text is synthetic fixture data.",
        "Decoder equality is structural only and does not measure model understanding or task quality.",
    )


_CANDIDATE_ORDER: tuple[BenchmarkCandidate, ...] = (
    "natural_language", "natural_language_freeform", "json", "ac1", "ac1_dictionary",
    "ac2", "ac2_dictionary", "awp1", "awp1_dictionary",
)
_SOURCE_ALLOWLIST = frozenset({
    "natural_language", "natural_language_freeform", "json", "ac1", "ac2", "awp1",
})
_SELECTED_ALLOWLIST = frozenset({
    "natural_language", "json", "awp1", "ac1_dictionary", "ac2_dictionary",
    "awp1_dictionary",
})
_DICTIONARY_CANDIDATES = frozenset({"ac1_dictionary", "ac2_dictionary", "awp1_dictionary"})
_HARD_FIELDS = ("task", "blocker", "next_step", "evidence", "constraints", "approval_required")
_SUPPORTED_REUSE_UNITS = frozenset({"same_message_instance", "whole_corpus_pass"})


def prepare_transport_message(message: MessageV1, dictionary: UserDictionary) -> MessageV1:
    """Return an immutable-by-convention transport copy with selected version metadata.

    The source is snapshotted and never mutated. The only permitted field change is
    setting a null dictionary_version to the explicitly selected dictionary version.
    """
    try:
        if not isinstance(message, MessageV1) or not isinstance(dictionary, UserDictionary):
            raise ValueError
        source = MessageV1.model_validate(
            copy.deepcopy(message.model_dump(mode="python")), strict=True
        )
        selected = UserDictionary.model_validate(
            copy.deepcopy(dictionary.model_dump(mode="python")), strict=True
        )
        identity = dictionary_identity_for(selected)
        if source.user_id != identity.user_id:
            raise ValueError
        if source.dictionary_version not in (None, identity.version):
            raise ValueError
        values = source.model_dump(mode="python")
        values["dictionary_version"] = identity.version
        return MessageV1.model_validate(values, strict=True)
    except (TypeError, ValueError, AttributeError, RecursionError):
        raise ValueError("transport message rejected") from None


def _domain_hash(kind: str, scenario: BenchmarkScenario, profile_id: str, dictionary: UserDictionary | None) -> str:
    selected_identity = None
    if dictionary is not None:
        try:
            selected_identity = dictionary_identity_for(dictionary).sha256
        except (TypeError, ValueError, AttributeError, RecursionError):
            selected_identity = "invalid"
    # Only namespace identifiers are hashed. No MessageV1, prose, payload, or
    # dictionary content enters this digest.
    namespace = json.dumps(
        {
            "kind": kind,
            "scenario": scenario.scenario_id,
            "scope": scenario.scope_id,
            "corpus": scenario.corpus_id,
            "receiver": scenario.receiver_capabilities.user_id,
            "profile": profile_id,
            "dictionary_identity": selected_identity,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"agentwire-benchmark-namespace-v1\0" + namespace).hexdigest()


def _new_event(
    event_id: str,
    kind: str,
    label: str,
    text: str,
    *,
    surfaces: tuple[str, ...] = ("transport",),
    multiplicity: int = 1,
) -> CostEvent:
    return CostEvent(
        event_id=event_id,
        kind=kind,
        surfaces=surfaces,
        parts=(CostPart(label=label, text=text),),
        multiplicity=multiplicity,
    )


def _copy_history_event(event: CostEvent, event_id: str) -> CostEvent:
    return CostEvent(
        event_id=event_id,
        kind=event.kind,
        simulated=True,
        surfaces=event.surfaces,
        parts=event.parts,
        multiplicity=event.multiplicity,
    )


def _direct_request_parts(
    complete_request: str,
    context_prefix: str,
    wire_payload: str,
) -> tuple[CostPart, ...]:
    marker = "\n\nPayload:\n"
    boundary = complete_request.find(marker, len(context_prefix))
    if boundary < 0 or not complete_request.startswith(context_prefix):
        raise ValueError("direct request cannot be attributed")
    payload_start = boundary + len(marker)
    payload_end = payload_start + len(wire_payload)
    if complete_request[payload_start:payload_end] != wire_payload:
        raise ValueError("direct request cannot be attributed")
    header_tail = complete_request[len(context_prefix):boundary]
    dictionary_tail = complete_request[payload_end:]
    parts = [CostPart(label="context", text=context_prefix)]
    if header_tail:
        parts.append(CostPart(label="protocol_instructions", text=header_tail))
    parts.append(CostPart(label="payload", text=marker + wire_payload))
    if dictionary_tail:
        parts.append(CostPart(label="dictionary_glossary", text=dictionary_tail))
    if "".join(part.text for part in parts) != complete_request:
        raise ValueError("direct request cannot be attributed")
    return tuple(parts)


def _payload_for(
    candidate: BenchmarkCandidate,
    message: MessageV1,
    case: BenchmarkCase,
    dictionary: UserDictionary | None,
) -> str:
    if candidate == "natural_language":
        return render_natural_language(message)
    if candidate == "natural_language_freeform":
        return case.freeform_text
    if candidate == "json":
        return render_json(message)
    if candidate == "ac1":
        return encode_message(message)
    if candidate == "ac1_dictionary":
        return encode_message(message, dictionary)
    if candidate == "ac2":
        return encode_message_ac2(message)
    if candidate == "ac2_dictionary":
        return encode_message_ac2(message, dictionary)
    if candidate == "awp1":
        return encode_awp1(message)
    if candidate == "awp1_dictionary":
        return encode_awp1(message, dictionary)
    raise ValueError("unsupported benchmark candidate") from None


def _stable_event_id(
    ordinal: int,
    profile_id: str,
    candidate: str,
    mode: str,
    case_id: str,
    event_kind: str,
) -> str:
    value = "\0".join((str(ordinal), profile_id, candidate, mode, case_id, event_kind))
    return "awpb-" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _not_applicable_comparison() -> BenchmarkComparison:
    return BenchmarkComparison(
        status="not_applicable", cost_delta_tokens=None,
        break_even_status="not_applicable", first_strict_win=None,
    )


def _cannot_compare_comparison() -> BenchmarkComparison:
    return BenchmarkComparison(
        status="cannot_compare", cost_delta_tokens=None,
        break_even_status="cannot_compare", first_strict_win=None,
    )


def _not_applicable_row(
    *, ordinal: int, profile_id: str, mode: ReceiverMode, candidate: BenchmarkCandidate,
    scenario: BenchmarkScenario, total_uses: int, source_hash: str, transport_hash: str,
) -> BenchmarkRow:
    return BenchmarkRow(
        synthetic_corpus_ordinal=ordinal,
        tokenizer_profile_id=profile_id,
        receiver_mode=mode,
        candidate_id=candidate,
        metric_surface="not_applicable",
        metric_status="not_applicable",
        dictionary_condition="not_applicable",
        dictionary_candidate_exposure="not_applicable",
        reuse_unit=scenario.reuse.unit,
        total_uses=total_uses,
        case_count=len(scenario.cases),
        event_count=0,
        source_hash=source_hash,
        transport_hash=transport_hash,
        transport_bytes=None,
        transport_tokens=None,
        model_input_tokens=None,
        synthetic_response_tokens=None,
        distinct_text_event_tokens=None,
        candidate_protocol_exposure_tokens=None,
        candidate_protocol_setup_history_tokens=None,
        candidate_protocol_setup_tokens=None,
        negotiation_tokens=None,
        dictionary_exposure_tokens=None,
        dictionary_history_tokens=None,
        dictionary_setup_tokens=None,
        request_tokens=None,
        response_tokens=None,
        retry_tokens=None,
        fallback_tokens=None,
        per_use_cost_tokens=None,
        amortized_cost_tokens=None,
        lifecycle_cost_tokens=None,
        strict_decode_status="not_applicable",
        strict_decode_success_count=None,
        full_field_equal_count=None,
        hard_field_equal_count=None,
        vs_natural_language_labelled=_not_applicable_comparison(),
        vs_natural_language_freeform=_not_applicable_comparison(),
    )


def _has_incomplete_state(
    state: ProtocolState | None,
    scenario: BenchmarkScenario,
    candidate: BenchmarkCandidate,
) -> tuple[bool, bool, bool]:
    """Return (protocol unknown, dictionary unknown, missing known text)."""
    if state is None:
        protocol_unknown = True
        unknown_text = False
    else:
        protocol_unknown = (
            state.state == "unknown_history"
            or (state.state == "ready_history" and not state.history_complete)
        )
        unknown_text = (
            (state.state == "cold_text_setup" and state.setup_text is None)
            or (state.negotiation_required and state.negotiation_text is None)
        )
    dictionary_candidate = candidate in _DICTIONARY_CANDIDATES
    if not dictionary_candidate or scenario.metadata_domain != "selected_dictionary_transport":
        dictionary_unknown = False
    else:
        exposure = scenario.dictionary_candidate_exposure
        dictionary_unknown = (
            exposure.state == "unknown_history"
            or (exposure.state == "ready_history" and not exposure.history_complete)
        )
        unknown_text = unknown_text or (
            exposure.state == "cold_text_setup" and exposure.setup_text is None
        )
    return protocol_unknown, dictionary_unknown, unknown_text


def _measure_group(events: list[CostEvent], profile: TokenizerProfile | None) -> int | None:
    if not events:
        return 0
    summary = measure_cost_events(tuple(events), profile)
    if summary.status != "exact_local_text":
        return None
    return summary.distinct_text_event_tokens


def _make_candidate_metrics(
    scenario: BenchmarkScenario,
    dictionary: UserDictionary | None,
    profile_id: str,
    profile: TokenizerProfile | None,
    candidate: BenchmarkCandidate,
    mode: ReceiverMode,
    total_uses: int,
    ordinal: int,
    transport_copy: tuple[MessageV1, ...] | None,
    payload_cache: dict[tuple[str, str], tuple[str, object | None]],
    request_cache: dict[tuple[str, str, str], tuple[str, tuple[CostPart, ...]]],
) -> tuple[dict, int | None, int | None]:
    """Return row values, one-unit setup, and one-use message cost."""
    is_dictionary_candidate = candidate in _DICTIONARY_CANDIDATES
    selected_dictionary = (
        dictionary if is_dictionary_candidate
        and scenario.metadata_domain == "selected_dictionary_transport" else None
    )
    protocol_state = next(
        (state for state in scenario.protocol_states
         if state.candidate_id == candidate and state.profile_id == profile_id),
        None,
    )
    protocol_unknown, dictionary_unknown, unknown_text = _has_incomplete_state(
        protocol_state, scenario, candidate
    )
    if scenario.metadata_domain == "selected_dictionary_transport" and is_dictionary_candidate:
        dictionary_unknown = dictionary_unknown or selected_dictionary is None

    tagged: dict[str, list[CostEvent]] = {
        "protocol_history": [], "protocol_setup": [], "negotiation": [],
        "dictionary_history": [], "dictionary_setup": [], "current_request": [],
        "current_response": [],
    }
    all_events: list[CostEvent] = []
    payload_bytes_known = True
    strict_successes = 0
    full_matches = 0
    hard_matches = 0
    freeform_gateway = candidate == "natural_language_freeform" and mode == "gateway"

    if protocol_state is not None:
        for index, history_event in enumerate(protocol_state.history):
            copied = _copy_history_event(history_event, _stable_event_id(
                ordinal, profile_id, candidate, mode, f"protocol-history-{index}", history_event.event_id,
            ))
            tagged["protocol_history"].append(copied)
            all_events.append(copied)
        if protocol_state.state == "cold_text_setup":
            if protocol_state.setup_text is None:
                unknown_text = True
            else:
                event = _new_event(
                    _stable_event_id(ordinal, profile_id, candidate, mode, "protocol-setup", "setup"),
                    "setup", "setup", protocol_state.setup_text,
                )
                tagged["protocol_setup"].append(event)
                all_events.append(event)
        if protocol_state.negotiation_text is not None:
            event = _new_event(
                _stable_event_id(ordinal, profile_id, candidate, mode, "negotiation", "negotiation"),
                "negotiation", "negotiation", protocol_state.negotiation_text,
            )
            tagged["negotiation"].append(event)
            all_events.append(event)

    if selected_dictionary is not None:
        exposure = scenario.dictionary_candidate_exposure
        for index, history_event in enumerate(scenario.dictionary_history):
            copied = _copy_history_event(history_event, _stable_event_id(
                ordinal, profile_id, candidate, mode, f"dictionary-history-{index}", history_event.event_id,
            ))
            tagged["dictionary_history"].append(copied)
            all_events.append(copied)
        if exposure.state == "cold_text_setup":
            if exposure.setup_text is None:
                unknown_text = True
            else:
                event = _new_event(
                    _stable_event_id(ordinal, profile_id, candidate, mode, "dictionary-setup", "distribution"),
                    "dictionary_distribution", "dictionary_transfer", exposure.setup_text,
                )
                tagged["dictionary_setup"].append(event)
                all_events.append(event)

    # A complete out-of-band declaration is a real zero setup cost. An absent
    # state is not interpreted as preinstalled and makes the total incomparable.
    per_use_events: list[CostEvent] = []
    for case_index, case in enumerate(scenario.cases):
        cache_key = (candidate, case.case_id)
        cached_payload = payload_cache.get(cache_key)
        try:
            if scenario.metadata_domain == "selected_dictionary_transport":
                if transport_copy is None:
                    raise ValueError("selected transport is unavailable")
                message = transport_copy[case_index]
            else:
                message = MessageV1.model_validate(
                    copy.deepcopy(case.source_message.model_dump(mode="python")), strict=True
                )
        except (TypeError, ValueError, AttributeError, RecursionError):
            unknown_text = True
            payload_bytes_known = False
            continue
        if cached_payload is None:
            try:
                payload = _payload_for(candidate, message, case, selected_dictionary)
            except (TypeError, ValueError, AttributeError, RecursionError):
                unknown_text = True
                payload_bytes_known = False
                payload_cache[cache_key] = ("", None)
                continue
            try:
                delivery = deliver_benchmark_at_gateway(
                    candidate, payload, scenario.receiver_capabilities,
                    dictionary=selected_dictionary,
                )
            except (TypeError, ValueError, AttributeError, RecursionError):
                delivery = None
            payload_cache[cache_key] = (payload, delivery)
        else:
            payload, delivery = cached_payload
            if not payload:
                unknown_text = True
                payload_bytes_known = False
                continue

        if candidate != "natural_language_freeform" and delivery is not None and delivery.decode_status == "decoded":
            try:
                restored = MessageV1.model_validate_json(delivery.model_context, strict=True)
                gold = message.model_dump()
                actual = restored.model_dump()
                strict_successes += 1
                full_matches += int(actual == gold)
                hard_matches += int(all(actual[field] == gold[field] for field in _HARD_FIELDS))
            except (TypeError, ValueError):
                pass

        if mode == "model_direct":
            try:
                request_key = (mode, candidate, case.case_id)
                cached_request = request_cache.get(request_key)
                if cached_request is None:
                    complete_request = build_direct_model_request(
                        case.task_prompt, candidate, payload, dictionary=selected_dictionary,
                    )
                    parts = _direct_request_parts(complete_request, case.task_prompt, payload)
                    request_cache[request_key] = (complete_request, parts)
                else:
                    complete_request, parts = cached_request
                request_event = CostEvent(
                    event_id=_stable_event_id(ordinal, profile_id, candidate, mode, case.case_id, "request"),
                    kind="request",
                    surfaces=("transport", "model_input"),
                    parts=parts,
                    multiplicity=total_uses,
                )
                response_event = _new_event(
                    _stable_event_id(ordinal, profile_id, candidate, mode, case.case_id, "response"),
                    "response", "response", case.synthetic_response_fixture,
                    surfaces=("model_output",), multiplicity=total_uses,
                )
            except (TypeError, ValueError, AttributeError, RecursionError):
                unknown_text = True
                payload_bytes_known = False
                continue
            per_use_events.extend((request_event.model_copy(update={"multiplicity": 1}),
                                   response_event.model_copy(update={"multiplicity": 1})))
            tagged["current_request"].append(request_event)
            tagged["current_response"].append(response_event)
            all_events.extend((request_event, response_event))
        else:
            wire_event = _new_event(
                _stable_event_id(ordinal, profile_id, candidate, mode, case.case_id, "wire-request"),
                "request", "payload", payload,
                surfaces=("transport",), multiplicity=total_uses,
            )
            per_use_events.append(wire_event.model_copy(update={"multiplicity": 1}))
            tagged["current_request"].append(wire_event)
            all_events.append(wire_event)
            if delivery is not None and delivery.decode_status == "decoded":
                context_event = _new_event(
                    _stable_event_id(ordinal, profile_id, candidate, mode, case.case_id, "model-context"),
                    "request", "context", delivery.model_context,
                    surfaces=("model_input",), multiplicity=total_uses,
                )
                response_event = _new_event(
                    _stable_event_id(ordinal, profile_id, candidate, mode, case.case_id, "response"),
                    "response", "response", case.synthetic_response_fixture,
                    surfaces=("model_output",), multiplicity=total_uses,
                )
                per_use_events.extend((context_event.model_copy(update={"multiplicity": 1}),
                                       response_event.model_copy(update={"multiplicity": 1})))
                tagged["current_request"].append(context_event)
                tagged["current_response"].append(response_event)
                all_events.extend((context_event, response_event))

    try:
        summary = measure_cost_events(tuple(all_events), profile)
    except (TypeError, ValueError, RecursionError):
        summary = None
        unknown_text = True

    profile_known = summary is not None and summary.status == "exact_local_text"
    state_unknown = protocol_unknown or dictionary_unknown
    if summary is None:
        transport_bytes = None
    else:
        transport_bytes = summary.transport_bytes if payload_bytes_known and not state_unknown else None

    def event_kind(kind: str) -> int | None:
        if not profile_known or state_unknown or unknown_text:
            return None
        return summary.event_kind_tokens[kind]

    def group_cost(tag: str, unknown: bool = False) -> int | None:
        if unknown:
            return None
        if not profile_known:
            return None
        return _measure_group(tagged[tag], profile)

    protocol_history_cost = group_cost("protocol_history", protocol_unknown)
    protocol_setup_cost = group_cost(
        "protocol_setup",
        protocol_unknown or (
            unknown_text and protocol_state is not None
            and protocol_state.state == "cold_text_setup"
        ),
    )
    negotiation_cost = group_cost(
        "negotiation",
        protocol_unknown or (
            unknown_text and protocol_state is not None
            and protocol_state.negotiation_required
        ),
    )
    dictionary_history_cost = group_cost("dictionary_history", dictionary_unknown)
    dictionary_setup_cost = group_cost(
        "dictionary_setup",
        dictionary_unknown or (
            unknown_text and selected_dictionary is not None
            and scenario.dictionary_candidate_exposure.state == "cold_text_setup"
        ),
    )
    if profile_known:
        protocol_exposure = (
            summary.part_label_tokens["protocol_instructions"]
            if not unknown_text else None
        )
        dictionary_exposure_cost = (
            summary.part_label_tokens["dictionary_glossary"]
            if not unknown_text else None
        )
        model_input_tokens = (
            summary.model_input_tokens if not state_unknown and not unknown_text else None
        )
        synthetic_response_tokens = (
            summary.model_output_tokens if not state_unknown and not unknown_text else None
        )
        distinct_tokens = summary.distinct_text_event_tokens if not state_unknown and not unknown_text else None
    else:
        protocol_exposure = None
        dictionary_exposure_cost = None
        model_input_tokens = None
        synthetic_response_tokens = None
        distinct_tokens = None

    request_tokens = event_kind("request")
    response_tokens = event_kind("response")
    retry_tokens = event_kind("retry")
    fallback_tokens = event_kind("fallback")
    transport_tokens = (
        summary.transport_tokens if profile_known and not state_unknown and not unknown_text else None
    )
    if (
        profile_known and not state_unknown and not unknown_text
        and scenario.reuse.unit in _SUPPORTED_REUSE_UNITS
    ):
        per_use_summary = measure_cost_events(tuple(per_use_events), profile)
        per_use = (
            per_use_summary.distinct_text_event_tokens
            if per_use_summary.status == "exact_local_text" else None
        )
        lifecycle = summary.distinct_text_event_tokens
        amortized = lifecycle / total_uses
    else:
        per_use = None
        lifecycle = None
        amortized = None

    metric_surface = (
        "end_to_end" if mode == "model_direct"
        else "transport_only" if freeform_gateway
        else "transport_and_model"
    )
    if not profile_known or state_unknown or unknown_text or scenario.reuse.unit not in _SUPPORTED_REUSE_UNITS:
        metric_status = "cannot_compare"
    elif freeform_gateway:
        metric_status = "transport_only"
    else:
        metric_status = "exact_local_text"

    if candidate == "natural_language_freeform":
        strict_decode_status = "not_applicable"
        strict_decode_success_count = None
        full_field_equal_count = None
        hard_field_equal_count = None
    else:
        strict_decode_status = "decoded" if strict_successes == len(scenario.cases) else "fallback_required"
        strict_decode_success_count = strict_successes
        full_field_equal_count = full_matches
        hard_field_equal_count = hard_matches

    request_and_response_per_use = per_use
    setup_and_history: int | None
    component_setup_values = (
        protocol_history_cost, protocol_setup_cost, negotiation_cost,
        dictionary_history_cost, dictionary_setup_cost,
    )
    if any(value is None for value in component_setup_values):
        setup_and_history = None
    else:
        setup_and_history = sum(component_setup_values)

    row_values = {
        "metric_surface": metric_surface,
        "metric_status": metric_status,
        "dictionary_condition": "selected" if selected_dictionary is not None else "none",
        "dictionary_candidate_exposure": (
            scenario.dictionary_candidate_exposure.state
            if selected_dictionary is not None else "not_applicable"
        ),
        "event_count": (
            None
            if state_unknown or unknown_text or scenario.reuse.unit not in _SUPPORTED_REUSE_UNITS
            else sum(event.multiplicity for event in all_events)
        ),
        "transport_bytes": transport_bytes,
        "transport_tokens": transport_tokens,
        "model_input_tokens": model_input_tokens if not freeform_gateway else None,
        "synthetic_response_tokens": synthetic_response_tokens if not freeform_gateway else None,
        "distinct_text_event_tokens": None if freeform_gateway else distinct_tokens,
        "candidate_protocol_exposure_tokens": None if freeform_gateway else protocol_exposure,
        "candidate_protocol_setup_history_tokens": None if freeform_gateway else protocol_history_cost,
        "candidate_protocol_setup_tokens": None if freeform_gateway else protocol_setup_cost,
        "negotiation_tokens": None if freeform_gateway else negotiation_cost,
        "dictionary_exposure_tokens": None if freeform_gateway else dictionary_exposure_cost,
        "dictionary_history_tokens": None if freeform_gateway else dictionary_history_cost,
        "dictionary_setup_tokens": None if freeform_gateway else dictionary_setup_cost,
        "request_tokens": None if freeform_gateway else request_tokens,
        "response_tokens": response_tokens if not freeform_gateway else None,
        "retry_tokens": None if freeform_gateway else retry_tokens,
        "fallback_tokens": None if freeform_gateway else fallback_tokens,
        "per_use_cost_tokens": None if freeform_gateway else request_and_response_per_use,
        "amortized_cost_tokens": None if freeform_gateway else amortized,
        "lifecycle_cost_tokens": None if freeform_gateway else lifecycle,
        "strict_decode_status": strict_decode_status,
        "strict_decode_success_count": strict_decode_success_count,
        "full_field_equal_count": full_field_equal_count,
        "hard_field_equal_count": hard_field_equal_count,
    }
    return row_values, setup_and_history, request_and_response_per_use


def _comparison(
    candidate_row: BenchmarkRow,
    baseline_row: BenchmarkRow | None,
    candidate_setup: int | None,
    candidate_per_use: int | None,
    baseline_setup: int | None,
    baseline_per_use: int | None,
    *,
    applicable: bool,
) -> BenchmarkComparison:
    if not applicable:
        return _not_applicable_comparison()
    if (
        baseline_row is None or candidate_row.metric_status != "exact_local_text"
        or baseline_row.metric_status != "exact_local_text"
        or candidate_row.lifecycle_cost_tokens is None
        or baseline_row.lifecycle_cost_tokens is None
        or candidate_setup is None or candidate_per_use is None
        or baseline_setup is None or baseline_per_use is None
    ):
        return _cannot_compare_comparison()
    result = calculate_break_even(
        candidate_setup, candidate_per_use, baseline_setup, baseline_per_use
    )
    return BenchmarkComparison(
        status=result.status,
        cost_delta_tokens=candidate_row.lifecycle_cost_tokens - baseline_row.lifecycle_cost_tokens,
        break_even_status=result.status,
        first_strict_win=result.first_strict_win,
    )


def _profile_map(tokenizer_profiles: Mapping[str, TokenizerProfile]) -> dict[str, TokenizerProfile]:
    try:
        if not isinstance(tokenizer_profiles, Mapping):
            raise ValueError
        result = {}
        for key, value in tokenizer_profiles.items():
            if not isinstance(key, str) or not key:
                continue
            if isinstance(value, TokenizerProfile):
                result[key] = TokenizerProfile.model_validate(value)
        return result
    except (TypeError, ValueError, AttributeError):
        return {}


def run_agentwire_benchmark(
    scenarios: tuple[BenchmarkScenario, ...],
    tokenizer_profiles: Mapping[str, TokenizerProfile],
    dictionary: UserDictionary | None = None,
) -> AgentWireBenchmarkReport:
    """Run paired local measurements for each independent domain/profile scope."""
    if type(scenarios) is not tuple or not scenarios:
        raise ValueError("benchmark scenarios rejected") from None
    checked_scenarios = tuple(BenchmarkScenario.model_validate(item) for item in scenarios)
    profiles = _profile_map(tokenizer_profiles)
    output_scenarios: list[ScenarioReport] = []

    for ordinal, scenario in enumerate(checked_scenarios, start=1):
        selected_dictionary = None
        transport_copy = None
        if scenario.metadata_domain == "selected_dictionary_transport":
            if not isinstance(dictionary, UserDictionary):
                raise ValueError("benchmark scenario rejected") from None
            try:
                selected_dictionary = UserDictionary.model_validate(
                    copy.deepcopy(dictionary.model_dump(mode="python")), strict=True
                )
                selected_identity = dictionary_identity_for(selected_dictionary)
                if (
                    selected_identity.user_id != scenario.receiver_capabilities.user_id
                    or scenario.receiver_capabilities.dictionary_identity != selected_identity
                ):
                    raise ValueError
                transport_copy = tuple(
                    prepare_transport_message(case.source_message, selected_dictionary)
                    for case in scenario.cases
                )
            except (TypeError, ValueError, AttributeError, RecursionError):
                raise ValueError("benchmark scenario rejected") from None

        state_profile_ids = {state.profile_id for state in scenario.protocol_states}
        profile_ids = sorted(state_profile_ids | set(profiles))
        if not profile_ids:
            profile_ids = ["unknown-tokenizer"]

        for profile_id in profile_ids:
            profile = profiles.get(profile_id)
            source_hash = _domain_hash("source", scenario, profile_id, selected_dictionary)
            transport_hash = _domain_hash(
                "selected_dictionary_transport", scenario, profile_id, selected_dictionary
            )
            intermediate: dict[tuple[str, str, int], tuple[dict, int | None, int | None]] = {}
            rows: list[BenchmarkRow] = []
            payload_cache: dict[tuple[str, str], tuple[str, object | None]] = {}
            request_cache: dict[tuple[str, str, str], tuple[str, tuple[CostPart, ...]]] = {}
            for mode in ("model_direct", "gateway"):
                for candidate in _CANDIDATE_ORDER:
                    allowed = (
                        candidate in _SOURCE_ALLOWLIST
                        if scenario.metadata_domain == "source"
                        else candidate in _SELECTED_ALLOWLIST
                    )
                    for total_uses in scenario.reuse.total_uses:
                        if not allowed:
                            row = _not_applicable_row(
                                ordinal=ordinal, profile_id=profile_id, mode=mode,
                                candidate=candidate, scenario=scenario, total_uses=total_uses,
                                source_hash=source_hash, transport_hash=transport_hash,
                            )
                            rows.append(row)
                            intermediate[(candidate, mode, total_uses)] = ({}, None, None)
                            continue
                        values, setup, per_use = _make_candidate_metrics(
                            scenario, selected_dictionary, profile_id, profile, candidate,
                            mode, total_uses, ordinal, transport_copy,
                            payload_cache, request_cache,
                        )
                        row = BenchmarkRow(
                            synthetic_corpus_ordinal=ordinal,
                            tokenizer_profile_id=profile_id,
                            receiver_mode=mode,
                            candidate_id=candidate,
                            reuse_unit=scenario.reuse.unit,
                            total_uses=total_uses,
                            case_count=len(scenario.cases),
                            source_hash=source_hash,
                            transport_hash=transport_hash,
                            vs_natural_language_labelled=_not_applicable_comparison(),
                            vs_natural_language_freeform=_not_applicable_comparison(),
                            **values,
                        )
                        rows.append(row)
                        intermediate[(candidate, mode, total_uses)] = (values, setup, per_use)

            completed_rows: list[BenchmarkRow] = []
            for row in rows:
                if row.metric_status == "not_applicable":
                    completed_rows.append(row)
                    continue
                _, candidate_setup, candidate_per_use = intermediate[
                    (row.candidate_id, row.receiver_mode, row.total_uses)
                ]
                labelled_row = next((item for item in rows if (
                    item.candidate_id == "natural_language"
                    and item.receiver_mode == row.receiver_mode
                    and item.total_uses == row.total_uses
                )), None)
                freeform_row = next((item for item in rows if (
                    item.candidate_id == "natural_language_freeform"
                    and item.receiver_mode == row.receiver_mode
                    and item.total_uses == row.total_uses
                )), None)
                _, labelled_setup, labelled_per_use = intermediate.get(
                    ("natural_language", row.receiver_mode, row.total_uses), ({}, None, None)
                )
                _, freeform_setup, freeform_per_use = intermediate.get(
                    ("natural_language_freeform", row.receiver_mode, row.total_uses), ({}, None, None)
                )
                freeform_applicable = (
                    scenario.metadata_domain == "source"
                    and row.receiver_mode == "model_direct"
                )
                labelled_applicable = (
                    row.candidate_id in (
                        _SOURCE_ALLOWLIST if scenario.metadata_domain == "source"
                        else _SELECTED_ALLOWLIST
                    )
                    and row.receiver_mode != "gateway"
                    or (
                        row.candidate_id in (
                            _SOURCE_ALLOWLIST if scenario.metadata_domain == "source"
                            else _SELECTED_ALLOWLIST
                        )
                        and row.receiver_mode == "gateway"
                        and row.metric_surface == "transport_and_model"
                    )
                )
                if row.receiver_mode == "gateway" and row.candidate_id == "natural_language_freeform":
                    labelled_applicable = False
                if scenario.metadata_domain == "selected_dictionary_transport":
                    freeform_applicable = False
                labelled_comparison = _comparison(
                    row, labelled_row, candidate_setup, candidate_per_use,
                    labelled_setup, labelled_per_use, applicable=labelled_applicable,
                )
                freeform_comparison = _comparison(
                    row, freeform_row, candidate_setup, candidate_per_use,
                    freeform_setup, freeform_per_use, applicable=freeform_applicable,
                )
                completed_rows.append(row.model_copy(update={
                    "vs_natural_language_labelled": labelled_comparison,
                    "vs_natural_language_freeform": freeform_comparison,
                }))

            output_scenarios.append(ScenarioReport(
                metadata_domain=scenario.metadata_domain,
                reuse_unit=scenario.reuse.unit,
                source_hash=source_hash,
                transport_hash=transport_hash,
                rows=tuple(completed_rows),
            ))

    return AgentWireBenchmarkReport(scenarios=tuple(output_scenarios))

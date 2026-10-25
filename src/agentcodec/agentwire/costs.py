"""Complete local AgentWire event accounting; never calls a model or provider."""

from agentcodec.adaptive import meter
from agentcodec.adaptive.contracts import (
    MAX_HISTORY_EVENTS,
    TextAttempt,
    TextComponent,
    TokenizerProfile,
)
from agentcodec.codec import FallbackRequired

from .contracts import (
    COST_EVENT_KINDS,
    COST_PART_LABELS,
    BreakEvenResult,
    CostEvent,
    CostEventKind,
    CostPartLabel,
    CostSummary,
)


_PART_COMPONENT_KIND = {
    "setup": "setup",
    "negotiation": "setup",
    "protocol_instructions": "context",
    "context": "context",
    "dictionary_glossary": "context",
    "dictionary_transfer": "setup",
    "payload": "payload",
    "response": "other",
    "retry": "retry",
    "fallback": "fallback",
    "other": "other",
}
_EVENT_ATTEMPT_KIND = {
    "setup": "setup",
    "negotiation": "setup",
    "dictionary_distribution": "setup",
    "request": "initial",
    "response": "other",
    "retry": "retry",
    "fallback": "fallback",
}


class _CostRejected(FallbackRequired):
    code = "agentwire_invalid_cost"

    def __init__(self) -> None:
        super().__init__("AgentWire cost configuration rejected")


def _unique_events(events: tuple[CostEvent, ...]) -> tuple[CostEvent, ...]:
    if type(events) is not tuple or len(events) > MAX_HISTORY_EVENTS:
        raise _CostRejected()
    unique: dict[str, CostEvent] = {}
    for value in events:
        try:
            event = CostEvent.model_validate(value, strict=True)
        except (TypeError, ValueError):
            raise _CostRejected() from None
        previous = unique.get(event.event_id)
        if previous is not None and previous != event:
            raise _CostRejected()
        unique[event.event_id] = event
    return tuple(unique.values())


def _attempts(events: tuple[CostEvent, ...]) -> tuple[TextAttempt, ...]:
    attempts = []
    for event in events:
        try:
            components = tuple(
                TextComponent(
                    kind=_PART_COMPONENT_KIND[part.label],
                    text=part.text,
                    resource_identity=None,
                )
                for part in event.parts
            )
            attempts.append(TextAttempt(
                event_id=event.event_id,
                attempt_kind=_EVENT_ATTEMPT_KIND[event.kind],
                components=components,
                simulated=True,
                multiplicity=event.multiplicity,
            ))
        except (TypeError, ValueError, KeyError):
            raise _CostRejected() from None
    return tuple(attempts)


def _transport_bytes(events: tuple[CostEvent, ...]) -> int:
    return sum(
        len("".join(part.text for part in event.parts).encode("utf-8"))
        * event.multiplicity
        for event in events
        if "transport" in event.surfaces
    )


def _cannot_compare(events: tuple[CostEvent, ...], reason_code: str) -> CostSummary:
    return CostSummary(
        status="cannot_compare",
        reason_code=reason_code,
        transport_bytes=_transport_bytes(events),
        transport_tokens=None,
        model_input_tokens=None,
        model_output_tokens=None,
        distinct_text_event_tokens=None,
        event_kind_tokens=None,
        part_label_tokens=None,
    )


def measure_cost_events(
    events: tuple[CostEvent, ...], profile: TokenizerProfile | None,
) -> CostSummary:
    """Measure unique complete events with the existing exact local text meter.

    Surface totals are overlapping facets, while distinct_text_event_tokens
    charges each event text once (with its declared multiplicity). Part deltas
    retain the meter's signed prefix attribution and are explanations, not extra
    costs. Repeated IDs must be fully identical; distinct IDs remain distinct
    requests even when their text happens to match.
    """
    unique = _unique_events(events)
    attempts = _attempts(unique)
    transport_bytes = _transport_bytes(unique)
    if profile is None:
        return _cannot_compare(unique, "tokenizer_unavailable")
    try:
        measured = meter.measure_attempts(attempts, profile)
    except meter.MeasurementUnavailable as error:
        return _cannot_compare(unique, error.reason_code)
    if len(measured) != len(unique):
        raise _CostRejected()

    event_totals: dict[CostEventKind, int] = dict.fromkeys(COST_EVENT_KINDS, 0)
    part_totals: dict[CostPartLabel, int] = dict.fromkeys(COST_PART_LABELS, 0)
    transport_tokens = 0
    model_input_tokens = 0
    model_output_tokens = 0
    distinct_total = 0
    for event, attempt, result in zip(unique, attempts, measured, strict=True):
        if (result.event_id != event.event_id
                or result.attempt_kind != attempt.attempt_kind
                or result.simulated is not True
                or result.multiplicity != event.multiplicity
                or len(result.component_tokens) != len(event.parts)):
            raise _CostRejected()
        repeated_tokens = result.total_tokens * event.multiplicity
        distinct_total += repeated_tokens
        event_totals[event.kind] += repeated_tokens
        if "transport" in event.surfaces:
            transport_tokens += repeated_tokens
        if "model_input" in event.surfaces:
            model_input_tokens += repeated_tokens
        if "model_output" in event.surfaces:
            model_output_tokens += repeated_tokens
        for part, delta in zip(event.parts, result.component_tokens, strict=True):
            part_totals[part.label] += delta * event.multiplicity

    return CostSummary(
        status="exact_local_text",
        reason_code=None,
        transport_bytes=transport_bytes,
        transport_tokens=transport_tokens,
        model_input_tokens=model_input_tokens,
        model_output_tokens=model_output_tokens,
        distinct_text_event_tokens=distinct_total,
        event_kind_tokens=event_totals,
        part_label_tokens=part_totals,
    )


def calculate_break_even(
    candidate_setup: int | None,
    candidate_per_message: int | None,
    baseline_setup: int | None,
    baseline_per_message: int | None,
) -> BreakEvenResult:
    """Return the first positive N where candidate cost is strictly lower.

    The inequality is evaluated with integers only:
    candidate_setup + N*candidate_per_message < baseline_setup + N*baseline_per_message.
    Equality is not a win. Unknown inputs remain incomparable rather than zero.
    """
    values = (candidate_setup, candidate_per_message, baseline_setup, baseline_per_message)
    for value in values:
        if value is not None and (type(value) is not int or value < 0):
            raise _CostRejected()
    if any(value is None for value in values):
        return BreakEvenResult(
            status="cannot_compare",
            first_strict_win=None,
            reason_code="required_cost_unknown",
        )

    candidate_setup, candidate_per_message, baseline_setup, baseline_per_message = values
    setup_delta = candidate_setup - baseline_setup
    per_message_savings = baseline_per_message - candidate_per_message
    if per_message_savings > 0:
        first = max(1, setup_delta // per_message_savings + 1)
        return BreakEvenResult(status="finite", first_strict_win=first, reason_code=None)
    if per_message_savings == 0:
        if setup_delta < 0:
            return BreakEvenResult(status="finite", first_strict_win=1, reason_code=None)
        return BreakEvenResult(status="never", first_strict_win=None, reason_code=None)

    # A more expensive per-message candidate can only win at N=1, if at all.
    if setup_delta - per_message_savings < 0:
        return BreakEvenResult(status="finite", first_strict_win=1, reason_code=None)
    return BreakEvenResult(status="never", first_strict_win=None, reason_code=None)

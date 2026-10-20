"""Pure resource snapshots: proposals are not initialization or execution.

Identity/configuration conflicts fail closed. Missing measurement evidence stays
unknown instead of being converted into a free setup or a fabricated history.
No files, clocks, caches, or caller-owned objects are changed here.
"""

from agentcodec.codec import FallbackRequired

from .candidates import validate_explicit_selection
from .contracts import (
    MAX_REPEAT_COUNT,
    CandidatePayload,
    HistoricalEvent,
    ResourceIdentity,
    ResourceSnapshot,
    ScopeSnapshot,
    SelectionRequest,
    freeze_request,
    profile_id,
)


class _ResourceRejected(FallbackRequired):
    code = "adaptive_invalid_resource"

    def __init__(self):
        super().__init__("adaptive resource configuration rejected")


class _EpochRejected(FallbackRequired):
    code = "adaptive_stale_epoch"

    def __init__(self):
        super().__init__("adaptive resource epoch rejected")


def _version_key(identity: ResourceIdentity) -> tuple[str, ...]:
    return (identity.scope_id, identity.receiver_id, identity.user_id,
            identity.resource_kind, identity.version, identity.tokenizer_profile_id)


def _check_hash(identity: ResourceIdentity, hashes: dict[tuple[str, ...], str]) -> None:
    key = _version_key(identity)
    if key in hashes and hashes[key] != identity.content_hash:
        raise _ResourceRejected()
    hashes[key] = identity.content_hash


def _check_exposure(resource: ResourceSnapshot) -> None:
    if resource.state != "invalid" and (
            (resource.state == "builtin") != (resource.exposure == "builtin")):
        raise _ResourceRejected()


def _resource_union(resources: tuple[ResourceSnapshot, ...]) -> tuple[ResourceSnapshot, ...]:
    if type(resources) is not tuple:
        raise _ResourceRejected()
    unique = {}
    hashes = {}
    for value in resources:
        resource = ResourceSnapshot.model_validate(value)
        _check_exposure(resource)
        _check_hash(resource.identity, hashes)
        previous = unique.get(resource.identity)
        if previous is not None and previous != resource:
            raise _ResourceRejected()
        unique[resource.identity] = resource
    return tuple(unique.values())


def _normalize_snapshot(snapshot: ScopeSnapshot, expected_profile: str | None = None) -> ScopeSnapshot:
    snapshot = ScopeSnapshot.model_validate(snapshot)
    resources = _resource_union(snapshot.resources)
    hashes = {}

    def check_identity(identity: ResourceIdentity) -> None:
        nonlocal expected_profile
        if (identity.scope_id != snapshot.scope_id or identity.receiver_id != snapshot.receiver_id
                or identity.user_id != snapshot.user_id):
            raise _ResourceRejected()
        if expected_profile is None:
            expected_profile = identity.tokenizer_profile_id
        if identity.tokenizer_profile_id != expected_profile:
            raise _ResourceRejected()
        _check_hash(identity, hashes)

    for resource in resources:
        check_identity(resource.identity)
        if resource.epoch != snapshot.epoch:
            raise _EpochRejected()
    events = {}
    for event in snapshot.historical_events:
        check_identity(event.identity)
        if event.profile_id != event.identity.tokenizer_profile_id:
            raise _ResourceRejected()
        previous = events.get(event.event_id)
        if previous is not None and previous != event:
            raise _ResourceRejected()
        events[event.event_id] = event
    return ScopeSnapshot(**(snapshot.model_dump() | {
        "resources": resources, "historical_events": tuple(events.values()),
    }))


def validate_snapshot(request: SelectionRequest) -> ScopeSnapshot:
    """Detach, bind, and deduplicate the caller's scope and all explicit identities.

    Incomplete history is preserved, not replaced by zero. Readiness evidence,
    context text, and repeat coverage are assessed per dependency by
    resource_cost_reason; they are not explicit identity conflicts.
    """
    request = freeze_request(request)
    validate_explicit_selection(request)
    snapshot = _normalize_snapshot(request.snapshot, profile_id(request.tokenizer))
    capabilities = request.capabilities
    if (snapshot.user_id != request.expected_user_id
            or snapshot.receiver_id != capabilities.receiver_id):
        raise _ResourceRejected()
    if snapshot.epoch != capabilities.epoch:
        raise _EpochRejected()
    hashes = {}
    for resource in snapshot.resources:
        _check_hash(resource.identity, hashes)
    available = {resource.identity for resource in snapshot.resources}
    for identities in capabilities.candidate_resources.values():
        # Check even unused capabilities: explicit conflicts cannot be ignored.
        for identity in identities:
            _check_hash(identity, hashes)
            if identity not in available:
                raise _ResourceRejected()
    dictionary = request.dictionary.dictionary
    if dictionary is not None:
        for resource in snapshot.resources:
            identity = resource.identity
            if identity.resource_kind == "dictionary" and (
                    identity.version != dictionary.version
                    or identity.content_hash != request.dictionary.content_hash):
                raise _ResourceRejected()
    return snapshot


def required_resources(candidate: CandidatePayload, snapshot: ScopeSnapshot) -> tuple[ResourceSnapshot, ...]:
    """Resolve exact dependencies once, in declaration order; never guess a version."""
    candidate = CandidatePayload.model_validate(candidate)
    snapshot = _normalize_snapshot(snapshot)
    available = {resource.identity: resource for resource in snapshot.resources}
    unique = {}
    for identity in candidate.required_resources:
        resource = available.get(identity)
        if resource is None:
            raise _ResourceRejected()
        unique[identity] = resource
    return tuple(unique.values())


def propose_resources(resources: tuple[ResourceSnapshot, ...]) -> tuple[ResourceSnapshot, ...]:
    """Return detached cold initialization proposals, still cold and unconfirmed.

    The consuming decision labels these as proposed; no fifth persistent state
    is introduced and neither history nor readiness is changed.
    """
    resources = _resource_union(resources)
    bindings = {(item.identity.scope_id, item.identity.receiver_id, item.identity.user_id,
                 item.identity.tokenizer_profile_id, item.epoch) for item in resources}
    if len(bindings) > 1:
        raise _ResourceRejected()
    return tuple(item for item in resources if item.state == "cold")


def resource_cost_reason(resource: ResourceSnapshot, *, repeat_count: int) -> str | None:
    """Report missing per-resource cost evidence without pricing or changing state.

    None means resource evidence is sufficient, not that its cost is zero.
    The later cost layer must still charge every per-request context and leave
    spent/lifecycle totals null whenever scope.history_complete is false.
    """
    resource = ResourceSnapshot.model_validate(resource)
    _check_exposure(resource)
    if type(repeat_count) is not int or not 1 <= repeat_count <= MAX_REPEAT_COUNT:
        raise _ResourceRejected()
    if resource.state == "invalid":
        return "resource_invalid"
    coverage = resource.valid_through_repeat
    if coverage is None or coverage < repeat_count:
        return "resource_validity_unknown"
    if resource.state in ("ready", "builtin") and (
            resource.readiness_basis is None or not resource.readiness_basis.strip()):
        return "required_cost_unknown"
    if resource.exposure == "builtin":
        return None
    if resource.exposure == "per_request_context" and resource.context_text is None:
        return "required_cost_unknown"
    needs_setup = resource.state == "cold" and (
        resource.exposure == "once_per_scope" or resource.independent_setup)
    if needs_setup and resource.setup_text is None:
        return "required_cost_unknown"
    return None


def confirm_initialization(snapshot: ScopeSnapshot, *, expected_epoch: int,
                           event: HistoricalEvent, succeeded: bool) -> ScopeSnapshot:
    """Apply only an explicit successful setup confirmation at the current epoch.

    Failure returns the same state in a new object. Successful changes advance
    the scope epoch and rebind unchanged resources to that new snapshot epoch.
    Replaying an identical event on its already-ready resource is idempotent.
    """
    snapshot = _normalize_snapshot(snapshot)
    event = HistoricalEvent.model_validate(event)
    if type(expected_epoch) is not int or expected_epoch != snapshot.epoch:
        raise _EpochRejected()
    if type(succeeded) is not bool:
        raise _ResourceRejected()
    target = next((item for item in snapshot.resources if item.identity == event.identity), None)
    if (target is None or target.state not in ("cold", "ready") or event.kind != "setup"
            or event.profile_id != event.identity.tokenizer_profile_id):
        raise _ResourceRejected()
    previous = next((item for item in snapshot.historical_events if item.event_id == event.event_id), None)
    if previous is not None and previous != event:
        raise _ResourceRejected()
    if not succeeded or (previous is not None and target.state == "ready"):
        return snapshot
    epoch = snapshot.epoch + 1
    resources = tuple(ResourceSnapshot(**(item.model_dump() | {
        "epoch": epoch,
        **({"state": "ready", "readiness_basis": "confirmed_setup:" + event.event_id}
           if item.identity == target.identity else {}),
    })) for item in snapshot.resources)
    events = snapshot.historical_events + (() if previous is not None else (event,))
    return ScopeSnapshot(**(snapshot.model_dump() | {
        "epoch": epoch, "resources": resources, "historical_events": events,
    }))

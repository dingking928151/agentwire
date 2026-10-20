"""Fixed, opt-in candidates in one unchanged canonical metadata domain.

Rendering is local, not sending. An unavailable candidate has no payload and is
not a fallback attempt. Explicit integrity failures reject the entire operation.
"""

import hashlib

from agentcodec.codec import FallbackRequired
from agentcodec.evaluation.formats import parse_input, render_input
from agentcodec.schema import MessageV1

from .contracts import (
    CANDIDATE_IDS,
    CandidatePayload,
    SelectionRequest,
    canonical_text,
    freeze_request,
)


class _SelectionRejected(FallbackRequired):
    code = "adaptive_invalid_selection"

    def __init__(self):
        super().__init__("adaptive explicit selection rejected")


class _CandidateIntegrityRejected(FallbackRequired):
    code = "adaptive_candidate_integrity"

    def __init__(self):
        super().__init__("adaptive candidate integrity rejected")


def _validate_explicit_request(request: SelectionRequest) -> None:
    """Validate even unused explicit dictionaries before considering eligibility."""
    if request.message.user_id != request.expected_user_id:
        raise _SelectionRejected()
    selection = request.dictionary
    dictionary = selection.dictionary
    if dictionary is None:
        if selection.content_hash is not None:
            raise _SelectionRejected()
        return
    digest = hashlib.sha256(canonical_text(dictionary.model_dump()).encode("utf-8")).hexdigest()
    if dictionary.user_id != request.expected_user_id or selection.content_hash != digest:
        raise _SelectionRejected()
    version = request.message.dictionary_version
    if version is not None and dictionary.version != version:
        raise _SelectionRejected()


def validate_explicit_selection(request: SelectionRequest) -> None:
    """Revalidate current fields and reject explicit configuration conflicts.

    A valid dictionary on a null-version input is not a conflict: it remains
    inapplicable, and must never fill in the input's dictionary_version.
    """
    _validate_explicit_request(freeze_request(request))


def build_candidates(request: SelectionRequest) -> tuple[CandidatePayload, ...]:
    """Return all five IDs in fixed order, with strictly verified payloads only."""
    request = freeze_request(request)
    _validate_explicit_request(request)
    candidates = []
    for candidate_id in CANDIDATE_IDS:
        declared_format = candidate_id.split("_")[0]
        uses_dictionary = candidate_id.endswith("_dictionary")
        resources = request.capabilities.candidate_resources.get(candidate_id, ())
        reasons = []
        if candidate_id not in request.allowlist:
            reasons.append("candidate_not_allowed")
        if candidate_id not in request.capabilities.supported_candidates:
            reasons.append("candidate_not_supported")
        if uses_dictionary:
            if not request.dictionary.enabled:
                reasons.append("dictionary_not_enabled")
            if request.dictionary.dictionary is None:
                reasons.append("dictionary_unavailable")
            if request.message.dictionary_version is None:
                reasons.append("dictionary_version_null")
        elif declared_format != "json" and request.message.dictionary_version is not None:
            reasons.append("dictionary_required")
        if any(resource.identity in resources and resource.state == "invalid"
               for resource in request.snapshot.resources):
            reasons.append("resource_invalid")
        payload = None
        if not reasons:
            # JSON always receives None: its version is metadata, not alias syntax.
            dictionary = request.dictionary.dictionary if uses_dictionary else None
            try:
                payload = render_input(request.message, declared_format, dictionary)
                restored = parse_input(payload, declared_format, request.expected_user_id, dictionary)
                # Revalidate current fields even if a boundary returns a model instance.
                restored = MessageV1.model_validate(vars(restored), strict=True)
                if restored.model_dump() != request.message.model_dump():
                    raise _CandidateIntegrityRejected()
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                raise _CandidateIntegrityRejected() from None
        candidates.append(CandidatePayload(
            candidate_id=candidate_id,
            declared_format=declared_format,
            payload=payload,
            eligibility="unavailable" if reasons else "eligible",
            reason_codes=tuple(reasons),
            required_resources=resources,
        ))
    return tuple(candidates)

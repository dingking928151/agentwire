"""Synthetic-only behavior checks for the fixed adaptive candidate boundary."""

import hashlib
import importlib

import pytest

from agentcodec.adaptive.contracts import (
    CANDIDATE_IDS, SelectionRequest, canonical_text, freeze_request,
)
from agentcodec.codec import FallbackRequired
from agentcodec.evaluation.formats import parse_input
from agentcodec.schema import MessageV1, UserDictionary


def candidates_module():
    return importlib.import_module("agentcodec.adaptive.candidates")


def message(version=None, **changes):
    values = dict(
        message_id="synthetic-1", message_type="delegate",
        task="Do not spend more than $120 by 2026-11-01; approval needed.  ",
        status="blocked", blocker="", next_step=None,
        evidence=["", "e\u0301|;%\n证据", "e\u0301|;%\n证据"],
        constraints=["No payment without approval", "", "预算 ≤ $120  "],
        approval_required=True, user_id="synthetic-user", dictionary_version=version,
    )
    values.update(changes)
    return MessageV1(**values)


def dictionary(**changes):
    values = dict(user_id="synthetic-user", version="v1", aliases={
        "T": "Do not spend more than $120 by 2026-11-01; approval needed.  ",
        "E": "e\u0301|;%\n证据", "N": "No payment without approval",
        "unused": "unmatched whole phrase",
    })
    values.update(changes)
    return UserDictionary(**values)


def selection(value=None, enabled=True):
    value = dictionary() if value is None else value
    digest = hashlib.sha256(canonical_text(value.model_dump()).encode("utf-8")).hexdigest()
    return dict(enabled=enabled, dictionary=value, content_hash=digest)


def request(source=None, **changes):
    values = dict(
        contract_version="adaptive-selection-v1", message=message() if source is None else source,
        expected_user_id="synthetic-user",
        tokenizer=dict(encoding_name="missing-local-meter", library_version="not-provisioned",
                       vocabulary_sha256="not-provisioned", accuracy="unknown", mapping_basis=None),
        capabilities=dict(receiver_id="receiver", supported_candidates=CANDIDATE_IDS,
                          candidate_resources={}, epoch=0),
        snapshot=dict(scope_id="scope", receiver_id="receiver", user_id="synthetic-user",
                      epoch=0, history_complete=True, historical_events=(), resources=()),
    )
    values.update(changes)
    return freeze_request(SelectionRequest(**values))


@pytest.mark.parametrize("allowlist", [("json", "ac1"), CANDIDATE_IDS])
@pytest.mark.parametrize("selected", [False, True])
def test_null_version_never_enables_dictionary(allowlist, selected):
    # Catches metadata auto-fill and dictionary use in the null comparison domain.
    source = message()
    before = source.model_dump()
    req = request(source, allowlist=allowlist, **({"dictionary": selection()} if selected else {}))
    result = candidates_module().build_candidates(req)
    assert tuple(item.candidate_id for item in result) == CANDIDATE_IDS
    assert [item.candidate_id for item in result if item.eligibility == "eligible"] == [
        item for item in ("json", "ac1", "ac2") if item in allowlist
    ]
    for item in result[3:]:
        assert item.eligibility == "unavailable"
        assert item.payload is None
    assert source.model_dump() == before
    assert req.message.model_dump() == before


def test_non_null_domain_keeps_json_metadata():
    # Catches clearing metadata to enable no-dictionary AC or passing a dictionary to JSON.
    req = request(message("v1"), allowlist=CANDIDATE_IDS, dictionary=selection())
    result = candidates_module().build_candidates(req)
    assert [item.candidate_id for item in result if item.eligibility == "eligible"] == [
        "json", "ac1_dictionary", "ac2_dictionary",
    ]
    for item in result:
        if item.eligibility == "eligible":
            selected = req.dictionary.dictionary if item.candidate_id.endswith("_dictionary") else None
            restored = parse_input(item.payload, item.declared_format, req.expected_user_id, selected)
            assert restored.model_dump() == req.message.model_dump()
        else:
            assert item.payload is None
    assert '"dictionary_version":"v1"' in result[0].payload


@pytest.mark.parametrize("conflict", ["owner", "hash", "version", "schema", "message_schema"])
@pytest.mark.parametrize("enabled", [False, True])
def test_explicit_conflict_wins_over_json_and_missing_meter(conflict, enabled):
    # Catches bypassing explicit integrity checks for JSON-only or unmetered requests.
    req = request(message("v1"), original_format="json", allowlist=("json",),
                  dictionary=selection(enabled=enabled))
    if conflict == "owner":
        req.dictionary.dictionary.user_id = "other-user"
    elif conflict == "hash":
        object.__setattr__(req.dictionary, "content_hash", "wrong-hash")
    elif conflict == "version":
        req.dictionary.dictionary.version = "v2"
        object.__setattr__(req.dictionary, "content_hash", selection(req.dictionary.dictionary)["content_hash"])
    elif conflict == "schema":
        req.dictionary.dictionary.aliases["bad"] = False
    else:
        req.message.approval_required = "false"
    module = candidates_module()
    for operation in (module.validate_explicit_selection, module.build_candidates):
        with pytest.raises(FallbackRequired) as caught:
            operation(req)
        assert caught.value.status == "fallback_required"
        assert "synthetic-user" not in str(caught.value)
        assert "Do not spend" not in str(caught.value)


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("version", [None, "v1"])
def test_five_candidates_exact_round_trip(version, empty):
    # Across the two metadata domains all five candidates must preserve all eleven fields.
    changes = dict(task="", blocker=None, next_step="", evidence=[], constraints=[],
                   approval_required=False, status="completed") if empty else {}
    source = message(version, **changes)
    expected = source.model_dump()
    req = request(source, allowlist=CANDIDATE_IDS, dictionary=selection())
    result = candidates_module().build_candidates(req)
    assert len(expected) == 11
    for item in result:
        if item.eligibility == "eligible":
            selected = req.dictionary.dictionary if item.candidate_id.endswith("_dictionary") else None
            restored = parse_input(item.payload, item.declared_format, "synthetic-user", selected)
            assert restored.model_dump() == expected
    assert req.message.model_dump() == expected


def test_low_coverage_keeps_whole_strings_and_literal_alias_text():
    # Catches substring replacement, normalization, literal alias expansion, or learning.
    source = message("v1", task="prefix unmatched whole phrase suffix  ",
                     next_step='{"a":"T"}', blocker="é ≠ e\u0301")
    req = request(source, allowlist=CANDIDATE_IDS, dictionary=selection())
    before = req.dictionary.dictionary.model_dump()
    for item in candidates_module().build_candidates(req):
        if item.eligibility == "eligible":
            selected = req.dictionary.dictionary if item.candidate_id.endswith("_dictionary") else None
            restored = parse_input(item.payload, item.declared_format, "synthetic-user", selected)
            assert restored.model_dump() == source.model_dump()
    assert req.dictionary.dictionary.model_dump() == before


@pytest.mark.parametrize("candidate_id", CANDIDATE_IDS)
@pytest.mark.parametrize("gate", ["allowlist", "capabilities"])
def test_capabilities_and_allowlist_gate_every_candidate(candidate_id, gate, monkeypatch):
    # Catches gating only plain AC while dictionary or JSON payloads escape the gate.
    module = candidates_module()
    version = "v1" if candidate_id.endswith("_dictionary") else None
    req = request(message(version), allowlist=CANDIDATE_IDS, dictionary=selection())
    remaining = tuple(item for item in CANDIDATE_IDS if item != candidate_id)
    if gate == "allowlist":
        req = req.model_copy(update={"allowlist": remaining})
    else:
        capabilities = req.capabilities.model_copy(update={"supported_candidates": remaining})
        req = req.model_copy(update={"capabilities": capabilities})
    real_render = module.render_input

    def reject_disabled_render(source, declared_format, selected):
        rendered_id = declared_format + ("_dictionary" if selected is not None else "")
        assert rendered_id != candidate_id, "disabled candidate must never be rendered"
        return real_render(source, declared_format, selected)

    monkeypatch.setattr(module, "render_input", reject_disabled_render)
    result = module.build_candidates(req)
    disabled = next(item for item in result if item.candidate_id == candidate_id)
    assert disabled.eligibility == "unavailable"
    assert disabled.payload is None
    assert ("candidate_not_allowed" if gate == "allowlist" else "candidate_not_supported") in disabled.reason_codes
    assert all(item.payload is None for item in result if item.eligibility == "unavailable")


@pytest.mark.parametrize("candidate_id", ["json", "ac1", "ac2", "ac1_dictionary", "ac2_dictionary"])
def test_expected_user_id_mismatch_rejects_before_render(candidate_id, monkeypatch):
    module = candidates_module()
    req = request(allowlist=(candidate_id,), expected_user_id="other-user")

    def forbidden_render(*args):
        pytest.fail("identity conflict must be rejected before rendering")

    monkeypatch.setattr(module, "render_input", forbidden_render)
    with pytest.raises(FallbackRequired):
        module.build_candidates(req)
    with pytest.raises(FallbackRequired):
        module.validate_explicit_selection(req)


@pytest.mark.parametrize("wire_format", ["ac1", "ac2"])
@pytest.mark.parametrize("corruption", ["prefix", "enum", "alias", "duplicate_alias_key", "partial"])
def test_corrupt_wire_alias_duplicate_key_is_integrity_failure(wire_format, corruption, monkeypatch):
    # Real strict parsers reject corruption at the renderer boundary; JSON cannot rescue it.
    import json
    from urllib.parse import quote

    module = candidates_module()
    candidate_id = wire_format + "_dictionary"
    req = request(message("v1"), original_format="json", allowlist=("json", candidate_id),
                  dictionary=selection())
    real_render = module.render_input

    def corrupt(source, declared_format, selected):
        payload = real_render(source, declared_format, selected)
        if declared_format != wire_format:
            return payload
        if corruption == "prefix":
            return "AC9" + payload[3:]
        if wire_format == "ac1":
            parts = payload.split("|")
            if corruption == "enum":
                parts[2] = "UNKNOWN"
            elif corruption == "alias":
                parts[3] = quote('{"a":"UNKNOWN"}', safe="")
            elif corruption == "duplicate_alias_key":
                parts[3] = quote('{"a":"UNKNOWN","a":"T"}', safe="")
            else:
                parts.pop()
            return "|".join(parts)
        values = json.loads(payload[4:])
        if corruption == "enum":
            values[1] = "UNKNOWN"
        elif corruption == "alias":
            values[2] = {"a": "UNKNOWN"}
        elif corruption == "duplicate_alias_key":
            return payload.replace('{"a":"T"}', '{"a":"UNKNOWN","a":"T"}', 1)
        else:
            values.pop()
        return "AC2|" + json.dumps(values, ensure_ascii=False, separators=(",", ":"))

    monkeypatch.setattr(module, "render_input", corrupt)
    with pytest.raises(FallbackRequired) as caught:
        module.build_candidates(req)
    assert caught.value.code == "adaptive_candidate_integrity"
    assert str(caught.value) == "adaptive candidate integrity rejected"
    assert caught.value.__cause__ is None


def test_duplicate_json_key_is_integrity_failure(monkeypatch):
    module = candidates_module()
    req = request(original_format="json")
    real_render = module.render_input

    def duplicate(source, declared_format, selected):
        payload = real_render(source, declared_format, selected)
        if declared_format == "json":
            return '{"task":"discarded",' + payload[1:]
        return payload

    monkeypatch.setattr(module, "render_input", duplicate)
    with pytest.raises(FallbackRequired) as caught:
        module.build_candidates(req)
    assert caught.value.code == "adaptive_candidate_integrity"


@pytest.mark.parametrize("field,replacement", [
    ("message_id", "changed"), ("message_type", "result"), ("task", "changed"),
    ("status", "running"), ("blocker", None), ("next_step", ""),
    ("evidence", []), ("constraints", ["changed"]), ("approval_required", False),
    ("user_id", "other-user"), ("dictionary_version", "v1"),
])
def test_partial_recovery_of_any_field_rejects_entire_build(field, replacement, monkeypatch):
    # Catches a permissive boundary returning a valid but lossy/rewritten message.
    module = candidates_module()
    real_parse = module.parse_input

    def partial(payload, declared_format, expected_user_id, selected):
        restored = real_parse(payload, declared_format, expected_user_id, selected)
        return restored.model_copy(update={field: replacement})

    monkeypatch.setattr(module, "parse_input", partial)
    with pytest.raises(FallbackRequired) as caught:
        module.build_candidates(request())
    assert caught.value.code == "adaptive_candidate_integrity"


@pytest.mark.parametrize("version", [None, "v1"])
def test_no_selected_dictionary_keeps_json_and_marks_dictionary_unavailable(version):
    req = request(message(version), allowlist=CANDIDATE_IDS,
                  dictionary=dict(enabled=True, dictionary=None, content_hash=None))
    result = candidates_module().build_candidates(req)
    assert result[0].eligibility == "eligible"
    assert all(item.payload is None for item in result[3:])
    assert all("dictionary_unavailable" in item.reason_codes for item in result[3:])
    if version is not None:
        assert all(item.payload is None for item in result[1:])


def test_required_resources_are_carried_and_invalid_dependency_has_no_payload():
    identity = dict(scope_id="scope", receiver_id="receiver", user_id="synthetic-user",
                    resource_kind="schema", version="v1", content_hash="synthetic-hash",
                    tokenizer_profile_id="synthetic-profile")
    req = request()
    capabilities = req.capabilities.model_copy(update={"candidate_resources": {"ac1": (identity,)}})
    snapshot = req.snapshot.model_copy(update={"resources": (dict(
        identity=identity, state="invalid", exposure="once_per_scope", epoch=0,
        valid_through_repeat=None, readiness_basis=None, setup_text=None, context_text=None,
    ),)})
    req = freeze_request(req.model_copy(update={"capabilities": capabilities, "snapshot": snapshot}))
    result = candidates_module().build_candidates(req)
    assert result[0].eligibility == "eligible"
    assert result[1].eligibility == "unavailable"
    assert result[1].payload is None
    assert result[1].reason_codes == ("resource_invalid",)
    assert result[1].required_resources == req.capabilities.candidate_resources["ac1"]

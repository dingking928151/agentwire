"""The local mock restores data; no status or approval flag authorizes actions."""

import builtins
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import random
import socket
import subprocess
import time
import urllib.request

import pytest

from agentcodec.codec import FallbackRequired
from agentcodec.evaluation.contracts import EvalRequest
from agentcodec.evaluation.formats import render_input
from agentcodec.schema import MessageV1, UserDictionary


FORMATS = ("natural_language", "json", "ac1", "ac2")
DATA = {
    "message_id": "m1", "message_type": "delegate", "task": "T 不付款",
    "status": "completed", "blocker": None, "next_step": "",
    "evidence": ["E", "", "E"], "constraints": ["Do not publish. ", "Do not publish. "],
    "approval_required": False, "user_id": "user-1", "dictionary_version": None,
}
DICTIONARY = UserDictionary(user_id="user-1", version="v1", aliases={"A": "T 不付款"})


def api():
    return importlib.import_module("agentcodec.evaluation.mock")


def digest(data):
    # Independent stdlib oracle, not the production canonical/hash helpers.
    return hashlib.sha256(json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def request(input_format="json", data=None, dictionary=None, **changes):
    data = DATA if data is None else data
    return EvalRequest(
        **({"contract_version": "offline-eval-v1", "case_id": "local/m1/" + input_format,
            "attempt_index": 0, "input_format": input_format,
            "payload": render_input(MessageV1(**data), input_format, dictionary),
            "expected_user_id": data["user_id"], "dictionary": dictionary,
            "operation": "inspect_message_v1"} | changes)
    )


def receipt(data=None, **changes):
    data = DATA if data is None else data
    return {"receipt_version": "mock-receipt-v1", "operation": "inspect_message_v1",
            "message": data.copy(), "message_sha256": digest(data), "actions": []} | changes


@pytest.mark.parametrize("input_format", FORMATS)
def test_same_request_same_response(input_format):
    # Catches state, attempt counters, random IDs, or noncanonical serialization.
    module = api()
    req = request(input_format)
    before = req.model_dump()
    adapter = module.DeterministicMockAdapter()
    first = adapter.invoke(req)
    assert first.model_dump() == adapter.invoke(req).model_dump()
    assert first.model_dump() == module.DeterministicMockAdapter().invoke(req).model_dump()
    assert first.status == "ok" and first.error is None
    assert first.raw_output == json.dumps(receipt(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert adapter.adapter_id == "deterministic-mock-v1"
    assert req.model_dump() == before


def test_adapter_receives_no_oracle():
    # Only the public request is supplied; no source/case/expected receipt exists.
    adapter: api().OfflineAdapter = api().DeterministicMockAdapter()
    req = request()
    response = adapter.invoke(req)
    assert api().validate_receipt(response.raw_output, req).message.model_dump() == DATA


@pytest.mark.parametrize("input_format", FORMATS)
@pytest.mark.parametrize("status", ["pending", "running", "blocked", "completed", "failed", "cancelled"])
@pytest.mark.parametrize("approval", [False, True])
def test_receipt_is_read_only(input_format, status, approval):
    # Catches interpreting status/approval as permission or losing any field.
    data = DATA | {"status": status, "approval_required": approval}
    req = request(input_format, data)
    response = api().DeterministicMockAdapter().invoke(req)
    restored = api().validate_receipt(response.raw_output, req)
    assert restored.operation == "inspect_message_v1"
    assert restored.actions == []
    assert restored.message.model_dump() == data
    assert restored.message_sha256 == digest(data)


@pytest.mark.parametrize("mutation", [
    "hash", "operation", "actions", "duplicate", "nested-duplicate", "missing-message",
    "version", "extra", "message-extra", "missing-field", "bool-coercion", "null-actions",
    "wrong-hash-type", "surrogate", "namespace",
])
def test_forged_receipt_is_invalid(mutation):
    # Catches trusting success claims, coercion, duplicate keys, or unbound metadata.
    forged = receipt()
    if mutation == "hash":
        forged["message_sha256"] = "0" * 64
    elif mutation == "operation":
        forged["operation"] = "execute_message_v1"
    elif mutation == "actions":
        forged["actions"] = ["publish"]
    elif mutation == "missing-message":
        del forged["message"]
    elif mutation == "version":
        forged["receipt_version"] = "mock-receipt-v2"
    elif mutation == "extra":
        forged["extra"] = None
    elif mutation == "message-extra":
        forged["message"]["extra"] = None
    elif mutation == "missing-field":
        del forged["message"]["blocker"]
    elif mutation == "bool-coercion":
        forged["message"]["approval_required"] = 0
    elif mutation == "null-actions":
        forged["actions"] = None
    elif mutation == "wrong-hash-type":
        forged["message_sha256"] = 123
    elif mutation == "surrogate":
        forged["message"]["task"] = "\ud800"
    elif mutation == "namespace":
        forged = receipt(DATA | {"user_id": "other-user"})
    raw = json.dumps(forged)
    if mutation == "duplicate":
        raw = raw[:-1] + ',"actions":[]}'
    elif mutation == "nested-duplicate":
        raw = raw.replace('"message": {', '"message": {"task":"T 不付款",', 1)
    with pytest.raises(FallbackRequired):
        api().validate_receipt(raw, request())


@pytest.mark.parametrize("raw", ["", "{", "null", "[]", "true", "{}{}", "NaN", "Infinity", None, b"{}", 1])
def test_malformed_receipt_requires_fallback(raw):
    with pytest.raises(FallbackRequired):
        api().validate_receipt(raw, request())


def test_validator_does_not_treat_request_payload_as_oracle():
    # A self-consistent changed task is valid structurally; later scoring must catch it.
    changed = DATA | {"task": "different task"}
    req = request(payload="not parseable")
    restored = api().validate_receipt(json.dumps(receipt(changed)), req)
    assert restored.message.model_dump() == changed


@pytest.mark.parametrize("input_format", ("natural_language", "json"))
def test_plain_receipt_preserves_nonnull_dictionary_metadata(input_format):
    data = DATA | {"dictionary_version": "legacy-v0"}
    req = request(input_format, data)
    response = api().DeterministicMockAdapter().invoke(req)
    assert api().validate_receipt(response.raw_output, req).message.model_dump() == data
    selected = req.model_copy(update={"dictionary": DICTIONARY})
    with pytest.raises(FallbackRequired):
        api().validate_receipt(json.dumps(receipt(data)), selected)


@pytest.mark.parametrize("input_format", ("ac1", "ac2"))
def test_paired_receipts_validate_without_mutating_dictionary(input_format):
    data = DATA | {"dictionary_version": "v1"}
    req = request(input_format, data, DICTIONARY)
    before = req.model_dump()
    response = api().DeterministicMockAdapter().invoke(req)
    assert api().validate_receipt(response.raw_output, req).message.model_dump() == data
    assert req.model_dump() == before


@pytest.mark.parametrize("input_format", ("ac1", "ac2"))
@pytest.mark.parametrize("mutation", ["missing", "wrong-user", "wrong-version", "receipt-version", "null-version"])
def test_receipt_dictionary_metadata_must_match_selection(input_format, mutation):
    data = DATA | {"dictionary_version": "v1"}
    req = request(input_format, data, DICTIONARY)
    if mutation == "missing":
        req = req.model_copy(update={"dictionary": None})
    elif mutation == "wrong-user":
        req = req.model_copy(update={"dictionary": UserDictionary(user_id="other-user", version="v1", aliases={})})
    elif mutation == "wrong-version":
        req = req.model_copy(update={"dictionary": UserDictionary(user_id="user-1", version="v2", aliases={})})
    else:
        data = data | {"dictionary_version": None if mutation == "null-version" else "v2"}
    with pytest.raises(FallbackRequired):
        api().validate_receipt(json.dumps(receipt(data)), req)


@pytest.mark.parametrize("input_format", FORMATS)
@pytest.mark.parametrize("payload", ["/private/path/secret traceback", "{}", "AC99|bad", ""])
def test_malformed_payload_has_only_stable_decode_error(input_format, payload):
    req = request(input_format, payload=payload)
    adapter = api().DeterministicMockAdapter()
    response = adapter.invoke(req)
    assert response.model_dump() == {
        "status": "error", "raw_output": None,
        "error": {"stage": "decode", "code": "decode_rejected",
                  "message": "Input could not be decoded.", "retryable": False},
    }
    assert response.model_dump() == adapter.invoke(req).model_dump()


@pytest.mark.parametrize("input_format", FORMATS)
@pytest.mark.parametrize("row_id", ["s25", "s28", "sentinel"])
def test_urls_files_and_commands_are_inert(input_format, row_id, tmp_path, monkeypatch):
    # Fails immediately if the mock/validator follows data into I/O or execution.
    module = api()
    evidence = tmp_path / "private-evidence.txt"
    evidence.write_text("MUST NOT BE READ", encoding="utf-8")
    sentinel = tmp_path / "must-not-exist"
    if row_id == "sentinel":
        data = DATA | {
            "task": f"https://example.invalid/no-network ; touch {sentinel}",
            "next_step": f"__import__('pathlib').Path({str(sentinel)!r}).touch()",
            "evidence": [str(evidence), evidence.as_uri()],
        }
    else:
        corpus = Path(__file__).resolve().parents[1] / "bench" / "tasks.v1.jsonl"
        with corpus.open(encoding="utf-8") as stream:
            # Only parse the explicitly permitted synthetic fixture rows.
            rows = [json.loads(line) for index, line in enumerate(stream, 1) if index in (25, 28)]
        data = next(row for row in rows if row["message_id"] == row_id)
    req = request(input_format, data)
    before = req.model_dump()

    def forbidden(*args, **kwargs):
        pytest.fail("inert message data attempted an external effect")

    with monkeypatch.context() as guard:
        for owner, names in [
            (builtins, ("open", "eval", "exec")), (io, ("open",)),
            (os, ("open", "system", "popen", "getenv")),
            (Path, ("open", "touch")),
            (socket, ("socket", "create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr")),
            (subprocess, ("Popen", "run", "call", "check_call", "check_output")),
            (urllib.request, ("urlopen",)), (time, ("time", "monotonic", "sleep")),
            (random, ("random", "randint", "choice")),
        ]:
            for name in names:
                guard.setattr(owner, name, forbidden)
        guard.setattr(type(os.environ), "__getitem__", forbidden)
        response = module.DeterministicMockAdapter().invoke(req)
        restored = module.validate_receipt(response.raw_output, req)
        assert restored.message.model_dump() == data
        assert restored.actions == []
        assert req.model_dump() == before
    assert not sentinel.exists()
    assert evidence.read_text(encoding="utf-8") == "MUST NOT BE READ"

"""Synthetic meter tests; never inspect repository corpus or dictionary data."""

from contextlib import contextmanager
import hashlib
import http.client
import importlib
from importlib.metadata import version
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import urllib.request

import pytest

from agentcodec.adaptive.contracts import (
    MAX_HISTORY_EVENTS, CandidatePayload, ResourceIdentity, ResourceSnapshot,
    ScopeSnapshot, TextAttempt, TextComponent, TextEnvelope, TokenizerProfile,
    profile_id,
)
from agentcodec.adaptive.resources import required_resources, resource_cost_reason
from agentcodec.benchmark import _VOCAB_HASHES
from agentcodec.codec import FallbackRequired


@pytest.fixture
def api():
    # Missing implementation is a test failure, not a collection/environment error.
    class Meter:
        def __getattr__(self, name):
            return getattr(importlib.import_module("agentcodec.adaptive.meter"), name)

        def __setattr__(self, name, value):
            setattr(importlib.import_module("agentcodec.adaptive.meter"), name, value)

    return Meter()


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("external I/O")
        raise AssertionError("external I/O forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    yield calls
    assert calls == []


def profile(name="cl100k_base", **changes):
    return TokenizerProfile(**(dict(
        encoding_name=name, library_version=version("tiktoken"),
        vocabulary_sha256=_VOCAB_HASHES.get(name, "0" * 64),
        accuracy="exact_local_text", mapping_basis=None,
    ) | changes))


def component(text, kind="payload", identity=None):
    return TextComponent(kind=kind, text=text, resource_identity=identity)


def attempt(*components, **changes):
    return TextAttempt(**(dict(event_id="initial-1", attempt_kind="initial",
                              components=components, simulated=False) | changes))


def stub_counter(monkeypatch, api, counts):
    # Only the module's counting boundary is replaced; event handling stays real.
    seen = []

    class Encoding:
        def encode_ordinary(self, text):
            seen.append(text)
            return [0] * counts[text]

    @contextmanager
    def get_encoding(profile):
        yield Encoding()

    monkeypatch.setattr(api, "_get_encoding", get_encoding)
    return seen


def test_whole_text_and_signed_prefix_deltas(api, monkeypatch):
    # Catches independent component sums, sorted components, and clipped negatives.
    seen = stub_counter(monkeypatch, api, {
        "": 0, "a": 4, "ab": 2, "abc": 5, "abcd": 3,
        "b": 4, "c": 4, "d": 4,
    })
    measured = api.measure_attempt(attempt(
        component("a", "setup"), component("b", "context"),
        component("c"), component("d", "other"), multiplicity=3,
    ), profile())
    assert measured.total_tokens == 3  # Independent component sum would be 16.
    assert measured.component_tokens == (4, -2, 3, -2)
    assert sum(measured.component_tokens) == 3
    assert measured.multiplicity == 3 and measured.simulated is False
    assert "abcd" in seen and not {"b", "c", "d"}.intersection(seen)
    assert seen[0] == "abcd"  # Whole-request count is authoritative, not a component sum.


def resource(**changes):
    identity = ResourceIdentity(scope_id="scope", receiver_id="receiver", user_id="user",
        resource_kind="schema", version="v1", content_hash="1" * 64,
        tokenizer_profile_id=profile_id(profile()))
    return ResourceSnapshot(**(dict(identity=identity, state="ready", exposure="per_request_context",
        epoch=0, valid_through_repeat=3, readiness_basis="caller confirmed",
        setup_text="sync", context_text="ctx", independent_setup=False) | changes))


def test_initial_context_not_double_setup(api, monkeypatch):
    # Catches automatic setup injection and ready-as-free accounting.
    stub_counter(monkeypatch, api, {"ctx": 3, "ctxbody": 6, "sync": 4})
    ready = resource()
    assert resource_cost_reason(ready, repeat_count=3) is None
    initial = attempt(component(ready.context_text, "context", ready.identity), component("body"),
                      multiplicity=3)
    measured = api.measure_attempts((initial,), profile())
    assert [(item.attempt_kind, item.total_tokens, item.component_tokens, item.multiplicity)
            for item in measured] == [("initial", 6, (3, 3), 3)]
    assert sum(item.total_tokens * item.multiplicity for item in measured) == 18
    independent = ready.model_copy(update={"independent_setup": True})
    setup = attempt(component(independent.setup_text, "setup", independent.identity),
                    event_id="setup-1", attempt_kind="setup")
    with_setup = api.measure_attempts((setup, initial), profile())
    assert [item.total_tokens for item in with_setup] == [4, 6]
    assert sum(item.total_tokens * item.multiplicity for item in with_setup) == 22


def test_shared_resource_and_duplicate_event_charged_once(api, monkeypatch):
    # Catches per-dependency resource charging and event-ID-only conflict masking.
    stub_counter(monkeypatch, api, {"sync": 4, "body": 2})
    shared = resource(state="cold", exposure="once_per_scope", context_text=None)
    snap = ScopeSnapshot(scope_id="scope", receiver_id="receiver", user_id="user", epoch=0,
        history_complete=True, historical_events=(), resources=(shared, shared))
    candidate = CandidatePayload(candidate_id="ac1", declared_format="ac1", payload="body",
        eligibility="eligible", reason_codes=(), required_resources=(shared.identity, shared.identity))
    needed = required_resources(candidate, snap)
    assert len(needed) == 1
    setup = attempt(*(component(item.setup_text, "setup", item.identity) for item in needed),
                    event_id="setup-1", attempt_kind="setup")
    initial = attempt(component("body"), multiplicity=3)
    result = api.measure_attempts((setup, setup, initial, initial), profile())
    assert [(item.event_id, item.total_tokens, item.multiplicity) for item in result] == [
        ("setup-1", 4, 1), ("initial-1", 2, 3)]
    assert sum(item.total_tokens * item.multiplicity for item in result) == 10
    for changes in ({"multiplicity": 2}, {"simulated": True}, {"attempt_kind": "other"},
                    {"components": (component("sync", "other"),)}):
        with pytest.raises(FallbackRequired) as error:
            api.measure_attempts((setup, setup.model_copy(update=changes)), profile())
        assert "sync" not in str(error.value)
    # Equal text with distinct event IDs is two separate requests, not a duplicate.
    other = initial.model_copy(update={"event_id": "initial-2"})
    assert len(api.measure_attempts((initial, other), profile())) == 2


def cache_path(name):
    directory = os.environ.get("TIKTOKEN_CACHE_DIR", os.environ.get(
        "DATA_GYM_CACHE_DIR", str(Path(tempfile.gettempdir()) / "data-gym-cache")))
    url = f"https://openaipublic.blob.core.windows.net/encodings/{name}.tiktoken"
    return Path(directory) / hashlib.sha1(url.encode()).hexdigest()


@pytest.mark.parametrize("case,reason", [
    ("unknown", "tokenizer_unavailable"), ("missing", "tokenizer_unavailable"),
    ("corrupt", "tokenizer_unavailable"), ("disabled", "tokenizer_unavailable"),
    ("version", "profile_unreliable"), ("hash", "profile_unreliable"),
    ("surrogate", "surrogate_only"), ("accuracy", "profile_unreliable"),
])
def test_unknown_missing_corrupt_surrogate_never_downloads(api, monkeypatch, tmp_path, case, reason):
    # Catches guessing, cache bypass, and invoking the normal downloader after a bad check.
    import tiktoken

    def no_lookup(*args, **kwargs):
        pytest.fail("invalid profile/cache must not call tiktoken.get_encoding")

    monkeypatch.setattr(tiktoken, "get_encoding", no_lookup)
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    current = profile()
    if case == "unknown":
        current = profile("not-a-tokenizer")
    elif case == "version":
        current = profile(library_version="not-installed")
    elif case == "hash":
        current = profile(vocabulary_sha256="0" * 64)
    elif case == "surrogate":
        current = profile(accuracy="surrogate_only")
    elif case == "accuracy":
        current = profile(accuracy="unknown")
    elif case == "disabled":
        monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "")
    elif case == "corrupt":
        cache_path(current.encoding_name).write_bytes(b"synthetic corrupt cache")
    for operation in (lambda: api.validate_profile(current),
                      lambda: api.measure_attempt(attempt(component("private synthetic text")), current),
                      lambda: api.measure_attempts((), current)):
        with pytest.raises(api.MeasurementUnavailable) as error:
            operation()
        assert error.value.reason_code == reason
        assert str(error.value) == reason
        assert "private" not in repr(error.value) and str(tmp_path) not in repr(error.value)


def test_cache_replacement_and_lf_boundary(api, monkeypatch, tmp_path):
    # Catches stale checked bytes and silent CLI-newline/special-token treatment.
    current = profile()
    api.validate_profile(current)
    envelope = TextEnvelope(prefix="ctx:", suffix="!", final_lf=False)
    literal = "<|endoftext|> synthetic 中文 e\u0301"
    text = envelope.prefix + literal + envelope.suffix
    plain = api.measure_attempt(attempt(component(text)), current)
    with_lf = api.measure_attempt(attempt(component(text), component("\n", "other")), current)
    import tiktoken
    encoding = tiktoken.get_encoding(current.encoding_name)
    assert plain.total_tokens == len(encoding.encode_ordinary(text))
    assert with_lf.total_tokens == len(encoding.encode_ordinary(text + "\n"))
    assert with_lf.component_tokens[-1] == len(encoding.encode_ordinary(text + "\n")) - plain.total_tokens
    # The cache is already initialized; a changed cache must still fail closed.
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    cache_path(current.encoding_name).write_bytes(b"synthetic replacement")
    with pytest.raises(api.MeasurementUnavailable):
        api.measure_attempt(attempt(component(literal)), current)


@pytest.mark.parametrize("name", ["cl100k_base", "o200k_base"])
def test_provisioned_ordinary_text_matches_independent_count(api, name):
    # Catches wrong encoding, normalization, component-only counts, and lost LF.
    current = profile(name)
    api.validate_profile(current)  # A missing provision is an environment failure, never a skip/download.
    import tiktoken
    independent = tiktoken.get_encoding(name)
    pieces = (component("schema\n", "setup"), component("上下文 ", "context"),
              component("<|endoftext|> A\u030a \t\n"), component("END\n", "other"))
    measured = api.measure_attempt(attempt(*pieces), current)
    whole = "schema\n上下文 <|endoftext|> A\u030a \t\nEND\n"
    assert measured.total_tokens - len(independent.encode_ordinary(whole)) == 0
    assert sum(measured.component_tokens) == measured.total_tokens


def test_empty_request_and_request_boundaries(api, monkeypatch):
    # Catches joining independent requests or applying multiplicity twice.
    stub_counter(monkeypatch, api, {"": 0, "a": 4, "b": 4, "ab": 2})
    empty = api.measure_attempt(attempt(), profile())
    assert empty.total_tokens == 0 and empty.component_tokens == ()
    empty_component = api.measure_attempt(attempt(component("")), profile())
    assert empty_component.total_tokens == 0 and empty_component.component_tokens == (0,)
    first = attempt(component("a"), multiplicity=2)
    second = attempt(component("b"), event_id="second", attempt_kind="retry", simulated=True)
    results = api.measure_attempts((first, second), profile())
    assert [(item.total_tokens, item.multiplicity, item.attempt_kind, item.simulated)
            for item in results] == [(4, 2, "initial", False), (4, 1, "retry", True)]
    assert api.measure_attempts((), profile()) == ()


def test_mutated_attempts_and_profile_are_revalidated(api, monkeypatch):
    # Catches trusting frozen=True on a caller-mutated model instance.
    stub_counter(monkeypatch, api, {"a": 1})
    bad = attempt(component("a"))
    object.__setattr__(bad.components[0], "text", "\ud800")
    with pytest.raises(FallbackRequired):
        api.measure_attempt(bad, profile())
    bad_profile = profile()
    object.__setattr__(bad_profile, "library_version", 7)
    with pytest.raises(FallbackRequired):
        api.measure_attempt(attempt(component("a")), bad_profile)
    with pytest.raises(FallbackRequired):
        api.measure_attempts([attempt(component("a"))], profile())


def test_ledger_capacity_is_checked_before_counting(api, monkeypatch):
    # Catches silently processing an oversized expanded event ledger.
    def forbidden(*args):
        pytest.fail("capacity must be checked before counting")
    monkeypatch.setattr(api, "_get_encoding", forbidden)
    large = attempt(component("synthetic"), multiplicity=MAX_HISTORY_EVENTS)
    with pytest.raises(FallbackRequired):
        api.measure_attempts((large, large.model_copy(update={"event_id": "extra"})), profile())


@pytest.mark.parametrize("stage", ["initialization", "counting", "library", "encoding"])
def test_replacement_between_checks_is_unavailable(api, monkeypatch, tmp_path, stage):
    # Catches TOCTOU downloads and returning a number after identity replacement.
    current = profile()
    api.validate_profile(current)
    source = cache_path(current.encoding_name)
    frozen_bytes = source.read_bytes()
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    target = cache_path(current.encoding_name)
    target.write_bytes(frozen_bytes)
    import tiktoken

    def no_lookup(*args, **kwargs):
        pytest.fail("adaptive construction must not enter the lazy downloader")
    monkeypatch.setattr(tiktoken, "get_encoding", no_lookup)
    if stage == "initialization":
        build = api._build_encoding
        def replacing_build(*args):
            target.write_bytes(b"synthetic replacement during initialization")
            return build(*args)
        monkeypatch.setattr(api, "_build_encoding", replacing_build)
    else:
        encode = tiktoken.Encoding.encode_ordinary
        def replacing_encode(self, text):
            result = encode(self, text)
            if stage == "counting":
                target.write_bytes(b"synthetic replacement during counting")
            elif stage == "library":
                monkeypatch.setattr(tiktoken, "__version__", "changed-during-count")
            else:
                self.name = "changed-during-count"
            return result
        monkeypatch.setattr(tiktoken.Encoding, "encode_ordinary", replacing_encode)
    with pytest.raises(api.MeasurementUnavailable) as error:
        api.measure_attempt(attempt(component("synthetic private input")), current)
    assert error.value.reason_code == "tokenizer_unavailable"
    assert str(error.value) == "tokenizer_unavailable"

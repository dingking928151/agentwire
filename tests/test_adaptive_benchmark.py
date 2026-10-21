"""Independent benchmark contracts, using only new synthetic test inputs.

The opt-in local validation entry point at the bottom reads approved fixtures
without returning their text. It is not a substitute-tokenizer pytest test.
"""
from contextlib import contextmanager
import hashlib
import importlib
from importlib.metadata import version
import json
from pathlib import Path
import socket
import subprocess

import pytest

from agentcodec.adaptive import meter
from agentcodec.adaptive.contracts import (
    CANDIDATE_IDS, CostPolicy, DictionarySelection, ResourceIdentity,
    ResourceSnapshot, SelectionRequest, TokenizerProfile, canonical_text, instance_id, profile_id,
)
from agentcodec.adaptive.offline import (
    OfflineCase, OfflineOutcome, OfflinePath, OfflineScenario, ScheduledSnapshot,
)
from agentcodec.adaptive.selector import select_encoding
from agentcodec.benchmark import _VOCAB_HASHES
from agentcodec.schema import MessageV1, UserDictionary


MESSAGE = dict(message_id="synthetic", message_type="delegate", task="do not spend $20 Ω ",
    status="pending", blocker=None, next_step="", evidence=["b", "a", "b"], constraints=[],
    approval_required=False, user_id="user", dictionary_version=None)
SECRET = "synthetic-private-marker"


def api():
    return importlib.import_module("agentcodec.adaptive.benchmark")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def profile(name="cl100k_base", **changes):
    return TokenizerProfile(**(dict(encoding_name=name, library_version=version("tiktoken"),
        vocabulary_sha256=_VOCAB_HASHES.get(name, "0" * 64), accuracy="exact_local_text",
        mapping_basis=None) | changes))


def request(message=None, *, prof=None, exposure="builtin", state="builtin", allowlist=CANDIDATE_IDS,
            dictionary=None, sdk_count=1, history=True, threshold=0, resource_text="SETUP"):
    message = MessageV1(**MESSAGE) if message is None else message
    prof = profile() if prof is None else prof
    identity = ResourceIdentity(scope_id="scope", receiver_id="receiver", user_id=message.user_id,
        resource_kind="schema", version="v1", content_hash="1" * 64,
        tokenizer_profile_id=profile_id(prof))
    resource = ResourceSnapshot(identity=identity, state=state, exposure=exposure, epoch=0,
        valid_through_repeat=100, readiness_basis="explicit local decoder confirmation",
        setup_text=resource_text, context_text="CTX", independent_setup=False)
    policy = CostPolicy(repeat_count=sdk_count, min_savings_tokens=threshold,
        repeat_declaration=None if sdk_count == 1 else dict(unit="same_message_instance",
            total_count=sdk_count, instance_sha256=instance_id(message)))
    return SelectionRequest(contract_version="adaptive-selection-v1", message=message,
        expected_user_id=message.user_id, tokenizer=prof, original_format="json", allowlist=allowlist,
        capabilities=dict(receiver_id="receiver", epoch=0, supported_candidates=CANDIDATE_IDS,
            candidate_resources={c: (identity,) for c in CANDIDATE_IDS if c != "json"}),
        snapshot=dict(scope_id="scope", receiver_id="receiver", user_id=message.user_id, epoch=0,
            history_complete=history, historical_events=(), resources=(resource,)), policy=policy,
        dictionary=DictionarySelection() if dictionary is None else DictionarySelection(enabled=True,
            dictionary=dictionary, content_hash=sha(canonical_text(dictionary.model_dump()).encode())))


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external operations forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture
def synthetic_counter(monkeypatch):
    # The only synthetic boundary is tokenization; all routing/replay is real.
    class Encoding:
        def encode_ordinary(self, text):
            if not text:
                return []
            if text == "SETUP":
                return [0] * 12
            if text == "CTX":
                return [0] * 3
            context = 3 if text.startswith("CTX") else 0
            body = text[3:] if context else text
            n = 8 if body.startswith("AC1|") else 10 if body.startswith("AC2|") else 20
            return [0] * (n + context)
    @contextmanager
    def get_encoding(prof):
        if prof.accuracy != "exact_local_text" or prof.encoding_name == "unknown":
            raise meter.MeasurementUnavailable("profile_unreliable")
        yield Encoding()
    monkeypatch.setattr(meter, "_get_encoding", get_encoding)


def fixture_input(tmp_path, corpus="diverse", messages=None, dictionary=None):
    messages = [MessageV1(**MESSAGE)] if messages is None else messages
    source = tmp_path / (corpus + ".jsonl")
    data = b"".join((canonical_text(m.model_dump()) + "\n").encode() for m in messages)
    source.write_bytes(data)
    dpath = None
    dhash = None
    if dictionary is not None:
        dpath = tmp_path / (corpus + "-dictionary.json")
        raw = canonical_text(dictionary.model_dump()).encode()
        dpath.write_bytes(raw)
        dhash = sha(raw)
    return api().BenchmarkInput(corpus_id=corpus, source_path=str(source), source_sha256=sha(data),
        source_count=len(messages), dictionary_path=None if dpath is None else str(dpath),
        dictionary_sha256=dhash, dictionary_source="none" if dictionary is None else "local_file")


def scenario(reqs, name="clean-main", repeats=1, *, sources=None, fixed=None, failure=False,
             remainder="continue_declared", invalid_after=False):
    cases = tuple(OfflineCase(source_message=req.message if sources is None else sources[i],
        request=req, metadata_authorized=sources is not None) for i, req in enumerate(reqs))
    schedules, outcomes, decisions = [], [], []
    for r in range(repeats):
        for c, req in enumerate(reqs):
            setup = req.snapshot
            initial = setup
            if any(x.state == "cold" for x in setup.resources):
                initial = setup.model_copy(update={"epoch": 1, "resources": tuple(
                    x.model_copy(update={"state": "ready", "epoch": 1}) for x in setup.resources)})
            if invalid_after and c > 0:
                initial = setup.model_copy(update={"resources": tuple(
                    x.model_copy(update={"state": "invalid"}) for x in setup.resources)})
            # Freeze each SDK decision before supplying the failure script.
            selected = select_encoding(req).selected_candidate or req.original_format
            decisions.append(selected if fixed is None else fixed)
            stages = ("initial", "retry", "fallback") if failure and c == 0 and r == 0 else ("initial",)
            schedules.extend(ScheduledSnapshot(case_index=c, repeat_index=r, stage=stage,
                snapshot=setup if stage == "setup" else initial) for stage in ("setup", *stages))
            outcomes.extend(OfflineOutcome(case_index=c, repeat_index=r, stage=stage,
                success=stage == stages[-1], response_text="") for stage in stages)
    return OfflineScenario(scenario_id=name, cases=cases, whole_corpus_repeats=repeats,
        fixed_decisions=tuple(decisions), resource_schedule=tuple(schedules),
        paths=(OfflinePath(path_id=SECRET, outcomes=tuple(outcomes)),), remainder_policy=remainder)


def run(inputs, scenarios):
    config = api().AdaptiveBenchmarkConfig(scenarios=tuple(scenarios), code_revision="synthetic-revision",
        input_manifest=tuple(inputs), scenario_corpora={s.scenario_id: inputs[i % len(inputs)].corpus_id
                                                    for i, s in enumerate(scenarios)})
    return api().run_adaptive_benchmark(config)


def test_report_separates_domains_and_never_blends_corpora(tmp_path, synthetic_counter):
    # Catches merged denominators and a source JSON cost entering transport minimum.
    source = MessageV1(**MESSAGE)
    dictionary = UserDictionary(user_id="user", version="manual-v1", aliases={"x": source.task})
    transport = source.model_copy(update={"dictionary_version": dictionary.version})
    a = fixture_input(tmp_path, "diverse", dictionary=dictionary)
    b = fixture_input(tmp_path, "repetitive", dictionary=dictionary)
    reqs = [request(), request(transport, dictionary=dictionary),
            request(exposure="per_request_context", state="ready"),
            request(allowlist=("json", "ac1")), request()]
    scenes = [scenario((req,), name=f"clean-{i}", sources=(source,) if i == 1 else None)
              for i, req in enumerate(reqs)]
    scenes[-1] = scenario((reqs[-1],), name="negative-fault", failure=True)
    out = run((a, b), scenes)
    groups = out.report["scenarios"]
    assert list(groups) == ["0", "1", "2", "3", "4"]
    assert [v["corpus_id"] for v in groups.values()] == ["diverse", "repetitive", "diverse", "repetitive", "diverse"]
    assert [v["logical_case_count"] for v in groups.values()] == [1] * 5
    assert groups["1"]["comparison_domain"]["metadata_selection"] == "authorized_transport"
    record = out.records[1]
    assert set(record["candidate_costs"]) == {"json", "ac1_dictionary", "ac2_dictionary"}
    assert record["source_json_reference"]["included_in_transport_minimum"] is False
    assert record["minimum_tokens"] == 8 and record["predicted_regret"] == 0
    assert groups["4"]["acceptance_role"] == "negative_expected"
    assert out.report["clean_scenario_count"] == 4
    assert out.report["acceptance_passed"] is True


def test_clean_acceptance_independent_regret_and_fidelity(tmp_path, synthetic_counter):
    # Catches trusting selector-reported regret, missing fidelity, and null-to-zero conversion.
    inp = fixture_input(tmp_path)
    clean = scenario((request(),))
    policy = scenario((request(threshold=12, history=False),), "policy-threshold")
    unknown = scenario((request(prof=profile("unknown")),), "negative-unknown")
    out = run((inp,), (clean, policy, unknown))
    a, b, c = out.records
    assert (a["full_equal"], a["hard_equal"], a["eligibility_violation_count"]) == (True, True, 0)
    assert a["predicted_regret"] == 0 and a["minimum_tokens"] == 8
    assert a["candidate_costs"] == {"json": 20, "ac1": 8, "ac2": 10}
    assert a["unavailable_count"] == 2
    assert b["policy_retained"] is True and b["predicted_regret"] == 12
    assert b["opportunity_cost_tokens"] == 12 and b["opportunity_cost_upper_bound"] == 12
    assert b["scope_spent_tokens"] is None and b["scope_lifecycle_expected_tokens"] is None
    assert c["predicted_regret"] is None and c["savings_tokens"] is None
    assert c["comparison_status"] == "cannot_compare"
    assert out.report["scenarios"]["2"]["predicted_regret"] is None
    assert out.report["scenarios"]["2"]["simulated_realized_regret"] is None
    assert out.report["acceptance_passed"] is True
    # A wrong fixed declaration is not silently reselected or accepted.
    wrong = scenario((request(),), fixed="json")
    failed = run((inp,), (wrong,))
    assert failed.records[0]["predicted_regret"] == 12
    assert failed.records[0]["declaration_matches_selector"] is False
    assert failed.report["acceptance_passed"] is False


def test_ablations_are_not_selector_claims(tmp_path, synthetic_counter):
    # Catches payload-only/fixed baselines counted as clean adaptive wins.
    inp = fixture_input(tmp_path)
    scenes = [scenario((request(),), name="ablation-payload-only", fixed="ac1")]
    scenes += [scenario((request(),), name=f"fixed-{c}", fixed=c) for c in CANDIDATE_IDS]
    out = run((inp,), scenes)
    assert out.report["acceptance_passed"] is False  # no declared clean denominator
    assert out.report["clean_scenario_count"] == 0
    assert out.report["scenarios"]["0"]["selector_claim"] is False
    assert out.records[0]["payload_only_ablation_tokens"] == 8
    assert all(s["selector_claim"] is False for s in out.report["scenarios"].values())
    assert out.report["reference_only_formats"] == ["natural_language", "drixl"]


@pytest.mark.parametrize("repeats", [0, 1, 10, 50])
@pytest.mark.parametrize("exposure,state", [("builtin", "builtin"), ("once_per_scope", "cold"),
    ("once_per_scope", "ready"), ("per_request_context", "ready")])
def test_matrix_coverage_and_input_immutability(tmp_path, synthetic_counter, repeats, exposure, state):
    # Catches unit multiplication, missing exposure costs, or input/readiness mutation.
    inputs = [fixture_input(tmp_path, corpus) for corpus in ("diverse", "repetitive")]
    scenes = [scenario((request(prof=profile(name), exposure=exposure, state=state, sdk_count=2),),
        name=f"clean-{i}", repeats=repeats) for i, name in enumerate(("o200k_base", "cl100k_base"))]
    before = [s.model_dump() for s in scenes]
    hashes = [sha(Path(x.source_path).read_bytes()) for x in inputs]
    out = run(inputs, scenes)
    assert [s.model_dump() for s in scenes] == before
    assert [sha(Path(x.source_path).read_bytes()) for x in inputs] == hashes
    for group in out.report["scenarios"].values():
        assert group["logical_case_count"] == repeats
        assert group["reuse_unit"] == "whole_corpus"
        assert group["whole_corpus_repeats"] == repeats
    assert all(r["sdk_repeat_count"] == 2 for r in out.records)
    if repeats == 0:
        assert not out.records
        assert all(g["cost_totals"]["simulated_tokens"] == 0 for g in out.report["scenarios"].values())
    else:
        assert all(r["sdk_total_tokens"] == (28 if state == "cold" else 22 if exposure == "per_request_context" else 16)
                   for r in out.records)


def test_approved_corpora_manifest_and_determinism(tmp_path, synthetic_counter):
    # Thirty-row synthetic substitutes; real approved fixtures are only read by the local runner below.
    inputs, scenes = [], []
    for corpus in ("diverse", "repetitive"):
        messages = [MessageV1(**(MESSAGE | {"message_id": f"synthetic-{i}", "task": SECRET})) for i in range(30)]
        inputs.append(fixture_input(tmp_path, corpus, messages))
        scenes.append(scenario(tuple(request(m) for m in messages), name=f"clean-{corpus}"))
    first = run(inputs, scenes)
    second = run(inputs, scenes)
    assert canonical_text(first.model_dump()) == canonical_text(second.model_dump())
    assert [i["source_count"] for i in first.manifest["input_manifest"]] == [30, 30]
    public = canonical_text({"report": first.report, "records": first.records})
    assert SECRET not in public and "synthetic-revision" not in public
    assert all(i.source_sha256 not in public for i in inputs)
    assert '"path_id"' not in public and '"payload"' not in public
    assert [g["full_equal_count"] for g in first.report["scenarios"].values()] == [30, 30]
    assert first.manifest["version"] == "adaptive-benchmark-manifest-v1"
    assert first.report["version"] == "adaptive-benchmark-v1"
    assert {r["version"] for r in first.records} == {"adaptive-benchmark-record-v1"}


@pytest.mark.parametrize("kind", ["hash", "count", "order", "dictionary", "mapping", "duplicate", "mixed"])
def test_manifest_mismatch_is_redacted_rejection(tmp_path, synthetic_counter, kind):
    # Catches unbound manifest/cases, ignored dictionary provenance and repeated scenario IDs.
    inp = fixture_input(tmp_path)
    scene = scenario((request(),))
    if kind == "hash":
        inp = inp.model_copy(update={"source_sha256": "0" * 64})
    elif kind == "count":
        inp = inp.model_copy(update={"source_count": 2})
    elif kind == "order":
        scene = scenario((request(MessageV1(**(MESSAGE | {"task": SECRET}))),))
    elif kind == "dictionary":
        inp = inp.model_copy(update={"dictionary_source": "local_file"})
    scenes = (scene, scene) if kind == "duplicate" else (scene,)
    if kind == "mixed":
        scene = scenario((request(), request(allowlist=("json", "ac1"))), name="clean-mixed")
        scenes = (scene,)
    with pytest.raises(Exception) as caught:
        cfg = api().AdaptiveBenchmarkConfig(scenarios=scenes, code_revision="r", input_manifest=(inp,),
            scenario_corpora={} if kind == "mapping" else {scene.scenario_id: "diverse"})
        api().run_adaptive_benchmark(cfg)
    assert SECRET not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)
    assert isinstance(caught.value, importlib.import_module("agentcodec.codec").FallbackRequired)


def test_unknown_cache_surrogate_and_faults_are_separate(tmp_path, synthetic_counter, monkeypatch):
    # Catches approximation/faults entering acceptance, and leaking path IDs or metadata versions.
    inp = fixture_input(tmp_path)
    scenes = [scenario((request(),), name="clean-good")]
    scenes += [scenario((request(prof=profile(accuracy=accuracy)),), name=f"negative-{accuracy}")
               for accuracy in ("unknown", "surrogate_only")]
    out = run((inp,), scenes)
    assert out.report["clean_scenario_count"] == 1 and out.report["acceptance_passed"] is True
    assert out.report["scenarios"]["1"]["predicted_regret"] is None
    assert out.report["scenarios"]["2"]["predicted_regret"] is None
    messages = [MessageV1(**(MESSAGE | {"message_id": f"synthetic-{i}"})) for i in range(2)]
    inp2 = fixture_input(tmp_path, "repetitive", messages)
    fault = scenario(tuple(request(m) for m in messages), name="negative-failure", failure=True,
                     remainder="stop_group", invalid_after=True)
    fault_out = run((inp2,), (fault,))
    assert fault_out.report["scenarios"]["0"]["cost_totals"]["simulated_tokens"] == 36  # 8 + 8 + 20
    assert SECRET not in canonical_text(out.model_dump())


def test_clean_label_cannot_mask_faults_or_unknown(tmp_path, synthetic_counter):
    # Catches a clean-name-only acceptance bypass.
    inp = fixture_input(tmp_path)
    for scene in (scenario((request(),), failure=True),
                  scenario((request(prof=profile("unknown")),))):
        assert run((inp,), (scene,)).report["acceptance_passed"] is False


@pytest.mark.parametrize("setup,variable,other_setup,other_variable,want", [
    (12, 8, 0, 20, ("finite", 2, (2, None))),
    (13, 8, 0, 20, ("finite", 2, (2, None))),
    (11, 8, 0, 20, ("finite", 1, (1, None))),
    (0, 20, 0, 20, ("infinity", None, None)),
    (0, 20, 25, 8, ("interval", 1, (1, 2))),
])
def test_break_even_matrix_is_in_report(tmp_path, synthetic_counter, setup, variable, other_setup, other_variable, want):
    # Catches dropping boundary diagnostics from the independent report.
    from agentcodec.adaptive.costs import break_even
    result = break_even(setup, variable, other_setup, other_variable)
    assert (result.kind, result.first_strict_win, result.winning_interval) == want
    out = run((fixture_input(tmp_path),), (scenario((request(),)),))
    assert out.records[0]["breakeven"]["ac1"]["first_strict_win"] == 1
    assert out.records[0]["candidate_full_equal_count"] == 3
    assert out.records[0]["candidate_hard_equal_count"] == 3


@pytest.mark.parametrize("policy", ["continue_declared", "stop_group"])
def test_fault_stop_and_reinitialization_diagnostics(tmp_path, synthetic_counter, policy):
    # Catches dropped unexecuted suffixes and loss of the fixed resource schedule.
    messages = [MessageV1(**(MESSAGE | {"message_id": str(i)})) for i in range(2)]
    inp = fixture_input(tmp_path, messages=messages)
    obj = scenario(tuple(request(m) for m in messages), name="negative-stopped", remainder=policy)
    outcomes = list(obj.paths[0].outcomes)
    outcomes[0] = outcomes[0].model_copy(update={"success": False})
    obj = obj.model_copy(update={"paths": (obj.paths[0].model_copy(update={"outcomes": tuple(outcomes)}),)})
    out = run((inp,), (obj,))
    counts = out.report["scenarios"]["0"]["status_counts"]
    assert counts["simulated_failure"] == 1
    assert counts["simulated_success" if policy == "continue_declared" else "not_executed"] == 1
    assert len(out.records) == 2


def test_bad_real_cache_is_unknown_without_loading(tmp_path, monkeypatch):
    # Catches runner-level cache failures masked as exact comparisons; no tokenizer stand-in.
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "missing-cache"))
    inp = fixture_input(tmp_path)
    out = run((inp,), (scenario((request(),), name="negative-cache"),))
    assert out.records[0]["predicted_regret"] is None
    assert out.records[0]["savings_tokens"] is None
    assert out.report["scenarios"]["0"]["cost_totals"]["simulated_tokens"] is None
    assert out.report["acceptance_passed"] is False
    cache = tmp_path / "bad-cache"
    cache.mkdir()
    url = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
    (cache / hashlib.sha1(url.encode()).hexdigest()).write_bytes(b"synthetic-corrupt-cache")
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(cache))
    out = run((inp,), (scenario((request(),), name="negative-cache"),))
    assert out.records[0]["predicted_regret"] is None


def test_explicit_conflict_fails_closed_and_never_looks_like_acceptance(tmp_path, synthetic_counter):
    # Catches swallowing resource identity conflicts into unknown/zero regret.
    req = request()
    bad = req.snapshot.resources[0].model_copy(update={"epoch": 4})
    snap = req.snapshot.model_copy(update={"resources": (bad,)})
    req = req.model_copy(update={"snapshot": snap})
    with pytest.raises(importlib.import_module("agentcodec.codec").FallbackRequired) as caught:
        run((fixture_input(tmp_path),), (scenario((req,), name="negative-conflict", fixed="json"),))
    assert str(caught.value) == "adaptive benchmark rejected"


def test_public_projection_never_copies_sdk_hashes_or_free_text(tmp_path, synthetic_counter):
    # Catches decision.model_dump leakage and envelope/response disclosure even in manifest.
    req = request()
    req = req.model_copy(update={"envelope": req.envelope.model_copy(update={"prefix": SECRET})})
    out = run((fixture_input(tmp_path),), (scenario((req,), name="negative-private"),))
    assert SECRET not in canonical_text(out.model_dump())
    public = canonical_text({"report": out.report, "records": out.records})
    assert select_encoding(req).decision_id not in public
    assert req.snapshot.resources[0].identity.content_hash not in public


def test_independent_audit_does_not_trust_sdk_cost_table(tmp_path, synthetic_counter, monkeypatch):
    # Catches calculating regret from the selector's own costs instead of full text.
    runner = api()
    original = runner.select_encoding
    def wrong_costs(req):
        decision = original(req)
        return decision.model_copy(update={"costs": tuple(c.model_copy(update={"remaining_expected_tokens": 0})
                                                        for c in decision.costs)})
    monkeypatch.setattr(runner, "select_encoding", wrong_costs)
    out = run((fixture_input(tmp_path),), (scenario((request(),), fixed="json"),))
    assert out.records[0]["minimum_tokens"] == 8
    assert out.records[0]["predicted_regret"] == 12
    assert out.report["acceptance_passed"] is False


def run_approved_local_matrix(root, revision):
    """Approved, finite validation; never a test-fixture substitute or downloader.

    Reuse one REAL verified local encoder per profile within its integrity-check
    context. This is a validation-harness optimization, not synthetic counts:
    encode_ordinary runs on every complete text; the real cache/library/encoder
    are checked before AND after the session. Production meter is unchanged.
    All new pytest tests above remain synthetic-only.
    """
    from unittest.mock import patch
    from agentcodec.benchmark import DEMO_DICTIONARY, PROTOCOL_INSTRUCTIONS, AC2_PROTOCOL_INSTRUCTIONS
    from agentcodec.adaptive.benchmark import BenchmarkInput, AdaptiveBenchmarkConfig, run_adaptive_benchmark
    root = Path(root)
    outdir = root / ".superpowers/sdd/2026-10-20-agentcodec-adaptive-routing/task-8-matrix"
    outdir.mkdir(exist_ok=True)
    initial_hashes = {}
    groups = clean_groups = positions = 0
    all_clean = True
    summary_rows = []
    real_get_encoding = meter._get_encoding

    def write_artifacts(index, artifacts):
        folder = outdir / str(index)
        folder.mkdir(exist_ok=True)
        for name, value in (("manifest", artifacts.manifest), ("report", artifacts.report),
                            ("records", artifacts.records)):
            (folder / (name + ".json")).write_text(canonical_text(value) + "\n", encoding="utf-8")

    try:
        for corpus, filename, dsource in (("diverse", "tasks.v1.jsonl", "legacy_demo"),
                                          ("repetitive", "tasks-repetitive.v1.jsonl", "local_file")):
            source_path = root / "bench" / filename
            raw = source_path.read_bytes()
            initial_hashes[source_path] = sha(raw)
            messages = tuple(MessageV1.model_validate(json.loads(line), strict=True)
                             for line in raw.decode().split("\n") if line.strip())
            if len(messages) != 30:
                raise RuntimeError("approved input count mismatch")
            dpath = root / "bench/dictionaries/repeat-v1.json" if dsource == "local_file" else None
            if dpath is None:
                dictionary = DEMO_DICTIONARY.model_copy(deep=True)
                dhash = sha(canonical_text(dictionary.model_dump()).encode())
            else:
                draw = dpath.read_bytes()
                initial_hashes[dpath] = sha(draw)
                dictionary = UserDictionary.model_validate(json.loads(draw), strict=True)
                dhash = sha(draw)
            inp = BenchmarkInput(corpus_id=corpus, source_path=str(source_path), source_sha256=sha(raw),
                source_count=30, dictionary_path=None if dpath is None else str(dpath),
                dictionary_sha256=dhash, dictionary_source=dsource)
            for name in ("o200k_base", "cl100k_base"):
                prof = profile(name)
                # Missing/corrupt provisioned cache blocks this validation outright.
                with real_get_encoding(prof) as real_encoding:
                    @contextmanager
                    def verified_session(candidate_profile):
                        if candidate_profile != prof:
                            # Unknown/surrogate profiles use the unmodified real guard.
                            with real_get_encoding(candidate_profile) as other:
                                yield other
                        else:
                            yield real_encoding

                    def make_requests(domain, exposure="builtin", state="builtin", allow="all", sdk=1, threshold=0):
                        reqs = []
                        for source in messages:
                            transport = source.model_copy(update={"dictionary_version": dictionary.version}) if domain else source
                            req = request(transport, prof=prof, exposure=exposure, state=state,
                                allowlist=CANDIDATE_IDS if allow == "all" else ("json", "ac1"),
                                dictionary=dictionary if domain else None, sdk_count=sdk, threshold=threshold)
                            texts = [("protocol-ac1", PROTOCOL_INSTRUCTIONS), ("protocol-ac2", AC2_PROTOCOL_INSTRUCTIONS)]
                            if domain:
                                texts.append(("dictionary", canonical_text(dictionary.model_dump())))
                            resources = []
                            for kind, text in texts:
                                identity = ResourceIdentity(scope_id="scope", receiver_id="receiver", user_id=transport.user_id,
                                    resource_kind=kind, version=dictionary.version if kind == "dictionary" else "v1",
                                    content_hash=sha(text.encode()), tokenizer_profile_id=profile_id(prof))
                                resources.append(ResourceSnapshot(identity=identity, state=state, exposure=exposure,
                                    epoch=0, valid_through_repeat=100, readiness_basis="confirmed deterministic local decoder",
                                    setup_text=text if exposure == "once_per_scope" and state == "cold" else None,
                                    context_text=text if exposure == "per_request_context" else None, independent_setup=False))
                            dependencies = {"ac1": (resources[0].identity,), "ac2": (resources[1].identity,)}
                            dependencies.update(ac1_dictionary=(resources[0].identity, *tuple(x.identity for x in resources[2:])),
                                                ac2_dictionary=(resources[1].identity, *tuple(x.identity for x in resources[2:])))
                            req = SelectionRequest(**(req.model_dump() | dict(
                                capabilities=req.capabilities.model_copy(update={"candidate_resources": dependencies}),
                                snapshot=req.snapshot.model_copy(update={"resources": tuple(resources)}))))
                            reqs.append(req)
                        return tuple(reqs)

                    def make_scene(reqs, label, repeats=1, fixed=None, fault=None):
                        cases = tuple(OfflineCase(source_message=source, request=req,
                            metadata_authorized=source != req.message) for source, req in zip(messages, reqs, strict=True))
                        schedules, outcomes, decisions = [], [], []
                        ready = set()
                        epoch = 0
                        for r in range(repeats):
                            for c, req in enumerate(reqs):
                                first = r == c == 0
                                setup = req.snapshot.model_copy(update={"epoch": epoch, "resources": tuple(
                                    x.model_copy(update={"epoch": epoch, "state": "ready" if x.identity in ready else x.state})
                                    for x in req.snapshot.resources)})
                                decision_req = SelectionRequest(**(req.model_dump() | dict(snapshot=setup,
                                    capabilities=req.capabilities.model_copy(update={"epoch": setup.epoch}))))
                                chosen = select_encoding(decision_req).selected_candidate or req.original_format
                                chosen = chosen if fixed is None else fixed
                                decisions.append(chosen)
                                needed = req.capabilities.candidate_resources.get(chosen, ())
                                newly_ready = {x.identity for x in setup.resources if x.identity in needed and x.state == "cold"}
                                if newly_ready:
                                    ready.update(newly_ready)
                                    epoch += 1
                                initial = setup.model_copy(update={"epoch": epoch, "resources": tuple(
                                    x.model_copy(update={"epoch": epoch, "state": "ready" if x.identity in ready else x.state})
                                    for x in setup.resources)})
                                if fault == "invalid" and c > 0:
                                    initial = initial.model_copy(update={"resources": tuple(
                                        x.model_copy(update={"state": "invalid"}) for x in initial.resources)})
                                stages = ("initial", "retry", "fallback") if first and fault in ("continue", "stop", "invalid") else ("initial",)
                                schedules.extend(ScheduledSnapshot(case_index=c, repeat_index=r, stage=stage,
                                    snapshot=setup if stage == "setup" else initial) for stage in ("setup", *stages))
                                outcomes.extend(OfflineOutcome(case_index=c, repeat_index=r, stage=stage,
                                    success=False if first and fault == "stop" else stage == stages[-1], response_text="") for stage in stages)
                        return OfflineScenario(scenario_id=label, cases=cases, whole_corpus_repeats=repeats,
                            fixed_decisions=tuple(decisions), resource_schedule=tuple(schedules),
                            paths=(OfflinePath(path_id="local-path", outcomes=tuple(outcomes)),),
                            remainder_policy="stop_group" if fault == "stop" else "continue_declared")

                    # Layered coverage, NOT an unnecessarily huge Cartesian product.
                    plans = []
                    for domain in (False, True):
                        for allow in ("conservative", "all"):
                            plans.append((domain, "builtin", "builtin", allow, 1, 1, "clean", None, None, 0))
                        for repeats in (0, 10, 50):
                            plans.append((domain, "builtin", "builtin", "all", repeats, 1, "clean", None, None, 0))
                        for exposure, state in (("once_per_scope", "cold"), ("once_per_scope", "ready"),
                                                ("per_request_context", "ready")):
                            plans.append((domain, exposure, state, "all", 1, 1, "clean", None, None, 0))
                        plans.append((domain, "builtin", "builtin", "all", 1, 2, "clean", None, None, 0))
                        plans.append((domain, "builtin", "builtin", "all", 1, 1, "ablation", None, None, 0))
                        for fixed in (("json", "ac1_dictionary", "ac2_dictionary") if domain else ("json", "ac1", "ac2")):
                            plans.append((domain, "builtin", "builtin", "all", 1, 1, "fixed", fixed, None, 0))
                    for fault in ("continue", "stop", "invalid"):
                        plans.append((False, "once_per_scope", "cold", "all", 1, 1, "negative", "ac2" if fault == "invalid" else None, fault, 0))
                    plans.append((False, "builtin", "builtin", "all", 1, 1, "policy", None, None, 100000))
                    with patch.object(meter, "_get_encoding", verified_session):
                        for domain, exposure, state, allow, repeats, sdk, role, fixed, fault, threshold in plans:
                            reqs = make_requests(domain, exposure, state, allow, sdk, threshold)
                            scene = make_scene(reqs, role + "-local", repeats, fixed, fault)
                            config = AdaptiveBenchmarkConfig(scenarios=(scene,), code_revision=revision,
                                input_manifest=(inp,), scenario_corpora={scene.scenario_id: corpus})
                            first = run_adaptive_benchmark(config)
                            second = run_adaptive_benchmark(config)
                            if canonical_text(first.model_dump()) != canonical_text(second.model_dump()):
                                raise RuntimeError("local matrix determinism failure")
                            public = canonical_text(dict(report=first.report, records=first.records))
                            # Compare only exact complete source/dictionary serializations,
                            # never print matches, terms, message hashes, or payloads.
                            if sha(raw) in public or dhash in public or any(
                                    canonical_text(m.model_dump()) in public for m in messages):
                                raise RuntimeError("local matrix projection failure")
                            group = first.report["scenarios"]["0"]
                            groups += 1
                            positions += group["logical_case_count"]
                            if role == "clean" and repeats:
                                clean_groups += 1
                                all_clean = all_clean and first.report["acceptance_passed"]
                            write_artifacts(groups - 1, first)
                            summary_rows.append(dict(group_index=groups - 1, corpus_id=corpus, tokenizer=name,
                                domain="transport" if domain else "source", exposure=exposure, state=state,
                                allowlist=allow, sdk_repeat_count=sdk, whole_corpus_repeats=repeats, role=role,
                                acceptance_passed=first.report["acceptance_passed"],
                                logical_case_count=group["logical_case_count"],
                                predicted_regret=group["predicted_regret"],
                                simulated_realized_regret=group["simulated_realized_regret"],
                                repeat_shortfall_loss_tokens=group["repeat_shortfall_loss_tokens"]))
                            if groups % 8 == 0:
                                print(f"local matrix: {groups} isolated groups verified", flush=True)
                # Real original context exits here and verifies cache/library/encoder unchanged.
                print(f"local matrix: tokenizer session verified; {groups} groups complete", flush=True)
        if any(sha(path.read_bytes()) != digest for path, digest in initial_hashes.items()):
            raise RuntimeError("local matrix input immutability failure")
        summary = dict(groups=groups, clean_groups=clean_groups, logical_positions=positions,
            all_clean_acceptance_passed=all_clean, deterministic=True, inputs_unchanged=True,
            corpora_counts=[30, 30], real_local_tokenizers=2,
            validation_meter="real_encode_ordinary_with_session_integrity_verification", groups_separate=summary_rows)
        (outdir / "summary.json").write_text(canonical_text(summary) + "\n")
        print(canonical_text({k: v for k, v in summary.items() if k != "groups_separate"}), flush=True)
        return summary
    except Exception:
        raise RuntimeError("approved local matrix validation failed; no fixture details disclosed") from None


def test_comparison_domain_names_profile_and_original_format(tmp_path, synthetic_counter):
    # Catches indistinguishable tokenizer/baseline groups in the public diagnostic domain.
    out = run((fixture_input(tmp_path),), (scenario((request(),)),))
    domain = out.report["scenarios"]["0"]["comparison_domain"]
    assert domain["encoding_name"] == "cl100k_base"
    assert domain["original_format"] == "json"


def test_manifest_pins_full_resource_and_envelope_without_text(tmp_path, synthetic_counter):
    # Catches same-length changed text retaining a falsely identical reproducibility identity.
    inp = fixture_input(tmp_path)
    a = run((inp,), (scenario((request(resource_text="ABC"),)),))
    b = run((inp,), (scenario((request(resource_text="XYZ"),)),))
    sa = a.manifest["scenarios"][0]["resource_schedule"][0]["snapshot"]["resources"][0]
    sb = b.manifest["scenarios"][0]["resource_schedule"][0]["snapshot"]["resources"][0]
    assert sa["setup_sha256"] == sha(b"ABC")
    assert sb["setup_sha256"] == sha(b"XYZ")
    assert "ABC" not in canonical_text(a.model_dump())


def test_group_lifecycle_counts_shared_history_once(tmp_path, synthetic_counter):
    # Catches summing per-decision sunk history or hiding a known group lifecycle.
    from agentcodec.adaptive.contracts import HistoricalEvent
    messages = [MessageV1(**(MESSAGE | {"message_id": str(i)})) for i in range(2)]
    reqs = []
    for message in messages:
        req = request(message)
        event = HistoricalEvent(event_id="prior", identity=req.snapshot.resources[0].identity,
            profile_id=profile_id(req.tokenizer), tokens=5, kind="setup")
        reqs.append(req.model_copy(update={"snapshot": req.snapshot.model_copy(update={"historical_events": (event,)})}))
    out = run((fixture_input(tmp_path, messages=messages),), (scenario(tuple(reqs)),))
    totals = out.report["scenarios"]["0"]["cost_totals"]
    assert totals["simulated_tokens"] == 16
    assert totals["scope_spent_tokens"] == 5
    assert totals["scope_lifecycle_expected_tokens"] == 21


@pytest.mark.parametrize("future,total", [(0, 1), (1, 2)])
def test_future_reuse_is_explicit_total_not_corpus_passes(tmp_path, synthetic_counter, future, total):
    # Catches losing the current use or multiplying SDK total by corpus passes.
    req = request(sdk_count=1 + future)
    out = run((fixture_input(tmp_path),), (scenario((req,), repeats=10),))
    assert len(out.records) == 10
    assert {r["sdk_repeat_count"] for r in out.records} == {total}
    assert out.report["scenarios"]["0"]["cost_totals"]["simulated_tokens"] == 80


def test_rational_paths_keep_separate_status_denominators(tmp_path, synthetic_counter):
    # Catches float rounding and summing different path costs instead of exact weighting.
    obj = scenario((request(),), name="negative-weighted")
    retry = OfflineOutcome(case_index=0, repeat_index=0, stage="retry", success=True, response_text="")
    initial_fail = obj.paths[0].outcomes[0].model_copy(update={"success": False})
    paths = (obj.paths[0].model_copy(update={"probability_numerator": 1, "probability_denominator": 3}),
             OfflinePath(path_id="second", probability_numerator=2, probability_denominator=3,
                         outcomes=(initial_fail, retry)))
    schedules = obj.resource_schedule + (ScheduledSnapshot(case_index=0, repeat_index=0,
        stage="retry", snapshot=obj.cases[0].request.snapshot),)
    obj = obj.model_copy(update={"paths": paths, "resource_schedule": schedules})
    out = run((fixture_input(tmp_path),), (obj,))
    group = out.report["scenarios"]["0"]
    assert group["logical_case_count"] == 1
    assert group["status_counts"]["simulated_success"] == 2
    assert group["cost_totals"]["simulated_tokens"] == {"numerator": 40, "denominator": 3}
    assert group["simulated_realized_regret"] == {"numerator": 16, "denominator": 3}
    assert group["cost_totals"]["scope_lifecycle_expected_tokens"] == {"numerator": 40, "denominator": 3}


def test_reinitialization_counts_each_explicit_epoch_setup(tmp_path, synthetic_counter):
    # Catches treating a later cold epoch as already paid or resetting the fixed format.
    messages = [MessageV1(**(MESSAGE | {"message_id": str(i)})) for i in range(2)]
    reqs = tuple(request(m, exposure="once_per_scope", state="cold") for m in messages)
    obj = scenario(reqs, name="negative-reinitialization", fixed="ac1")
    schedule = []
    for s in obj.resource_schedule:
        epoch = s.case_index * 2 + (s.stage == "initial")
        snap = s.snapshot.model_copy(update={"epoch": epoch, "resources": tuple(
            r.model_copy(update={"epoch": epoch}) for r in s.snapshot.resources)})
        schedule.append(s.model_copy(update={"snapshot": snap}))
    obj = obj.model_copy(update={"resource_schedule": tuple(schedule)})
    out = run((fixture_input(tmp_path, messages=messages),), (obj,))
    totals = out.report["scenarios"]["0"]["cost_totals"]
    assert totals["simulated_tokens"] == 40
    assert totals["paths"][0]["attempt_kind_tokens"]["setup"] == 24
    assert [r["fixed_candidate"] for r in out.records] == ["ac1", "ac1"]


if __name__ == "__main__":
    import sys
    run_approved_local_matrix(sys.argv[1], sys.argv[2])

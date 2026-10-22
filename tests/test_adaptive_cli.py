"""Opt-in local CLI boundaries; all input text here is newly synthetic.

Each test names the validation, privacy or publication break it catches.
Only OS write failures and tokenization are substituted where necessary.
"""
from contextlib import contextmanager
import hashlib
import importlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from agentcodec import cli
from agentcodec.adaptive import meter
from agentcodec.adaptive.contracts import MAX_TEXT_BYTES, SelectionRequest
from agentcodec.adaptive.selector import select_encoding
from agentcodec.benchmark import _VOCAB_HASHES
from agentcodec.schema import MessageV1

ROOT = Path(__file__).resolve().parents[1]
RUNNER = CliRunner()
SECRET = "synthetic-private-marker"
MESSAGE = dict(message_id="synthetic", message_type="delegate", task=SECRET,
    status="pending", blocker=None, next_step="", evidence=["b", "a", "b"],
    constraints=[], approval_required=False, user_id="user", dictionary_version=None)
FILES = {"manifest.json", "records.jsonl", "report.json"}


def api():
    return importlib.import_module("agentcodec.adaptive.cli")


def request_data():
    # Missing policy is intentional: --expected-future-reuses may then declare it.
    return dict(contract_version="adaptive-selection-v1", message=dict(MESSAGE),
        expected_user_id="user", tokenizer=dict(encoding_name="cl100k_base",
            library_version=version("tiktoken"), vocabulary_sha256=_VOCAB_HASHES["cl100k_base"],
            accuracy="exact_local_text", mapping_basis=SECRET),
        original_format="json", allowlist=["json", "ac1"],
        capabilities=dict(receiver_id="receiver", epoch=0, supported_candidates=["json", "ac1"],
            candidate_resources={}),
        snapshot=dict(scope_id="scope", receiver_id="receiver", user_id="user", epoch=0,
            history_complete=True, historical_events=[], resources=[]))


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def select(path, *args):
    return RUNNER.invoke(cli.app, ["select-encoding", "--request", str(path), *map(str, args)])


def bench(path, out):
    return RUNNER.invoke(cli.app, ["benchmark-adaptive", "--config", str(path), "--output-dir", str(out)])


def assert_private(result, *values):
    public = result.stdout + result.stderr
    for value in (SECRET, "Traceback", *values):
        assert value not in public


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    # No external work: permit only the exact read-only revision command.
    real_run = subprocess.run
    def guarded(command, *args, **kwargs):
        assert command == ["git", "rev-parse", "HEAD"]
        assert not args and Path(kwargs["cwd"]).resolve() == ROOT
        assert not kwargs.get("shell") and "env" not in kwargs
        return real_run(command, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError("external operation forbidden")
    monkeypatch.setattr(subprocess, "run", guarded)
    for name in ("call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(os, "system", forbidden)


@pytest.fixture
def synthetic_counter(monkeypatch):
    class Encoding:
        def encode_ordinary(self, text):
            # Whole-request oracle: AC1=8, JSON=20, one explicit LF adds 1.
            return [0] * ((8 if text.startswith("AC1|") else 20) + int(text.endswith("\n"))) if text else []
    @contextmanager
    def get_encoding(profile):
        yield Encoding()
    monkeypatch.setattr(meter, "_get_encoding", get_encoding)


def benchmark_data(tmp_path, *, clean=True):
    data = request_data()
    source = save(tmp_path / "source.jsonl", MESSAGE)
    source.write_bytes(source.read_bytes() + b"\n")
    snapshot = data["snapshot"]
    return dict(code_revision=subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
            check=True, capture_output=True, text=True).stdout.strip(),
        input_manifest=[dict(corpus_id=SECRET, source_path=source.name,
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), source_count=1,
            dictionary_path=None, dictionary_sha256=None, dictionary_source="none")],
        scenario_corpora={"clean-main" if clean else "fixed-main": SECRET},
        scenarios=[dict(scenario_id="clean-main" if clean else "fixed-main",
            cases=[dict(source_message=dict(MESSAGE), request=data, metadata_authorized=False)],
            whole_corpus_repeats=1, fixed_decisions=["ac1"],
            resource_schedule=[dict(case_index=0, repeat_index=0, stage=stage, snapshot=snapshot)
                for stage in ("setup", "initial")],
            paths=[dict(path_id=SECRET, outcomes=[dict(case_index=0, repeat_index=0,
                stage="initial", success=True, response_text="")])], remainder_policy="continue_declared")])


def test_cli_is_opt_in_and_summary_is_redacted(tmp_path, synthetic_counter):
    # Catches routing legacy encode, absent opt-in commands, and raw SDK dumps.
    legacy = RUNNER.invoke(cli.app, ["encode"], input=json.dumps(MESSAGE))
    assert legacy.exit_code == 0 and legacy.stdout.startswith("AC1|")
    path = save(tmp_path / "request.json", request_data())
    result = select(path)
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["status"] == "selected"
    assert summary["comparison_status"] == "exact_local_text"
    assert summary["selected_candidate"] == "ac1"
    assert summary["savings_tokens"] == 12
    assert summary["costs"][0]["remaining_expected_tokens"] == 20
    assert "payload" not in summary and all("payload" not in c for c in summary["candidates"])
    assert_private(result, str(path), _VOCAB_HASHES["cl100k_base"], '"decision_id"')
    help_result = RUNNER.invoke(cli.app, ["benchmark-adaptive", "--help"])
    assert help_result.exit_code == 0


@pytest.mark.parametrize("final_lf,want", [(False, 8), (True, 9)])
def test_payload_requires_explicit_local_file(tmp_path, synthetic_counter, final_lf, want):
    # Catches payload on stdout, truncated payload, and charging the document LF.
    data = request_data() | {"envelope": {"final_lf": final_lf}}
    path = save(tmp_path / "request.json", data)
    result = select(path, "--summary-output", "summary.json", "--payload-output", "payload.ac1")
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert next(c for c in summary["costs"] if c["candidate_id"] == "ac1")["remaining_expected_tokens"] == want
    from agentcodec.codec import decode_payload
    payload = (tmp_path / "payload.ac1").read_bytes()
    assert payload.endswith(b"\n") and not payload.endswith(b"\n\n")
    assert decode_payload(payload[:-1].decode()).model_dump() == MESSAGE
    assert SECRET not in json.dumps(summary)


@pytest.mark.parametrize("future,total,want", [(0, 1, 8), (1, 2, 16)])
def test_future_reuses_and_no_production_retry_flags(tmp_path, synthetic_counter, future, total, want):
    # Catches future-to-total off-by-one or losing same-instance declaration.
    path = save(tmp_path / "request.json", request_data())
    result = select(path, "--expected-future-reuses", future)
    assert result.exit_code == 0, result.output
    cost = next(c for c in json.loads(result.stdout)["costs"] if c["candidate_id"] == "ac1")
    assert cost["remaining_expected_tokens"] == want
    assert cost["attempts"][0]["multiplicity"] == total


@pytest.mark.parametrize("args", [["--expected-future-reuses", "-1"],
    ["--expected-future-reuses", SECRET], ["--expected-future-reuses", "1.0"],
    ["--expected-future-reuses", "1000000"], ["--retry", SECRET], ["--provider", SECRET]])
def test_bad_production_options_are_redacted_usage(tmp_path, synthetic_counter, args):
    path = save(tmp_path / "request.json", request_data())
    result = select(path, *args)
    assert result.exit_code == 2
    assert_private(result, str(path))
    assert "adaptive_usage_error" in result.stderr


@pytest.mark.parametrize("policy", [{"repeat_count": 1}, {"repeat_declaration": None},
    {"repeat_count": 1, "repeat_declaration": None}])
def test_future_reuses_refuses_any_explicit_count_field(tmp_path, synthetic_counter, policy):
    path = save(tmp_path / "request.json", request_data() | {"policy": policy})
    result = select(path, "--expected-future-reuses", 0, "--payload-output", "payload")
    assert result.exit_code == 2
    assert not (tmp_path / "payload").exists()
    assert_private(result)


@pytest.mark.parametrize("raw", [b"\xff", b'{"message":"' + SECRET.encode() + b'","message":{}}',
    b"{}" + b" " * MAX_TEXT_BYTES, b'{"x":NaN}', b'{"x":Infinity}', b"[]", b"{}"])
def test_input_and_output_failures_do_not_leak_or_overwrite(tmp_path, raw):
    # Catches non-strict JSON, byte-limit bypass and writing payload after parse refusal.
    path = tmp_path / "request.json"
    path.write_bytes(raw)
    result = select(path, "--payload-output", "payload")
    assert result.exit_code == 1
    summary = json.loads(result.stdout)
    assert summary["status"] == "fallback_required" and summary["comparison_status"] == "cannot_compare"
    assert not (tmp_path / "payload").exists()
    assert_private(result, str(path))


@pytest.mark.parametrize("mutation", ["str_count", "bool_count", "str_allowlist", "str_resources", "bad_list_item", "surrogate"])
def test_json_arrays_convert_only_declared_tuples_without_coercion(tmp_path, mutation, synthetic_counter):
    data = request_data()
    if mutation == "str_count":
        data["policy"] = {"repeat_count": "1"}
    elif mutation == "bool_count":
        data["policy"] = {"repeat_count": True}
    elif mutation == "str_allowlist":
        data["allowlist"] = "json"
    elif mutation == "str_resources":
        data["capabilities"]["candidate_resources"] = {"ac1": "not-array"}
    elif mutation == "bad_list_item":
        data["message"]["evidence"] = [12]
    else:
        data["message"]["task"] = "\ud800"
    path = tmp_path / "request.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = select(path)
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "fallback_required"
    assert_private(result)


@pytest.mark.parametrize("target", ["input", "same", "exists", "symlink", "url", "stdout", "directory"])
def test_output_preflight_never_overwrites_or_uses_nonlocal_targets(tmp_path, synthetic_counter, target):
    path = save(tmp_path / "request.json", request_data())
    keep = tmp_path / "keep"
    keep.write_bytes(b"keep")
    link = tmp_path / "link"
    link.symlink_to(keep)
    destinations = {"input": path, "same": tmp_path / "new", "exists": keep, "symlink": link,
        "url": "https://example.invalid/" + SECRET, "stdout": "-", "directory": tmp_path}
    out = destinations[target]
    args = ["--payload-output", str(out)]
    if target == "same":
        args += ["--summary-output", str(out)]
    result = select(path, *args)
    assert result.exit_code in (1, 2)
    assert keep.read_bytes() == b"keep"
    assert json.loads(path.read_text())["message"]["task"] == SECRET
    assert not (tmp_path / "new").exists()
    assert_private(result, str(path), str(keep))


def test_permission_and_missing_input_errors_are_fixed(tmp_path, monkeypatch):
    path = save(tmp_path / "request.json", request_data())
    real_open = Path.open
    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError(SECRET + str(path))
        return real_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    for source in (path, tmp_path / "missing", Path("https://example.invalid/" + SECRET)):
        result = select(source)
        assert result.exit_code in (1, 2)
        assert_private(result, str(path))
    assert "adaptive_io_error" in select(path).stderr


def run_cli_process(*args):
    # A real CLI process exposes tracebacks that CliRunner would capture as exceptions.
    # Popen is local only; the run() guard still permits only the revision command.
    command = [sys.executable, "-m", "agentcodec.cli", *map(str, args)]
    with subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=os.environ | {"PYTHONDONTWRITEBYTECODE": "1"}) as process:
        stdout, stderr = process.communicate(timeout=30)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


@pytest.mark.parametrize("target", ["request", "summary", "payload"])
def test_select_symlink_loop_is_redacted_and_publishes_nothing(tmp_path, target):
    # Catches ancestor resolve() failures escaping request and explicit-output validation.
    marker = "synthetic-select-loop-marker"
    loop = tmp_path / marker
    loop.symlink_to(loop.name, target_is_directory=True)
    path = save(tmp_path / "request.json", request_data())
    request = loop / "request.json" if target == "request" else path
    summary = Path(marker) / "summary.json" if target == "summary" else tmp_path / "summary.json"
    payload = Path(marker) / "payload.ac1" if target == "payload" else tmp_path / "payload.ac1"
    before = set(tmp_path.rglob("*"))
    result = run_cli_process("select-encoding", "--request", request,
        "--summary-output", summary, "--payload-output", payload)
    public = result.stdout + result.stderr
    assert ("Traceback" in public, marker in public) == (False, False)
    assert result.returncode == 1
    assert result.stderr == "adaptive_invalid_input\n"
    failure = json.loads(result.stdout)
    assert failure["status"] == "fallback_required"
    assert failure["comparison_status"] == "cannot_compare"
    assert failure["reason_codes"] == ["adaptive_invalid_input"]
    assert SECRET not in public
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("target", ["config", "output", "source", "dictionary"])
def test_benchmark_symlink_loop_is_redacted_and_publishes_nothing(tmp_path, target):
    # Catches the same failure in config/output and nested manifest path resolution.
    marker = "synthetic-benchmark-loop-marker"
    loop = tmp_path / marker
    loop.symlink_to(loop.name, target_is_directory=True)
    data = benchmark_data(tmp_path)
    if target == "source":
        data["input_manifest"][0]["source_path"] = str(Path(marker) / "source.jsonl")
    elif target == "dictionary":
        data["input_manifest"][0].update(dictionary_source="local_file",
            dictionary_path=str(Path(marker) / "dictionary.json"), dictionary_sha256="0" * 64)
    path = save(tmp_path / "config.json", data)
    config = loop / "config.json" if target == "config" else path
    out = Path(marker) / "out" if target == "output" else tmp_path / "out"
    before = set(tmp_path.rglob("*"))
    result = run_cli_process("benchmark-adaptive", "--config", config, "--output-dir", out)
    public = result.stdout + result.stderr
    assert ("Traceback" in public, marker in public) == (False, False)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "adaptive_invalid_input\n"
    assert SECRET not in public
    assert set(tmp_path.rglob("*")) == before


def test_fallback_required_never_writes_payload(tmp_path, synthetic_counter):
    data = request_data()
    data["expected_user_id"] = "other"
    result = select(save(tmp_path / "request.json", data), "--payload-output", "payload")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "fallback_required"
    assert not (tmp_path / "payload").exists()


def test_public_projection_drops_internal_identity_and_free_text(synthetic_counter):
    # Catches nested reasons/assumptions, component keys and event IDs leaking.
    data = request_data()
    data["allowlist"] = tuple(data["allowlist"])
    data["capabilities"]["supported_candidates"] = tuple(data["capabilities"]["supported_candidates"])
    data["snapshot"].update(historical_events=(), resources=())
    decision = select_encoding(SelectionRequest(**data))
    decision = decision.model_copy(update=dict(reason_codes=(SECRET,), assumptions=(SECRET,), excluded=(SECRET,),
        costs=tuple(c.model_copy(update=dict(reason_codes=(SECRET,), excluded=(SECRET,),
            attempts=tuple(a.model_copy(update={"event_id": SECRET}) for a in c.attempts))) for c in decision.costs),
        candidates=tuple(c.model_copy(update={"reason_codes": (SECRET,)}) for c in decision.candidates),
            breakeven={k: b.model_copy(update=dict(reason_codes=(SECRET,), assumptions=(SECRET,))) for k,b in decision.breakeven.items()}))
    public = json.dumps(api().redacted_decision(decision))
    assert SECRET not in public
    assert "payload" not in api().redacted_decision(decision)
    assert all("payload" not in c for c in api().redacted_decision(decision)["candidates"])
    for key in ("event_id", "decision_id", "profile_id", "metadata_selection", "required_resources"):
        assert '"' + key + '"' not in public
    assert decision.decision_id not in public and decision.profile_id not in public
    assert "unrecognized_code" in public


def test_cache_missing_is_cannot_compare_not_download(tmp_path, monkeypatch):
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "missing-cache"))
    import tiktoken
    def forbidden(*args, **kwargs):
        raise AssertionError("tokenizer must not download")
    monkeypatch.setattr(tiktoken, "get_encoding", forbidden)
    path = save(tmp_path / "request.json", request_data())
    result = select(path)
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["status"] == "preserved_without_comparison"
    assert summary["comparison_status"] == "cannot_compare"
    assert summary["savings_tokens"] is None and "tokenizer_unavailable" in summary["reason_codes"]
    # Review Focus 5: legacy record-only evaluation must never initialize meter.
    monkeypatch.setattr(meter, "measure_attempts", forbidden)
    monkeypatch.setattr(meter, "_get_encoding", forbidden)
    args = ["evaluate-offline", "--adapter", "mock-v1", "--diverse-input", str(ROOT / "bench/tasks.v1.jsonl"),
        "--repetitive-input", str(ROOT / "bench/tasks-repetitive.v1.jsonl"), "--repetitive-dictionary",
        str(ROOT / "bench/dictionaries/repeat-v1.json"), "--output-dir", str(tmp_path / "legacy")]
    assert RUNNER.invoke(cli.app, args).exit_code == 0


@pytest.mark.parametrize("clean,acceptance", [(True, True), (False, False)])
def test_benchmark_complete_artifacts_relative_paths_and_determinism(tmp_path, monkeypatch, synthetic_counter, clean, acceptance):
    # Catches incomplete configs, hidden templates, cwd-relative resolution, acceptance exit conflation.
    config = benchmark_data(tmp_path, clean=clean)
    path = save(tmp_path / "config.json", config)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    for name in ("first", "second"):
        result = bench(path, name)
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {"status": "artifacts_written", "acceptance_passed": acceptance}
        assert {p.name for p in (tmp_path / name).iterdir()} == FILES
        report = json.loads((tmp_path / name / "report.json").read_text())
        assert report["acceptance_passed"] is acceptance
        public = (tmp_path / name / "records.jsonl").read_text() + json.dumps(report) + result.output
        assert SECRET not in public and config["code_revision"] not in public
        manifest = json.loads((tmp_path / name / "manifest.json").read_text())
        assert manifest["code_revision"] == config["code_revision"]
        assert manifest["input_manifest"][0]["source_sha256"] == config["input_manifest"][0]["source_sha256"]
    for name in FILES:
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()


@pytest.mark.parametrize("mutation", ["revision", "hash", "count", "source", "missing_case", "url", "duplicate", "invalid_utf8", "oversize"])
def test_benchmark_rejects_unbound_or_malformed_inputs(tmp_path, synthetic_counter, mutation):
    data = benchmark_data(tmp_path)
    if mutation == "revision":
        data["code_revision"] = SECRET
    elif mutation in ("hash", "count", "url"):
        field, value = {"hash": ("source_sha256", "0" * 64), "count": ("source_count", 2),
            "url": ("source_path", "https://example.invalid/" + SECRET)}[mutation]
        data["input_manifest"][0][field] = value
    elif mutation == "source":
        data["scenarios"][0]["cases"][0]["source_message"]["task"] = "different"
    elif mutation == "missing_case":
        del data["scenarios"][0]["cases"][0]["source_message"]
    path = save(tmp_path / "config.json", data)
    if mutation == "duplicate":
        path.write_bytes(b'{"x":1,"x":2}')
    elif mutation == "invalid_utf8":
        (tmp_path / "source.jsonl").write_bytes(b"\xff")
    elif mutation == "oversize":
        path.write_bytes(b" " * (MAX_TEXT_BYTES + 1))
    out = tmp_path / "out"
    result = bench(path, out)
    assert result.exit_code == 1
    assert not out.exists() or not list(out.iterdir())
    assert_private(result, str(path), str(out))


def test_manifest_inputs_are_read_once_from_original_paths(tmp_path, monkeypatch, synthetic_counter):
    data = benchmark_data(tmp_path)
    path = save(tmp_path / "config.json", data)
    source = tmp_path / "source.jsonl"
    counts = {path: 0, source: 0}
    real_open = Path.open
    def counted(self, *args, **kwargs):
        if self in counts and args and "r" in args[0]:
            counts[self] += 1
            assert counts[self] == 1, "input snapshot must not be re-read"
        return real_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", counted)
    result = bench(path, tmp_path / "out")
    assert result.exit_code == 0, result.output
    assert counts == {path: 1, source: 1}


@pytest.mark.parametrize("target", ["nonempty", "file", "input_parent", "symlink"])
def test_benchmark_output_preflight_preserves_user_files(tmp_path, synthetic_counter, target):
    data = benchmark_data(tmp_path)
    path = save(tmp_path / "config.json", data)
    out = tmp_path / "out"
    if target in ("nonempty", "symlink"):
        out.mkdir()
        (out / "keep").write_bytes(b"keep")
    elif target == "file":
        out.write_bytes(b"keep")
    elif target == "input_parent":
        out = tmp_path
    if target == "symlink":
        link = tmp_path / "link"
        link.symlink_to(out, target_is_directory=True)
        out = link
    result = bench(path, out)
    assert result.exit_code == 1
    assert not any((out / name).exists() for name in FILES) if out.is_dir() else out.read_bytes() == b"keep"
    assert_private(result, str(out))


@pytest.mark.parametrize("operation", ["select", "benchmark"])
@pytest.mark.parametrize("failure", ["stage", "publish", "race"])
def test_failed_publication_cleans_only_invocation_files(tmp_path, monkeypatch, synthetic_counter, operation, failure):
    # Catches a partial report/payload or unlinking an unrelated race winner.
    module = api()
    real_link, real_fdopen = os.link, os.fdopen
    calls = 0
    if failure == "stage":
        def broken_fdopen(*args, **kwargs):
            stream = real_fdopen(*args, **kwargs)
            class Broken:
                def __enter__(self):
                    return self
                def write(self, data):
                    stream.write(data[:2])
                    raise OSError(SECRET)
                def __exit__(self, *args):
                    stream.close()
            return Broken()
        monkeypatch.setattr(module.os, "fdopen", broken_fdopen)
    else:
        def broken_link(src, dst, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                if failure == "race":
                    Path(dst).write_bytes(b"unrelated")
                raise OSError(SECRET)
            return real_link(src, dst, *args, **kwargs)
        monkeypatch.setattr(module.os, "link", broken_link)
    if operation == "select":
        path = save(tmp_path / "request.json", request_data())
        result = select(path, "--summary-output", "summary", "--payload-output", "payload")
        assert not (tmp_path / "summary").exists()
        assert not (tmp_path / "payload").exists() or (tmp_path / "payload").read_bytes() == b"unrelated"
    else:
        path = save(tmp_path / "config.json", benchmark_data(tmp_path))
        out = tmp_path / "out"
        result = bench(path, out)
        assert not (out / "manifest.json").exists()
        assert not (out / "report.json").exists()
        assert not (out / "records.jsonl").exists() or (out / "records.jsonl").read_bytes() == b"unrelated"
    assert result.exit_code == 1
    assert result.stdout == "" and "adaptive_io_error" in result.stderr
    assert_private(result, str(tmp_path))
    assert not any(p.name.startswith(".adaptive-") for p in tmp_path.rglob("*"))


def test_special_file_is_refused_before_blocking_open(tmp_path, monkeypatch):
    # Catches opening a named pipe (which could block indefinitely) as JSON input.
    pipe = tmp_path / "pipe"
    os.mkfifo(pipe)
    original = Path.open
    def guarded(self, *args, **kwargs):
        if self == pipe:
            raise AssertionError("nonregular input reached blocking open")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    result = select(pipe)
    assert result.exit_code == 1
    assert "adaptive_invalid_input" in result.stderr
    assert_private(result, str(pipe))


def test_embedded_dictionary_is_explicit_and_redacted(tmp_path, synthetic_counter):
    # Catches alias text/versions escaping and silent dictionary_version rewriting.
    data = request_data()
    dictionary = dict(user_id="user", version="private-version", aliases={"X": SECRET})
    data["message"]["dictionary_version"] = "private-version"
    data["allowlist"] = ["json", "ac1_dictionary"]
    data["capabilities"]["supported_candidates"] = ["json", "ac1_dictionary"]
    from agentcodec.adaptive.contracts import canonical_text
    data["dictionary"] = dict(enabled=True, dictionary=dictionary,
        content_hash=hashlib.sha256(canonical_text(dictionary).encode()).hexdigest())
    path = save(tmp_path / "request.json", data)
    result = select(path, "--payload-output", "payload")
    assert result.exit_code == 0, result.output
    public = json.loads(result.stdout)
    assert public["selected_candidate"] == "ac1_dictionary"
    assert_private(result, "private-version", data["dictionary"]["content_hash"])
    from agentcodec.codec import decode_payload
    from agentcodec.schema import UserDictionary
    assert decode_payload((tmp_path / "payload").read_text().removesuffix("\n"),
        UserDictionary(**dictionary)).model_dump() == data["message"]


def test_benchmark_dictionary_snapshot_hash_and_output_protection(tmp_path, synthetic_counter):
    # Catches ignoring the dictionary manifest or overwriting its local snapshot.
    from agentcodec.adaptive.contracts import canonical_text
    dictionary = dict(user_id="user", version="v1", aliases={"X": SECRET})
    raw = canonical_text(dictionary).encode()
    dictionary_path = tmp_path / "dictionary.json"
    dictionary_path.write_bytes(raw)
    data = benchmark_data(tmp_path)
    data["input_manifest"][0].update(dictionary_source="local_file", dictionary_path="dictionary.json",
        dictionary_sha256=hashlib.sha256(raw).hexdigest())
    path = save(tmp_path / "config.json", data)
    result = bench(path, "out")
    assert result.exit_code == 0, result.output
    assert dictionary_path.read_bytes() == raw
    data["input_manifest"][0]["dictionary_sha256"] = "0" * 64
    save(path, data)
    result = bench(path, "bad")
    assert result.exit_code == 1 and not (tmp_path / "bad").exists()
    assert_private(result, str(dictionary_path))

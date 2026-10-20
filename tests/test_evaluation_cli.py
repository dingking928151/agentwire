"""Offline CLI contracts: explicit selection, preflight, reproducibility, safe writes."""

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from agentcodec import benchmark, cli
from agentcodec.evaluation.contracts import AdapterResponse, EvalError
from agentcodec.evaluation.mock import DeterministicMockAdapter


ROOT = Path(__file__).resolve().parents[1]
RUNNER = CliRunner()
FILES = ("manifest.json", "records.jsonl", "report.json")
INPUTS = {
    "--diverse-input": ROOT / "bench/tasks.v1.jsonl",
    "--repetitive-input": ROOT / "bench/tasks-repetitive.v1.jsonl",
    "--repetitive-dictionary": ROOT / "bench/dictionaries/repeat-v1.json",
}
HASHES = {
    "diverse": "9284dc0b7a878355c6e8ddc0195cd7adcade9b4966dc3a07aeb58f2f1f2ad7b8",
    "repetitive": "f5c8d2d98837edd5944f10e6fb7a63bd44dfcff6b022aab57502b2197cd239fe",
    "repetitive_dictionary": "9b320df2e125804c07dc53418164633345c4d26ff4e56406e42b3fcd94338d86",
}


def forbidden(*args, **kwargs):
    pytest.fail("offline CLI attempted a forbidden external effect")


@pytest.fixture(autouse=True)
def offline_guards(monkeypatch):
    # The sole permitted subprocess is a fixed, read-only local revision query.
    real_run, real_popen = subprocess.run, subprocess.Popen
    calls = []

    def check(command, kwargs):
        assert command == ["git", "rev-parse", "HEAD"]
        assert Path(kwargs["cwd"]).resolve() == ROOT
        assert not kwargs.get("shell", False)
        assert "env" not in kwargs

    def guarded_run(command, *args, **kwargs):
        check(command, kwargs)
        assert not args
        return real_run(command, **kwargs)

    def guarded_popen(command, *args, **kwargs):
        check(command, kwargs)
        assert not args
        calls.append(tuple(command))
        return real_popen(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
    for owner, names in (
        (socket, ("socket", "create_connection", "getaddrinfo", "gethostbyname",
                  "gethostbyname_ex", "gethostbyaddr")),
        (subprocess, ("call", "check_call", "check_output")),
        (os, ("system", "popen", "fork", "posix_spawn", "posix_spawnp")),
        (benchmark, ("_get_encoding", "count_tokens")),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)
    import tiktoken
    monkeypatch.setattr(tiktoken, "get_encoding", forbidden)
    monkeypatch.setattr(tiktoken, "encoding_for_model", forbidden)
    yield calls


def args_for(output, **replacements):
    args = ["evaluate-offline", "--adapter", "mock-v1"]
    for option, path in INPUTS.items():
        args += [option, str(replacements.get(option, path))]
    return [*args, "--output-dir", str(output)]


def invoke(args, calls):
    before = len(calls)
    result = RUNNER.invoke(cli.app, args)
    assert len(calls) - before <= 1
    return result


def artifacts(output):
    assert {path.name for path in output.iterdir()} == set(FILES)
    data = {name: (output / name).read_bytes() for name in FILES}
    for name, raw in data.items():
        assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
        assert b"\r\n" not in raw
        for line in raw.decode("utf-8").split("\n")[:-1]:
            value = json.loads(line)
            assert line == json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (json.loads(data["manifest.json"]),
            [json.loads(line) for line in data["records.jsonl"].decode("utf-8").split("\n") if line],
            json.loads(data["report.json"]))


def assert_matrix(output, conditions, cases_per_group):
    manifest, records, report = artifacts(output)
    assert manifest["formats"] == [name for name in ("natural_language", "json", "ac1", "ac2")
                                   if name in conditions]
    assert manifest["conditions"] == conditions
    assert manifest["scenario_counts"] == {"diverse": 30, "repetitive": 30}
    assert manifest["input_hashes"] == HASHES
    assert len(manifest["code_revision"]) in (40, 64)
    assert set(manifest["code_revision"]) <= set("0123456789abcdef")
    assert manifest["adapter_id"] == "deterministic-mock-v1"
    assert manifest["policies"] == [{"fallback_mode": "record_only", "max_retries": 0}]
    assert len(records) == 2 * cases_per_group
    assert len({record["record_id"] for record in records}) == len(records)
    assert {record["adapter_id"] for record in records} == {"deterministic-mock-v1"}
    assert all(record["request"]["attempt_index"] == 0 for record in records)
    assert report["run_id"] == manifest["run_id"]
    assert report["adapter_kind"] == "mock" and report["pipeline_only"] is True
    assert "not model understanding" in report["disclaimer"]
    assert report["acceptance_passed"] is True
    assert set(report["scenarios"]) == {"diverse", "repetitive"}
    for name in ("diverse", "repetitive"):
        rows = report["scenarios"][name]["conditions"]
        assert set(rows) == set(conditions)
        assert sum(row["logical_case_count"] for row in rows.values()) == cases_per_group
        for row in rows.values():
            assert row["logical_case_count"] == row["attempt_count"] == 30
            for phase in ("initial", "final"):
                assert row[phase]["mock_task_completed_count"] == 30
                assert row[phase]["equal_field_total"] == 330
                assert row[phase]["equal_hard_total"] == 180
            for metric in ("fallback_required_case_count", "fallback_performed_case_count",
                           "retried_case_count", "retry_attempt_count"):
                assert row[metric] == 0


def test_eval_default_is_ac1_only(tmp_path, offline_guards):
    # Catches enabling AC2 or blending/deduplicating either 30-message group by default.
    output = tmp_path / "default"
    result = invoke(args_for(output), offline_guards)
    assert result.exit_code == 0, result.output
    assert len(offline_guards) == 1
    assert_matrix(output, ["ac1"], 30)


def test_each_input_is_read_once_for_hash_and_parse(tmp_path, monkeypatch, offline_guards):
    # Catches re-reading an input for parsing after hashing a separate snapshot.
    read_counts = {path.resolve(): 0 for path in INPUTS.values()}
    original_read_bytes = Path.read_bytes

    def counted_read_bytes(path):
        resolved = path.resolve()
        if resolved in read_counts:
            read_counts[resolved] += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
    result = invoke(args_for(tmp_path / "single-snapshot"), offline_guards)
    assert result.exit_code == 0
    assert read_counts == {path.resolve(): 1 for path in INPUTS.values()}


def test_eval_ac2_requires_explicit_selection(tmp_path, offline_guards):
    output = tmp_path / "four"
    result = invoke([*args_for(output), "--formats", "ac2,json,natural_language,ac1"], offline_guards)
    assert result.exit_code == 0, result.output
    assert_matrix(output, ["natural_language", "json", "ac1", "ac2"], 120)


@pytest.mark.parametrize("formats,conditions,count", [
    (None, ["ac1", "ac1_dictionary"], 60),
    ("natural_language,json,ac1,ac2", ["natural_language", "json", "ac1", "ac2",
                                     "ac1_dictionary", "ac2_dictionary"], 180),
])
def test_dictionary_pairs_only_add_selected_codecs(tmp_path, offline_guards, formats, conditions, count):
    output = tmp_path / "paired"
    args = [*args_for(output), "--dictionary-pairs", "paired"]
    if formats is not None:
        args += ["--formats", formats]
    result = invoke(args, offline_guards)
    assert result.exit_code == 0, result.output
    assert_matrix(output, conditions, count)
    manifest, _, _ = artifacts(output)
    assert {name: entry["snapshot"]["version"] for name, entry in manifest["dictionaries"].items()} == {
        "diverse": "demo-v1", "repetitive": "repeat-v1"}


@pytest.mark.parametrize("missing", ["--adapter", *INPUTS, "--output-dir"])
def test_required_options_are_usage_errors(tmp_path, offline_guards, missing):
    output = tmp_path / "missing-option"
    args = args_for(output)
    index = args.index(missing)
    del args[index:index + 2]
    result = invoke(args, offline_guards)
    assert result.exit_code == 2
    assert "Missing option" in result.stderr and missing in result.stderr
    assert not output.exists() and not offline_guards


@pytest.mark.parametrize("option,value", [
    ("--adapter", "unknown"), ("--adapter", "scripted-mock-test-v1"),
    ("--adapter", "agentcodec.evaluation.mock.DeterministicMockAdapter"),
    ("--formats", "ac1,ac1"), ("--formats", "ac1,unknown"),
    ("--formats", ""), ("--formats", "ac1,"),
    ("--dictionary-pairs", "unknown"),
])
def test_invalid_selections_are_usage_errors(tmp_path, offline_guards, option, value):
    output = tmp_path / "usage"
    args = args_for(output)
    if option == "--adapter":
        args[args.index(option) + 1] = value
    else:
        args += [option, value]
    result = invoke(args, offline_guards)
    assert result.exit_code == 2
    assert "Invalid value" in result.stderr and option in result.stderr
    assert not output.exists() and not offline_guards


@pytest.mark.parametrize("option", ["--endpoint", "--model", "--key", "--concurrency", "--network",
                                  "--retry", "--max-retries", "--fallback", "--provider"])
def test_provider_and_recovery_options_are_not_exposed(tmp_path, offline_guards, option):
    result = invoke([*args_for(tmp_path / "forbidden"), option, "x"], offline_guards)
    assert result.exit_code == 2
    assert "No such option" in result.stderr
    assert not offline_guards


@pytest.mark.parametrize("option", INPUTS)
@pytest.mark.parametrize("content", [None, b"\xff\n", b"{}\n", b"", b"changed\n"])
def test_invalid_local_inputs_fail_before_output(tmp_path, offline_guards, option, content):
    # Catches implicit corpus defaults, skipped UTF-8/schema/hash checks, or early output creation.
    source, output = tmp_path / "input", tmp_path / "output"
    if content is not None:
        source.write_bytes(content)
    result = invoke(args_for(output, **{option: source}), offline_guards)
    assert result.exit_code == 1, result.output
    assert "fallback_required" in result.stderr and "Traceback" not in result.stderr
    assert not output.exists() and not offline_guards
    if content is not None:
        assert source.read_bytes() == content


@pytest.mark.parametrize("option", INPUTS)
def test_valid_but_wrong_hash_is_rejected(tmp_path, offline_guards, option):
    source, output = tmp_path / "changed", tmp_path / "output"
    content = INPUTS[option].read_bytes() + b"\n"
    source.write_bytes(content)
    result = invoke(args_for(output, **{option: source}), offline_guards)
    assert result.exit_code == 1
    assert "hash" in result.stderr.lower()
    assert source.read_bytes() == content and not output.exists() and not offline_guards


@pytest.mark.parametrize("kind", ["nonempty", "file", "blocked-parent"])
def test_output_preflight_preserves_user_data(tmp_path, offline_guards, kind):
    marker = tmp_path / "keep"
    marker.write_bytes(b"user data\x00")
    if kind == "nonempty":
        output = tmp_path
    elif kind == "file":
        output = marker
    else:
        output = marker / "child"
    result = invoke(args_for(output), offline_guards)
    assert result.exit_code == 1, result.output
    assert marker.read_bytes() == b"user data\x00"
    assert set(tmp_path.iterdir()) == {marker}
    assert not offline_guards


def test_three_files_are_reproducible_and_inputs_unchanged(tmp_path, offline_guards):
    before = {option: hashlib.sha256(path.read_bytes()).hexdigest() for option, path in INPUTS.items()}
    first, second = tmp_path / "first", tmp_path / "second"
    second.mkdir()
    for output in (first, second):
        result = invoke([*args_for(output), "--formats", "natural_language,json,ac1,ac2",
                         "--dictionary-pairs", "paired"], offline_guards)
        assert result.exit_code == 0, result.output
    for name in FILES:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert {option: hashlib.sha256(path.read_bytes()).hexdigest() for option, path in INPUTS.items()} == before


def test_faults_remain_in_records_and_report(tmp_path, monkeypatch, offline_guards):
    # Fault injection is test-only patching, never a public adapter or CLI option.
    original = DeterministicMockAdapter.invoke

    def one_failure(self, request):
        if request.case_id == "diverse/s01/ac1":
            return AdapterResponse(status="error", raw_output=None, error=EvalError(
                stage="decode", code="injected_failure", message="Local test failure.", retryable=False))
        return original(self, request)

    monkeypatch.setattr(DeterministicMockAdapter, "invoke", one_failure)
    output = tmp_path / "fault"
    result = invoke(args_for(output), offline_guards)
    assert result.exit_code == 0, result.output
    _, records, report = artifacts(output)
    assert len(records) == 60
    assert records[0]["error"]["code"] == "injected_failure"
    assert records[0]["attempt_score"]["mock_task_completed"] is False
    assert report["acceptance_passed"] is False
    row = report["scenarios"]["diverse"]["conditions"]["ac1"]
    assert row["initial"]["mock_task_completed_count"] == row["final"]["mock_task_completed_count"] == 29
    assert row["fallback_required_case_count"] == 1
    assert row["fallback_performed_case_count"] == row["retry_attempt_count"] == 0


@pytest.mark.parametrize("user_file", [False, True])
@pytest.mark.parametrize("preexisting", [False, True])
@pytest.mark.parametrize("failing_name", FILES)
def test_write_failure_cleans_only_created_outputs(
    tmp_path, monkeypatch, offline_guards, preexisting, failing_name, user_file,
):
    output = tmp_path / "output"
    if preexisting:
        output.mkdir()
    original_open = Path.open

    class FailingWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def write(self, data):
            self.stream.write(data[:10])
            raise OSError("injected partial write failure")

        def __exit__(self, *exc):
            self.stream.close()

    def failing_open(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        if path == output / failing_name and any(flag in mode for flag in "wxa"):
            if user_file:
                # Unrelated user data appearing after preflight must survive cleanup.
                (output / "keep-user-data").write_bytes(b"keep")
            return FailingWriter(stream)
        return stream

    monkeypatch.setattr(Path, "open", failing_open)
    result = invoke(args_for(output), offline_guards)
    assert result.exit_code == 1, result.output
    assert "fallback_required" in result.stderr
    if user_file:
        assert (output / "keep-user-data").read_bytes() == b"keep"
        assert {path.name for path in output.iterdir()} == {"keep-user-data"}
    elif preexisting:
        assert output.is_dir() and not list(output.iterdir())
    else:
        assert not output.exists()


def test_mkdir_failure_does_not_remove_directory_created_by_another_writer(
    tmp_path, monkeypatch, offline_guards,
):
    output = tmp_path / "output"
    original_mkdir = Path.mkdir

    def raced_mkdir(path, *args, **kwargs):
        if path == output:
            original_mkdir(path)
            raise FileExistsError("another writer created this directory")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", raced_mkdir)
    result = invoke(args_for(output), offline_guards)
    assert result.exit_code == 1, result.output
    assert output.is_dir() and not list(output.iterdir())


def test_revision_failure_creates_no_outputs(tmp_path, monkeypatch, offline_guards):
    def fail_revision(command, **kwargs):
        assert command == ["git", "rev-parse", "HEAD"]
        raise subprocess.CalledProcessError(128, command)

    monkeypatch.setattr(subprocess, "run", fail_revision)
    output = tmp_path / "revision-failure"
    result = invoke(args_for(output), offline_guards)
    assert result.exit_code == 1, result.output
    assert "fallback_required" in result.stderr and not output.exists()

"""CLI boundary behavior: data-only round trips, strict errors, and offline reports."""
import importlib
import json
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
RUNNER = CliRunner()


@pytest.fixture
def app():
    try:
        return importlib.import_module("agentcodec.cli").app
    except ModuleNotFoundError:
        return None


def invoke(app, args, **kwargs):
    if app is None:
        pytest.fail("Required CLI implementation agentcodec.cli is missing")
    return RUNNER.invoke(app, args, **kwargs)


@pytest.fixture
def message():
    return dict(message_id="cli-1", message_type="delegate", task="Review 中文 | 20%.",
                status="pending", blocker=None, next_step="Ask before sending.",
                evidence=["local.txt"], constraints=["Do not publish.", "Budget <= $25."],
                approval_required=True, user_id="demo-user", dictionary_version=None)


def test_encode_stdin_preserves_canonical_wire(app, message):
    result = invoke(app, ["encode"], input=json.dumps(message))
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("AC1|%22cli-1%22|D|")
    from agentcodec.codec import decode_message
    assert decode_message(result.stdout.rstrip("\n")).model_dump() == message


def test_file_encode_and_stdin_decode_preserve_every_field(app, message, tmp_path):
    source, wire = tmp_path / "message.json", tmp_path / "message.ac1"
    source.write_text(json.dumps(message), encoding="utf-8")
    encoded = invoke(app, ["encode", "--input", str(source), "--output", str(wire)])
    assert encoded.exit_code == 0, encoded.output
    assert encoded.stdout == ""
    decoded = invoke(app, ["decode"], input=wire.read_text(encoding="utf-8"))
    assert decoded.exit_code == 0, decoded.output
    assert json.loads(decoded.stdout) == message


def test_decode_file_to_output_is_data_not_execution(app, message, tmp_path):
    marker = tmp_path / "never-created"
    message["task"] = f"__import__('pathlib').Path({str(marker)!r}).touch()"
    from agentcodec.codec import encode_message
    from agentcodec.schema import MessageV1
    source, target = tmp_path / "wire.txt", tmp_path / "decoded.json"
    source.write_text(encode_message(MessageV1(**message)) + "\n", encoding="utf-8")
    result = invoke(app, ["decode", "--input", str(source), "--output", str(target)])
    assert result.exit_code == 0, result.output
    assert json.loads(target.read_text(encoding="utf-8")) == message
    assert not marker.exists()


@pytest.mark.parametrize("wire", [False, True])
def test_validate_json_or_wire_without_changing_data(app, message, wire):
    text = json.dumps(message)
    args = ["validate"]
    if wire:
        from agentcodec.codec import encode_message
        from agentcodec.schema import MessageV1
        text = encode_message(MessageV1(**message))
        args += ["--format", "wire"]
    result = invoke(app, args, input=text)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"status": "valid"}


@pytest.fixture
def dictionary(tmp_path):
    path = tmp_path / "dictionary.json"
    path.write_text(json.dumps({"user_id": "demo-user", "version": "v1",
                                "aliases": {"NP": "Do not publish."}}), encoding="utf-8")
    return path


def test_dictionary_round_trip_requires_explicit_matching_selection(app, message, dictionary):
    message["dictionary_version"] = "v1"
    selection = ["--dictionary", str(dictionary), "--user-id", "demo-user"]
    encoded = invoke(app, ["encode", *selection], input=json.dumps(message))
    assert encoded.exit_code == 0, encoded.output
    assert "%22a%22%3A%22NP%22" in encoded.stdout
    decoded = invoke(app, ["decode", *selection], input=encoded.stdout)
    assert decoded.exit_code == 0, decoded.output
    assert json.loads(decoded.stdout) == message


@pytest.mark.parametrize("command", ["encode", "decode", "validate"])
@pytest.mark.parametrize("user_id,reason", [(None, "user-id"), ("other-user", "user")])
def test_dictionary_missing_or_mismatched_user_fails_closed(app, dictionary, command, user_id, reason):
    args = [command, "--dictionary", str(dictionary)]
    if user_id:
        args += ["--user-id", user_id]
    result = invoke(app, args, input="{}")
    assert result.exit_code != 0
    assert "fallback_required" in result.stderr
    assert reason in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("command", ["decode", "validate"])
def test_unknown_code_requires_fallback_without_partial_output(app, message, command):
    from agentcodec.codec import encode_message
    from agentcodec.schema import MessageV1
    parts = encode_message(MessageV1(**message)).split("|")
    parts[2] = "UNKNOWN"
    args = [command] + (["--format", "wire"] if command == "validate" else [])
    result = invoke(app, args, input="|".join(parts))
    assert result.exit_code != 0
    assert "fallback_required" in result.stderr
    assert "enum" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("text", ["{bad}", "{}", "[]", '{"task":"x","task":"y"}'])
def test_invalid_json_is_readable_nonzero_error(app, text):
    result = invoke(app, ["encode"], input=text)
    assert result.exit_code != 0
    assert "fallback_required" in result.stderr
    assert result.stdout == ""
    assert "Traceback" not in result.stderr


def test_read_and_write_errors_are_readable(app, message, tmp_path):
    for args in (["encode", "--input", str(tmp_path / "missing")],
                 ["encode", "--output", str(tmp_path)]):
        result = invoke(app, args, input=json.dumps(message))
        assert result.exit_code != 0
        assert "fallback_required" in result.stderr
        assert "Traceback" not in result.stderr


def test_bad_input_does_not_overwrite_existing_output(app, tmp_path):
    path = tmp_path / "keep.json"
    path.write_text("keep me", encoding="utf-8")
    result = invoke(app, ["decode", "--output", str(path)], input="AC99|bad")
    assert result.exit_code != 0
    assert path.read_text(encoding="utf-8") == "keep me"


def test_benchmark_real_local_corpus_is_deterministic_offline(app, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("CLI attempted network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    args = ["benchmark", "--input", str(ROOT / "bench/tasks.v1.jsonl")]
    first = invoke(app, args)
    second = invoke(app, args)
    assert first.exit_code == second.exit_code == 0, first.output
    assert first.stdout == second.stdout
    report = json.loads(first.stdout)
    assert report["task_count"] == 30
    assert report["reuse_counts"] == [1, 10, 50]
    assert set(report["results"]) == {"o200k_base", "cl100k_base"}
    for rows in report["results"].values():
        for name in ("codec", "codec_dictionary"):
            assert rows[name]["round_trip_equal_count"] == 30
            assert rows[name]["fallback_count"] == 0
            assert rows[name]["critical_constraint_loss_count"] == 0
            assert rows[name]["total_tokens"] > rows["json"]["total_tokens"]


def test_benchmark_stdin_and_output_file(app, message, tmp_path):
    path = tmp_path / "report.json"
    result = invoke(app, ["benchmark", "--output", str(path)], input=json.dumps(message) + "\n")
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert json.loads(path.read_text(encoding="utf-8"))["task_count"] == 1


def test_benchmark_missing_cache_fails_offline(app, tmp_path, monkeypatch):
    from agentcodec import benchmark
    benchmark._get_encoding.cache_clear()
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    def forbidden(*args, **kwargs):
        pytest.fail("CLI attempted network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    try:
        result = invoke(app, ["benchmark", "--input", str(ROOT / "bench/tasks.v1.jsonl")])
        assert result.exit_code != 0
        assert "cache" in result.stderr and "fallback_required" in result.stderr
        assert result.stdout == ""
    finally:
        benchmark._get_encoding.cache_clear()


@pytest.mark.parametrize("text", ["\n", "{}\n", '{"task":"x","task":"y"}\n'])
def test_benchmark_rejects_invalid_stdin(app, text):
    result = invoke(app, ["benchmark"], input=text)
    assert result.exit_code != 0
    assert "fallback_required" in result.stderr
    assert result.stdout == ""

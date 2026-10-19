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


@pytest.mark.parametrize("format", ["ac1", "ac2"])
def test_decode_file_to_output_is_data_not_execution(app, message, tmp_path, format):
    marker = tmp_path / "never-created"
    message["task"] = f"__import__('pathlib').Path({str(marker)!r}).touch()"
    from agentcodec.ac2 import encode_message_ac2
    from agentcodec.codec import encode_message
    from agentcodec.schema import MessageV1
    source, target = tmp_path / "wire.txt", tmp_path / "decoded.json"
    encoder = encode_message_ac2 if format == "ac2" else encode_message
    source.write_text(encoder(MessageV1(**message)) + "\n", encoding="utf-8")
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


@pytest.mark.parametrize("format", [None, "ac1", "ac2"])
def test_dictionary_round_trip_requires_explicit_matching_selection(app, message, dictionary, format):
    message["dictionary_version"] = "v1"
    selection = ["--dictionary", str(dictionary), "--user-id", "demo-user"]
    format_args = ["--format", format] if format else []
    encoded = invoke(app, ["encode", *format_args, *selection], input=json.dumps(message))
    assert encoded.exit_code == 0, encoded.output
    assert ('{"a":"NP"}' if format == "ac2" else "%22a%22%3A%22NP%22") in encoded.stdout
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


@pytest.mark.parametrize("command", ["benchmark", "benchmark-suite"])
def test_benchmark_missing_cache_fails_offline(app, tmp_path, monkeypatch, command):
    from agentcodec import benchmark
    benchmark._get_encoding.cache_clear()
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    def forbidden(*args, **kwargs):
        pytest.fail("CLI attempted network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    try:
        args = suite_args() if command == "benchmark-suite" else ["benchmark", "--input", str(ROOT / "bench/tasks.v1.jsonl")]
        result = invoke(app, args)
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


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
@pytest.mark.parametrize("record_delimiter", ["\n", "\r\n"])
@pytest.mark.parametrize("record_count", [1, 2])
@pytest.mark.parametrize("source", ["file", "stdin"])
def test_benchmark_preserves_unicode_line_separators(
    app, message, tmp_path, separator, record_delimiter, record_count, source,
):
    # Catches either CLI path splitting valid content rather than record delimiters.
    text = f"before{separator}after"
    records = [message | dict(message_id=f"cli-{index}", task=text, blocker=text,
                              next_step=text, evidence=[text], constraints=[text])
               for index in range(record_count)]
    serialized = record_delimiter.join(json.dumps(row, ensure_ascii=False) for row in records)
    serialized += record_delimiter
    args = ["benchmark"]
    kwargs = {"input": serialized}
    if source == "file":
        path = tmp_path / "unicode.jsonl"
        path.write_bytes(serialized.encode("utf-8"))
        args += ["--input", str(path)]
        kwargs = {}
    result = invoke(app, args, **kwargs)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["task_count"] == record_count
    for rows in report["results"].values():
        for name in ("codec", "codec_dictionary"):
            assert [row["message_id"] for row in rows[name]["messages"]] == [
                row["message_id"] for row in records
            ]
            assert rows[name]["round_trip_equal_count"] == record_count
            assert rows[name]["critical_constraint_loss_count"] == 0
            assert rows[name]["fallback_count"] == 0


@pytest.mark.parametrize("command", ["decode", "validate"])
@pytest.mark.parametrize("escaped", ["%22%5Cud800%22", "%22%5Cudfff%22"])
def test_escaped_lone_surrogate_wire_fails_closed(app, message, command, escaped):
    # Catches reporting invalid escaped Unicode as valid or emitting partial JSON.
    from agentcodec.codec import encode_message
    from agentcodec.schema import MessageV1
    parts = encode_message(MessageV1(**message)).split("|")
    parts[3] = escaped
    args = [command] + (["--format", "wire"] if command == "validate" else [])
    result = invoke(app, args, input="|".join(parts))
    assert result.exit_code != 0
    assert "fallback_required" in result.stderr
    assert result.stdout == ""
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("source", ["file", "stdin"])
def test_benchmark_rejects_bare_carriage_return_record_delimiter(app, message, tmp_path, source):
    # Catches inconsistent record delimiters between file and stdin paths.
    serialized = json.dumps(message) + "\r" + json.dumps(message | {"message_id": "cli-2"})
    args, kwargs = ["benchmark"], {"input": serialized}
    if source == "file":
        path = tmp_path / "bare-cr.jsonl"
        path.write_bytes(serialized.encode("utf-8"))
        args += ["--input", str(path)]
        kwargs = {}
    result = invoke(app, args, **kwargs)
    assert result.exit_code != 0
    assert "fallback_required" in result.stderr
    assert "Extra data" in result.stderr
    assert result.stdout == ""


# Task 3: lock the default wire bytes, format dispatch, and explicit suite inputs.
AC1_GOLDEN = (
    'AC1|%22cli-1%22|D|%22Review%20%E4%B8%AD%E6%96%87%20%7C%2020%25.%22|P|null|'
    '%22Ask%20before%20sending.%22|%5B%22local.txt%22%5D|'
    '%5B%22Do%20not%20publish.%22%2C%22Budget%20%3C%3D%20%2425.%22%5D|true|%22demo-user%22|null\n'
)
AC2_GOLDEN = (
    'AC2|["cli-1","D","Review 中文 | 20%.","P",null,"Ask before sending.",'
    '["local.txt"],["Do not publish.","Budget <= $25."],true,"demo-user",null]\n'
)


@pytest.mark.parametrize("format,expected", [(None, AC1_GOLDEN), ("ac1", AC1_GOLDEN),
                                             ("ac2", AC2_GOLDEN)])
def test_encode_format_has_exact_wire_and_ac1_default(app, message, format, expected):
    # Catches changing default AC1 bytes or routing explicit AC2 to AC1.
    args = ["encode"] + (["--format", format] if format else [])
    result = invoke(app, args, input=json.dumps(message))
    assert result.exit_code == 0, result.output
    assert result.stdout == expected


@pytest.mark.parametrize("wire", [AC1_GOLDEN, AC2_GOLDEN])
@pytest.mark.parametrize("command", ["decode", "validate"])
def test_known_wire_prefix_is_autodetected(app, message, wire, command):
    # Literal wires independently test both decoder/validator dispatch branches.
    args = [command] + (["--format", "wire"] if command == "validate" else [])
    result = invoke(app, args, input=wire)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == (message if command == "decode" else {"status": "valid"})


@pytest.mark.parametrize("wire", ["AC99|[]", "AC2x|[]", "ac2|[]", "AC2|[]", "AC2|[bad]"])
@pytest.mark.parametrize("command", ["decode", "validate"])
def test_unknown_or_malformed_wire_fails_without_partial_output(app, wire, command):
    args = [command] + (["--format", "wire"] if command == "validate" else [])
    result = invoke(app, args, input=wire)
    assert result.exit_code == 1
    assert "fallback_required" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("format", ["ac1", "ac2"])
@pytest.mark.parametrize("failure", ["missing", "alias", "version"])
@pytest.mark.parametrize("command", ["decode", "validate"])
def test_wire_dictionary_failure_is_closed(app, message, dictionary, format, failure, command):
    from agentcodec.ac2 import encode_message_ac2
    from agentcodec.codec import encode_message
    from agentcodec.dictionary import load_dictionary
    from agentcodec.schema import MessageV1
    message["dictionary_version"] = "v1"
    encoder = encode_message_ac2 if format == "ac2" else encode_message
    wire = encoder(MessageV1(**message), load_dictionary(dictionary, "demo-user"))
    selection = ["--dictionary", str(dictionary), "--user-id", "demo-user"]
    if failure == "missing":
        selection = []
    elif failure == "alias":
        wire = wire.replace("NP", "UNKNOWN")
    else:
        wire = wire.replace("v1", "v99")
    args = [command, *selection] + (["--format", "wire"] if command == "validate" else [])
    result = invoke(app, args, input=wire)
    assert result.exit_code == 1
    assert "fallback_required" in result.stderr
    assert result.stdout == ""


def suite_args():
    return ["benchmark-suite", "--diverse-input", str(ROOT / "bench/tasks.v1.jsonl"),
            "--repetitive-input", str(ROOT / "bench/tasks-repetitive.v1.jsonl"),
            "--repetitive-dictionary", str(ROOT / "bench/dictionaries/repeat-v1.json")]


@pytest.mark.parametrize("missing", ["--diverse-input", "--repetitive-input", "--repetitive-dictionary"])
def test_suite_requires_each_explicit_path(app, missing):
    args = suite_args()
    index = args.index(missing)
    del args[index:index + 2]
    result = invoke(app, args)
    assert result.exit_code == 2
    assert "Missing option" in result.stderr and missing in result.stderr


def test_benchmark_suite_reports_separate_matched_scenarios_offline(app, monkeypatch, tmp_path):
    # Catches missing suite, wrong dictionaries/formats, blended corpora, or network use.
    def forbidden(*args, **kwargs):
        pytest.fail("CLI attempted network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    first = invoke(app, suite_args())
    target = tmp_path / "suite.json"
    second = invoke(app, [*suite_args(), "--output", str(target)])
    assert first.exit_code == second.exit_code == 0, first.output + second.output
    assert second.stdout == ""
    assert first.stdout == target.read_text(encoding="utf-8")
    report = json.loads(first.stdout)
    assert report["report_version"] == "suite-v1"
    assert report["reuse_counts"] == [1, 10, 50]
    assert set(report["scenarios"]) == {"diverse", "repetitive"}
    for name, owner, version in [("diverse", "demo-user", "demo-v1"),
                                  ("repetitive", "repeat-user", "repeat-v1")]:
        scenario = report["scenarios"][name]
        assert scenario["task_count"] == 30
        assert scenario["dictionary"]["user_id"] == owner
        assert scenario["dictionary"]["version"] == version
        assert set(scenario["results"]) == {"o200k_base", "cl100k_base"}
        for encoding, rows in scenario["results"].items():
            assert set(rows) == {"natural_language", "json", "drixl", "ac1", "ac1_dictionary",
                                 "ac2", "ac2_dictionary"}
            assert scenario["ac1_diagnostics"][encoding]["diagnostic_only"] is True
            assert rows["ac1_dictionary"]["dictionary_tokens"] == rows["ac2_dictionary"]["dictionary_tokens"] > 0
            for codec in ("ac1", "ac1_dictionary", "ac2", "ac2_dictionary"):
                row = rows[codec]
                assert row["round_trip_equal_count"] == 30
                assert row["critical_constraint_loss_count"] == row["fallback_count"] == 0
                for reuse in (1, 10, 50):
                    assert row["amortized"][str(reuse)]["total_tokens"] == row["setup_tokens"] + reuse * row["total_tokens"]
    # Preserve negative empirical cases, using complete-session rather than payload cost.
    diverse = report["scenarios"]["diverse"]["results"]
    for rows in diverse.values():
        assert rows["ac1"]["amortized"]["1"]["total_tokens"] > rows["json"]["total_tokens"]
        assert rows["ac2_dictionary"]["total_tokens"] > rows["ac2"]["total_tokens"]
    repeat = report["scenarios"]["repetitive"]["results"]["o200k_base"]
    assert repeat["ac1_dictionary"]["amortized"]["1"]["total_tokens"] == 7779
    assert repeat["json"]["total_tokens"] == 7760


def test_suite_consumes_supplied_paths_and_dictionary_not_repo_defaults(app, message, tmp_path, dictionary):
    # Nonstandard paths, different corpus sizes and owner/version reveal implicit defaults.
    diverse, repetitive = tmp_path / "one.jsonl", tmp_path / "two.jsonl"
    diverse.write_text(json.dumps(message) + "\n", encoding="utf-8")
    repetitive.write_text("\n".join(json.dumps(message | {"message_id": f"custom-{n}"})
                                     for n in range(2)) + "\n", encoding="utf-8")
    result = invoke(app, ["benchmark-suite", "--diverse-input", str(diverse),
                         "--repetitive-input", str(repetitive),
                         "--repetitive-dictionary", str(dictionary)])
    assert result.exit_code == 0, result.output
    scenarios = json.loads(result.stdout)["scenarios"]
    assert scenarios["diverse"]["task_count"] == 1
    assert scenarios["repetitive"]["task_count"] == 2
    assert scenarios["repetitive"]["dictionary"] == {"user_id": "demo-user", "version": "v1",
                                                        "aliases": {"NP": "Do not publish."}}
    for rows in scenarios["repetitive"]["results"].values():
        for name in ("ac1_dictionary", "ac2_dictionary"):
            assert rows[name]["round_trip_equal_count"] == 2
            assert [m["message_id"] for m in rows[name]["messages"]] == ["custom-0", "custom-1"]


@pytest.mark.parametrize("option", ["--diverse-input", "--repetitive-input", "--repetitive-dictionary"])
@pytest.mark.parametrize("content", [None, "{}", '{"user_id":"repeat-user","user_id":"repeat-user"}'])
def test_suite_invalid_local_input_preserves_output(app, tmp_path, option, content):
    source, target = tmp_path / "invalid", tmp_path / "keep.json"
    if content is not None:
        source.write_text(content, encoding="utf-8")
    target.write_text("keep me", encoding="utf-8")
    args = suite_args()
    args[args.index(option) + 1] = str(source)
    result = invoke(app, [*args, "--output", str(target)])
    assert result.exit_code == 1
    assert "fallback_required" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
    assert target.read_text(encoding="utf-8") == "keep me"


def test_suite_dictionary_owner_must_match_repetitive_input(app, dictionary):
    args = suite_args()
    args[args.index("--repetitive-dictionary") + 1] = str(dictionary)
    result = invoke(app, args)
    assert result.exit_code == 1
    assert "fallback_required" in result.stderr and "user" in result.stderr
    assert result.stdout == ""

"""Opt-in AgentWire CLI tests over the fixed synthetic fixtures only."""

import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "bench" / "agentwire"
RUNNER = CliRunner()
FIXTURE_MANIFEST_ID = "agentwire-synthetic-fixtures-v1"


@pytest.fixture
def app():
    return importlib.import_module("agentcodec.cli").app


def invoke(app, args):
    return RUNNER.invoke(app, ["benchmark-agentwire", *args])


def _fake_report():
    from agentcodec.agentwire.benchmark import (
        AgentWireBenchmarkReport, BenchmarkComparison, BenchmarkRow, ScenarioReport,
    )

    comparison = BenchmarkComparison(
        status="cannot_compare", cost_delta_tokens=None,
        break_even_status="cannot_compare", first_strict_win=None,
    )
    base_row = BenchmarkRow(
        synthetic_corpus_ordinal=1,
        tokenizer_profile_id="o200k_base",
        receiver_mode="gateway",
        candidate_id="natural_language",
        metric_surface="end_to_end",
        metric_status="cannot_compare",
        dictionary_condition="none",
        dictionary_candidate_exposure="not_applicable",
        reuse_unit="same_message_instance",
        total_uses=1,
        case_count=1,
        event_count=1,
        source_hash="a" * 64,
        transport_hash="b" * 64,
        transport_bytes=1,
        transport_tokens=None,
        model_input_tokens=None,
        synthetic_response_tokens=None,
        distinct_text_event_tokens=None,
        candidate_protocol_exposure_tokens=None,
        candidate_protocol_setup_history_tokens=None,
        candidate_protocol_setup_tokens=None,
        negotiation_tokens=None,
        dictionary_exposure_tokens=None,
        dictionary_history_tokens=None,
        dictionary_setup_tokens=None,
        request_tokens=None,
        response_tokens=None,
        retry_tokens=None,
        fallback_tokens=None,
        per_use_cost_tokens=None,
        amortized_cost_tokens=None,
        lifecycle_cost_tokens=None,
        strict_decode_status="decoded",
        strict_decode_success_count=1,
        full_field_equal_count=1,
        hard_field_equal_count=1,
        vs_natural_language_labelled=comparison,
        vs_natural_language_freeform=comparison,
    )
    scenarios = []
    for domain in ("source", "selected_dictionary_transport"):
        for unit in ("same_message_instance", "whole_corpus_pass"):
            rows = tuple(
                base_row.model_copy(update={
                    "synthetic_corpus_ordinal": 1,
                    "tokenizer_profile_id": profile,
                    "receiver_mode": mode,
                    "reuse_unit": unit,
                    "total_uses": count,
                })
                for profile in ("o200k_base", "cl100k_base")
                for mode in ("model_direct", "gateway")
                for count in (1, 10, 50)
            )
            scenarios.append(ScenarioReport(
                metadata_domain=domain,
                reuse_unit=unit,
                source_hash="a" * 64,
                transport_hash="b" * 64,
                rows=rows,
            ))
    return AgentWireBenchmarkReport(scenarios=tuple(scenarios))


def _stub_runner(monkeypatch, captured=None, inject_private=False):
    benchmark = importlib.import_module("agentcodec.agentwire.benchmark")

    class Report:
        def __init__(self, data):
            self.data = data

        def model_dump(self, *, mode):
            assert mode == "json"
            data = self.data
            if inject_private:
                data["input_hash"] = "c" * 64
                data["absolute_path"] = str(ROOT / "bench" / "agentwire" / "exchanges.v1.jsonl")
                data["private_note"] = "RAW-TOP-LEVEL-NOTE"
                data["notes"].append("RAW-NOTE-WITH-PATH /private/absolute/path")
                for scenario in data["scenarios"]:
                    scenario.update({
                        "scenario_id": "RAW-SCENARIO-ID",
                        "scope_id": "RAW-SCOPE-ID",
                        "user_id": "RAW-USER-ID",
                        "corpus_id": "RAW-CORPUS-ID",
                        "message": "RAW-MESSAGE-CONTENT",
                        "dictionary": "RAW-DICTIONARY-CONTENT",
                        "payload": "RAW-PAYLOAD-CONTENT",
                        "absolute_path": "/private/scenario/path",
                    })
                    for row in scenario["rows"]:
                        row.update({
                            "input_hash": "d" * 64,
                            "glossary": "RAW-GLOSSARY-CONTENT",
                            "absolute_path": "/private/row/path",
                        })
                        row["vs_natural_language_labelled"]["payload"] = "RAW-NESTED-PAYLOAD"
            return data

    def run(scenarios, profiles, dictionary=None, modes=("model_direct", "gateway")):
        if captured is not None:
            captured.append((scenarios, profiles, dictionary, modes))
        selected_by_unit = {}
        for scenario in scenarios:
            selected_by_unit.setdefault(scenario.reuse.unit, set()).update(
                scenario.reuse.total_uses,
            )
        data = _fake_report().model_dump(mode="json")
        for scenario_report in data["scenarios"]:
            selected = selected_by_unit[scenario_report["reuse_unit"]]
            scenario_report["rows"] = [
                row for row in scenario_report["rows"]
                if row["total_uses"] in selected and row["receiver_mode"] in modes
            ]
        return Report(data)

    monkeypatch.setattr(benchmark, "run_agentwire_benchmark", run)


def test_benchmark_agentwire_writes_versioned_report(app, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("AgentWire CLI attempted network access")

    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "absent-tokenizer-cache"))
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    output_dir = tmp_path / "report"
    result = invoke(app, ["--output-dir", str(output_dir)])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert result.stderr == ""
    assert {path.name for path in output_dir.iterdir()} == {"report.json"}
    raw = (output_dir / "report.json").read_text(encoding="utf-8")
    report = json.loads(raw)
    assert report["version"] == "agentwire-benchmark-v1"
    assert report["fixture_manifest_id"] == FIXTURE_MANIFEST_ID
    assert set(report) == {"version", "fixture_manifest_id", "scenarios", "notes"}
    assert {item["metadata_domain"] for item in report["scenarios"]} == {
        "source", "selected_dictionary_transport",
    }
    assert {item["reuse_unit"] for item in report["scenarios"]} == {
        "same_message_instance", "whole_corpus_pass",
    }
    rows = [row for item in report["scenarios"] for row in item["rows"]]
    assert {row["receiver_mode"] for row in rows} == {"gateway", "model_direct"}
    assert {row["total_uses"] for row in rows} == {1, 10, 50}
    assert {row["tokenizer_profile_id"] for row in rows} == {"o200k_base", "cl100k_base"}
    assert {row["metric_status"] for row in rows} <= {"cannot_compare", "not_applicable"}
    assert any(row["metric_status"] == "cannot_compare" for row in rows)
    assert str(ROOT) not in raw
    assert "exchanges.v1.jsonl" not in raw
    assert "dictionary.v1.json" not in raw
    assert all("payload" not in key.lower() and "message" not in key.lower()
               for item in report["scenarios"] for row in item["rows"] for key in row)


def test_report_allowlist_drops_hashes_and_private_values(app, tmp_path, monkeypatch):
    _stub_runner(monkeypatch, inject_private=True)
    output_dir = tmp_path / "strict-public-report"
    result = invoke(app, [
        "--output-dir", str(output_dir), "--tokenizers", "cl100k_base",
        "--instance-uses", "1", "--corpus-passes", "1", "--modes", "gateway",
    ])

    assert result.exit_code == 0, result.output
    raw = (output_dir / "report.json").read_text(encoding="utf-8")
    report = json.loads(raw)

    def recursive_keys_and_values(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from recursive_keys_and_values(child)
        elif isinstance(value, list):
            for child in value:
                yield from recursive_keys_and_values(child)
        else:
            yield value

    serialized_values = tuple(recursive_keys_and_values(report))
    assert not any(isinstance(value, str) and "hash" in value.lower()
                   for value in serialized_values)
    for secret_hash in ("a" * 64, "b" * 64, "c" * 64, "d" * 64):
        assert secret_hash not in raw
        assert secret_hash not in serialized_values
    for private_value in (
        "RAW-SCENARIO-ID", "RAW-SCOPE-ID", "RAW-USER-ID", "RAW-CORPUS-ID",
        "RAW-MESSAGE-CONTENT", "RAW-DICTIONARY-CONTENT", "RAW-PAYLOAD-CONTENT",
        "RAW-GLOSSARY-CONTENT", "RAW-NESTED-PAYLOAD", "RAW-TOP-LEVEL-NOTE",
        "RAW-NOTE-WITH-PATH /private/absolute/path", "/private/scenario/path",
        "/private/row/path", str(ROOT / "bench" / "agentwire" / "exchanges.v1.jsonl"),
    ):
        assert private_value not in raw


def test_cli_import_has_no_tokenizer_or_network_side_effect():
    script = r'''\
import os, socket, sys
fixture_paths = {
    os.path.realpath(os.path.join(os.getcwd(), "bench", "agentwire", name))
    for name in ("exchanges.v1.jsonl", "dictionary.v1.json")
}
def blocked(*args, **kwargs):
    raise AssertionError("network access during CLI import")
socket.socket.connect = blocked
socket.create_connection = blocked
def audit(event, args):
    if event == "open" and args:
        try:
            candidate = os.path.realpath(os.fspath(args[0]))
        except (TypeError, ValueError):
            return
        if candidate in fixture_paths:
            raise AssertionError("fixed benchmark fixture read during CLI import")
sys.addaudithook(audit)
import agentcodec.cli
assert "tiktoken" not in sys.modules
assert "agentcodec.agentwire.benchmark" not in sys.modules
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_cli_uses_only_fixed_synthetic_fixtures(app, tmp_path, monkeypatch):
    captured = []
    _stub_runner(monkeypatch, captured)
    reads = []
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text

    def tracked_read_bytes(path):
        if path.parent == FIXTURE_DIR:
            reads.append(path.name)
        return original_read_bytes(path)

    def tracked_read_text(path, *args, **kwargs):
        if path.parent == FIXTURE_DIR:
            reads.append(path.name)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)
    monkeypatch.setattr(Path, "read_text", tracked_read_text)
    help_result = invoke(app, ["--help"])
    assert help_result.exit_code == 0, help_result.output
    assert "--output-dir" in help_result.stdout
    assert "--tokenizers" in help_result.stdout
    assert "--instance-uses" in help_result.stdout
    assert "--corpus-passes" in help_result.stdout
    assert "--modes" in help_result.stdout
    assert "--input" not in help_result.stdout
    assert "--dictionary" not in help_result.stdout

    output_dir = tmp_path / "fixed"
    result = invoke(app, [
        "--output-dir", str(output_dir), "--tokenizers", "cl100k_base",
        "--instance-uses", "1", "--corpus-passes", "1", "--modes", "gateway",
    ])
    assert result.exit_code == 0, result.output
    assert sorted(reads) == ["dictionary.v1.json", "exchanges.v1.jsonl"]
    assert len(captured) == 1
    assert captured[0][2] is not None
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    assert report["fixture_manifest_id"] == FIXTURE_MANIFEST_ID
    assert str(ROOT) not in json.dumps(report)


def test_reuse_options_remain_separate(app, tmp_path, monkeypatch):
    captured = []
    _stub_runner(monkeypatch, captured)
    output_dir = tmp_path / "separate-reuse"
    result = invoke(app, [
        "--output-dir", str(output_dir), "--tokenizers", "cl100k_base",
        "--instance-uses", "10", "--corpus-passes", "50", "--modes", "gateway",
    ])

    assert result.exit_code == 0, result.output
    assert len(captured) == 1
    scenarios, profiles, _, modes = captured[0]
    assert set(profiles) == {"cl100k_base"}
    assert {scenario.reuse.unit for scenario in scenarios} == {
        "same_message_instance", "whole_corpus_pass",
    }
    for scenario in scenarios:
        wanted = (10,) if scenario.reuse.unit == "same_message_instance" else (50,)
        assert scenario.reuse.total_uses == wanted
    assert modes == ("gateway",)
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    for scenario in report["scenarios"]:
        wanted = {10} if scenario["reuse_unit"] == "same_message_instance" else {50}
        assert {row["total_uses"] for row in scenario["rows"]} == wanted
        assert {row["receiver_mode"] for row in scenario["rows"]} == {"gateway"}
        assert {row["tokenizer_profile_id"] for row in scenario["rows"]} == {"cl100k_base"}


def test_reuse_defaults_pass_full_ranges_to_runner(app, tmp_path, monkeypatch):
    captured = []
    _stub_runner(monkeypatch, captured)
    output_dir = tmp_path / "default-reuse"
    result = invoke(app, ["--output-dir", str(output_dir)])

    assert result.exit_code == 0, result.output
    scenarios, _, _, _ = captured[0]
    assert scenarios
    assert all(scenario.reuse.total_uses == (1, 10, 50) for scenario in scenarios)
    assert captured[0][3] == ("gateway", "model_direct")


@pytest.mark.parametrize("args", [
    ["--input", "caller.jsonl"],
    ["--dictionary", "caller.json"],
    ["--instance-uses", "0"],
    ["--instance-uses", "51"],
    ["--instance-uses", "1,2"],
    ["--corpus-passes", "-1"],
    ["--corpus-passes", "10,10"],
    ["--tokenizers", "remote-model"],
    ["--modes", "provider"],
])
def test_unknown_options_and_invalid_reuse_fail_closed(app, tmp_path, args):
    output_dir = tmp_path / "must-not-exist"
    result = invoke(app, ["--output-dir", str(output_dir), *args])
    assert result.exit_code != 0
    assert not output_dir.exists()
    assert "Traceback" not in result.stderr


def test_output_path_must_be_new_empty_directory(app, tmp_path, monkeypatch):
    _stub_runner(monkeypatch)

    empty = tmp_path / "empty"
    empty.mkdir()
    accepted = invoke(app, ["--output-dir", str(empty), "--tokenizers", "cl100k_base",
                            "--instance-uses", "1", "--corpus-passes", "1", "--modes", "gateway"])
    assert accepted.exit_code == 0, accepted.output
    assert {path.name for path in empty.iterdir()} == {"report.json"}

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    sentinel = nonempty / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    file_collision = FIXTURE_DIR / "exchanges.v1.jsonl"
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    special = tmp_path / "pipe"
    os.mkfifo(special)

    for path in (nonempty, file_collision, link, special):
        result = invoke(app, ["--output-dir", str(path), "--tokenizers", "cl100k_base",
                              "--instance-uses", "1", "--corpus-passes", "1", "--modes", "gateway"])
        assert result.exit_code != 0
        assert "Traceback" not in result.stderr
        assert "report.json" not in {child.name for child in path.iterdir()} if path == nonempty else True
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "report.json").exists()


def test_missing_local_tokenizer_never_downloads(app, tmp_path, monkeypatch):
    from agentcodec.adaptive import meter

    def missing(*args, **kwargs):
        raise meter.MeasurementUnavailable("tokenizer_unavailable")

    def forbidden(*args, **kwargs):
        pytest.fail("AgentWire CLI attempted network access")

    monkeypatch.setattr(meter, "_read_vocabulary", missing)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    output_dir = tmp_path / "no-tokenizer"
    result = invoke(app, [
        "--output-dir", str(output_dir), "--tokenizers", "cl100k_base",
        "--instance-uses", "1", "--corpus-passes", "1", "--modes", "gateway",
    ])

    assert result.exit_code == 0, result.output
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    statuses = {row["metric_status"] for scenario in report["scenarios"] for row in scenario["rows"]}
    assert "cannot_compare" in statuses
    assert statuses <= {"cannot_compare", "not_applicable"}
    assert result.stdout == ""

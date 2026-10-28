"""Legacy API and report compatibility sentinels; every fixture is synthetic."""

import hashlib
import json
import subprocess
from importlib.metadata import version
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentcodec import benchmark, cli
from agentcodec.ac2 import encode_message_ac2
from agentcodec.codec import FallbackRequired, decode_message, decode_payload, encode_message
from agentcodec.agentwire.codec import encode_awp1
from agentcodec.schema import MessageV1

ROOT = Path(__file__).resolve().parents[1]
RUNNER = CliRunner()
MESSAGE = {
    "message_id": "compat-1",
    "message_type": "delegate",
    "task": "Review the local report.",
    "status": "pending",
    "blocker": None,
    "next_step": "Return a summary",
    "evidence": [],
    "constraints": ["Do not publish."],
    "approval_required": True,
    "user_id": "compat-user",
    "dictionary_version": None,
}
AC1_GOLDEN = (
    "AC1|%22compat-1%22|D|%22Review%20the%20local%20report.%22|P|null|"
    "%22Return%20a%20summary%22|%5B%5D|%5B%22Do%20not%20publish.%22%5D|"
    "true|%22compat-user%22|null"
)
AC2_GOLDEN = (
    'AC2|["compat-1","D","Review the local report.","P",null,'
    '"Return a summary",[],["Do not publish."],true,"compat-user",null]'
)


def _adaptive_request(message):
    return {
        "contract_version": "adaptive-selection-v1",
        "message": message,
        "expected_user_id": message["user_id"],
        "tokenizer": {
            "encoding_name": "unknown",
            "library_version": version("tiktoken"),
            "vocabulary_sha256": "0" * 64,
            "renderer_version": "adaptive-text-v1",
            "accuracy": "unknown",
            "mapping_basis": None,
        },
        "original_format": "ac1",
        "allowlist": ["json", "ac1"],
        "capabilities": {
            "receiver_id": "compat-receiver",
            "supported_candidates": ["json", "ac1"],
            "candidate_resources": {},
            "epoch": 0,
        },
        "snapshot": {
            "scope_id": "compat-scope",
            "receiver_id": "compat-receiver",
            "user_id": message["user_id"],
            "epoch": 0,
            "history_complete": True,
            "historical_events": [],
            "resources": [],
        },
        "dictionary": {"enabled": False, "dictionary": None, "content_hash": None},
        "policy": {
            "repeat_count": 1,
            "repeat_declaration": None,
            "min_savings_tokens": 0,
            "min_savings_ratio": 0.0,
            "fallback_mode": "record_only",
            "max_retries": 0,
            "assumption": "deterministic_clean",
        },
        "envelope": {"prefix": "", "suffix": "", "final_lf": False},
    }


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_legacy_ac1_default_and_ac2_opt_in_are_unchanged():
    message = MessageV1.model_validate(MESSAGE, strict=True)

    assert encode_message(message) == AC1_GOLDEN
    assert decode_message(AC1_GOLDEN).model_dump() == MESSAGE
    assert decode_payload(AC1_GOLDEN).model_dump() == MESSAGE
    assert encode_message_ac2(message) == AC2_GOLDEN
    assert decode_payload(AC2_GOLDEN).model_dump() == MESSAGE
    # The legacy encoder must remain AC1 even when AC2 is available separately.
    assert encode_message(message) == AC1_GOLDEN


def test_legacy_dispatcher_does_not_guess_awp1():
    valid_awp1 = encode_awp1(MessageV1.model_validate(MESSAGE, strict=True))
    assert valid_awp1.startswith("AWP1|")

    for payload in (valid_awp1, "AWP1|[null,[]]"):
        with pytest.raises(FallbackRequired) as error:
            decode_payload(payload)
        assert error.value.status == "fallback_required"


def test_legacy_cli_and_report_versions_remain_stable(tmp_path, monkeypatch):
    command_names = {
        command.name or command.callback.__name__
        for command in cli.app.registered_commands
    }
    assert {
        "benchmark", "benchmark-suite", "evaluate-offline",
        "select-encoding", "benchmark-adaptive",
    } <= command_names

    # Legacy benchmark commands run against a deterministic local text stub and
    # handwritten input, so this guard never initializes or downloads a tokenizer.
    class SyntheticEncoding:
        def encode_ordinary(self, text):
            return list(text)

    monkeypatch.setattr(benchmark, "_get_encoding", lambda _name: SyntheticEncoding())
    encode_result = RUNNER.invoke(cli.app, ["encode"], input=json.dumps(MESSAGE))
    assert encode_result.exit_code == 0
    assert encode_result.stdout == AC1_GOLDEN + "\n"

    benchmark_result = RUNNER.invoke(
        cli.app, ["benchmark"], input=json.dumps(MESSAGE) + "\n"
    )
    assert benchmark_result.exit_code == 0, benchmark_result.output
    assert json.loads(benchmark_result.stdout)["report_version"] == "1"

    dictionary = {"user_id": "compat-user", "version": "compat-v1", "aliases": {}}
    diverse = tmp_path / "synthetic-diverse.jsonl"
    repetitive = tmp_path / "synthetic-repetitive.jsonl"
    dictionary_path = tmp_path / "synthetic-dictionary.json"
    row = json.dumps(MESSAGE, ensure_ascii=False)
    diverse.write_text(row + "\n", encoding="utf-8")
    repetitive.write_text(row + "\n", encoding="utf-8")
    _write_json(dictionary_path, dictionary)
    suite_result = RUNNER.invoke(cli.app, [
        "benchmark-suite",
        "--diverse-input", str(diverse),
        "--repetitive-input", str(repetitive),
        "--repetitive-dictionary", str(dictionary_path),
    ])
    assert suite_result.exit_code == 0, suite_result.output
    assert json.loads(suite_result.stdout)["report_version"] == "suite-v1"

    # The legacy evaluator pins repository input hashes. Replace only its file
    # boundary with 30 auditable synthetic messages per group; no corpus or
    # dictionary file from the repository is opened.
    def synthetic_eval_text(_path, name):
        if name == "repetitive_dictionary":
            return json.dumps(dictionary, ensure_ascii=False)
        rows = [
            json.dumps(MESSAGE | {"message_id": f"{name}-{index:02d}"}, ensure_ascii=False)
            for index in range(30)
        ]
        return "\n".join(rows)

    monkeypatch.setattr(cli, "_offline_text", synthetic_eval_text)
    eval_output = tmp_path / "synthetic-evaluation-output"
    eval_result = RUNNER.invoke(cli.app, [
        "evaluate-offline", "--adapter", "mock-v1",
        "--diverse-input", str(tmp_path / "unread-diverse.jsonl"),
        "--repetitive-input", str(tmp_path / "unread-repetitive.jsonl"),
        "--repetitive-dictionary", str(tmp_path / "unread-dictionary.json"),
        "--output-dir", str(eval_output),
    ])
    assert eval_result.exit_code == 0, eval_result.output
    manifest = json.loads((eval_output / "manifest.json").read_text(encoding="utf-8"))
    report = json.loads((eval_output / "report.json").read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (eval_output / "records.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert manifest["manifest_version"] == "offline-eval-manifest-v1"
    assert manifest["contract_version"] == manifest["scorer_version"] == "offline-eval-v1"
    assert report["report_version"] == "offline-eval-report-v1"
    assert records and all(record["record_version"] == "offline-eval-v1" for record in records)

    request = _adaptive_request(MESSAGE)
    request_path = _write_json(tmp_path / "synthetic-request.json", request)
    select_result = RUNNER.invoke(cli.app, [
        "select-encoding", "--request", str(request_path),
    ])
    assert select_result.exit_code == 0, select_result.output
    assert json.loads(select_result.stdout)["contract_version"] == "adaptive-selection-v1"

    source = tmp_path / "synthetic-source.jsonl"
    source_bytes = (json.dumps(MESSAGE, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    source.write_bytes(source_bytes)
    snapshot = request["snapshot"]
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    config = {
        "code_revision": revision,
        "input_manifest": [{
            "corpus_id": "compat-corpus",
            "source_path": source.name,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "source_count": 1,
            "dictionary_path": None,
            "dictionary_sha256": None,
            "dictionary_source": "none",
        }],
        "scenario_corpora": {"compat-scenario": "compat-corpus"},
        "scenarios": [{
            "scenario_id": "compat-scenario",
            "cases": [{
                "source_message": MESSAGE,
                "request": request,
                "metadata_authorized": False,
            }],
            "whole_corpus_repeats": 1,
            "fixed_decisions": ["ac1"],
            "resource_schedule": [
                {"case_index": 0, "repeat_index": 0, "stage": stage, "snapshot": snapshot}
                for stage in ("setup", "initial")
            ],
            "paths": [{
                "path_id": "compat-path",
                "outcomes": [{
                    "case_index": 0, "repeat_index": 0, "stage": "initial",
                    "success": True, "response_text": "",
                }],
            }],
            "remainder_policy": "continue_declared",
        }],
    }
    config_path = _write_json(tmp_path / "synthetic-adaptive-config.json", config)
    adaptive_output = tmp_path / "synthetic-adaptive-output"
    adaptive_result = RUNNER.invoke(cli.app, [
        "benchmark-adaptive", "--config", str(config_path),
        "--output-dir", str(adaptive_output),
    ])
    assert adaptive_result.exit_code == 0, adaptive_result.output
    adaptive_manifest = json.loads((adaptive_output / "manifest.json").read_text(encoding="utf-8"))
    adaptive_report = json.loads((adaptive_output / "report.json").read_text(encoding="utf-8"))
    assert adaptive_manifest["version"] == "adaptive-benchmark-manifest-v1"
    assert adaptive_report["version"] == "adaptive-benchmark-v1"

"""Offline file/stdin CLI. Messages are untrusted data, never actions."""

import hashlib
import json
import os
import stat
import subprocess
import sys
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from .ac2 import encode_message_ac2
from .benchmark import DEMO_DICTIONARY, BenchmarkScenario, load_tasks, run_benchmark, run_benchmark_suite
from .codec import FallbackRequired, decode_payload, encode_message
from .dictionary import load_dictionary
from .evaluation.contracts import canonical_json
from .evaluation.mock import DeterministicMockAdapter
from .evaluation.runner import build_cases, run_offline_evaluation
from .schema import MessageV1, UserDictionary

app = typer.Typer(help="Offline AgentCodec: encode, decode, validate, and estimate token costs.",
                  add_completion=False, pretty_exceptions_enable=False)
Input = Annotated[str, typer.Option("--input", "-i", help="UTF-8 input file, or - for stdin.")]
Output = Annotated[str, typer.Option("--output", "-o", help="UTF-8 output file, or - for stdout.")]
Dictionary = Annotated[Path | None, typer.Option("--dictionary", help="Manually maintained dictionary JSON.")]
User = Annotated[str | None, typer.Option("--user-id", help="Explicit expected dictionary user namespace.")]


class InputFormat(str, Enum):
    json = "json"
    wire = "wire"


class WireFormat(str, Enum):
    ac1 = "ac1"
    ac2 = "ac2"


@contextmanager
def _errors():
    try:
        yield
    except (OSError, ValueError, TypeError, RuntimeError, RecursionError) as error:
        typer.echo(f"fallback_required: {error}", err=True)
        raise typer.Exit(code=1) from error


def _read(source: str) -> str:
    # Preserve line endings: text I/O's universal-newline translation invents LF.
    data = sys.stdin.buffer.read() if source == "-" else Path(source).read_bytes()
    return data.decode("utf-8")


def _write(text: str, destination: str):
    if destination == "-":
        typer.echo(text)
    else:
        Path(destination).write_text(text + "\n", encoding="utf-8")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FallbackRequired(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _message(text: str) -> MessageV1:
    return MessageV1.model_validate(json.loads(text, object_pairs_hook=_unique_object), strict=True)


def _dictionary(path: Path | None, user_id: str | None):
    if path is None:
        return None
    if user_id is None:
        raise FallbackRequired("--user-id is required when selecting --dictionary")
    return load_dictionary(path, expected_user_id=user_id)


@app.command()
def encode(input: Input = "-", output: Output = "-", dictionary: Dictionary = None,
           user_id: User = None,
           format: Annotated[WireFormat, typer.Option("--format", help="Output wire format; AC2 is opt-in.")] = WireFormat.ac1):
    """Encode one JSON message to AC1 (default) or AC2; never execute content."""
    with _errors():
        selected = _dictionary(dictionary, user_id)
        encoder = encode_message_ac2 if format == WireFormat.ac2 else encode_message
        payload = encoder(_message(_read(input)), selected)
        _write(payload, output)


@app.command()
def decode(input: Input = "-", output: Output = "-", dictionary: Dictionary = None, user_id: User = None):
    """Decode one AC1/AC2 message by exact prefix, or require fallback."""
    with _errors():
        selected = _dictionary(dictionary, user_id)
        # Accept one CLI-generated line ending, not arbitrary trailing whitespace.
        payload = _read(input).removesuffix("\n").removesuffix("\r")
        message = decode_payload(payload, selected)
        _write(message.model_dump_json(), output)


@app.command()
def validate(input: Input = "-", output: Output = "-", dictionary: Dictionary = None,
             user_id: User = None,
             format: Annotated[InputFormat, typer.Option("--format", help="Input representation.")] = InputFormat.json):
    """Validate JSON (default) or AC1/AC2 wire data; not authorization."""
    with _errors():
        selected = _dictionary(dictionary, user_id)
        text = _read(input)
        if format == InputFormat.wire:
            decode_payload(text.removesuffix("\n").removesuffix("\r"), selected)
        else:
            encode_message(_message(text), selected)
        _write('{"status":"valid"}', output)


@app.command()
def benchmark(input: Input = "-", output: Output = "-"):
    """Estimate costs for synthetic JSONL, both local tokenizers, reuse 1/10/50.

    Requires pre-provisioned local vocabulary caches; never downloads them.
    """
    with _errors():
        tasks = ([_message(line) for line in _read(input).split("\n") if line.strip()]
                 if input == "-" else load_tasks(Path(input)))
        report = run_benchmark(tasks, ["o200k_base", "cl100k_base"])
        _write(report.model_dump_json(indent=2), output)


@app.command("benchmark-suite")
def benchmark_suite(
    diverse_input: Annotated[Path, typer.Option("--diverse-input", help="Diverse scenario UTF-8 JSONL file.")],
    repetitive_input: Annotated[Path, typer.Option("--repetitive-input", help="Repetitive scenario UTF-8 JSONL file.")],
    repetitive_dictionary: Annotated[Path, typer.Option("--repetitive-dictionary", help="Strict manual dictionary JSON for repetitive data.")],
    output: Output = "-",
):
    """Compare seven formats in two separate scenarios, offline, reuse 1/10/50.

    Diverse uses the built-in demo dictionary. Repetitive dictionary ownership
    must match the first repetitive message; all messages retain namespace checks.
    Both tokenizer vocabulary caches must already exist locally.
    """
    with _errors():
        diverse_tasks = load_tasks(diverse_input)
        repetitive_tasks = load_tasks(repetitive_input)
        dictionary = load_dictionary(repetitive_dictionary, expected_user_id=repetitive_tasks[0].user_id)
        scenarios = {
            "diverse": BenchmarkScenario(tasks=diverse_tasks, dictionary=DEMO_DICTIONARY),
            "repetitive": BenchmarkScenario(tasks=repetitive_tasks, dictionary=dictionary),
        }
        report = run_benchmark_suite(scenarios, ["o200k_base", "cl100k_base"])
        _write(report.model_dump_json(indent=2), output)


_OFFLINE_HASHES = {
    "diverse": "9284dc0b7a878355c6e8ddc0195cd7adcade9b4966dc3a07aeb58f2f1f2ad7b8",
    "repetitive": "f5c8d2d98837edd5944f10e6fb7a63bd44dfcff6b022aab57502b2197cd239fe",
    "repetitive_dictionary": "9b320df2e125804c07dc53418164633345c4d26ff4e56406e42b3fcd94338d86",
}


def _offline_text(path: Path, name: str) -> str:
    # Hash and parse the same snapshot, never a second read of a changing file.
    data = path.read_bytes()
    text = data.decode("utf-8")
    if hashlib.sha256(data).hexdigest() != _OFFLINE_HASHES[name]:
        raise ValueError(f"{name} input hash does not match the approved corpus")
    return text


def _offline_tasks(text: str) -> list[MessageV1]:
    tasks = [_message(line) for line in text.split("\n") if line.strip()]
    if len(tasks) != 30 or len({task.message_id for task in tasks}) != 30:
        raise ValueError("offline scenario requires exactly 30 unique messages")
    return tasks


def _offline_output_preflight(output_dir: Path):
    if output_dir.exists():
        if not output_dir.is_dir() or any(output_dir.iterdir()):
            raise ValueError("output directory must be absent or empty")
        parent = output_dir
    else:
        parent = output_dir.parent
        while not parent.exists():
            parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        raise ValueError("output directory parent must be a writable directory")


def _offline_revision() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
            check=True, capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()
    except subprocess.SubprocessError as error:
        raise ValueError("cannot read the local git revision") from error


def _offline_write(output_dir: Path, artifacts: dict[str, bytes]):
    # Recheck after computation; exclusive creation never overwrites user files.
    _offline_output_preflight(output_dir)
    created = []
    created_directory = False
    try:
        if not output_dir.exists():
            output_dir.mkdir(parents=True)
            created_directory = True
        for name, data in artifacts.items():
            path = output_dir / name
            stream = path.open("xb")
            created.append(path)
            with stream:
                stream.write(data)
    except OSError:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        if created_directory:
            try:
                output_dir.rmdir()  # Only remove our directory if it is still empty.
            except OSError:
                pass
        raise


@app.command("evaluate-offline")
def evaluate_offline(
    adapter: Annotated[str, typer.Option("--adapter", help="Required local adapter: mock-v1 only.")],
    diverse_input: Annotated[Path, typer.Option("--diverse-input", help="Approved diverse UTF-8 JSONL.")],
    repetitive_input: Annotated[Path, typer.Option("--repetitive-input", help="Approved repetitive UTF-8 JSONL.")],
    repetitive_dictionary: Annotated[Path, typer.Option("--repetitive-dictionary", help="Approved manual dictionary JSON.")],
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Absent or empty local output directory.")],
    formats: Annotated[str, typer.Option("--formats", help="Comma-separated natural_language,json,ac1,ac2; AC2 is opt-in.")] = "ac1",
    dictionary_pairs: Annotated[str, typer.Option("--dictionary-pairs", help="none or paired; selected codecs only.")] = "none",
):
    """Evaluate a deterministic local mock pipeline, not model understanding."""
    if adapter != "mock-v1":
        raise typer.BadParameter("only mock-v1 is supported", param_hint="--adapter")
    selected = tuple(formats.split(","))
    if len(set(selected)) != len(selected) or any(
        name not in ("natural_language", "json", "ac1", "ac2") for name in selected
    ):
        raise typer.BadParameter("select unique known formats", param_hint="--formats")
    if dictionary_pairs not in ("none", "paired"):
        raise typer.BadParameter("select none or paired", param_hint="--dictionary-pairs")
    with _errors():
        diverse = _offline_tasks(_offline_text(diverse_input, "diverse"))
        repetitive = _offline_tasks(_offline_text(repetitive_input, "repetitive"))
        dictionary = UserDictionary.model_validate(json.loads(
            _offline_text(repetitive_dictionary, "repetitive_dictionary"),
            object_pairs_hook=_unique_object,
        ), strict=True)
        if dictionary.user_id != repetitive[0].user_id:
            raise ValueError("dictionary user does not match repetitive scenario")
        _offline_output_preflight(output_dir)
        scenarios = {
            "diverse": BenchmarkScenario(tasks=diverse, dictionary=DEMO_DICTIONARY),
            "repetitive": BenchmarkScenario(tasks=repetitive, dictionary=dictionary),
        }
        revision = _offline_revision()
        manifest, records, report = run_offline_evaluation(
            build_cases(scenarios, selected, dictionary_pairs),
            DeterministicMockAdapter(), dict(_OFFLINE_HASHES), revision,
        )
        # Finish computation and UTF-8 serialization before creating any output.
        artifacts = {
            "manifest.json": (canonical_json(manifest) + "\n").encode("utf-8"),
            "records.jsonl": "".join(canonical_json(record.model_dump()) + "\n"
                                     for record in records).encode("utf-8"),
            "report.json": (canonical_json(report) + "\n").encode("utf-8"),
        }
        _offline_write(output_dir, artifacts)


# Separate opt-in commands: no legacy command, renderer or default is changed.
from .adaptive.cli import AdaptiveCommand


@app.command("select-encoding", cls=AdaptiveCommand)
def select_encoding_cli(
    request: Annotated[Path, typer.Option("--request")],
    summary_output: Annotated[Path | None, typer.Option("--summary-output")] = None,
    payload_output: Annotated[Path | None, typer.Option("--payload-output")] = None,
    expected_future_reuses: Annotated[str | None, typer.Option("--expected-future-reuses")] = None,
):
    """Select one encoding locally; summaries are redacted, payload files opt-in."""
    from .adaptive.cli import parse_future_reuses, select_encoding_command
    select_encoding_command(request, summary_output, payload_output, parse_future_reuses(expected_future_reuses))


@app.command("benchmark-adaptive", cls=AdaptiveCommand)
def benchmark_adaptive_cli(
    config: Annotated[Path, typer.Option("--config")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
):
    """Write independent local diagnostics, not model or provider evidence."""
    from .adaptive.cli import benchmark_adaptive_command
    benchmark_adaptive_command(config, output_dir)


_AGENTWIRE_FIXTURE_MANIFEST_ID = "agentwire-synthetic-fixtures-v1"
_AGENTWIRE_TOKENIZERS = ("o200k_base", "cl100k_base")
_AGENTWIRE_MODES = ("gateway", "model_direct")
_AGENTWIRE_CANDIDATES = (
    "natural_language", "natural_language_freeform", "json", "ac1",
    "ac1_dictionary", "ac2", "ac2_dictionary", "awp1", "awp1_dictionary",
)
_AGENTWIRE_REUSE_COUNTS = (1, 10, 50)
_AGENTWIRE_PUBLIC_ROW_FIELDS = (
    "synthetic_corpus_ordinal", "tokenizer_profile_id", "receiver_mode", "candidate_id",
    "metric_surface", "metric_status", "dictionary_condition",
    "dictionary_candidate_exposure", "reuse_unit", "total_uses", "case_count",
    "event_count", "transport_bytes", "transport_tokens", "model_input_tokens",
    "synthetic_response_tokens", "distinct_text_event_tokens",
    "candidate_protocol_exposure_tokens", "candidate_protocol_setup_history_tokens",
    "candidate_protocol_setup_tokens", "negotiation_tokens", "dictionary_exposure_tokens",
    "dictionary_history_tokens", "dictionary_setup_tokens", "request_tokens",
    "response_tokens", "retry_tokens", "fallback_tokens", "per_use_cost_tokens",
    "amortized_cost_tokens", "lifecycle_cost_tokens", "strict_decode_status",
    "strict_decode_success_count", "full_field_equal_count", "hard_field_equal_count",
)
_AGENTWIRE_PUBLIC_COMPARISON_FIELDS = (
    "status", "cost_delta_tokens", "break_even_status", "first_strict_win",
)
_AGENTWIRE_PUBLIC_NOTES = (
    "Tokenizer counts are local text estimates, not provider billing.",
    "No model or provider was contacted; response text is synthetic fixture data.",
    "Decoder equality is structural only and does not measure model understanding or task quality.",
)


def _agentwire_csv(value: str, allowed: tuple[str, ...], option: str) -> tuple[str, ...]:
    parts = value.split(",")
    if (not value or any(part not in allowed for part in parts)
            or len(parts) != len(set(parts))):
        raise typer.BadParameter("select unique supported values", param_hint=option)
    return tuple(parts)


def _agentwire_reuse_counts(value: str, option: str) -> tuple[int, ...]:
    parts = value.split(",")
    allowed = tuple(str(count) for count in _AGENTWIRE_REUSE_COUNTS)
    if (not value or any(part not in allowed for part in parts)
            or len(parts) != len(set(parts))):
        raise typer.BadParameter("select unique counts from 1, 10, 50", param_hint=option)
    return tuple(int(part) for part in parts)


def _agentwire_output_preflight(output_dir: Path) -> Path:
    """Accept only an absent or empty real directory outside fixed inputs."""
    output_dir = Path(os.path.abspath(os.fspath(output_dir)))
    if any(path.is_symlink() for path in (output_dir, *output_dir.parents)):
        raise ValueError("invalid output directory")
    fixture_dir = Path(__file__).resolve().parents[2] / "bench" / "agentwire"
    resolved = output_dir.resolve(strict=False)
    fixture_dir = fixture_dir.resolve()
    if resolved == fixture_dir or resolved in fixture_dir.parents or fixture_dir in resolved.parents:
        raise ValueError("output directory collides with fixed fixtures")
    try:
        info = output_dir.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if not stat.S_ISDIR(info.st_mode) or any(output_dir.iterdir()):
            raise ValueError("output directory must be new or empty")
        writable_parent = output_dir
    else:
        writable_parent = output_dir.parent
        if not writable_parent.is_dir():
            raise ValueError("output directory parent must exist")
    if not os.access(writable_parent, os.W_OK | os.X_OK):
        raise OSError("output directory is not writable")
    return output_dir


def _agentwire_write(output_dir: Path, contents: bytes) -> None:
    output_dir = _agentwire_output_preflight(output_dir)
    created_directory = False
    directory_identity = None
    report_path = output_dir / "report.json"
    file_identity = None
    try:
        if not output_dir.exists():
            output_dir.mkdir()
            directory_stat = output_dir.lstat()
            directory_identity = (directory_stat.st_dev, directory_stat.st_ino)
            created_directory = True
        _agentwire_output_preflight(output_dir)
        with report_path.open("xb") as stream:
            file_stat = os.fstat(stream.fileno())
            file_identity = (file_stat.st_dev, file_stat.st_ino)
            if stream.write(contents) != len(contents):
                raise OSError("incomplete report write")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if file_identity is not None:
            try:
                current = report_path.lstat()
                if (current.st_dev, current.st_ino) == file_identity:
                    report_path.unlink()
            except OSError:
                pass
        if created_directory:
            try:
                current = output_dir.lstat()
                if ((current.st_dev, current.st_ino) == directory_identity
                        and not any(output_dir.iterdir())):
                    output_dir.rmdir()
            except OSError:
                pass
        raise


def _agentwire_fixed_inputs():
    from .agentwire.benchmark import BenchmarkCase
    from .schema import MessageV1, UserDictionary

    fixture_dir = Path(__file__).resolve().parents[2] / "bench" / "agentwire"
    exchanges_path = fixture_dir / "exchanges.v1.jsonl"
    dictionary_path = fixture_dir / "dictionary.v1.json"
    exchanges_text = exchanges_path.read_bytes().decode("utf-8")
    dictionary_text = dictionary_path.read_bytes().decode("utf-8")
    cases = []
    for line in exchanges_text.split("\n"):
        if not line.strip():
            continue
        record = json.loads(line, object_pairs_hook=_unique_object)
        cases.append(BenchmarkCase(
            case_id=record["case_id"],
            task_prompt=record["task_prompt"],
            source_message=MessageV1.model_validate(record["source_message"], strict=True),
            freeform_text=record["freeform_text"],
            freeform_annotations={key: tuple(items)
                                  for key, items in record["freeform_annotations"].items()},
            synthetic_response_fixture=record["synthetic_response_fixture"],
        ))
    if not cases:
        raise ValueError("fixed benchmark fixtures rejected")
    dictionary = UserDictionary.model_validate(
        json.loads(dictionary_text, object_pairs_hook=_unique_object), strict=True,
    )
    user_ids = {case.source_message.user_id for case in cases}
    if len(user_ids) != 1 or dictionary.user_id not in user_ids:
        raise ValueError("fixed benchmark fixtures rejected")
    return tuple(cases), dictionary


def _agentwire_profiles(tokenizer_names: tuple[str, ...]):
    from importlib.metadata import PackageNotFoundError, version

    from .adaptive.contracts import TokenizerProfile
    from .benchmark import _VOCAB_HASHES

    try:
        library_version = version("tiktoken")
    except PackageNotFoundError:
        # The meter will report cannot_compare; profile construction must not fetch anything.
        library_version = "unavailable"
    return {
        name: TokenizerProfile(
            encoding_name=name,
            library_version=library_version,
            vocabulary_sha256=_VOCAB_HASHES[name],
            accuracy="exact_local_text",
            mapping_basis="verified fixed local tokenizer vocabulary only",
        )
        for name in tokenizer_names
    }


def _agentwire_scenarios(
    cases, dictionary, tokenizer_names: tuple[str, ...],
    instance_uses: tuple[int, ...], corpus_passes: tuple[int, ...],
):
    from .agentwire.benchmark import (
        BenchmarkScenario, DictionaryExposure, ProtocolState, ReuseScenario,
    )
    from .agentwire.contracts import ReceiverCapabilities, dictionary_identity_for

    dictionary_identity = dictionary_identity_for(dictionary)
    user_id = cases[0].source_message.user_id
    scenarios = []
    for domain in ("source", "selected_dictionary_transport"):
        for unit in ("same_message_instance", "whole_corpus_pass"):
            groups = (
                tuple((index, (case,)) for index, case in enumerate(cases, start=1))
                if unit == "same_message_instance" else ((0, cases),)
            )
            for ordinal, group in groups:
                receiver = ReceiverCapabilities(
                    user_id=user_id,
                    supported_profile_versions=("AWP1",),
                    supported_schema_versions=("MessageV1",),
                    supported_enum_table_versions=("AWP1",),
                    max_message_bytes=2_000_000,
                    dictionary_identity=(dictionary_identity
                                         if domain == "selected_dictionary_transport" else None),
                )
                states = tuple(
                    ProtocolState(
                        candidate_id=candidate,
                        profile_id=profile_id,
                        state="out_of_band_preinstalled",
                    )
                    for profile_id in tokenizer_names
                    for candidate in _AGENTWIRE_CANDIDATES
                )
                scenario = BenchmarkScenario(
                    scenario_id=f"agentwire-{domain}-{unit}-{ordinal:03d}",
                    scope_id="agentwire-cli-scope-v1",
                    corpus_id="agentwire-exchanges-v1",
                    metadata_domain=domain,
                    receiver_capabilities=receiver,
                    protocol_states=states,
                    dictionary_candidate_exposure=DictionaryExposure(
                        state=("out_of_band_preinstalled"
                               if domain == "selected_dictionary_transport" else "not_applicable"),
                    ),
                    dictionary_history=(),
                    cases=group,
                    reuse=ReuseScenario(unit=unit, total_uses=_AGENTWIRE_REUSE_COUNTS),
                )
                selected_uses = instance_uses if unit == "same_message_instance" else corpus_passes
                if selected_uses != _AGENTWIRE_REUSE_COUNTS:
                    # CLI values were strictly allowlisted above. Copy the already-validated
                    # scenario so the runner iterates only the requested execution counts.
                    selected_reuse = scenario.reuse.model_copy(update={"total_uses": selected_uses})
                    scenario = scenario.model_copy(update={"reuse": selected_reuse})
                scenarios.append(scenario)
    return tuple(scenarios)


def _agentwire_public_comparison(comparison):
    return {field: comparison[field] for field in _AGENTWIRE_PUBLIC_COMPARISON_FIELDS}


def _agentwire_public_row(row):
    public = {field: row[field] for field in _AGENTWIRE_PUBLIC_ROW_FIELDS}
    public["vs_natural_language_labelled"] = _agentwire_public_comparison(
        row["vs_natural_language_labelled"],
    )
    public["vs_natural_language_freeform"] = _agentwire_public_comparison(
        row["vs_natural_language_freeform"],
    )
    return public


def _agentwire_redacted_report(report, tokenizer_names, modes):
    data = report.model_dump(mode="json")
    if data.get("version") != "agentwire-benchmark-v1":
        raise ValueError("unsupported benchmark report")
    filtered_scenarios = []
    for scenario in data["scenarios"]:
        if scenario["metadata_domain"] not in ("source", "selected_dictionary_transport"):
            raise ValueError("unsupported benchmark metadata domain")
        if scenario["reuse_unit"] not in ("same_message_instance", "whole_corpus_pass"):
            raise ValueError("unsupported benchmark reuse unit")
        rows = []
        for row in scenario["rows"]:
            if row["tokenizer_profile_id"] not in tokenizer_names or row["receiver_mode"] not in modes:
                continue
            rows.append(_agentwire_public_row(row))
        filtered_scenarios.append({
            "metadata_domain": scenario["metadata_domain"],
            "reuse_unit": scenario["reuse_unit"],
            "rows": rows,
        })
    return {
        "version": "agentwire-benchmark-v1",
        "fixture_manifest_id": _AGENTWIRE_FIXTURE_MANIFEST_ID,
        "scenarios": filtered_scenarios,
        "notes": list(_AGENTWIRE_PUBLIC_NOTES),
    }


@app.command("benchmark-agentwire")
def benchmark_agentwire_cli(
    output_dir: Annotated[Path, typer.Option("--output-dir", help="New or empty local report directory.")],
    tokenizers: Annotated[str, typer.Option("--tokenizers", help="Comma-separated o200k_base,cl100k_base.")] = "o200k_base,cl100k_base",
    instance_uses: Annotated[str, typer.Option("--instance-uses", help="Reuse counts for one message instance: 1,10,50.")] = "1,10,50",
    corpus_passes: Annotated[str, typer.Option("--corpus-passes", help="Reuse counts for a whole corpus pass: 1,10,50.")] = "1,10,50",
    modes: Annotated[str, typer.Option("--modes", help="Comma-separated gateway,model_direct.")] = "gateway,model_direct",
):
    """Run the fixed synthetic AgentWire benchmark locally; never contacts a provider."""
    tokenizer_names = _agentwire_csv(tokenizers, _AGENTWIRE_TOKENIZERS, "--tokenizers")
    selected_modes = _agentwire_csv(modes, _AGENTWIRE_MODES, "--modes")
    instance_counts = _agentwire_reuse_counts(instance_uses, "--instance-uses")
    corpus_counts = _agentwire_reuse_counts(corpus_passes, "--corpus-passes")
    try:
        _agentwire_output_preflight(output_dir)
        cases, dictionary = _agentwire_fixed_inputs()
        profiles = _agentwire_profiles(tokenizer_names)
        scenarios = _agentwire_scenarios(
            cases, dictionary, tokenizer_names, instance_counts, corpus_counts,
        )
        from .agentwire.benchmark import run_agentwire_benchmark

        report = run_agentwire_benchmark(
            scenarios, profiles, dictionary=dictionary, modes=selected_modes,
        )
        public = _agentwire_redacted_report(
            report, tokenizer_names, selected_modes,
        )
        contents = (json.dumps(public, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        _agentwire_write(output_dir, contents)
    except OSError:
        typer.echo("agentwire_benchmark_io_error", err=True)
        raise typer.Exit(code=1) from None
    except (FallbackRequired, ValueError, TypeError, KeyError, RuntimeError, ImportError, RecursionError):
        typer.echo("agentwire_benchmark_failed", err=True)
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    app()

"""Offline file/stdin CLI. Messages are untrusted data, never actions."""

import hashlib
import json
import os
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


if __name__ == "__main__":
    app()

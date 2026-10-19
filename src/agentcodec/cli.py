"""Offline file/stdin CLI. Messages are untrusted data, never actions."""

import json
import sys
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from .benchmark import load_tasks, run_benchmark
from .codec import FallbackRequired, decode_message, encode_message
from .dictionary import load_dictionary
from .schema import MessageV1

app = typer.Typer(help="Offline AgentCodec: encode, decode, validate, and estimate token costs.",
                  add_completion=False, pretty_exceptions_enable=False)
Input = Annotated[str, typer.Option("--input", "-i", help="UTF-8 input file, or - for stdin.")]
Output = Annotated[str, typer.Option("--output", "-o", help="UTF-8 output file, or - for stdout.")]
Dictionary = Annotated[Path | None, typer.Option("--dictionary", help="Manually maintained dictionary JSON.")]
User = Annotated[str | None, typer.Option("--user-id", help="Explicit expected dictionary user namespace.")]


class InputFormat(str, Enum):
    json = "json"
    wire = "wire"


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
def encode(input: Input = "-", output: Output = "-", dictionary: Dictionary = None, user_id: User = None):
    """Encode one canonical JSON message to AC1; never execute its content."""
    with _errors():
        selected = _dictionary(dictionary, user_id)
        payload = encode_message(_message(_read(input)), selected)
        _write(payload, output)


@app.command()
def decode(input: Input = "-", output: Output = "-", dictionary: Dictionary = None, user_id: User = None):
    """Decode one AC1 message to canonical JSON, or require fallback."""
    with _errors():
        selected = _dictionary(dictionary, user_id)
        # Accept one CLI-generated line ending, not arbitrary trailing whitespace.
        payload = _read(input).removesuffix("\n").removesuffix("\r")
        message = decode_message(payload, selected)
        _write(message.model_dump_json(), output)


@app.command()
def validate(input: Input = "-", output: Output = "-", dictionary: Dictionary = None,
             user_id: User = None,
             format: Annotated[InputFormat, typer.Option("--format", help="Input representation.")] = InputFormat.json):
    """Validate JSON (default) or AC1 wire data; validity is not authorization."""
    with _errors():
        selected = _dictionary(dictionary, user_id)
        text = _read(input)
        if format == InputFormat.wire:
            decode_message(text.removesuffix("\n").removesuffix("\r"), selected)
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


if __name__ == "__main__":
    app()

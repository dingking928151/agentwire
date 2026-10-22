"""Explicit local adaptive commands; no wire output, execution or network defaults.

Public data is built by allowlisted projection, never by dumping SDK objects.
Output publication is exclusive and rollback is inode-guarded. This is not a
cross-file power-loss transaction: all bytes are staged before any publication.
"""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from types import UnionType
from typing import Annotated, Union, get_args, get_origin

from pydantic import BaseModel
import typer
from typer.core import TyperCommand
try:  # New Typer vendors Click; older supported Typer uses installed Click.
    from typer._click.exceptions import UsageError
except ImportError:
    from click import UsageError

from agentcodec.codec import FallbackRequired
from .contracts import (
    CANDIDATE_IDS, COMPONENT_KINDS, MAX_REPEAT_COUNT, MAX_TEXT_BYTES,
    SelectionDecision, SelectionRequest, canonical_text, instance_id,
)


class AdaptiveCommand(TyperCommand):
    """Keep Click/Typer conversion errors from echoing caller-supplied values."""

    def parse_args(self, ctx, args):
        try:
            return super().parse_args(ctx, args)
        except UsageError:
            raise UsageError("adaptive_usage_error", ctx=ctx) from None

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except UsageError:
            raise UsageError("adaptive_usage_error", ctx=ctx) from None


_CODES = frozenset({
    "adaptive_invalid_contract", "adaptive_invalid_selection", "adaptive_candidate_integrity",
    "adaptive_invalid_resource", "adaptive_stale_epoch", "adaptive_invalid_cost", "adaptive_invalid_event",
    "adaptive_invalid_input", "adaptive_io_error", "no_eligible_candidate", "candidate_not_allowed",
    "candidate_not_supported", "dictionary_not_enabled", "dictionary_unavailable", "dictionary_version_null",
    "dictionary_required", "resource_invalid", "tokenizer_unavailable", "profile_unreliable", "surrogate_only",
    "required_cost_unknown", "resource_validity_unknown", "policy_retained", "history_incomplete",
    "deterministic_clean", "no_model_response", "no_network_ack", "record_only", "no_send", "no_retry",
    "no_automatic_fallback", "local_ordinary_text_only", "initialization_proposed_not_ready",
    "stationary_costs", "resources_remain_valid", "snapshot_repeat_coverage_bound", "no_win_within_validity",
    "nonstationary_costs", "local_cpu", "output_direction",
})


def _codes(values):
    return list(dict.fromkeys(value if value in _CODES else "unrecognized_code" for value in values))


def redacted_decision(decision: SelectionDecision) -> dict[str, object]:
    """Project only fixed enums, numbers and fixed public codes at EVERY depth."""
    return dict(contract_version="adaptive-selection-v1", status=decision.status,
        comparison_status=decision.comparison_status, reason_codes=_codes(decision.reason_codes),
        selected_candidate=decision.selected_candidate, minimum_candidate=decision.minimum_candidate,
        baseline_candidate=decision.baseline_candidate, policy_retained=decision.policy_retained,
        savings_tokens=decision.savings_tokens, savings_ratio=decision.savings_ratio,
        opportunity_cost_tokens=decision.opportunity_cost_tokens,
        opportunity_cost_upper_bound=decision.opportunity_cost_upper_bound,
        candidates=[dict(candidate_id=c.candidate_id, eligibility=c.eligibility,
            reason_codes=_codes(c.reason_codes)) for c in decision.candidates],
        costs=[dict(candidate_id=c.candidate_id, remaining_expected_tokens=c.remaining_expected_tokens,
            scope_spent_tokens=c.scope_spent_tokens, scope_lifecycle_expected_tokens=c.scope_lifecycle_expected_tokens,
            new_resource_count=c.new_resource_count, reason_codes=_codes(c.reason_codes), excluded=_codes(c.excluded),
            component_totals=None if c.component_totals is None else
                {k: c.component_totals[k] for k in COMPONENT_KINDS},
            attempts=[dict(attempt_kind=a.attempt_kind, total_tokens=a.total_tokens,
                component_tokens=list(a.component_tokens), simulated=a.simulated, multiplicity=a.multiplicity)
                for a in c.attempts]) for c in decision.costs],
        breakeven={k: dict(kind=b.kind, first_strict_win=b.first_strict_win,
            winning_interval=b.winning_interval, assumptions=_codes(b.assumptions), reason_codes=_codes(b.reason_codes))
            for k,b in decision.breakeven.items() if k in CANDIDATE_IDS},
        proposed_resource_count=len(decision.proposed_resources), snapshot_epoch=decision.snapshot_epoch,
        policy_version="adaptive-policy-v1", renderer_version="adaptive-text-v1",
        assumptions=_codes(decision.assumptions), excluded=_codes(decision.excluded))


class _InvalidInput(ValueError):
    pass


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidInput()
        result[key] = value
    return result


def _constant(value):
    raise _InvalidInput()


def _json(data):
    value = json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=_unique, parse_constant=_constant)
    canonical_text(value)  # Reject escaped lone surrogates and nonfinite numbers.
    return value


def _read(path):
    # Bound memory even when stat sizes lie. Hashing and parsing use these bytes.
    if not stat.S_ISREG(path.stat().st_mode):
        raise _InvalidInput()
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise _InvalidInput()
        data = stream.read(MAX_TEXT_BYTES + 1)
    if len(data) > MAX_TEXT_BYTES:
        raise _InvalidInput()
    return data


def _boundary(value, annotation):
    """Convert JSON arrays ONLY at declared tuple fields; retain strict SDK types."""
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Annotated:
        return _boundary(value, args[0])
    if origin in (Union, UnionType):
        if value is None:
            return None
        non_null = [a for a in args if a is not type(None)]
        return _boundary(value, non_null[0]) if len(non_null) == 1 else value
    if isinstance(annotation, type) and issubclass(annotation, BaseModel) and isinstance(value, dict):
        return {k: _boundary(v, annotation.model_fields[k].annotation) if k in annotation.model_fields else v
                for k,v in value.items()}
    if origin is tuple and isinstance(value, list):
        return tuple(_boundary(v, args[0] if len(args) == 2 and args[1] is Ellipsis else args[i])
                     for i,v in enumerate(value))
    if origin is list and isinstance(value, list):
        return [_boundary(v, args[0]) for v in value]
    if origin is dict and isinstance(value, dict):
        return {k: _boundary(v, args[1]) for k,v in value.items()}
    return value


def _local(path, base=None):
    text = str(path)
    if text == "-" or text.startswith(("//", "\\\\")) or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text):
        raise _InvalidInput()
    result = Path(path)
    if base is not None and not result.is_absolute():
        result = base / result
    # Inspect the actual leaf before resolving: even a dangling link is occupied.
    if result.is_symlink():
        raise _InvalidInput()
    return result.resolve()


def _outputs(paths, protected):
    if len(paths) != len(set(paths)) or set(paths) & set(protected):
        raise _InvalidInput()
    for path in paths:
        if path.exists() or path.is_symlink() or not path.parent.is_dir():
            raise _InvalidInput()
        if not os.access(path.parent, os.W_OK | os.X_OK):
            raise OSError()


def _directory(path, protected):
    if path in protected or any(path in p.parents for p in protected):
        raise _InvalidInput()
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise _InvalidInput()
    elif not path.parent.is_dir():
        raise _InvalidInput()
    parent = path if path.exists() else path.parent
    if not os.access(parent, os.W_OK | os.X_OK):
        raise OSError()


def _identity(path):
    info = path.lstat()
    return info.st_dev, info.st_ino


def _unlink_owned(path, identity):
    try:
        if _identity(path) == identity:
            path.unlink()
    except OSError:
        pass


def _publish(artifacts):
    """Stage complete UTF-8 bytes, then link exclusively; never replace a target."""
    staged, published = [], []
    try:
        for destination, data in artifacts.items():
            fd, name = tempfile.mkstemp(prefix=".adaptive-", dir=destination.parent)
            temporary = Path(name)
            identity = _identity(temporary)
            staged.append((temporary, identity, destination))
            try:
                stream = os.fdopen(fd, "wb")
            except BaseException:
                os.close(fd)
                raise
            with stream:
                if stream.write(data) != len(data):
                    raise OSError()
                stream.flush()
                os.fsync(stream.fileno())
        for temporary, identity, destination in staged:
            os.link(temporary, destination)  # Atomic, no overwrite, same filesystem.
            published.append((destination, identity))
    except BaseException:
        for destination, identity in reversed(published):
            _unlink_owned(destination, identity)
        raise
    finally:
        for temporary, identity, _ in staged:
            _unlink_owned(temporary, identity)


def _bytes(value):
    return (canonical_text(value) + "\n").encode("utf-8")


def _error(code, exit_code=1):
    typer.echo(code, err=True)
    raise typer.Exit(exit_code)


def parse_future_reuses(value: str | None) -> int | None:
    """Keep framework conversion errors and arbitrary option values private."""
    if value is None:
        return None
    if not value.isascii() or not value.isdecimal() or len(value) > 7:
        _error("adaptive_usage_error", 2)
    return int(value)


def _failure(code):
    from .selector import select_encoding
    decision = redacted_decision(select_encoding(None))
    decision["reason_codes"] = [code]
    return decision


def select_encoding_command(request: Path, summary_output: Path | None = None,
        payload_output: Path | None = None, expected_future_reuses: int | None = None) -> None:
    """Return redacted local advice; payload is written only to an explicit new file."""
    from .selector import select_encoding
    try:
        source = _local(request)
        summary = None if summary_output is None else _local(summary_output, source.parent)
        payload = None if payload_output is None else _local(payload_output, source.parent)
        _outputs([p for p in (summary, payload) if p is not None], (source,))
        data = _json(_read(source))
        if expected_future_reuses is not None:
            if (type(expected_future_reuses) is not int or not 0 <= expected_future_reuses < MAX_REPEAT_COUNT
                    or not isinstance(data, dict) or not isinstance(data.get("policy", {}), dict)
                    or {"repeat_count", "repeat_declaration"} & data.get("policy", {}).keys()):
                _error("adaptive_usage_error", 2)
            policy = dict(data.get("policy", {}))
            from agentcodec.schema import MessageV1
            message = MessageV1.model_validate(data.get("message"), strict=True)
            policy.update(repeat_count=expected_future_reuses + 1,
                repeat_declaration=dict(unit="same_message_instance", total_count=expected_future_reuses + 1,
                    instance_sha256=instance_id(message)))
            data = data | {"policy": policy}
        req = SelectionRequest.model_validate(_boundary(data, SelectionRequest), strict=True)
        decision = select_encoding(req)
        public = redacted_decision(decision)
    except OSError:
        _error("adaptive_io_error")
    except (FallbackRequired, ValueError, TypeError, RecursionError):
        # The parser, not legacy _errors(), owns this boundary. No exception text.
        typer.echo(canonical_text(_failure("adaptive_invalid_input")))
        _error("adaptive_invalid_input")
    artifacts = {}
    if summary is not None:
        artifacts[summary] = _bytes(public)
    if payload is not None and decision.status != "fallback_required":
        artifacts[payload] = (decision.payload + "\n").encode("utf-8")
    try:
        _outputs(list(artifacts), (source,))
        _publish(artifacts)
    except (OSError, ValueError):
        _error("adaptive_io_error")
    if summary is None:
        typer.echo(canonical_text(public))
    if decision.status == "fallback_required":
        raise typer.Exit(1)


def _revision():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[3],
            check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()
    except subprocess.SubprocessError:
        raise _InvalidInput() from None


def _benchmark_snapshots(config, base):
    """Read each ORIGINAL file once; keep original identities for the manifest.

    Task 8 rechecks inputs before/after replay. Feed it private frozen files so
    both checks see exactly the hash/parse snapshot, not a later original file.
    The private paths never escape in the controlled manifest.
    """
    from agentcodec.schema import MessageV1, UserDictionary
    import hashlib
    frozen, original, protected = {}, [], []
    for item in config.input_manifest:
        source = _local(item.source_path, base)
        dictionary = None if item.dictionary_path is None else _local(item.dictionary_path, base)
        for path, digest, kind in ((source, item.source_sha256, "source"),
                (dictionary, item.dictionary_sha256, "dictionary")):
            if path is None:
                continue
            protected.append(path)
            if path not in frozen:
                frozen[path] = _read(path)
            raw = frozen[path]
            if hashlib.sha256(raw).hexdigest() != digest:
                raise _InvalidInput()
            if kind == "source":
                messages = tuple(MessageV1.model_validate(_json(line.encode("utf-8")), strict=True)
                    for line in raw.decode("utf-8", errors="strict").split("\n") if line.strip())
                if len(messages) != item.source_count or len({m.message_id for m in messages}) != len(messages):
                    raise _InvalidInput()
                for scenario in config.scenarios:
                    if config.scenario_corpora[scenario.scenario_id] == item.corpus_id:
                        if tuple(c.source_message for c in scenario.cases) != messages:
                            raise _InvalidInput()
            else:
                UserDictionary.model_validate(_json(raw), strict=True)
        original.append(item.model_copy(update=dict(source_path=str(source),
            dictionary_path=None if dictionary is None else str(dictionary))))
    return frozen, tuple(original), protected


def benchmark_adaptive_command(config: Path, output_dir: Path) -> None:
    """Write three complete local artifacts; exit 0 does not imply acceptance."""
    from .benchmark import AdaptiveBenchmarkConfig, run_adaptive_benchmark
    created_directory = None
    try:
        source = _local(config)
        out = _local(output_dir, source.parent)
        _directory(out, (source,))
        cfg = AdaptiveBenchmarkConfig.model_validate(_boundary(_json(_read(source)), AdaptiveBenchmarkConfig), strict=True)
        if cfg.code_revision != _revision():
            raise _InvalidInput()
        frozen, originals, protected = _benchmark_snapshots(cfg, source.parent)
        _directory(out, (source, *protected))
        # TemporaryDirectory is mode 0700. Never expose source/dictionary text.
        with tempfile.TemporaryDirectory(prefix=".adaptive-", dir=out.parent) as temporary:
            snapshots = {}
            for index, (path, raw) in enumerate(frozen.items()):
                target = Path(temporary) / str(index)
                with target.open("xb") as stream:
                    stream.write(raw)
                snapshots[str(path)] = str(target)
            snapshot_manifest = tuple(i.model_copy(update=dict(source_path=snapshots[i.source_path],
                dictionary_path=None if i.dictionary_path is None else snapshots[i.dictionary_path])) for i in originals)
            artifacts = run_adaptive_benchmark(cfg.model_copy(update={"input_manifest": snapshot_manifest}))
        manifest = artifacts.manifest | {"input_manifest": [i.model_dump() for i in originals]}
        outputs = {out / "manifest.json": _bytes(manifest),
            out / "records.jsonl": b"".join(_bytes(r) for r in artifacts.records),
            out / "report.json": _bytes(artifacts.report)}
        _directory(out, (source, *protected))
        if not out.exists():
            out.mkdir()
            created_directory = _identity(out)
        _outputs(list(outputs), (source, *protected))
        _publish(outputs)
    except OSError:
        if created_directory is not None:
            try:
                if _identity(out) == created_directory:
                    out.rmdir()  # Never remove nonempty or unowned directories.
            except OSError:
                pass
        _error("adaptive_io_error")
    except (FallbackRequired, ValueError, TypeError, KeyError, RecursionError):
        _error("adaptive_invalid_input")
    typer.echo(canonical_text(dict(status="artifacts_written", acceptance_passed=artifacts.report["acceptance_passed"])))

"""Exact local ordinary-text requests, not provider usage or resource pricing.

The legacy vocabulary allowlist/cache layout is retained, but its lru-cached
loader is deliberately not trusted. Each adapter builds from verified, frozen
bytes: a cache replacement can never make tiktoken's lazy loader download.
Only the two fixed built-in recipes below are supported; no plugin discovery,
model mapping, character heuristic, or caller-provided executable is used.
"""

import base64
from contextlib import contextmanager
import hashlib
from importlib.metadata import version
import os
from pathlib import Path
import tempfile

from agentcodec.benchmark import _VOCAB_HASHES
from agentcodec.codec import FallbackRequired

from .contracts import (
    MAX_HISTORY_EVENTS, MeasuredAttempt, TextAttempt, TokenizerProfile,
)


class MeasurementUnavailable(Exception):
    """A stable reason code only, without text, cache paths, or loader errors."""

    def __init__(self, reason_code: str):
        if reason_code not in {"tokenizer_unavailable", "profile_unreliable", "surrogate_only"}:
            reason_code = "tokenizer_unavailable"
        self.reason_code = reason_code
        super().__init__(reason_code)


class _EventRejected(FallbackRequired):
    code = "adaptive_invalid_event"

    def __init__(self):
        super().__init__("adaptive text event rejected")


# Built-in tiktoken ordinary-text recipes. Keeping the recipes local avoids
# registry/plugin execution and the download-capable load_tiktoken_bpe path.
_CL100K_PATTERN = (
    r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+|"
    r" ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"
)
_O200K_PATTERN = "|".join((
    r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
    r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
    r"\p{N}{1,3}", r" ?[^\s\p{L}\p{N}]+[\r\n/]*", r"\s*[\r\n]+", r"\s+(?!\S)", r"\s+",
))


def _build_encoding(name: str, vocabulary: bytes):
    """Initialize exclusively from the already hash-verified byte snapshot."""
    import tiktoken

    ranks = {}
    for line in vocabulary.splitlines():
        if line:
            token, rank = line.split()
            ranks[base64.b64decode(token, validate=True)] = int(rank)
    if name == "cl100k_base":
        pattern = _CL100K_PATTERN
        special = {"<|endoftext|>": 100257, "<|fim_prefix|>": 100258,
                   "<|fim_middle|>": 100259, "<|fim_suffix|>": 100260,
                   "<|endofprompt|>": 100276}
    else:
        pattern = _O200K_PATTERN
        special = {"<|endoftext|>": 199999, "<|endofprompt|>": 200018}
    return tiktoken.Encoding(name=name, pat_str=pattern, mergeable_ranks=ranks,
                             special_tokens=special)


def _library_identity() -> tuple[str, str]:
    import tiktoken

    return version("tiktoken"), tiktoken.__version__


def _read_vocabulary(name: str) -> tuple[Path, os.stat_result, bytes]:
    directory = os.environ.get("TIKTOKEN_CACHE_DIR", os.environ.get(
        "DATA_GYM_CACHE_DIR", str(Path(tempfile.gettempdir()) / "data-gym-cache")))
    if not directory:
        raise MeasurementUnavailable("tokenizer_unavailable")
    url = f"https://openaipublic.blob.core.windows.net/encodings/{name}.tiktoken"
    path = Path(directory) / hashlib.sha1(url.encode()).hexdigest()
    before = path.stat()
    vocabulary = path.read_bytes()
    after = path.stat()
    if (_file_identity(before) != _file_identity(after)
            or hashlib.sha256(vocabulary).hexdigest() != _VOCAB_HASHES[name]):
        raise MeasurementUnavailable("tokenizer_unavailable")
    return path, after, vocabulary


def _file_identity(stat: os.stat_result) -> tuple[int, ...]:
    # Nanosecond timestamps and inode catch same-content replacements as well.
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _encoding_identity(encoding) -> tuple:
    # Copy mutable Python metadata and pin the compiled encoder as well.
    return (encoding.name, encoding._pat_str, dict(encoding._special_tokens),
            dict(encoding._mergeable_ranks), id(encoding._core_bpe))


@contextmanager
def _get_encoding(profile: TokenizerProfile):
    """Freeze and recheck profile, library, vocabulary, and constructed encoding.

    There is intentionally no adapter cache, nor any call to get_encoding or
    encoding_for_model. Even initialization reads only frozen verified bytes.
    The post-check runs before any measured result can leave this context.
    """
    profile = TokenizerProfile.model_validate(profile)
    if profile.accuracy == "surrogate_only":
        raise MeasurementUnavailable("surrogate_only")
    if profile.accuracy != "exact_local_text":
        raise MeasurementUnavailable("profile_unreliable")
    if profile.encoding_name not in _VOCAB_HASHES:
        raise MeasurementUnavailable("tokenizer_unavailable")
    if profile.vocabulary_sha256 != _VOCAB_HASHES[profile.encoding_name]:
        raise MeasurementUnavailable("profile_unreliable")
    try:
        library = _library_identity()
        if library != (profile.library_version, profile.library_version):
            raise MeasurementUnavailable("profile_unreliable")
        path, stat, vocabulary = _read_vocabulary(profile.encoding_name)
        encoding = _build_encoding(profile.encoding_name, vocabulary)
        identity = _encoding_identity(encoding)

        def verify():
            current_path, current_stat, current_bytes = _read_vocabulary(profile.encoding_name)
            if (current_path != path or _file_identity(current_stat) != _file_identity(stat)
                    or current_bytes != vocabulary or _library_identity() != library
                    or _encoding_identity(encoding) != identity):
                raise MeasurementUnavailable("tokenizer_unavailable")

        verify()  # Recheck initialization before counting anything.
        yield encoding
        verify()  # Recheck counting before releasing any numbers.
    except MeasurementUnavailable:
        raise
    except Exception:
        # Dependency exceptions can contain text, hashes, URLs, or local paths.
        raise MeasurementUnavailable("tokenizer_unavailable") from None


def validate_profile(profile: TokenizerProfile) -> None:
    """Prove this profile is usable now; no cached validation grants later use."""
    profile = TokenizerProfile.model_validate(profile)
    with _get_encoding(profile):
        pass


def _measure(attempt: TextAttempt, encoding) -> MeasuredAttempt:
    whole = "".join(component.text for component in attempt.components)
    total = len(encoding.encode_ordinary(whole))
    prefix = ""
    previous = 0
    deltas = []
    for index, component in enumerate(attempt.components):
        prefix += component.text
        current = (total if index == len(attempt.components) - 1
                   else len(encoding.encode_ordinary(prefix)))
        deltas.append(current - previous)
        previous = current
    return MeasuredAttempt(event_id=attempt.event_id, attempt_kind=attempt.attempt_kind,
        total_tokens=total, component_tokens=tuple(deltas), simulated=attempt.simulated,
        multiplicity=attempt.multiplicity)


def measure_attempt(attempt: TextAttempt, profile: TokenizerProfile) -> MeasuredAttempt:
    """Count a complete request; prefix deltas are explanatory, never extra cost."""
    attempt = TextAttempt.model_validate(attempt)
    profile = TokenizerProfile.model_validate(profile)
    with _get_encoding(profile) as encoding:
        measured = _measure(attempt, encoding)
    return measured


def measure_attempts(attempts: tuple[TextAttempt, ...],
                     profile: TokenizerProfile) -> tuple[MeasuredAttempt, ...]:
    """Count distinct immutable events once, preserving first declaration order.

    Equal text in different events is still charged independently. Reusing an
    event ID with any different content/metadata is a configuration error, not
    an excuse to silently skip that request. Multiplicity remains per-event.
    """
    if type(attempts) is not tuple or len(attempts) > MAX_HISTORY_EVENTS:
        raise _EventRejected()
    unique = {}
    for value in attempts:
        attempt = TextAttempt.model_validate(value)
        previous = unique.get(attempt.event_id)
        if previous is not None and previous != attempt:
            raise _EventRejected()
        unique[attempt.event_id] = attempt
    if sum(item.multiplicity for item in unique.values()) > MAX_HISTORY_EVENTS:
        raise _EventRejected()
    profile = TokenizerProfile.model_validate(profile)
    with _get_encoding(profile) as encoding:
        measured = tuple(_measure(item, encoding) for item in unique.values())
    return measured

"""Read manually maintained user dictionaries without learning or side effects."""

import json
from pathlib import Path

from .codec import FallbackRequired
from .schema import UserDictionary


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FallbackRequired("duplicate dictionary JSON key")
        result[key] = value
    return result


def load_dictionary(path: Path, expected_user_id: str) -> UserDictionary:
    """Load strict UTF-8 JSON using the canonical schema representation."""
    try:
        dictionary = UserDictionary.model_validate(
            json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object),
            strict=True,
        )
    except (OSError, ValueError, RecursionError) as error:
        raise FallbackRequired("invalid dictionary file") from error
    if dictionary.user_id != expected_user_id:
        raise FallbackRequired("dictionary user does not match expected user")
    return dictionary

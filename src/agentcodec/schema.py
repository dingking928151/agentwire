"""Canonical v1 data uses Unicode scalars; approval flags never grant authorization."""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator


def _scalar_unicode(value: str) -> str:
    """Reject surrogate code points without replacing or normalizing any text."""
    if any("\ud800" <= character <= "\udfff" for character in value):
        raise ValueError("text must contain only Unicode scalar values (no surrogate code points)")
    return value


UnicodeText = Annotated[str, AfterValidator(_scalar_unicode)]
Identifier = Annotated[UnicodeText, Field(min_length=1)]


class MessageV1(BaseModel):
    """All fields are required, even when their value may explicitly be null."""

    model_config = ConfigDict(strict=True, extra="forbid")

    message_id: Identifier
    message_type: Literal["delegate", "status", "result"]
    task: UnicodeText
    status: Literal["pending", "running", "blocked", "completed", "failed", "cancelled"]
    blocker: UnicodeText | None
    next_step: UnicodeText | None
    evidence: list[UnicodeText]
    constraints: list[UnicodeText]
    approval_required: bool
    user_id: Identifier
    dictionary_version: Identifier | None


class UserDictionary(BaseModel):
    """Manual code→exact-phrase map; JSON duplicate-key detection belongs to loading."""

    model_config = ConfigDict(strict=True, extra="forbid")

    user_id: Identifier
    version: Identifier
    aliases: dict[Identifier, Identifier]

    @field_validator("aliases")
    @classmethod
    def unique_phrases(cls, aliases: dict[str, str]) -> dict[str, str]:
        if len(set(aliases.values())) != len(aliases):
            raise ValueError("dictionary phrases must be unique")
        return aliases

"""Canonical v1 data. Approval flags never grant authorization."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Identifier = Annotated[str, Field(min_length=1)]


class MessageV1(BaseModel):
    """All fields are required, even when their value may explicitly be null."""

    model_config = ConfigDict(strict=True, extra="forbid")

    message_id: Identifier
    message_type: Literal["delegate", "status", "result"]
    task: str
    status: Literal["pending", "running", "blocked", "completed", "failed", "cancelled"]
    blocker: str | None
    next_step: str | None
    evidence: list[str]
    constraints: list[str]
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

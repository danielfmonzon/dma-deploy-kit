"""Pydantic v2 models for the dma-deploy-kit client configuration.

A single client is described by one YAML file that validates against
``ClientConfig``. Every model forbids unknown keys (``extra="forbid"``) so that
typos in a config fail loudly rather than being silently ignored.

This module defines the schema only; loading/validation entry point lives in
``loader.py``.
"""

from __future__ import annotations

import re
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# A slug is lowercase alphanumeric words joined by single hyphens, e.g. "acme-wellness".
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# A snake_case identifier: starts with a letter, then lowercase letters/digits/underscores.
SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class _Base(BaseModel):
    """Base model: reject unknown keys everywhere so config typos surface."""

    model_config = ConfigDict(extra="forbid")


def _nonblank(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("must not be empty or whitespace-only")
    return value


# --------------------------------------------------------------------------- #
# client metadata
# --------------------------------------------------------------------------- #
class ClientMeta(_Base):
    slug: str
    business_name: str
    vertical: str
    timezone: str

    @field_validator("business_name", "vertical")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return _nonblank(v)

    @field_validator("slug")
    @classmethod
    def _valid_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(
                f"slug '{v}' is invalid: use lowercase alphanumeric words separated by "
                "single hyphens, e.g. 'acme-wellness'"
            )
        return v

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception as exc:  # ZoneInfoNotFoundError, ValueError, etc.
            raise ValueError(
                f"timezone '{v}' is not a valid IANA zone (e.g. 'America/New_York')"
            ) from exc
        return v


# --------------------------------------------------------------------------- #
# language profiles
# --------------------------------------------------------------------------- #
class LanguageProfile(_Base):
    code: str
    voice_id: str
    greeting: str  # the Retell begin_message for this language
    language_notes: str | None = None  # the LANGUAGE-section directive, optional

    @field_validator("code", "voice_id", "greeting")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return _nonblank(v)


# --------------------------------------------------------------------------- #
# facts
# --------------------------------------------------------------------------- #
class Hours(_Base):
    days: str
    open: str
    close: str


class Service(_Base):
    name: str
    description: str | None = None
    price: str | None = None  # free-form string, e.g. "$150" or "from $99"

    @field_validator("name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return _nonblank(v)


class Faq(_Base):
    q: str
    a: str


class Facts(_Base):
    description: str
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    hours: list[Hours] | None = None
    services: list[Service] = Field(default_factory=list)
    faq: list[Faq] | None = None

    @field_validator("description")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return _nonblank(v)


# --------------------------------------------------------------------------- #
# booking
# --------------------------------------------------------------------------- #
class Booking(_Base):
    url: str | None = None
    sms_consent: bool = False


# --------------------------------------------------------------------------- #
# escalation
# --------------------------------------------------------------------------- #
class Escalation(_Base):
    contact_name: str
    escalate_when: list[str] = Field(default_factory=list)
    handoff_message: str | None = None

    @field_validator("contact_name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return _nonblank(v)


# --------------------------------------------------------------------------- #
# guardrails
# --------------------------------------------------------------------------- #
class Guardrails(_Base):
    never_say: list[str] = Field(default_factory=list)
    off_limits: list[str] = Field(default_factory=list)
    preset: Literal["medical_adjacent", "none"] = "none"


# --------------------------------------------------------------------------- #
# agent settings
# --------------------------------------------------------------------------- #
class Pronunciation(_Base):
    word: str
    alphabet: str
    phoneme: str


class AgentSettings(_Base):
    max_call_duration_ms: int = Field(default=600_000, ge=60_000, le=3_600_000)
    ambient_sound: str | None = None
    enable_expressive_mode: bool = False
    expressive_emotion_tags: list[str] = Field(default_factory=list)
    pronunciation: list[Pronunciation] = Field(default_factory=list)
    knowledge_base_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# post-call analysis schema
# --------------------------------------------------------------------------- #
class PostCallField(_Base):
    name: str
    type: Literal["string", "boolean", "enum", "number"]
    description: str
    choices: list[str] | None = None  # required iff type == "enum"

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not SNAKE_RE.match(v):
            raise ValueError(
                f"name '{v}' must be snake_case (lowercase letters, digits, underscores; "
                "starting with a letter)"
            )
        return v

    @field_validator("description")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return _nonblank(v)

    @model_validator(mode="after")
    def _choices_match_type(self) -> PostCallField:
        if self.type == "enum":
            if not self.choices:
                raise ValueError(
                    f"post_call field '{self.name}': type 'enum' requires a non-empty "
                    "'choices' list"
                )
        elif self.choices is not None:
            raise ValueError(
                f"post_call field '{self.name}': 'choices' is only allowed when type == "
                f"'enum' (this field's type is '{self.type}')"
            )
        return self


# --------------------------------------------------------------------------- #
# top-level config
# --------------------------------------------------------------------------- #
class ClientConfig(_Base):
    client: ClientMeta
    languages: list[LanguageProfile] = Field(min_length=1)
    facts: Facts
    booking: Booking = Field(default_factory=Booking)
    escalation: Escalation
    guardrails: Guardrails = Field(default_factory=Guardrails)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    post_call: list[PostCallField] = Field(min_length=1)

    @field_validator("languages")
    @classmethod
    def _unique_language_codes(cls, v: list[LanguageProfile]) -> list[LanguageProfile]:
        codes = [lp.code for lp in v]
        dupes = sorted({c for c in codes if codes.count(c) > 1})
        if dupes:
            raise ValueError(f"languages: duplicate language codes not allowed: {dupes}")
        return v

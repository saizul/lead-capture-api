from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from leadcapture.fields import FieldDef
from leadcapture.models import LeadStatus
from leadcapture.notifiers.configs import NotifierConfig

ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(?::\d{1,5})?$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_.:\-]{0,31}$")
MAX_TAGS_PER_LEAD = 20
MAX_NOTE_LENGTH = 5000
MAX_BULK_IDS = 500
SLUG_PATTERN = r"^[a-z0-9][a-z0-9\-_]{1,62}$"

CONTACT_FORM_EXAMPLE: dict[str, Any] = {
    "slug": "contact",
    "name": "Website contact form",
    "fields": [
        {"name": "name", "type": "text", "required": True, "max_length": 120},
        {"name": "email", "type": "email", "required": True},
        {"name": "phone", "type": "phone"},
        {
            "name": "budget",
            "type": "select",
            "options": ["under-1k", "1k-5k", "5k-plus"],
        },
        {"name": "message", "type": "textarea", "required": True, "max_length": 2000},
    ],
    "allowed_origins": ["https://example.com"],
    "require_consent": True,
    "min_submit_seconds": 3,
    "notifiers": [
        {
            "type": "webhook",
            "url": "https://n8n.example.com/webhook/leads",
            "secret": "whsec_replace_me",
        },
        {"type": "slack", "webhook_url": "https://hooks.slack.com/services/T000/B000/XXXX"},
    ],
}


class FormBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    fields: list[FieldDef] = Field(min_length=1, max_length=50)
    allowed_origins: list[str] = Field(default_factory=list, max_length=50)
    notifiers: list[NotifierConfig] = Field(default_factory=list, max_length=10)
    honeypot_field: str = Field(default="website_url", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    min_submit_seconds: int = Field(default=3, ge=0, le=3600)
    require_consent: bool = False
    spam_threshold: int = Field(default=50, ge=1, le=1000)
    blocked_words: list[str] = Field(default_factory=list, max_length=200)
    rate_limit_per_ip: int = Field(default=10, ge=1, le=100000)
    rate_limit_per_form: int = Field(default=300, ge=1, le=1000000)
    success_redirect_url: str | None = Field(default=None, max_length=2048)
    is_active: bool = True

    @field_validator("allowed_origins")
    @classmethod
    def _origins(cls, value: list[str]) -> list[str]:
        cleaned = []
        for origin in value:
            origin = origin.strip().rstrip("/")
            if origin != "*" and not ORIGIN_RE.match(origin):
                raise ValueError(f"invalid origin {origin!r}; use scheme://host[:port] or '*'")
            cleaned.append(origin)
        return cleaned

    @field_validator("success_redirect_url")
    @classmethod
    def _redirect(cls, value: str | None) -> str | None:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("must be an absolute http(s) URL")
        return value

    @model_validator(mode="after")
    def _unique_fields(self) -> FormBase:
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("field names must be unique")
        if self.honeypot_field in names:
            raise ValueError("honeypot_field collides with a real field name")
        return self


class FormCreate(FormBase):
    model_config = ConfigDict(json_schema_extra={"examples": [CONTACT_FORM_EXAMPLE]})

    slug: str = Field(pattern=SLUG_PATTERN)


class FormUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    fields: list[FieldDef] | None = Field(default=None, min_length=1, max_length=50)
    allowed_origins: list[str] | None = None
    notifiers: list[dict[str, Any]] | None = None
    honeypot_field: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    min_submit_seconds: int | None = Field(default=None, ge=0, le=3600)
    require_consent: bool | None = None
    spam_threshold: int | None = Field(default=None, ge=1, le=1000)
    blocked_words: list[str] | None = None
    rate_limit_per_ip: int | None = Field(default=None, ge=1, le=100000)
    rate_limit_per_form: int | None = Field(default=None, ge=1, le=1000000)
    success_redirect_url: str | None = None
    is_active: bool | None = None

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"min_submit_seconds": 5, "is_active": True}]}
    )


class FormOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    description: str | None
    fields: list[FieldDef]
    allowed_origins: list[str]
    notifiers: list[dict[str, Any]]
    honeypot_field: str
    min_submit_seconds: int
    require_consent: bool
    spam_threshold: int
    blocked_words: list[str]
    rate_limit_per_ip: int
    rate_limit_per_form: int
    success_redirect_url: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PublicFormSchema(BaseModel):
    slug: str
    name: str
    fields: list[FieldDef]
    honeypot_field: str
    min_submit_seconds: int
    require_consent: bool
    form_token: str = Field(description="Send back as `_form_token` when submitting.")


class SubmitResponse(BaseModel):
    id: str
    status: str = "received"

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"id": "6f0c1b52-6c0e-4f0a-8d77-4c1f7a3c9a10", "status": "received"}]
        }
    )


def normalize_tag(value: str) -> str:
    """Trim and lower-case a tag, rejecting anything outside ``TAG_RE``."""
    tag = value.strip().lower()
    if not TAG_RE.match(tag):
        raise ValueError(
            f"invalid tag {value!r}: use 1-32 characters from a-z, 0-9, '-', '_', '.', ':'"
            " starting with a letter or digit"
        )
    return tag


def normalize_tags(values: list[str]) -> list[str]:
    """Normalise a list of tags, dropping duplicates while keeping the first occurrence."""
    return list(dict.fromkeys(normalize_tag(v) for v in values))


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    form: str
    data: dict[str, Any]
    name: str | None
    email: str | None
    consent: bool
    consent_at: datetime | None
    status: LeadStatus
    tags: list[str]
    note: str | None
    spam_score: int
    is_spam: bool
    spam_reasons: list[str]
    source_url: str | None
    user_agent: str | None
    created_at: datetime
    updated_at: datetime


class LeadPage(BaseModel):
    items: list[LeadOut]
    total: int
    limit: int
    offset: int


class _TagChanges(BaseModel):
    add_tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS_PER_LEAD)
    remove_tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS_PER_LEAD)

    @field_validator("add_tags", "remove_tags")
    @classmethod
    def _normalize(cls, value: list[str]) -> list[str]:
        return normalize_tags(value)

    @model_validator(mode="after")
    def _no_overlap(self) -> _TagChanges:
        both = set(self.add_tags) & set(self.remove_tags)
        if both:
            raise ValueError(f"tags both added and removed: {', '.join(sorted(both))}")
        return self


class LeadUpdate(_TagChanges):
    """Partial update of a lead's triage fields. Omitted fields are left unchanged."""

    status: LeadStatus | None = None
    tags: list[str] | None = Field(
        default=None,
        max_length=MAX_TAGS_PER_LEAD,
        description="Replace all tags. Cannot be combined with add_tags / remove_tags.",
    )
    note: str | None = Field(
        default=None,
        max_length=MAX_NOTE_LENGTH,
        description="Internal note. Send null or an empty string to clear it.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"status": "contacted"},
                {"add_tags": ["vip"], "note": "Called on Monday, follow up next week."},
                {"tags": ["agency", "q4"]},
            ]
        }
    )

    @field_validator("tags")
    @classmethod
    def _normalize_replacement(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalize_tags(value)

    @model_validator(mode="after")
    def _check(self) -> LeadUpdate:
        fields = self.model_fields_set
        if not fields:
            raise ValueError("provide at least one of status, tags, add_tags, remove_tags, note")
        if "status" in fields and self.status is None:
            raise ValueError("status cannot be null")
        if "tags" in fields and (self.add_tags or self.remove_tags):
            raise ValueError("use either tags or add_tags / remove_tags, not both")
        if "tags" in fields and self.tags is None:
            raise ValueError("tags cannot be null; send [] to clear them")
        return self


# Backwards-compatible name for the original status-only body.
LeadStatusUpdate = LeadUpdate


class LeadBulkUpdate(_TagChanges):
    """Apply the same status and/or tag change to many leads at once."""

    ids: list[str] = Field(min_length=1, max_length=MAX_BULK_IDS)
    status: LeadStatus | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "ids": ["0b7c0d6e-5d1f-4c37-9a8e-1f2d3c4b5a69"],
                    "status": "contacted",
                    "add_tags": ["campaign:spring"],
                }
            ]
        }
    )

    @field_validator("ids")
    @classmethod
    def _dedupe_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _check(self) -> LeadBulkUpdate:
        if self.status is None and not self.add_tags and not self.remove_tags:
            raise ValueError("provide status, add_tags or remove_tags")
        return self


class LeadBulkResult(BaseModel):
    updated: int
    not_found: list[str]


class TagCount(BaseModel):
    tag: str
    count: int


class OutboxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    lead_id: str
    notifier_type: str
    status: str
    attempts: int
    max_attempts: int
    next_attempt_at: datetime
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None


class DeliveryLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    outbox_id: str
    lead_id: str
    notifier_type: str
    attempt: int
    success: bool
    status_code: int | None
    error: str | None
    duration_ms: int
    created_at: datetime

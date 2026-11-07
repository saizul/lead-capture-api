"""Per-form field schema definitions and submission validation."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import date
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)+$"
)
PHONE_RE = re.compile(r"^\+?[\d\s().\-]{6,32}$")

RESERVED_FIELD_NAMES = {"consent"}
TRUE_VALUES = {"true", "1", "on", "yes", "y", "checked"}
FALSE_VALUES = {"false", "0", "off", "no", "n", ""}


class FieldType(str, Enum):
    text = "text"
    textarea = "textarea"
    email = "email"
    phone = "phone"
    url = "url"
    number = "number"
    integer = "integer"
    boolean = "boolean"
    select = "select"
    date = "date"


DEFAULT_MAX_LENGTH = {
    FieldType.text: 255,
    FieldType.textarea: 5000,
    FieldType.email: 254,
    FieldType.phone: 32,
    FieldType.url: 2048,
    FieldType.select: 255,
    FieldType.date: 10,
}

STRING_TYPES = {
    FieldType.text,
    FieldType.textarea,
    FieldType.email,
    FieldType.phone,
    FieldType.url,
    FieldType.select,
    FieldType.date,
}


class FieldDef(BaseModel):
    """Definition of one input accepted by a form."""

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$", examples=["email"])
    type: FieldType = FieldType.text
    label: str | None = Field(default=None, max_length=200)
    required: bool = False
    min_length: int | None = Field(default=None, ge=0, le=10000)
    max_length: int | None = Field(default=None, ge=1, le=10000)
    min_value: float | None = None
    max_value: float | None = None
    options: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        if value.lower() in RESERVED_FIELD_NAMES:
            raise ValueError(f"'{value}' is a reserved field name")
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> FieldDef:
        if self.type is FieldType.select and not self.options:
            raise ValueError("select fields need at least one option")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("min_length cannot exceed max_length")
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.min_value > self.max_value
        ):
            raise ValueError("min_value cannot exceed max_value")
        return self

    @property
    def effective_max_length(self) -> int | None:
        if self.max_length is not None:
            return self.max_length
        return DEFAULT_MAX_LENGTH.get(self.type)


def parse_bool(value: Any) -> bool | None:
    """Interpret checkbox-ish input. Returns None when the value is not boolean-like."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in TRUE_VALUES:
            return True
        if lowered in FALSE_VALUES:
            return False
    return None


def _clean_scalar(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _validate_value(field: FieldDef, value: Any) -> tuple[Any, str | None]:
    ftype = field.type

    if ftype is FieldType.boolean:
        parsed = parse_bool(value)
        if parsed is None:
            return None, "Must be true or false."
        if field.required and not parsed:
            return None, "This box must be checked."
        return parsed, None

    if ftype in (FieldType.number, FieldType.integer):
        if isinstance(value, bool):
            return None, "Must be a number."
        try:
            if ftype is FieldType.integer:
                if isinstance(value, float):
                    if not value.is_integer():
                        return None, "Must be a whole number."
                    number: float | int = int(value)
                else:
                    number = int(str(value).strip())
            else:
                number = float(value)
        except (TypeError, ValueError):
            return (
                None,
                "Must be a whole number." if ftype is FieldType.integer else "Must be a number.",
            )
        if isinstance(number, float) and (math.isnan(number) or math.isinf(number)):
            return None, "Must be a finite number."
        if field.min_value is not None and number < field.min_value:
            return None, f"Must be at least {field.min_value:g}."
        if field.max_value is not None and number > field.max_value:
            return None, f"Must be at most {field.max_value:g}."
        return number, None

    # Everything else is a string type.
    if isinstance(value, (bool, list, dict)):
        return None, "Must be a string."
    text = str(value).strip() if not isinstance(value, str) else value.strip()

    max_len = field.effective_max_length
    if max_len is not None and len(text) > max_len:
        return None, f"Must be at most {max_len} characters."
    if field.min_length is not None and len(text) < field.min_length:
        return None, f"Must be at least {field.min_length} characters."

    if ftype is FieldType.email:
        if not EMAIL_RE.match(text) or ".." in text:
            return None, "Enter a valid email address."
        return text.lower(), None
    if ftype is FieldType.phone:
        digits = sum(ch.isdigit() for ch in text)
        if not PHONE_RE.match(text) or not 6 <= digits <= 15:
            return None, "Enter a valid phone number."
        return text, None
    if ftype is FieldType.url:
        url = urlparse(text)
        if url.scheme not in ("http", "https") or not url.netloc:
            return None, "Enter a valid http(s) URL."
        return text, None
    if ftype is FieldType.select:
        if text not in field.options:
            return None, "Choose one of the available options."
        return text, None
    if ftype is FieldType.date:
        try:
            date.fromisoformat(text)
        except ValueError:
            return None, "Use the format YYYY-MM-DD."
        return text, None
    return text, None


def validate_submission(
    fields: list[FieldDef], raw: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate raw input against the form's fields.

    Returns `(clean_data, errors)`. Unknown keys are dropped, never stored.
    """
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for field in fields:
        value = _clean_scalar(raw.get(field.name))
        if value is None:
            if field.required:
                errors[field.name] = "This field is required."
            continue
        cleaned, error = _validate_value(field, value)
        if error:
            errors[field.name] = error
        else:
            clean[field.name] = cleaned
    return clean, errors

from __future__ import annotations

import pytest
from pydantic import ValidationError

from leadcapture.fields import FieldDef, FieldType, parse_bool, validate_submission


def fd(**kw):
    return FieldDef(**kw)


def run(field: FieldDef, value):
    return validate_submission([field], {field.name: value})


def test_required_field_missing():
    clean, errors = validate_submission([fd(name="email", type="email", required=True)], {})
    assert clean == {}
    assert errors == {"email": "This field is required."}


def test_required_blank_string_counts_as_missing():
    _, errors = run(fd(name="name", required=True), "   ")
    assert "name" in errors


def test_optional_missing_is_skipped():
    clean, errors = validate_submission([fd(name="note")], {})
    assert clean == {} and errors == {}


def test_text_is_trimmed():
    clean, _ = run(fd(name="name"), "  Ada  ")
    assert clean["name"] == "Ada"


def test_max_length_enforced():
    _, errors = run(fd(name="name", max_length=5), "abcdef")
    assert "at most 5" in errors["name"]


def test_min_length_enforced():
    _, errors = run(fd(name="name", min_length=3), "ab")
    assert "at least 3" in errors["name"]


def test_default_max_length_for_text():
    _, errors = run(fd(name="name"), "x" * 256)
    assert "name" in errors


def test_unknown_fields_are_dropped():
    clean, errors = validate_submission([fd(name="name")], {"name": "A", "evil": "payload"})
    assert clean == {"name": "A"} and not errors


@pytest.mark.parametrize("value", ["ada@example.com", "A.B+tag@sub.example.co.uk"])
def test_valid_emails(value):
    clean, errors = run(fd(name="email", type="email"), value)
    assert not errors
    assert clean["email"] == value.lower()


@pytest.mark.parametrize(
    "value", ["nope", "a@b", "a@@b.com", "a b@example.com", "a..b@example.com"]
)
def test_invalid_emails(value):
    _, errors = run(fd(name="email", type="email"), value)
    assert "email" in errors


@pytest.mark.parametrize("value", ["+8801712345678", "(555) 123-4567", "555.123.4567"])
def test_valid_phone(value):
    _, errors = run(fd(name="phone", type="phone"), value)
    assert not errors


@pytest.mark.parametrize("value", ["abc", "12", "+1234567890123456789"])
def test_invalid_phone(value):
    _, errors = run(fd(name="phone", type="phone"), value)
    assert "phone" in errors


def test_url_requires_http_scheme():
    f = fd(name="site", type="url")
    assert not run(f, "https://saizul.com")[1]
    assert run(f, "javascript:alert(1)")[1]
    assert run(f, "ftp://example.com")[1]


def test_number_and_bounds():
    f = fd(name="budget", type="number", min_value=10, max_value=100)
    assert run(f, "42.5")[0]["budget"] == 42.5
    assert run(f, 5)[1]
    assert run(f, 500)[1]
    assert run(f, "abc")[1]
    assert run(f, "nan")[1]
    assert run(f, True)[1]


def test_integer_rejects_fractions():
    f = fd(name="qty", type="integer")
    assert run(f, "3")[0]["qty"] == 3
    assert run(f, 3.0)[0]["qty"] == 3
    assert run(f, "3.5")[1]
    assert run(f, 3.5)[1]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("on", True), ("true", True), ("1", True), (True, True), ("off", False), ("no", False)],
)
def test_boolean_parsing(raw, expected):
    clean, errors = run(fd(name="newsletter", type="boolean"), raw)
    assert not errors
    assert clean["newsletter"] is expected


def test_boolean_garbage_is_error():
    assert run(fd(name="newsletter", type="boolean"), "maybe")[1]


def test_required_boolean_must_be_true():
    f = fd(name="terms", type="boolean", required=True)
    assert run(f, "on")[0]["terms"] is True
    assert run(f, "false")[1]


def test_select_options():
    f = fd(name="plan", type="select", options=["a", "b"])
    assert not run(f, "a")[1]
    assert run(f, "c")[1]


def test_select_requires_options():
    with pytest.raises(ValidationError):
        fd(name="plan", type="select")


def test_date_iso_format():
    f = fd(name="when", type="date")
    assert not run(f, "2026-02-28")[1]
    assert run(f, "28/02/2026")[1]
    assert run(f, "2026-02-30")[1]


def test_structured_values_rejected_for_text():
    assert run(fd(name="name"), {"a": 1})[1]
    assert run(fd(name="name"), ["x"])[1]


def test_reserved_field_name_rejected():
    with pytest.raises(ValidationError):
        fd(name="consent")


def test_invalid_field_name_rejected():
    with pytest.raises(ValidationError):
        fd(name="1bad name")


def test_length_bounds_must_be_consistent():
    with pytest.raises(ValidationError):
        fd(name="a", min_length=10, max_length=5)


def test_multiple_errors_reported_together():
    fields = [fd(name="a", required=True), fd(name="b", type="email", required=True)]
    _, errors = validate_submission(fields, {"b": "bad"})
    assert set(errors) == {"a", "b"}


def test_parse_bool_helper():
    assert parse_bool("YES") is True
    assert parse_bool(0) is False
    assert parse_bool("what") is None
    assert FieldType("email") is FieldType.email

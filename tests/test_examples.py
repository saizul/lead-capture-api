"""The files in examples/ must stay in step with the API they document."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from leadcapture.fields import FieldType
from leadcapture.schemas import CONTACT_FORM_EXAMPLE, FormBase
from leadcapture.security import SIGNATURE_HEADER, TIMESTAMP_HEADER

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
LANDING = EXAMPLES / "static-landing" / "index.html"
HONEYPOT = FormBase.model_fields["honeypot_field"].default
TOKEN_FIELD = "_form_token"

# <input type=...> / element name -> API field type
HTML_TO_FIELD_TYPE = {
    "text": FieldType.text,
    "email": FieldType.email,
    "tel": FieldType.phone,
    "textarea": FieldType.textarea,
    "select": FieldType.select,
}


class FormParser(HTMLParser):
    """Collects the controls of the first <form> in a page."""

    def __init__(self) -> None:
        super().__init__()
        self.in_form = False
        self.controls: dict[str, dict[str, Any]] = {}
        self.form_attrs: dict[str, str | None] = {}
        self._select: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "form" and not self.form_attrs:
            self.in_form = True
            self.form_attrs = a
        if not self.in_form:
            return
        if tag in ("input", "textarea", "select") and a.get("name"):
            kind = (a.get("type") or "text") if tag == "input" else tag
            self.controls[str(a["name"])] = {
                "kind": kind,
                "required": "required" in a,
                "maxlength": int(a["maxlength"]) if a.get("maxlength") else None,
                "options": [],
            }
            if tag == "select":
                self._select = str(a["name"])
        elif tag == "option" and self._select and a.get("value"):
            self.controls[self._select]["options"].append(a["value"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._select = None
        elif tag == "form":
            self.in_form = False


def parse_landing() -> FormParser:
    parser = FormParser()
    parser.feed(LANDING.read_text(encoding="utf-8"))
    return parser


def test_landing_form_fields_match_default_example_schema() -> None:
    controls = parse_landing().controls
    api_fields = {f["name"]: f for f in CONTACT_FORM_EXAMPLE["fields"]}

    # every API field is on the page, and the page has nothing the API would reject as unknown
    plumbing = {HONEYPOT, TOKEN_FIELD, "consent"}
    assert set(controls) - plumbing == set(api_fields)

    for name, field in api_fields.items():
        control = controls[name]
        assert HTML_TO_FIELD_TYPE[control["kind"]].value == field["type"], name
        assert control["required"] == field.get("required", False), name
        if "max_length" in field:
            assert control["maxlength"] == field["max_length"], name
        if "options" in field:
            assert control["options"] == field["options"], name


def test_landing_form_has_honeypot_consent_and_token() -> None:
    controls = parse_landing().controls
    assert HONEYPOT in controls and not controls[HONEYPOT]["required"]
    assert controls["consent"]["kind"] == "checkbox"
    assert CONTACT_FORM_EXAMPLE["require_consent"] is True
    assert controls[TOKEN_FIELD]["kind"] == "hidden"


def test_landing_page_uses_the_public_endpoints() -> None:
    source = LANDING.read_text(encoding="utf-8")
    form = parse_landing().form_attrs
    assert form["data-slug"] == CONTACT_FORM_EXAMPLE["slug"]
    assert "/v1/forms/${SLUG}/schema" in source
    assert "/v1/forms/${SLUG}/submit" in source
    assert "schema.form_token" in source  # response key of GET /schema
    assert "validation_failed" in source and "error.fields" in source  # 422 body shape


def test_nextjs_proxy_targets_the_public_endpoints() -> None:
    source = (EXAMPLES / "nextjs" / "app" / "api" / "lead" / "route.ts").read_text(encoding="utf-8")
    assert "/v1/forms/" in source
    assert "${FORM_URL}/schema" in source and "${FORM_URL}/submit" in source
    assert "export async function GET" in source and "export async function POST" in source
    assert "X-Forwarded-For" in source


def test_n8n_snippet_matches_the_signing_scheme() -> None:
    source = (EXAMPLES / "n8n" / "verify-signature.js").read_text(encoding="utf-8")
    # header names (n8n lower-cases them)
    assert SIGNATURE_HEADER.lower() in source
    assert TIMESTAMP_HEADER.lower() in source
    # "sha256=" + hex HMAC-SHA256 over "<timestamp>.<raw body>"
    assert "'sha256='" in source
    assert "createHmac('sha256', secret)" in source
    assert re.search(r"update\(`\$\{timestamp\}\.`\)\s*\.update\(raw\)", source)
    assert ".digest('hex')" in source
    assert "timingSafeEqual" in source


def test_examples_index_lists_every_example_folder() -> None:
    readme = (EXAMPLES / "README.md").read_text(encoding="utf-8")
    for folder in ("static-landing", "nextjs", "n8n"):
        assert (EXAMPLES / folder).is_dir()
        assert f"{folder}/" in readme

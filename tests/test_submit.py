from __future__ import annotations

import time
from typing import Any

import pytest

from leadcapture.models import Lead, OutboxEvent
from leadcapture.security import hash_ip, make_form_token
from tests.helpers import VALID_SUBMISSION, create_form, fetch_all

URL = "/v1/forms/contact/submit"


async def test_json_submission_is_stored(app, client):
    await create_form(app)
    resp = await client.post(URL, json=VALID_SUBMISSION)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "received"
    leads = await fetch_all(app, Lead)
    assert len(leads) == 1
    lead = leads[0]
    assert lead.id == body["id"]
    assert lead.email == "ada@example.com" and lead.name == "Ada Lovelace"
    assert lead.data["message"].startswith("I would like")
    assert lead.status == "new" and not lead.is_spam


async def test_form_encoded_submission(app, client):
    await create_form(app)
    resp = await client.post(URL, data=VALID_SUBMISSION)
    assert resp.status_code == 201
    assert (await fetch_all(app, Lead))[0].email == "ada@example.com"


async def test_multipart_submission(app, client):
    await create_form(app)
    resp = await client.post(URL, files={k: (None, v) for k, v in VALID_SUBMISSION.items()})
    assert resp.status_code == 201


async def test_unknown_form_404(client):
    resp = await client.post("/v1/forms/missing/submit", json=VALID_SUBMISSION)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "form_not_found"


async def test_inactive_form_404(app, client):
    await create_form(app, is_active=False)
    assert (await client.post(URL, json=VALID_SUBMISSION)).status_code == 404


async def test_validation_errors_are_per_field(app, client):
    await create_form(app)
    resp = await client.post(URL, json={"name": "", "email": "nope"})
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_failed"
    assert set(error["fields"]) == {"name", "email"}
    assert await fetch_all(app, Lead) == []


async def test_unknown_fields_are_not_stored(app, client):
    await create_form(app)
    await client.post(URL, json={**VALID_SUBMISSION, "evil": "x", "_form_token": "t"})
    assert "evil" not in (await fetch_all(app, Lead))[0].data


async def test_invalid_json_400(app, client):
    await create_form(app)
    resp = await client.post(URL, content=b"{oops", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "invalid_json"


async def test_json_array_body_400(app, client):
    await create_form(app)
    assert (await client.post(URL, json=[1, 2])).status_code == 400


async def test_unsupported_content_type_415(app, client):
    await create_form(app)
    resp = await client.post(URL, content=b"hi", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 415


class TestBodyLimit:
    @pytest.fixture
    def settings_overrides(self):
        return {"max_body_bytes": 200}

    async def test_large_body_413(self, app, client):
        await create_form(app)
        resp = await client.post(URL, json={**VALID_SUBMISSION, "message": "x" * 500})
        assert resp.status_code == 413


async def test_honeypot_returns_success_but_stores_nothing(app, client):
    await create_form(app, notifiers=[{"type": "webhook", "url": "https://hook.example.com/x"}])
    resp = await client.post(URL, json={**VALID_SUBMISSION, "website_url": "http://spam.test"})
    assert resp.status_code == 201 and resp.json()["status"] == "received"
    assert await fetch_all(app, Lead) == []
    assert await fetch_all(app, OutboxEvent) == []


async def test_custom_honeypot_field_name(app, client):
    await create_form(app, honeypot_field="company_fax")
    await client.post(URL, json={**VALID_SUBMISSION, "company_fax": "123"})
    assert await fetch_all(app, Lead) == []


async def test_missing_token_rejected_when_time_check_enabled(app, client):
    await create_form(app, min_submit_seconds=3)
    resp = await client.post(URL, json=VALID_SUBMISSION)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "form_token_invalid"


async def test_too_fast_submission_rejected(app, client):
    await create_form(app, min_submit_seconds=3)
    token = make_form_token("test-secret", "contact")
    resp = await client.post(URL, json={**VALID_SUBMISSION, "_form_token": token})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "submitted_too_fast"


async def test_submission_after_min_time_accepted(app, client):
    await create_form(app, min_submit_seconds=3)
    token = make_form_token("test-secret", "contact", now=time.time() - 10)
    resp = await client.post(URL, json={**VALID_SUBMISSION, "_form_token": token})
    assert resp.status_code == 201


async def test_tampered_token_rejected(app, client):
    await create_form(app, min_submit_seconds=3)
    token = make_form_token("wrong-secret", "contact", now=time.time() - 10)
    resp = await client.post(URL, json={**VALID_SUBMISSION, "_form_token": token})
    assert resp.json()["error"]["code"] == "form_token_invalid"


async def test_token_for_another_form_rejected(app, client):
    await create_form(app, min_submit_seconds=3)
    token = make_form_token("test-secret", "other-form", now=time.time() - 10)
    resp = await client.post(URL, json={**VALID_SUBMISSION, "_form_token": token})
    assert resp.json()["error"]["code"] == "form_token_invalid"


async def test_expired_token_rejected(app, client):
    await create_form(app, min_submit_seconds=3)
    token = make_form_token("test-secret", "contact", now=time.time() - 200000)
    resp = await client.post(URL, json={**VALID_SUBMISSION, "_form_token": token})
    assert resp.json()["error"]["code"] == "form_token_expired"


async def test_schema_endpoint_issues_usable_token(app, client):
    await create_form(app, min_submit_seconds=1)
    schema = (await client.get("/v1/forms/contact/schema")).json()
    assert schema["slug"] == "contact" and schema["honeypot_field"] == "website_url"
    assert {f["name"] for f in schema["fields"]} >= {"name", "email"}
    assert (
        await client.post(URL, json={**VALID_SUBMISSION, "_form_token": schema["form_token"]})
    ).status_code == 422
    # Age the token by re-signing with an older timestamp.
    aged = make_form_token("test-secret", "contact", now=time.time() - 5)
    assert (
        await client.post(URL, json={**VALID_SUBMISSION, "_form_token": aged})
    ).status_code == 201


async def test_consent_required_when_enabled(app, client):
    await create_form(app, require_consent=True)
    resp = await client.post(URL, json=VALID_SUBMISSION)
    assert resp.status_code == 422 and "consent" in resp.json()["error"]["fields"]


async def test_consent_is_recorded(app, client):
    await create_form(app, require_consent=True)
    resp = await client.post(URL, data={**VALID_SUBMISSION, "consent": "on"})
    assert resp.status_code == 201
    lead = (await fetch_all(app, Lead))[0]
    assert lead.consent is True and lead.consent_at is not None


async def test_consent_defaults_to_false(app, client):
    await create_form(app)
    await client.post(URL, json=VALID_SUBMISSION)
    lead = (await fetch_all(app, Lead))[0]
    assert lead.consent is False and lead.consent_at is None


async def test_ip_is_stored_hashed_only(app, client):
    await create_form(app)
    await client.post(URL, json=VALID_SUBMISSION, headers={"User-Agent": "pytest-agent"})
    lead = (await fetch_all(app, Lead))[0]
    assert lead.ip_hash == hash_ip("127.0.0.1", "test-salt")
    assert "127.0.0.1" not in repr(lead.__dict__)
    assert lead.user_agent == "pytest-agent"


async def test_source_url_from_referer(app, client):
    await create_form(app)
    await client.post(
        URL, json=VALID_SUBMISSION, headers={"Referer": "https://example.com/landing"}
    )
    assert (await fetch_all(app, Lead))[0].source_url == "https://example.com/landing"


async def test_spam_is_stored_flagged_and_not_notified(app, client):
    await create_form(app, notifiers=[{"type": "webhook", "url": "https://hook.example.com/x"}])
    spammy = {**VALID_SUBMISSION, "message": "casino https://a.com https://b.com"}
    resp = await client.post(URL, json=spammy)
    assert resp.status_code == 201  # bots get no signal
    lead = (await fetch_all(app, Lead))[0]
    assert lead.is_spam and lead.spam_score >= 50
    assert any(r.startswith("blocked_word") for r in lead.spam_reasons)
    assert await fetch_all(app, OutboxEvent) == []


async def test_disposable_email_flagged(app, client):
    await create_form(app, spam_threshold=40)
    await client.post(URL, json={**VALID_SUBMISSION, "email": "bot@mailinator.com"})
    assert (await fetch_all(app, Lead))[0].is_spam


async def test_per_form_blocked_words(app, client):
    await create_form(app, blocked_words=["cheap widgets"], spam_threshold=30)
    await client.post(URL, json={**VALID_SUBMISSION, "message": "Buy cheap widgets today"})
    assert (await fetch_all(app, Lead))[0].is_spam


async def test_url_field_links_do_not_count_as_spam(app, client):
    fields: list[dict[str, Any]] = [
        {"name": "name", "type": "text", "required": True},
        {"name": "site", "type": "url"},
    ]
    await create_form(app, fields=fields)
    await client.post(URL, json={"name": "Ada", "site": "https://saizul.com"})
    assert not (await fetch_all(app, Lead))[0].is_spam


class TestCors:
    async def test_allowed_origin_gets_cors_header(self, app, client):
        await create_form(app, allowed_origins=["https://example.com"])
        resp = await client.post(
            URL, json=VALID_SUBMISSION, headers={"Origin": "https://example.com"}
        )
        assert resp.status_code == 201
        assert resp.headers["access-control-allow-origin"] == "https://example.com"
        assert "Origin" in resp.headers["vary"]

    async def test_disallowed_origin_blocked(self, app, client):
        await create_form(app, allowed_origins=["https://example.com"])
        resp = await client.post(
            URL, json=VALID_SUBMISSION, headers={"Origin": "https://evil.test"}
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "origin_not_allowed"
        assert await fetch_all(app, Lead) == []

    async def test_empty_allow_list_blocks_browser_origins(self, app, client):
        await create_form(app, allowed_origins=[])
        resp = await client.post(
            URL, json=VALID_SUBMISSION, headers={"Origin": "https://example.com"}
        )
        assert resp.status_code == 403

    async def test_server_to_server_without_origin_allowed(self, app, client):
        await create_form(app, allowed_origins=["https://example.com"])
        resp = await client.post(URL, json=VALID_SUBMISSION)
        assert resp.status_code == 201
        assert "access-control-allow-origin" not in resp.headers

    async def test_wildcard_origin(self, app, client):
        await create_form(app, allowed_origins=["*"])
        resp = await client.post(URL, json=VALID_SUBMISSION, headers={"Origin": "https://any.test"})
        assert resp.headers["access-control-allow-origin"] == "*"

    async def test_preflight_allowed(self, app, client):
        await create_form(app, allowed_origins=["https://example.com"])
        resp = await client.options(
            URL,
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert resp.status_code == 204
        assert resp.headers["access-control-allow-origin"] == "https://example.com"
        assert "POST" in resp.headers["access-control-allow-methods"]

    async def test_preflight_disallowed(self, app, client):
        await create_form(app, allowed_origins=["https://example.com"])
        resp = await client.options(URL, headers={"Origin": "https://evil.test"})
        assert resp.status_code == 403

    async def test_error_responses_carry_cors_headers(self, app, client):
        await create_form(app, allowed_origins=["https://example.com"])
        resp = await client.post(URL, json={}, headers={"Origin": "https://example.com"})
        assert resp.status_code == 422
        assert resp.headers["access-control-allow-origin"] == "https://example.com"

    async def test_schema_respects_origin_list(self, app, client):
        await create_form(app, allowed_origins=["https://example.com"])
        ok = await client.get("/v1/forms/contact/schema", headers={"Origin": "https://example.com"})
        bad = await client.get("/v1/forms/contact/schema", headers={"Origin": "https://evil.test"})
        assert ok.status_code == 200 and bad.status_code == 403


async def test_html_form_post_redirects_when_configured(app, client):
    await create_form(app, success_redirect_url="https://example.com/thanks")
    resp = await client.post(URL, data=VALID_SUBMISSION, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "https://example.com/thanks"


async def test_json_submission_ignores_redirect(app, client):
    await create_form(app, success_redirect_url="https://example.com/thanks")
    assert (await client.post(URL, json=VALID_SUBMISSION)).status_code == 201


async def test_request_id_header_echoed(app, client):
    await create_form(app)
    resp = await client.post(URL, json=VALID_SUBMISSION, headers={"X-Request-ID": "abc123"})
    assert resp.headers["x-request-id"] == "abc123"

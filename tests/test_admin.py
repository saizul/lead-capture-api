from __future__ import annotations

import csv
import io
from datetime import timedelta

import pytest

from leadcapture.db import utcnow
from leadcapture.models import ApiKey, Form, Lead
from tests.helpers import (
    DEFAULT_FIELDS,
    VALID_SUBMISSION,
    create_api_key,
    create_form,
    create_lead,
    fetch_all,
)

FORM_BODY = {
    "slug": "landing",
    "name": "Landing page",
    "fields": [
        {"name": "name", "type": "text", "required": True},
        {"name": "email", "type": "email", "required": True},
    ],
    "allowed_origins": ["https://example.com/"],
    "notifiers": [{"type": "webhook", "url": "https://hook.example.com/x", "secret": "topsecret"}],
}


# ------------------------------------------------------------ auth


async def test_admin_requires_key(client):
    resp = await client.get("/v1/admin/forms")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    assert resp.headers["www-authenticate"] == "Bearer"


async def test_admin_rejects_wrong_key(client):
    resp = await client.get("/v1/admin/forms", headers={"X-API-Key": "lc_wrong"})
    assert resp.status_code == 401


async def test_admin_accepts_x_api_key(client, admin):
    assert (await client.get("/v1/admin/forms", headers=admin)).status_code == 200


async def test_admin_accepts_bearer_token(client, admin):
    headers = {"Authorization": f"Bearer {admin['X-API-Key']}"}
    assert (await client.get("/v1/admin/forms", headers=headers)).status_code == 200


async def test_revoked_key_is_rejected(app, client):
    key = await create_api_key(app, active=False)
    assert (await client.get("/v1/admin/forms", headers={"X-API-Key": key})).status_code == 401


async def test_key_is_stored_hashed(app, admin):
    stored = (await fetch_all(app, ApiKey))[0]
    assert admin["X-API-Key"] not in (stored.key_hash, stored.prefix, stored.name)
    assert len(stored.key_hash) == 64


async def test_last_used_is_tracked(app, client, admin):
    await client.get("/v1/admin/forms", headers=admin)
    assert (await fetch_all(app, ApiKey))[0].last_used_at is not None


@pytest.mark.parametrize(
    "path",
    [
        "/v1/admin/leads",
        "/v1/admin/leads/export.csv",
        "/v1/admin/deliveries",
        "/v1/admin/outbox",
        "/v1/admin/stats",
        "/v1/admin/stats/daily",
    ],
)
async def test_every_admin_route_is_protected(client, path):
    assert (await client.get(path)).status_code == 401


# ------------------------------------------------------------ forms


async def test_create_form(client, admin):
    resp = await client.post("/v1/admin/forms", json=FORM_BODY, headers=admin)
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "landing" and body["allowed_origins"] == ["https://example.com"]
    assert body["min_submit_seconds"] == 3 and body["honeypot_field"] == "website_url"


async def test_created_form_accepts_submissions(client, admin):
    await client.post("/v1/admin/forms", json={**FORM_BODY, "min_submit_seconds": 0}, headers=admin)
    resp = await client.post("/v1/forms/landing/submit", json={"name": "A", "email": "a@b.co"})
    assert resp.status_code == 201


async def test_notifier_secrets_are_masked(client, admin):
    created = (await client.post("/v1/admin/forms", json=FORM_BODY, headers=admin)).json()
    assert created["notifiers"][0]["secret"] == "***"
    fetched = (await client.get("/v1/admin/forms/landing", headers=admin)).json()
    assert fetched["notifiers"][0]["secret"] == "***"
    listed = (await client.get("/v1/admin/forms", headers=admin)).json()
    assert "topsecret" not in str(listed)


async def test_update_keeps_masked_secret(app, client, admin):
    await client.post("/v1/admin/forms", json=FORM_BODY, headers=admin)
    resp = await client.patch(
        "/v1/admin/forms/landing",
        json={
            "notifiers": [{"type": "webhook", "url": "https://new.example.com/h", "secret": "***"}]
        },
        headers=admin,
    )
    assert resp.status_code == 200
    form = (await fetch_all(app, Form))[0]
    assert form.notifiers[0]["secret"] == "topsecret"
    assert form.notifiers[0]["url"] == "https://new.example.com/h"


async def test_update_rejects_bad_notifier(client, admin):
    await client.post("/v1/admin/forms", json=FORM_BODY, headers=admin)
    resp = await client.patch(
        "/v1/admin/forms/landing",
        json={"notifiers": [{"type": "webhook", "url": "nope"}]},
        headers=admin,
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "invalid_notifiers"


async def test_update_masked_secret_without_stored_value_rejected(client, admin):
    body = {**FORM_BODY, "notifiers": []}
    await client.post("/v1/admin/forms", json=body, headers=admin)
    resp = await client.patch(
        "/v1/admin/forms/landing",
        json={
            "notifiers": [{"type": "webhook", "url": "https://h.example.com/x", "secret": "***"}]
        },
        headers=admin,
    )
    assert resp.status_code == 422


async def test_duplicate_slug_conflict(client, admin):
    await client.post("/v1/admin/forms", json=FORM_BODY, headers=admin)
    resp = await client.post("/v1/admin/forms", json=FORM_BODY, headers=admin)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "slug_taken"


@pytest.mark.parametrize(
    "patch",
    [
        {"slug": "Bad Slug"},
        {"fields": []},
        {"fields": [{"name": "a"}, {"name": "a"}]},
        {"allowed_origins": ["not-an-origin"]},
        {"min_submit_seconds": -1},
        {"honeypot_field": "name"},
        {"notifiers": [{"type": "sms", "to": "1"}]},
        {"success_redirect_url": "javascript:alert(1)"},
    ],
)
async def test_create_form_validation(client, admin, patch):
    resp = await client.post("/v1/admin/forms", json={**FORM_BODY, **patch}, headers=admin)
    assert resp.status_code == 422


async def test_get_missing_form_404(client, admin):
    assert (await client.get("/v1/admin/forms/nope", headers=admin)).status_code == 404


async def test_patch_form(client, admin):
    await client.post("/v1/admin/forms", json=FORM_BODY, headers=admin)
    resp = await client.patch(
        "/v1/admin/forms/landing",
        json={"name": "Renamed", "is_active": False, "allowed_origins": ["http://localhost:3000"]},
        headers=admin,
    )
    body = resp.json()
    assert body["name"] == "Renamed" and body["is_active"] is False
    assert body["allowed_origins"] == ["http://localhost:3000"]
    assert (await client.post("/v1/forms/landing/submit", json={})).status_code == 404


async def test_patch_rejects_invalid_origin(client, admin):
    await client.post("/v1/admin/forms", json=FORM_BODY, headers=admin)
    resp = await client.patch(
        "/v1/admin/forms/landing", json={"allowed_origins": ["ftp://x"]}, headers=admin
    )
    assert resp.status_code == 422


async def test_delete_form_cascades_to_leads(app, client, admin):
    form = await create_form(app)
    await create_lead(app, form)
    assert (await client.delete("/v1/admin/forms/contact", headers=admin)).status_code == 204
    assert await fetch_all(app, Form) == []
    assert await fetch_all(app, Lead) == []


# ------------------------------------------------------------ leads


async def seed(app):
    form = await create_form(app)
    other = await create_form(app, slug="other", name="Other", fields=DEFAULT_FIELDS)
    now = utcnow()
    await create_lead(
        app,
        form,
        name="Alice Smith",
        email="alice@acme.com",
        data={"company": "Acme"},
        created_at=now - timedelta(days=3),
    )
    await create_lead(
        app,
        form,
        name="Bob Jones",
        email="bob@globex.com",
        data={"company": "Globex"},
        status="contacted",
        created_at=now - timedelta(days=2),
    )
    await create_lead(
        app,
        other,
        name="Carol",
        email="carol@initech.com",
        data={"company": "Initech"},
        status="won",
        created_at=now - timedelta(days=1),
    )
    await create_lead(
        app,
        form,
        name="Spammer",
        email="s@spam.test",
        is_spam=True,
        spam_score=90,
        data={"company": "Spam"},
    )
    return form


async def test_list_leads_excludes_spam_by_default(app, client, admin):
    await seed(app)
    page = (await client.get("/v1/admin/leads", headers=admin)).json()
    assert page["total"] == 3
    assert [lead["name"] for lead in page["items"]] == ["Carol", "Bob Jones", "Alice Smith"]


@pytest.mark.parametrize(("spam", "expected"), [("include", 4), ("only", 1), ("exclude", 3)])
async def test_spam_filter(app, client, admin, spam, expected):
    await seed(app)
    page = (await client.get("/v1/admin/leads", params={"spam": spam}, headers=admin)).json()
    assert page["total"] == expected


async def test_filter_by_status(app, client, admin):
    await seed(app)
    page = (
        await client.get("/v1/admin/leads", params={"status": "contacted"}, headers=admin)
    ).json()
    assert [lead["name"] for lead in page["items"]] == ["Bob Jones"]


async def test_filter_by_form(app, client, admin):
    await seed(app)
    page = (await client.get("/v1/admin/leads", params={"form": "other"}, headers=admin)).json()
    assert page["total"] == 1 and page["items"][0]["form"] == "other"


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("alice", ["Alice Smith"]),
        ("GLOBEX", ["Bob Jones"]),
        ("initech", ["Carol"]),
        ("nomatch", []),
    ],
)
async def test_search(app, client, admin, q, expected):
    await seed(app)
    page = (await client.get("/v1/admin/leads", params={"q": q}, headers=admin)).json()
    assert [lead["name"] for lead in page["items"]] == expected


async def test_search_treats_wildcards_literally(app, client, admin):
    await seed(app)
    page = (await client.get("/v1/admin/leads", params={"q": "%"}, headers=admin)).json()
    assert page["total"] == 0


async def test_filter_by_date(app, client, admin):
    await seed(app)
    since = (utcnow() - timedelta(days=2, hours=12)).isoformat()
    page = (
        await client.get("/v1/admin/leads", params={"created_from": since}, headers=admin)
    ).json()
    assert page["total"] == 2


async def test_pagination(app, client, admin):
    await seed(app)
    page = (
        await client.get("/v1/admin/leads", params={"limit": 2, "offset": 2}, headers=admin)
    ).json()
    assert page["total"] == 3 and len(page["items"]) == 1 and page["limit"] == 2


async def test_pagination_bounds(client, admin):
    assert (
        await client.get("/v1/admin/leads", params={"limit": 0}, headers=admin)
    ).status_code == 422
    assert (
        await client.get("/v1/admin/leads", params={"limit": 1000}, headers=admin)
    ).status_code == 422


async def test_lead_detail_and_404(app, client, admin):
    form = await create_form(app)
    lead = await create_lead(app, form)
    got = await client.get(f"/v1/admin/leads/{lead.id}", headers=admin)
    assert got.status_code == 200 and got.json()["email"] == "test@example.com"
    assert "ip_hash" not in got.json()
    assert (await client.get("/v1/admin/leads/missing", headers=admin)).status_code == 404


@pytest.mark.parametrize("status", ["contacted", "won", "lost", "new"])
async def test_mark_lead_status(app, client, admin, status):
    lead = await create_lead(app, await create_form(app))
    resp = await client.patch(f"/v1/admin/leads/{lead.id}", json={"status": status}, headers=admin)
    assert resp.status_code == 200 and resp.json()["status"] == status
    assert (await fetch_all(app, Lead))[0].status == status


async def test_invalid_status_rejected(app, client, admin):
    lead = await create_lead(app, await create_form(app))
    resp = await client.patch(
        f"/v1/admin/leads/{lead.id}", json={"status": "archived"}, headers=admin
    )
    assert resp.status_code == 422


async def test_delete_lead(app, client, admin):
    lead = await create_lead(app, await create_form(app))
    assert (await client.delete(f"/v1/admin/leads/{lead.id}", headers=admin)).status_code == 204
    assert (await client.delete(f"/v1/admin/leads/{lead.id}", headers=admin)).status_code == 404


async def test_stats(app, client, admin):
    await seed(app)
    stats = (await client.get("/v1/admin/stats", headers=admin)).json()
    assert stats == {"by_status": {"new": 1, "contacted": 1, "won": 1, "lost": 0}, "spam": 1}


async def test_daily_stats_is_zero_filled_and_per_form(app, client, admin):
    await seed(app)
    data = (await client.get("/v1/admin/stats/daily", params={"days": 5}, headers=admin)).json()
    today = utcnow().date()
    assert data["days"] == 5 and data["spam"] == "exclude"
    assert data["dates"] == [(today - timedelta(days=n)).isoformat() for n in (4, 3, 2, 1, 0)]
    # seed(): 2 leads on the first form (3 and 2 days ago), 1 on "other" (yesterday); spam excluded
    assert data["total"] == [0, 1, 1, 1, 0]
    assert sorted(data["forms"]) == ["contact", "other"]
    assert sum(sum(counts) for counts in data["forms"].values()) == 3
    assert all(len(counts) == 5 for counts in data["forms"].values())


async def test_daily_stats_window_and_spam_filter(app, client, admin):
    await seed(app)
    narrow = (await client.get("/v1/admin/stats/daily", params={"days": 1}, headers=admin)).json()
    assert narrow["total"] == [0]
    spam = (
        await client.get("/v1/admin/stats/daily", params={"spam": "only"}, headers=admin)
    ).json()
    assert spam["days"] == 30 and sum(spam["total"]) == 1
    everything = (
        await client.get("/v1/admin/stats/daily", params={"spam": "include"}, headers=admin)
    ).json()
    assert sum(everything["total"]) == 4


async def test_daily_stats_form_filter(app, client, admin):
    await seed(app)
    resp = await client.get(
        "/v1/admin/stats/daily", params={"form": "other", "days": 3}, headers=admin
    )
    data = resp.json()
    assert list(data["forms"]) == ["other"] and data["total"] == [0, 1, 0]
    missing = await client.get("/v1/admin/stats/daily", params={"form": "nope"}, headers=admin)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "form_not_found"


@pytest.mark.parametrize("days", [0, 367])
async def test_daily_stats_rejects_bad_window(client, admin, days):
    resp = await client.get("/v1/admin/stats/daily", params={"days": days}, headers=admin)
    assert resp.status_code in (400, 422)


# ------------------------------------------------------------ CSV


def parse_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


async def test_csv_export(app, client, admin):
    await seed(app)
    resp = await client.get("/v1/admin/leads/export.csv", headers=admin)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    rows = parse_csv(resp.text)
    assert len(rows) == 3
    assert {"id", "created_at", "form", "status", "name", "email", "data.company"} <= set(rows[0])
    assert {r["email"] for r in rows} == {"alice@acme.com", "bob@globex.com", "carol@initech.com"}
    assert {r["data.company"] for r in rows} == {"Acme", "Globex", "Initech"}


async def test_csv_export_respects_filters(app, client, admin):
    await seed(app)
    resp = await client.get("/v1/admin/leads/export.csv", params={"status": "won"}, headers=admin)
    rows = parse_csv(resp.text)
    assert [r["name"] for r in rows] == ["Carol"] and rows[0]["form"] == "other"


async def test_csv_export_empty(client, admin):
    resp = await client.get("/v1/admin/leads/export.csv", headers=admin)
    assert resp.status_code == 200 and parse_csv(resp.text) == []
    assert resp.text.startswith("id,created_at")


async def test_csv_neutralises_formula_injection(app, client, admin):
    form = await create_form(app)
    await create_lead(
        app, form, name='=HYPERLINK("http://evil")', email="+cmd@x.com", data={"note": "@SUM(1)"}
    )
    rows = parse_csv((await client.get("/v1/admin/leads/export.csv", headers=admin)).text)
    assert rows[0]["name"].startswith("'=")
    assert rows[0]["email"].startswith("'+")
    assert rows[0]["data.note"].startswith("'@")


async def test_csv_quotes_commas_and_newlines(app, client, admin):
    form = await create_form(app)
    await create_lead(app, form, data={"message": "line1\nline2, with comma"})
    rows = parse_csv((await client.get("/v1/admin/leads/export.csv", headers=admin)).text)
    assert rows[0]["data.message"] == "line1\nline2, with comma"


# ------------------------------------------------------------ deliveries


async def test_delivery_logs_and_retry(app, client, admin, mock_http):
    route = mock_http.post("https://hook.example.com/x").respond(500)
    await create_form(app, notifiers=[{"type": "webhook", "url": "https://hook.example.com/x"}])
    lead_id = (await client.post("/v1/forms/contact/submit", json=VALID_SUBMISSION)).json()["id"]

    logs = (
        await client.get("/v1/admin/deliveries", params={"lead_id": lead_id}, headers=admin)
    ).json()
    assert len(logs) == 1 and logs[0]["success"] is False and logs[0]["status_code"] == 500

    failed = (
        await client.get("/v1/admin/deliveries", params={"success": "false"}, headers=admin)
    ).json()
    assert len(failed) == 1
    assert (
        await client.get("/v1/admin/deliveries", params={"success": "true"}, headers=admin)
    ).json() == []

    outbox = (
        await client.get("/v1/admin/outbox", params={"status": "pending"}, headers=admin)
    ).json()
    assert len(outbox) == 1 and outbox[0]["attempts"] == 1

    route.respond(200)
    retried = await client.post(f"/v1/admin/outbox/{outbox[0]['id']}/retry", headers=admin)
    assert retried.status_code == 200 and retried.json()["status"] == "pending"

    from leadcapture.services.outbox import process_due

    assert await process_due(app.state.session_factory, app.state.settings) == 1
    done = (
        await client.get("/v1/admin/outbox", params={"status": "delivered"}, headers=admin)
    ).json()
    assert len(done) == 1


async def test_cannot_retry_delivered_event(app, client, admin, mock_http):
    mock_http.post("https://hook.example.com/x").respond(200)
    await create_form(app, notifiers=[{"type": "webhook", "url": "https://hook.example.com/x"}])
    await client.post("/v1/forms/contact/submit", json=VALID_SUBMISSION)
    event_id = (await client.get("/v1/admin/outbox", headers=admin)).json()[0]["id"]
    resp = await client.post(f"/v1/admin/outbox/{event_id}/retry", headers=admin)
    assert resp.status_code == 409


async def test_retry_unknown_event_404(client, admin):
    assert (await client.post("/v1/admin/outbox/nope/retry", headers=admin)).status_code == 404

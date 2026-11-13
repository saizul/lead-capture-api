from __future__ import annotations

import csv
import io
import sqlite3

import pytest
from typer.testing import CliRunner

from leadcapture.cli import app as cli_app
from leadcapture.models import Lead
from leadcapture.schemas import MAX_TAGS_PER_LEAD, normalize_tags
from leadcapture.services.leads import merge_tags
from tests.helpers import create_form, create_lead, fetch_all

# ------------------------------------------------------------ helpers


def test_normalize_tags_lowercases_trims_and_dedupes():
    assert normalize_tags([" VIP ", "vip", "Campaign:Spring", "q4"]) == [
        "vip",
        "campaign:spring",
        "q4",
    ]


@pytest.mark.parametrize("bad", ["", " ", "-leading", "has space", "a" * 33, "emoji-✨", '"q"'])
def test_normalize_tags_rejects_invalid(bad):
    with pytest.raises(ValueError):
        normalize_tags([bad])


def test_merge_tags():
    assert merge_tags(["b", "a"], add=["c"], remove=["a"]) == ["b", "c"]
    assert merge_tags(["b", "a"], replace=["x"], add=["y"]) == ["x", "y"]
    assert merge_tags(["a"], replace=[]) == []


# ------------------------------------------------------------ PATCH /leads/{id}


async def patch(client, admin, lead_id, body):
    return await client.patch(f"/v1/admin/leads/{lead_id}", json=body, headers=admin)


async def test_new_lead_has_no_tags_or_note(app, client, admin):
    lead = await create_lead(app, await create_form(app))
    body = (await client.get(f"/v1/admin/leads/{lead.id}", headers=admin)).json()
    assert body["tags"] == [] and body["note"] is None


async def test_add_and_remove_tags(app, client, admin):
    lead = await create_lead(app, await create_form(app))
    resp = await patch(client, admin, lead.id, {"add_tags": ["VIP", "agency"]})
    assert resp.status_code == 200 and resp.json()["tags"] == ["agency", "vip"]
    resp = await patch(client, admin, lead.id, {"add_tags": ["q4"], "remove_tags": ["agency"]})
    assert resp.json()["tags"] == ["q4", "vip"]
    # status untouched by a tag-only update
    assert resp.json()["status"] == "new"
    assert (await fetch_all(app, Lead))[0].tags == ["q4", "vip"]


async def test_replace_tags(app, client, admin):
    lead = await create_lead(app, await create_form(app), tags=["old"])
    resp = await patch(client, admin, lead.id, {"tags": ["new", "New"]})
    assert resp.json()["tags"] == ["new"]
    resp = await patch(client, admin, lead.id, {"tags": []})
    assert resp.json()["tags"] == []


async def test_status_tags_and_note_together(app, client, admin):
    lead = await create_lead(app, await create_form(app))
    resp = await patch(
        client,
        admin,
        lead.id,
        {"status": "contacted", "add_tags": ["vip"], "note": "  Called, send a quote.  "},
    )
    body = resp.json()
    assert body["status"] == "contacted" and body["tags"] == ["vip"]
    assert body["note"] == "Called, send a quote."


@pytest.mark.parametrize("cleared", [None, "", "   "])
async def test_clear_note(app, client, admin, cleared):
    lead = await create_lead(app, await create_form(app), note="keep me?")
    resp = await patch(client, admin, lead.id, {"note": cleared})
    assert resp.status_code == 200 and resp.json()["note"] is None


async def test_note_survives_unrelated_update(app, client, admin):
    lead = await create_lead(app, await create_form(app), note="important")
    resp = await patch(client, admin, lead.id, {"status": "won"})
    assert resp.json()["note"] == "important"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"status": None},
        {"tags": None},
        {"tags": ["a"], "add_tags": ["b"]},
        {"add_tags": ["a"], "remove_tags": ["A"]},
        {"add_tags": ["not valid"]},
        {"note": "x" * 5001},
        {"tags": [f"t{i}" for i in range(MAX_TAGS_PER_LEAD + 1)]},
    ],
)
async def test_patch_validation(app, client, admin, body):
    lead = await create_lead(app, await create_form(app))
    assert (await patch(client, admin, lead.id, body)).status_code == 422


async def test_tag_limit_applies_to_the_merged_list(app, client, admin):
    full = [f"t{i:02d}" for i in range(MAX_TAGS_PER_LEAD)]
    lead = await create_lead(app, await create_form(app), tags=full)
    resp = await patch(client, admin, lead.id, {"add_tags": ["one-more"]})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "too_many_tags"
    # re-adding an existing tag is fine
    assert (await patch(client, admin, lead.id, {"add_tags": ["t00"]})).status_code == 200


async def test_patch_unknown_lead_404(client, admin):
    assert (await patch(client, admin, "missing", {"add_tags": ["x"]})).status_code == 404


# ------------------------------------------------------------ filtering and export


async def seed_tagged(app):
    form = await create_form(app)
    other = await create_form(app, slug="other", name="Other")
    await create_lead(app, form, name="Alice", tags=["vip", "agency"])
    await create_lead(app, form, name="Bob", tags=["vip"])
    await create_lead(app, other, name="Carol", tags=["agency", "vip_2"])
    await create_lead(app, form, name="Dan")
    await create_lead(app, form, name="Spam", tags=["vip"], is_spam=True)


async def names(client, admin, params):
    page = (await client.get("/v1/admin/leads", params=params, headers=admin)).json()
    return sorted(lead["name"] for lead in page["items"])


async def test_filter_by_tag(app, client, admin):
    await seed_tagged(app)
    assert await names(client, admin, {"tag": "vip"}) == ["Alice", "Bob"]
    assert await names(client, admin, {"tag": "AGENCY"}) == ["Alice", "Carol"]
    assert await names(client, admin, {"tag": ["vip", "agency"]}) == ["Alice"]
    assert await names(client, admin, {"tag": "vip", "spam": "include"}) == ["Alice", "Bob", "Spam"]
    assert await names(client, admin, {"tag": "none"}) == []


async def test_tag_filter_matches_whole_tags_only(app, client, admin):
    await seed_tagged(app)
    # "vip" must not match "vip_2", and "_" is not a LIKE wildcard here
    assert await names(client, admin, {"tag": "vip_2"}) == ["Carol"]
    assert await names(client, admin, {"tag": "vi"}) == []
    assert await names(client, admin, {"tag": "vip_"}) == []


async def test_invalid_tag_filter_rejected(client, admin):
    resp = await client.get("/v1/admin/leads", params={"tag": "bad tag"}, headers=admin)
    assert resp.status_code == 422


async def test_csv_export_includes_tags_and_note(app, client, admin):
    form = await create_form(app)
    await create_lead(app, form, tags=["agency", "vip"], note="=cmd")
    await create_lead(app, form, name="Untagged")
    resp = await client.get("/v1/admin/leads/export.csv", params={"tag": "vip"}, headers=admin)
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert len(rows) == 1
    assert rows[0]["tags"] == "agency;vip"
    assert rows[0]["note"] == "'=cmd"  # formula-injection guard applies to notes too


async def test_tag_counts(app, client, admin):
    await seed_tagged(app)
    resp = await client.get("/v1/admin/tags", headers=admin)
    assert resp.status_code == 200
    assert resp.json() == [
        {"tag": "agency", "count": 2},
        {"tag": "vip", "count": 2},
        {"tag": "vip_2", "count": 1},
    ]
    resp = await client.get("/v1/admin/tags", params={"form": "other"}, headers=admin)
    assert [t["tag"] for t in resp.json()] == ["agency", "vip_2"]
    resp = await client.get("/v1/admin/tags", params={"spam": "only"}, headers=admin)
    assert resp.json() == [{"tag": "vip", "count": 1}]


async def test_tag_counts_empty(client, admin):
    assert (await client.get("/v1/admin/tags", headers=admin)).json() == []


# ------------------------------------------------------------ bulk update


async def bulk(client, admin, body):
    return await client.post("/v1/admin/leads/bulk-update", json=body, headers=admin)


async def test_bulk_update(app, client, admin):
    form = await create_form(app)
    a = await create_lead(app, form, tags=["old"])
    b = await create_lead(app, form)
    untouched = await create_lead(app, form)
    resp = await bulk(
        client,
        admin,
        {
            "ids": [a.id, b.id, "missing", a.id],
            "status": "contacted",
            "add_tags": ["Campaign:Spring"],
            "remove_tags": ["old"],
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"updated": 2, "not_found": ["missing"]}
    leads = {lead.id: lead for lead in await fetch_all(app, Lead)}
    for lead_id in (a.id, b.id):
        assert leads[lead_id].status == "contacted"
        assert leads[lead_id].tags == ["campaign:spring"]
    assert leads[untouched.id].status == "new" and leads[untouched.id].tags == []


async def test_bulk_update_is_all_or_nothing(app, client, admin):
    form = await create_form(app)
    ok = await create_lead(app, form)
    full = await create_lead(app, form, tags=[f"t{i:02d}" for i in range(MAX_TAGS_PER_LEAD)])
    resp = await bulk(client, admin, {"ids": [ok.id, full.id], "add_tags": ["new"]})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "too_many_tags"
    leads = {lead.id: lead for lead in await fetch_all(app, Lead)}
    assert leads[ok.id].tags == []


@pytest.mark.parametrize(
    "body",
    [
        {"ids": [], "status": "won"},
        {"ids": ["x"]},
        {"ids": ["x"], "add_tags": ["a"], "remove_tags": ["a"]},
        {"ids": [str(i) for i in range(501)], "status": "won"},
        {"ids": ["x"], "status": "archived"},
    ],
)
async def test_bulk_update_validation(client, admin, body):
    assert (await bulk(client, admin, body)).status_code == 422


async def test_bulk_update_requires_admin(client):
    resp = await client.post("/v1/admin/leads/bulk-update", json={"ids": ["x"], "status": "won"})
    assert resp.status_code == 401


# ------------------------------------------------------------ migration


def test_migration_adds_columns_with_defaults_for_existing_leads(cli_env):
    runner = CliRunner()
    assert runner.invoke(cli_app, ["migrate", "0001"]).exit_code == 0
    con = sqlite3.connect(cli_env)
    try:
        con.execute(
            "INSERT INTO forms (id, slug, name, fields, allowed_origins, notifiers, honeypot_field,"
            " min_submit_seconds, require_consent, spam_threshold, blocked_words,"
            " rate_limit_per_ip, rate_limit_per_form, is_active, created_at, updated_at)"
            " VALUES ('f1', 'contact', 'Contact', '[]', '[]', '[]', 'website_url', 0, 0, 50, '[]',"
            " 10, 300, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        con.execute(
            "INSERT INTO leads (id, form_id, data, consent, spam_score, is_spam, spam_reasons,"
            " status, created_at, updated_at) VALUES ('l1', 'f1', '{}', 0, 0, 0, '[]', 'new',"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        con.commit()
    finally:
        con.close()

    result = runner.invoke(cli_app, ["migrate"])
    assert result.exit_code == 0, result.output
    con = sqlite3.connect(cli_env)
    try:
        assert con.execute("SELECT tags, note FROM leads").fetchall() == [("[]", None)]
    finally:
        con.close()

from __future__ import annotations

from datetime import timedelta

import pytest

from leadcapture.db import utcnow
from leadcapture.services.stats import spam_rate, spam_rate_series
from tests.helpers import create_form, create_lead

DATES = ["2026-01-01", "2026-01-02", "2026-01-03"]


# ------------------------------------------------------------ pure helpers


@pytest.mark.parametrize(
    ("total", "spam", "expected"),
    [(0, 0, None), (-1, 0, None), (4, 0, 0.0), (4, 1, 0.25), (3, 1, 0.3333), (5, 5, 1.0)],
)
def test_spam_rate(total, spam, expected):
    assert spam_rate(total, spam) == expected


def test_series_is_zero_filled_and_gaps_are_null():
    rows = [
        ("contact", "2026-01-01", False, 3),
        ("contact", "2026-01-01", True, 1),
        ("contact", "2026-01-03", True, 2),
        ("other", "2026-01-01", False, 1),
    ]
    out = spam_rate_series(rows, DATES)
    contact = out["forms"]["contact"]
    assert contact["total"] == [4, 0, 2]
    assert contact["spam"] == [1, 0, 2]
    assert contact["rate"] == [0.25, None, 1.0]
    assert contact["overall_rate"] == pytest.approx(0.5)
    assert out["overall"]["total"] == [5, 0, 2]
    assert out["overall"]["rate"] == [0.2, None, 1.0]
    assert list(out["forms"]) == ["contact", "other"]


def test_series_ignores_days_outside_the_window():
    out = spam_rate_series(
        [("contact", "2025-12-31", True, 9), ("contact", "2026-01-02", False, 1)], DATES
    )
    assert out["forms"]["contact"]["total"] == [0, 1, 0]
    assert out["overall"]["overall_rate"] == 0.0


def test_empty_series():
    out = spam_rate_series([], DATES)
    assert out["forms"] == {}
    assert out["overall"]["rate"] == [None, None, None]
    assert out["overall"]["overall_rate"] is None


# ------------------------------------------------------------ endpoint


async def seed_spam(app):
    contact = await create_form(app)
    other = await create_form(app, slug="other", name="Other")
    now = utcnow()
    await create_lead(app, contact, created_at=now - timedelta(days=1))
    await create_lead(app, contact, created_at=now - timedelta(days=1))
    await create_lead(app, contact, created_at=now - timedelta(days=1), is_spam=True)
    await create_lead(app, contact, created_at=now, is_spam=True)
    await create_lead(app, other, created_at=now)
    await create_lead(app, contact, created_at=now - timedelta(days=40))  # outside a 30 day window


async def test_spam_rate_endpoint(app, client, admin):
    await seed_spam(app)
    data = (await client.get("/v1/admin/stats/spam-rate", params={"days": 3}, headers=admin)).json()
    assert data["days"] == 3 and len(data["dates"]) == 3
    assert data["dates"][-1] == utcnow().date().isoformat()
    contact = data["forms"]["contact"]
    assert contact["total"] == [0, 3, 1]
    assert contact["spam"] == [0, 1, 1]
    assert contact["rate"] == [None, 0.3333, 1.0]
    assert data["forms"]["other"]["rate"] == [None, None, 0.0]
    assert data["overall"]["total"] == [0, 3, 2]
    assert data["overall"]["overall_rate"] == 0.4


async def test_spam_rate_defaults_to_30_days(app, client, admin):
    await seed_spam(app)
    data = (await client.get("/v1/admin/stats/spam-rate", headers=admin)).json()
    assert data["days"] == 30 and sum(data["overall"]["total"]) == 5


async def test_spam_rate_form_filter(app, client, admin):
    await seed_spam(app)
    data = (
        await client.get("/v1/admin/stats/spam-rate", params={"form": "other"}, headers=admin)
    ).json()
    assert list(data["forms"]) == ["other"]
    assert data["overall"]["overall_rate"] == 0.0
    missing = await client.get("/v1/admin/stats/spam-rate", params={"form": "nope"}, headers=admin)
    assert missing.status_code == 404


@pytest.mark.parametrize("days", [0, 367])
async def test_spam_rate_validates_days(client, admin, days):
    resp = await client.get("/v1/admin/stats/spam-rate", params={"days": days}, headers=admin)
    assert resp.status_code == 422

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from httpx import Response

from leadcapture.db import utcnow
from leadcapture.models import DeliveryLog, Lead, OutboxEvent
from leadcapture.services.outbox import compute_backoff, deliver_event, process_due
from tests.helpers import VALID_SUBMISSION, create_form, fetch_all

HOOK = "https://hook.example.com/leads"
URL = "/v1/forms/contact/submit"
WEBHOOK = {"type": "webhook", "url": HOOK, "secret": "s"}


@pytest.fixture
def settings_overrides():
    return {
        "outbox_max_attempts": 3,
        "outbox_backoff_base_seconds": 30,
        "outbox_backoff_max_seconds": 3600,
    }


def test_backoff_grows_exponentially_without_jitter():
    delays = [compute_backoff(n, 30, 3600, jitter=0) for n in range(1, 6)]
    assert delays == [30, 60, 120, 240, 480]


def test_backoff_is_capped():
    assert compute_backoff(20, 30, 3600, jitter=0) == 3600


def test_backoff_jitter_bounds():
    low = compute_backoff(1, 100, 1000, jitter=0.1, rng=lambda: 0.0)
    high = compute_backoff(1, 100, 1000, jitter=0.1, rng=lambda: 1.0)
    assert low == pytest.approx(90) and high == pytest.approx(110)


async def test_notification_is_delivered_in_background(app, client, mock_http):
    route = mock_http.post(HOOK).respond(200)
    await create_form(app, notifiers=[WEBHOOK])
    assert (await client.post(URL, json=VALID_SUBMISSION)).status_code == 201
    assert route.call_count == 1
    event = (await fetch_all(app, OutboxEvent))[0]
    assert event.status == "delivered" and event.attempts == 1 and event.delivered_at
    log = (await fetch_all(app, DeliveryLog))[0]
    assert log.success and log.status_code == 200 and log.attempt == 1


async def test_payload_has_lead_data_but_no_ip(app, client, mock_http):
    route = mock_http.post(HOOK).respond(200)
    await create_form(app, notifiers=[WEBHOOK])
    await client.post(URL, json=VALID_SUBMISSION)
    sent = route.calls.last.request.content.decode()
    assert "ada@example.com" in sent and "127.0.0.1" not in sent and "ip_hash" not in sent


async def test_one_event_per_enabled_notifier(app, client, mock_http):
    mock_http.post(HOOK).respond(200)
    mock_http.post("https://hooks.slack.com/services/T/B/X").respond(200)
    await create_form(
        app,
        notifiers=[
            WEBHOOK,
            {"type": "slack", "webhook_url": "https://hooks.slack.com/services/T/B/X"},
            {"type": "webhook", "url": "https://disabled.example.com", "enabled": False},
        ],
    )
    await client.post(URL, json=VALID_SUBMISSION)
    events = await fetch_all(app, OutboxEvent)
    assert sorted(e.notifier_type for e in events) == ["slack", "webhook"]


async def test_failed_delivery_is_scheduled_for_retry(app, client, mock_http):
    mock_http.post(HOOK).respond(503)
    await create_form(app, notifiers=[WEBHOOK])
    resp = await client.post(URL, json=VALID_SUBMISSION)
    assert resp.status_code == 201  # the visitor is never blocked by a failing integration
    event = (await fetch_all(app, OutboxEvent))[0]
    assert event.status == "pending" and event.attempts == 1
    assert "503" in event.last_error
    delay = (event.next_attempt_at - utcnow()).total_seconds()
    assert 20 < delay < 40


async def test_retry_succeeds_after_backoff(app, client, mock_http):
    route = mock_http.post(HOOK).mock(side_effect=[Response(500), Response(502), Response(200)])
    await create_form(app, notifiers=[WEBHOOK])
    await client.post(URL, json=VALID_SUBMISSION)
    sf, settings = app.state.session_factory, app.state.settings

    # Not due yet: nothing happens.
    assert await process_due(sf, settings, now=utcnow() + timedelta(seconds=5)) == 0
    assert route.call_count == 1

    assert await process_due(sf, settings, now=utcnow() + timedelta(minutes=5)) == 1
    event = (await fetch_all(app, OutboxEvent))[0]
    assert event.status == "pending" and event.attempts == 2

    assert await process_due(sf, settings, now=utcnow() + timedelta(hours=1)) == 1
    event = (await fetch_all(app, OutboxEvent))[0]
    assert event.status == "delivered" and event.attempts == 3 and event.last_error is None
    logs = await fetch_all(app, DeliveryLog)
    assert sorted((entry.attempt, entry.success) for entry in logs) == [
        (1, False),
        (2, False),
        (3, True),
    ]


async def test_backoff_doubles_between_attempts(app, client, mock_http):
    mock_http.post(HOOK).respond(500)
    await create_form(app, notifiers=[WEBHOOK])
    await client.post(URL, json=VALID_SUBMISSION)
    sf, settings = app.state.session_factory, app.state.settings
    base = utcnow() + timedelta(minutes=10)
    await process_due(sf, settings, now=base)
    event = (await fetch_all(app, OutboxEvent))[0]
    gap = (event.next_attempt_at - base).total_seconds()
    assert 54 < gap < 66  # ~60s for the second failure


async def test_event_becomes_dead_after_max_attempts(app, client, mock_http):
    mock_http.post(HOOK).respond(500)
    await create_form(app, notifiers=[WEBHOOK])
    await client.post(URL, json=VALID_SUBMISSION)
    sf, settings = app.state.session_factory, app.state.settings
    for hours in (1, 2):
        await process_due(sf, settings, now=utcnow() + timedelta(hours=hours))
    event = (await fetch_all(app, OutboxEvent))[0]
    assert event.status == "dead" and event.attempts == 3
    assert await process_due(sf, settings, now=utcnow() + timedelta(days=1)) == 0


async def test_permanent_failure_is_not_retried(app, client, mock_http):
    route = mock_http.post(HOOK).respond(404)
    await create_form(app, notifiers=[WEBHOOK])
    await client.post(URL, json=VALID_SUBMISSION)
    event = (await fetch_all(app, OutboxEvent))[0]
    assert event.status == "dead" and event.attempts == 1 and route.call_count == 1


async def test_network_errors_are_retried(app, client, mock_http):
    mock_http.post(HOOK).mock(side_effect=httpx.ConnectError("down"))
    await create_form(app, notifiers=[WEBHOOK])
    await client.post(URL, json=VALID_SUBMISSION)
    event = (await fetch_all(app, OutboxEvent))[0]
    assert event.status == "pending" and "network error" in event.last_error


async def test_one_failing_notifier_does_not_block_others(app, client, mock_http):
    mock_http.post(HOOK).respond(500)
    slack = mock_http.post("https://hooks.slack.com/services/T/B/X").respond(200)
    await create_form(
        app,
        notifiers=[
            WEBHOOK,
            {"type": "slack", "webhook_url": "https://hooks.slack.com/services/T/B/X"},
        ],
    )
    await client.post(URL, json=VALID_SUBMISSION)
    statuses = {e.notifier_type: e.status for e in await fetch_all(app, OutboxEvent)}
    assert statuses == {"webhook": "pending", "slack": "delivered"}
    assert slack.called


async def test_event_cannot_be_delivered_twice(app, client, mock_http):
    route = mock_http.post(HOOK).respond(200)
    await create_form(app, notifiers=[WEBHOOK])
    await client.post(URL, json=VALID_SUBMISSION)
    event = (await fetch_all(app, OutboxEvent))[0]
    again = await deliver_event(app.state.session_factory, app.state.settings, event.id)
    assert again is None and route.call_count == 1


async def test_stale_processing_events_are_reclaimed(app, client, mock_http):
    route = mock_http.post(HOOK).respond(200)
    await create_form(app, notifiers=[WEBHOOK])
    await client.post(URL, json=VALID_SUBMISSION)
    sf, settings = app.state.session_factory, app.state.settings
    async with sf() as session:
        event = (await session.execute(__import__("sqlalchemy").select(OutboxEvent))).scalar_one()
        event.status, event.locked_at, event.attempts = (
            "processing",
            utcnow() - timedelta(hours=1),
            0,
        )
        await session.commit()
    assert await process_due(sf, settings) == 1
    assert route.call_count == 2
    assert (await fetch_all(app, OutboxEvent))[0].status == "delivered"


async def test_unknown_notifier_type_goes_dead(app, client):
    await create_form(app, notifiers=[{"type": "carrier-pigeon"}])
    await client.post(URL, json=VALID_SUBMISSION)
    event = (await fetch_all(app, OutboxEvent))[0]
    assert event.status == "dead" and "unknown notifier" in event.last_error


async def test_deleting_lead_removes_outbox_and_logs(app, client, mock_http, admin):
    mock_http.post(HOOK).respond(200)
    await create_form(app, notifiers=[WEBHOOK])
    lead_id = (await client.post(URL, json=VALID_SUBMISSION)).json()["id"]
    assert (await client.delete(f"/v1/admin/leads/{lead_id}", headers=admin)).status_code == 204
    assert await fetch_all(app, Lead) == []
    assert await fetch_all(app, OutboxEvent) == []
    assert await fetch_all(app, DeliveryLog) == []

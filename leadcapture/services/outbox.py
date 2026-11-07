"""Transactional outbox: notifications are persisted with the lead, then delivered with retries."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from leadcapture.config import Settings
from leadcapture.db import utcnow
from leadcapture.models import DeliveryLog, Form, Lead, OutboxEvent, OutboxStatus
from leadcapture.notifiers import DeliveryError, get_notifier

logger = logging.getLogger("leadcapture.outbox")

SessionFactory = async_sessionmaker[AsyncSession]


def compute_backoff(
    attempt: int,
    base: float,
    maximum: float,
    *,
    jitter: float = 0.1,
    rng: Callable[[], float] = random.random,
) -> float:
    """Exponential backoff: base * 2^(attempt-1), capped, with +/- jitter fraction."""
    delay = min(base * (2 ** max(attempt - 1, 0)), maximum)
    if jitter:
        delay *= 1 + jitter * (2 * rng() - 1)
    return max(delay, 0.0)


def build_payload(form: Form, lead: Lead) -> dict[str, Any]:
    return {
        "event": "lead.created",
        "id": lead.id,
        "form": {"slug": form.slug, "name": form.name},
        "created_at": lead.created_at.isoformat(),
        "name": lead.name,
        "email": lead.email,
        "consent": lead.consent,
        "data": lead.data,
        "source_url": lead.source_url,
        "spam_score": lead.spam_score,
    }


def enqueue_notifications(
    session: AsyncSession, form: Form, lead: Lead, settings: Settings
) -> list[OutboxEvent]:
    """Add one outbox row per enabled notifier. Caller commits (same transaction as the lead)."""
    payload = build_payload(form, lead)
    events = []
    for cfg in form.notifiers:
        if not cfg.get("enabled", True):
            continue
        event = OutboxEvent(
            lead_id=lead.id,
            notifier_type=str(cfg.get("type")),
            config=dict(cfg),
            payload=payload,
            max_attempts=settings.outbox_max_attempts,
            next_attempt_at=utcnow(),
        )
        session.add(event)
        events.append(event)
    return events


async def _claim(session: AsyncSession, event_id: str, now: datetime) -> bool:
    result = await session.execute(
        update(OutboxEvent)
        .where(OutboxEvent.id == event_id, OutboxEvent.status == OutboxStatus.pending.value)
        .values(status=OutboxStatus.processing.value, locked_at=now)
    )
    await session.commit()
    return bool(getattr(result, "rowcount", 0))


async def deliver_event(
    session_factory: SessionFactory,
    settings: Settings,
    event_id: str,
    now: datetime | None = None,
) -> str | None:
    """Attempt one delivery. Returns the new status, or None if the event was not claimable."""
    now = now or utcnow()
    async with session_factory() as session:
        if not await _claim(session, event_id, now):
            return None
        event = await session.get(OutboxEvent, event_id)
        if event is None:  # pragma: no cover - deleted between claim and load
            return None

        started = time.perf_counter()
        success = False
        status_code: int | None = None
        error: str | None = None
        retryable = True
        try:
            notifier = get_notifier(event.notifier_type, settings)
            result = await notifier.send(event.config, event.payload, event.id)
            success = True
            status_code = result.status_code
        except DeliveryError as exc:
            error, retryable, status_code = str(exc), exc.retryable, exc.status_code
        except Exception as exc:  # unexpected notifier bug: treat as retryable
            logger.exception("notifier crashed", extra={"outbox_id": event.id})
            error = f"{exc.__class__.__name__}: {exc}"
        duration_ms = int((time.perf_counter() - started) * 1000)

        event.attempts += 1
        event.locked_at = None
        session.add(
            DeliveryLog(
                outbox_id=event.id,
                lead_id=event.lead_id,
                notifier_type=event.notifier_type,
                attempt=event.attempts,
                success=success,
                status_code=status_code,
                error=error,
                duration_ms=duration_ms,
            )
        )
        if success:
            event.status = OutboxStatus.delivered.value
            event.delivered_at = now
            event.last_error = None
        else:
            event.last_error = error
            if not retryable or event.attempts >= event.max_attempts:
                event.status = OutboxStatus.dead.value
            else:
                event.status = OutboxStatus.pending.value
                delay = compute_backoff(
                    event.attempts,
                    settings.outbox_backoff_base_seconds,
                    settings.outbox_backoff_max_seconds,
                )
                event.next_attempt_at = now + timedelta(seconds=delay)
        await session.commit()
        logger.info(
            "delivery attempt",
            extra={
                "outbox_id": event.id,
                "notifier": event.notifier_type,
                "attempt": event.attempts,
                "success": success,
                "status": event.status,
            },
        )
        return event.status


async def deliver_events(
    session_factory: SessionFactory, settings: Settings, event_ids: Sequence[str]
) -> None:
    """Background-task entry point used right after a lead is stored."""
    for event_id in event_ids:
        try:
            await deliver_event(session_factory, settings, event_id)
        except Exception:
            logger.exception("delivery crashed", extra={"outbox_id": event_id})


async def process_due(
    session_factory: SessionFactory,
    settings: Settings,
    now: datetime | None = None,
    limit: int = 50,
) -> int:
    """Deliver everything that is due, including events stuck in `processing` after a crash."""
    now = now or utcnow()
    stale = now - timedelta(seconds=settings.outbox_lock_timeout_seconds)
    async with session_factory() as session:
        await session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.status == OutboxStatus.processing.value, OutboxEvent.locked_at < stale
            )
            .values(status=OutboxStatus.pending.value, locked_at=None)
        )
        await session.commit()
        rows = await session.execute(
            select(OutboxEvent.id)
            .where(
                OutboxEvent.status == OutboxStatus.pending.value,
                or_(OutboxEvent.next_attempt_at <= now, OutboxEvent.next_attempt_at.is_(None)),
            )
            .order_by(OutboxEvent.next_attempt_at)
            .limit(limit)
        )
        ids = [r[0] for r in rows]
    count = 0
    for event_id in ids:
        if await deliver_event(session_factory, settings, event_id, now=now) is not None:
            count += 1
    return count


async def run_worker(session_factory: SessionFactory, settings: Settings) -> None:
    """Poll loop that retries failed deliveries. Cancelled on shutdown."""
    logger.info("outbox worker started", extra={"poll_seconds": settings.outbox_poll_seconds})
    while True:
        try:
            await process_due(session_factory, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("outbox worker iteration failed")
        await asyncio.sleep(settings.outbox_poll_seconds)

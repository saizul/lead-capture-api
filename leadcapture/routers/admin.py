from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from leadcapture.config import Settings
from leadcapture.db import utcnow
from leadcapture.deps import get_app_settings, get_session, require_admin
from leadcapture.errors import ApiError
from leadcapture.models import DeliveryLog, Form, Lead, LeadStatus, OutboxEvent, OutboxStatus
from leadcapture.notifiers.configs import NotifierConfig, mask_config, restore_masked
from leadcapture.schemas import (
    MAX_TAGS_PER_LEAD,
    DeliveryLogOut,
    FormCreate,
    FormOut,
    FormUpdate,
    LeadBulkResult,
    LeadBulkUpdate,
    LeadOut,
    LeadPage,
    LeadUpdate,
    OutboxOut,
    TagCount,
    normalize_tag,
)
from leadcapture.services.leads import (
    LeadFilters,
    leads_to_csv,
    merge_tags,
    query_leads,
    tag_counts,
)
from leadcapture.services.stats import spam_rate_series

router = APIRouter(
    prefix="/v1/admin",
    tags=["Admin"],
    dependencies=[Depends(require_admin)],
    responses={401: {"description": "Missing or invalid API key."}},
)

Session = Annotated[AsyncSession, Depends(get_session)]
_notifier_adapter: TypeAdapter[Any] = TypeAdapter(list[NotifierConfig])


def form_out(form: Form) -> FormOut:
    out = FormOut.model_validate(form)
    out.notifiers = [mask_config(n) for n in form.notifiers]
    return out


def lead_out(lead: Lead, form_slug: str) -> LeadOut:
    return LeadOut(
        id=lead.id,
        form=form_slug,
        data=lead.data,
        name=lead.name,
        email=lead.email,
        consent=lead.consent,
        consent_at=lead.consent_at,
        status=LeadStatus(lead.status),
        tags=list(lead.tags or []),
        note=lead.note,
        spam_score=lead.spam_score,
        is_spam=lead.is_spam,
        spam_reasons=lead.spam_reasons,
        source_url=lead.source_url,
        user_agent=lead.user_agent,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
    )


async def _get_form(session: AsyncSession, slug: str) -> Form:
    form = (await session.execute(select(Form).where(Form.slug == slug))).scalar_one_or_none()
    if form is None:
        raise ApiError(404, "form_not_found", f"Form {slug!r} does not exist.")
    return form


def lead_filters(
    form: Annotated[str | None, Query(description="Form slug")] = None,
    status: LeadStatus | None = None,
    q: Annotated[str | None, Query(description="Search name, email and field values")] = None,
    spam: Annotated[Literal["exclude", "include", "only"], Query()] = "exclude",
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    tag: Annotated[
        list[str] | None,
        Query(description="Only leads carrying this tag; repeat to require several tags"),
    ] = None,
) -> LeadFilters:
    try:
        tags = tuple(dict.fromkeys(normalize_tag(t) for t in tag or []))
    except ValueError as exc:
        raise ApiError(422, "validation_failed", str(exc)) from None
    return LeadFilters(
        form=form,
        status=status.value if status else None,
        q=q,
        spam=spam,
        created_from=created_from,
        created_to=created_to,
        tags=tags,
    )


Filters = Annotated[LeadFilters, Depends(lead_filters)]


# ---------------------------------------------------------------- forms


@router.post("/forms", response_model=FormOut, status_code=201, summary="Create a form")
async def create_form(body: FormCreate, session: Session) -> FormOut:
    form = Form(
        slug=body.slug,
        **body.model_dump(mode="json", exclude={"slug", "fields", "notifiers"}),
        fields=[f.model_dump(mode="json") for f in body.fields],
        notifiers=[n.model_dump(mode="json") for n in body.notifiers],
    )
    session.add(form)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ApiError(
            409, "slug_taken", f"A form with slug {body.slug!r} already exists."
        ) from None
    return form_out(form)


@router.get("/forms", response_model=list[FormOut], summary="List forms")
async def list_forms(session: Session) -> list[FormOut]:
    forms = (await session.execute(select(Form).order_by(Form.created_at))).scalars().all()
    return [form_out(f) for f in forms]


@router.get("/forms/{slug}", response_model=FormOut, summary="Get a form")
async def get_form(slug: str, session: Session) -> FormOut:
    return form_out(await _get_form(session, slug))


@router.patch("/forms/{slug}", response_model=FormOut, summary="Update a form")
async def update_form(slug: str, body: FormUpdate, session: Session) -> FormOut:
    form = await _get_form(session, slug)
    changes = body.model_dump(exclude_unset=True, mode="json")

    if "fields" in changes:
        names = [f["name"] for f in changes["fields"]]
        if len(names) != len(set(names)):
            raise ApiError(422, "validation_failed", "field names must be unique")
    if "notifiers" in changes:
        try:
            restored = restore_masked(changes["notifiers"], form.notifiers)
            validated = _notifier_adapter.validate_python(restored)
        except (ValueError, ValidationError) as exc:
            raise ApiError(422, "invalid_notifiers", str(exc)) from None
        changes["notifiers"] = [n.model_dump(mode="json") for n in validated]
    if "allowed_origins" in changes:
        try:
            probe = FormCreate.model_validate(
                {
                    "slug": "probe-slug",
                    "name": "x",
                    "fields": [{"name": "a"}],
                    "allowed_origins": changes["allowed_origins"],
                }
            )
        except ValidationError as exc:
            raise ApiError(422, "invalid_origins", exc.errors()[0]["msg"]) from None
        changes["allowed_origins"] = probe.allowed_origins
    redirect = changes.get("success_redirect_url")
    if redirect and not str(redirect).startswith(("http://", "https://")):
        raise ApiError(422, "validation_failed", "success_redirect_url must be http(s)")

    for key, value in changes.items():
        setattr(form, key, value)
    await session.commit()
    return form_out(form)


@router.delete("/forms/{slug}", status_code=204, summary="Delete a form and all its leads")
async def delete_form(slug: str, session: Session) -> Response:
    form = await _get_form(session, slug)
    await session.delete(form)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- leads


@router.get("/leads", response_model=LeadPage, summary="List and search leads")
async def list_leads(
    session: Session,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LeadPage:
    rows, total = await query_leads(session, filters, limit=limit, offset=offset)
    return LeadPage(
        items=[lead_out(lead, slug) for lead, slug in rows], total=total, limit=limit, offset=offset
    )


@router.get(
    "/leads/export.csv",
    summary="Export leads as CSV",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "CSV file"}},
)
async def export_leads(
    session: Session, filters: Filters, settings: Annotated[Settings, Depends(get_app_settings)]
) -> Response:
    rows, _ = await query_leads(session, filters, limit=settings.export_max_rows)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    return Response(
        content=leads_to_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="leads-{stamp}.csv"'},
    )


async def _get_lead(session: AsyncSession, lead_id: str) -> tuple[Lead, str]:
    row = (
        await session.execute(
            select(Lead, Form.slug).join(Form, Form.id == Lead.form_id).where(Lead.id == lead_id)
        )
    ).first()
    if row is None:
        raise ApiError(404, "lead_not_found", "Lead not found.")
    return row[0], row[1]


@router.get("/leads/{lead_id}", response_model=LeadOut, summary="Get a lead")
async def get_lead(lead_id: str, session: Session) -> LeadOut:
    lead, slug = await _get_lead(session, lead_id)
    return lead_out(lead, slug)


def _apply_tags(
    lead: Lead,
    *,
    replace: list[str] | None = None,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> None:
    tags = merge_tags(lead.tags or [], replace=replace, add=add or (), remove=remove or ())
    if len(tags) > MAX_TAGS_PER_LEAD:
        raise ApiError(
            422,
            "too_many_tags",
            f"A lead can carry at most {MAX_TAGS_PER_LEAD} tags (lead {lead.id}).",
        )
    if tags != list(lead.tags or []):
        lead.tags = tags


@router.patch(
    "/leads/{lead_id}",
    response_model=LeadOut,
    summary="Update a lead's status, tags or note",
)
async def update_lead(lead_id: str, body: LeadUpdate, session: Session) -> LeadOut:
    """Partial update. ``tags`` replaces the whole list; ``add_tags`` / ``remove_tags`` edit it.

    Tags are lower-cased and de-duplicated. A ``note`` of ``null`` or ``""`` clears the note.
    """
    lead, slug = await _get_lead(session, lead_id)
    fields = body.model_fields_set
    if body.status is not None:
        lead.status = body.status.value
    if "tags" in fields or body.add_tags or body.remove_tags:
        _apply_tags(lead, replace=body.tags, add=body.add_tags, remove=body.remove_tags)
    if "note" in fields:
        lead.note = (body.note or "").strip() or None
    await session.commit()
    return lead_out(lead, slug)


@router.post(
    "/leads/bulk-update",
    response_model=LeadBulkResult,
    summary="Set status and/or add or remove tags on many leads",
)
async def bulk_update_leads(body: LeadBulkUpdate, session: Session) -> LeadBulkResult:
    """All-or-nothing: if any lead would exceed the tag limit, nothing is changed.

    Unknown IDs are reported in ``not_found`` and do not fail the request.
    """
    leads = (await session.execute(select(Lead).where(Lead.id.in_(body.ids)))).scalars().all()
    found = {lead.id for lead in leads}
    for lead in leads:
        if body.status is not None:
            lead.status = body.status.value
        if body.add_tags or body.remove_tags:
            _apply_tags(lead, add=body.add_tags, remove=body.remove_tags)
    await session.commit()
    return LeadBulkResult(updated=len(found), not_found=[i for i in body.ids if i not in found])


@router.delete("/leads/{lead_id}", status_code=204, summary="Erase a lead (GDPR erasure)")
async def delete_lead(lead_id: str, session: Session) -> Response:
    lead, _ = await _get_lead(session, lead_id)
    await session.delete(lead)
    await session.commit()
    return Response(status_code=204)


@router.get("/tags", response_model=list[TagCount], summary="Tags in use, with lead counts")
async def list_tags(session: Session, filters: Filters) -> list[TagCount]:
    """Every tag carried by the leads matching the usual lead filters, most used first."""
    return [TagCount(tag=t, count=n) for t, n in await tag_counts(session, filters)]


# ---------------------------------------------------------------- deliveries


@router.get("/deliveries", response_model=list[DeliveryLogOut], summary="Delivery attempt logs")
async def list_deliveries(
    session: Session,
    lead_id: str | None = None,
    success: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DeliveryLogOut]:
    stmt = select(DeliveryLog).order_by(DeliveryLog.created_at.desc(), DeliveryLog.attempt.desc())
    if lead_id:
        stmt = stmt.where(DeliveryLog.lead_id == lead_id)
    if success is not None:
        stmt = stmt.where(DeliveryLog.success.is_(success))
    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return [DeliveryLogOut.model_validate(r) for r in rows]


@router.get("/outbox", response_model=list[OutboxOut], summary="Outbox events and their state")
async def list_outbox(
    session: Session,
    status: OutboxStatus | None = None,
    lead_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[OutboxOut]:
    stmt = select(OutboxEvent).order_by(OutboxEvent.created_at.desc())
    if status:
        stmt = stmt.where(OutboxEvent.status == status.value)
    if lead_id:
        stmt = stmt.where(OutboxEvent.lead_id == lead_id)
    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return [OutboxOut.model_validate(r) for r in rows]


@router.post(
    "/outbox/{event_id}/retry", response_model=OutboxOut, summary="Requeue a failed delivery"
)
async def retry_outbox_event(event_id: str, session: Session) -> OutboxOut:
    event = await session.get(OutboxEvent, event_id)
    if event is None:
        raise ApiError(404, "outbox_not_found", "Outbox event not found.")
    if event.status == OutboxStatus.delivered.value:
        raise ApiError(409, "already_delivered", "This delivery already succeeded.")
    event.status = OutboxStatus.pending.value
    event.attempts = 0
    event.next_attempt_at = utcnow()
    event.locked_at = None
    await session.commit()
    return OutboxOut.model_validate(event)


@router.get("/stats", summary="Lead counts per status")
async def stats(session: Session) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(Lead.status, func.count(Lead.id))
            .where(Lead.is_spam.is_(False))
            .group_by(Lead.status)
        )
    ).all()
    counts = {s.value: 0 for s in LeadStatus}
    counts.update({status: int(n) for status, n in rows})
    spam = (
        await session.execute(select(func.count(Lead.id)).where(Lead.is_spam.is_(True)))
    ).scalar_one()
    return {"by_status": counts, "spam": int(spam)}


@router.get("/stats/daily", summary="Daily lead counts per form")
async def daily_stats(
    session: Session,
    days: Annotated[int, Query(ge=1, le=366, description="Window length, ending today (UTC)")] = 30,
    form: Annotated[str | None, Query(description="Only this form slug")] = None,
    spam: Annotated[Literal["exclude", "include", "only"], Query()] = "exclude",
) -> dict[str, Any]:
    """Zero-filled daily series, ready for charts.

    ``dates`` lists every UTC day in the window; ``forms[slug]`` and ``total`` hold the
    lead count for the day at the same index.
    """
    if form is not None:
        await _get_form(session, form)
    end_day = utcnow().date()
    start_day = end_day - timedelta(days=days - 1)
    start = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
    day = func.date(Lead.created_at)
    query = (
        select(Form.slug, day, func.count(Lead.id))
        .join(Form, Form.id == Lead.form_id)
        .where(Lead.created_at >= start)
        .group_by(Form.slug, day)
    )
    if form is not None:
        query = query.where(Form.slug == form)
    if spam == "exclude":
        query = query.where(Lead.is_spam.is_(False))
    elif spam == "only":
        query = query.where(Lead.is_spam.is_(True))
    dates = [(start_day + timedelta(days=i)).isoformat() for i in range(days)]
    index = {d: i for i, d in enumerate(dates)}
    forms: dict[str, list[int]] = {}
    total = [0] * days
    for slug, bucket, count in (await session.execute(query)).all():
        i = index.get(str(bucket))
        if i is None:  # a lead dated in the future falls outside the window
            continue
        forms.setdefault(slug, [0] * days)[i] += int(count)
        total[i] += int(count)
    return {
        "days": days,
        "spam": spam,
        "dates": dates,
        "forms": dict(sorted(forms.items())),
        "total": total,
    }


@router.get("/stats/spam-rate", summary="Daily spam rate per form")
async def spam_rate_stats(
    session: Session,
    days: Annotated[int, Query(ge=1, le=366, description="Window length, ending today (UTC)")] = 30,
    form: Annotated[str | None, Query(description="Only this form slug")] = None,
) -> dict[str, Any]:
    """Share of submissions flagged as spam, per UTC day and form.

    Every series is aligned with ``dates``. ``rate`` is ``spam / total`` (0-1) and ``null`` on
    days without submissions. ``overall`` aggregates all forms in the window.
    """
    if form is not None:
        await _get_form(session, form)
    end_day = utcnow().date()
    start_day = end_day - timedelta(days=days - 1)
    start = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
    day = func.date(Lead.created_at)
    query = (
        select(Form.slug, day, Lead.is_spam, func.count(Lead.id))
        .join(Form, Form.id == Lead.form_id)
        .where(Lead.created_at >= start)
        .group_by(Form.slug, day, Lead.is_spam)
    )
    if form is not None:
        query = query.where(Form.slug == form)
    dates = [(start_day + timedelta(days=i)).isoformat() for i in range(days)]
    rows = [
        (slug, str(bucket), bool(is_spam), int(count))
        for slug, bucket, is_spam, count in (await session.execute(query)).all()
    ]
    return {"days": days, "dates": dates, **spam_rate_series(rows, dates)}

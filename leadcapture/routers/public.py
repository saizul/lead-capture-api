from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select

from leadcapture.config import Settings
from leadcapture.db import utcnow
from leadcapture.deps import client_ip
from leadcapture.errors import ApiError
from leadcapture.fields import FieldDef, FieldType, parse_bool, validate_submission
from leadcapture.models import Form, Lead
from leadcapture.schemas import PublicFormSchema, SubmitResponse
from leadcapture.security import hash_ip, make_form_token, parse_form_token
from leadcapture.services import spam as spam_service
from leadcapture.services.outbox import deliver_events, enqueue_notifications

logger = logging.getLogger("leadcapture.submit")
router = APIRouter(prefix="/v1/forms", tags=["Public submissions"])

FORM_TOKEN_FIELD = "_form_token"
SOURCE_URL_FIELD = "_source_url"
FORM_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")

SUBMIT_OPENAPI: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {"type": "object", "additionalProperties": True},
                "examples": {
                    "contact": {
                        "summary": "Contact form",
                        "value": {
                            "name": "Ada Lovelace",
                            "email": "ada@example.com",
                            "message": "I'd like a quote for a landing page.",
                            "consent": True,
                            "_form_token": "<token from GET /v1/forms/{slug}/schema>",
                        },
                    }
                },
            },
            "application/x-www-form-urlencoded": {
                "schema": {"type": "object", "additionalProperties": {"type": "string"}},
                "example": "name=Ada&email=ada%40example.com&message=Hi&consent=on&_form_token=...",
            },
        },
    }
}


def _allowed(form: Form, origin: str | None) -> bool:
    return origin is not None and ("*" in form.allowed_origins or origin in form.allowed_origins)


def cors_headers(form: Form, origin: str | None) -> dict[str, str]:
    if not _allowed(form, origin):
        return {}
    allow = "*" if "*" in form.allowed_origins else str(origin)
    headers = {"Access-Control-Allow-Origin": allow}
    if allow != "*":
        headers["Vary"] = "Origin"
    return headers


async def _load_form(request: Request, slug: str) -> Form:
    async with request.app.state.session_factory() as session:
        form = (
            await session.execute(select(Form).where(Form.slug == slug, Form.is_active.is_(True)))
        ).scalar_one_or_none()
    if form is None:
        raise ApiError(404, "form_not_found", "No active form with this slug.")
    return form


def _enforce_origin(form: Form, request: Request) -> dict[str, str]:
    origin = request.headers.get("origin")
    if origin and not _allowed(form, origin):
        raise ApiError(403, "origin_not_allowed", "This origin may not submit to this form.")
    return cors_headers(form, origin)


async def _parse_body(request: Request, max_bytes: int, cors: dict[str, str]) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > max_bytes:
        raise ApiError(413, "payload_too_large", "Request body is too large.", headers=cors)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype == "application/json":
        try:
            parsed = json.loads(raw or b"null")
        except ValueError:
            raise ApiError(400, "invalid_json", "Body is not valid JSON.", headers=cors) from None
        if not isinstance(parsed, dict):
            raise ApiError(400, "invalid_json", "Body must be a JSON object.", headers=cors)
        return parsed
    if ctype == "application/x-www-form-urlencoded":
        try:
            return dict(parse_qsl(raw.decode("utf-8"), keep_blank_values=True))
        except UnicodeDecodeError:
            raise ApiError(400, "invalid_body", "Body is not valid UTF-8.", headers=cors) from None
    if ctype == "multipart/form-data":
        form_data = await request.form()
        return {k: v for k, v in form_data.items() if isinstance(v, str)}
    raise ApiError(
        415,
        "unsupported_media_type",
        "Use application/json, application/x-www-form-urlencoded or multipart/form-data.",
        headers=cors,
    )


def _check_form_token(
    form: Form, data: dict[str, Any], settings: Settings, cors: dict[str, str]
) -> None:
    if form.min_submit_seconds <= 0:
        return
    token = data.get(FORM_TOKEN_FIELD)
    issued = (
        parse_form_token(settings.secret_key, form.slug, token) if isinstance(token, str) else None
    )
    if issued is None:
        raise ApiError(
            422,
            "form_token_invalid",
            "Missing or invalid form token. Load it from the form schema endpoint.",
            headers=cors,
        )
    age = time.time() - issued
    if age < form.min_submit_seconds:
        raise ApiError(
            422,
            "submitted_too_fast",
            f"Please wait a moment before submitting (minimum {form.min_submit_seconds}s).",
            headers=cors,
        )
    if age > settings.form_token_ttl_seconds:
        raise ApiError(
            422, "form_token_expired", "This form has expired. Reload and try again.", headers=cors
        )


def _extract_identity(
    fields: list[FieldDef], clean: dict[str, Any]
) -> tuple[str | None, str | None]:
    email = next(
        (clean[f.name] for f in fields if f.type is FieldType.email and f.name in clean), None
    )
    name = clean.get("name") or clean.get("full_name")
    return (str(name)[:255] if name else None), email


@router.options("/{slug}/submit", include_in_schema=False)
@router.options("/{slug}/schema", include_in_schema=False)
async def preflight(slug: str, request: Request) -> Response:
    form = await _load_form(request, slug)
    origin = request.headers.get("origin")
    if not _allowed(form, origin):
        raise ApiError(403, "origin_not_allowed", "This origin may not submit to this form.")
    headers = {
        **cors_headers(form, origin),
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "600",
    }
    return Response(status_code=204, headers=headers)


@router.get(
    "/{slug}/schema",
    response_model=PublicFormSchema,
    summary="Get the public field schema and a signed form token",
)
async def form_schema(slug: str, request: Request) -> JSONResponse:
    """Front-ends call this when the form renders. The returned `form_token` proves *when*
    the form was shown and powers the minimum time-to-submit check."""
    settings: Settings = request.app.state.settings
    form = await _load_form(request, slug)
    cors = _enforce_origin(form, request)
    body = PublicFormSchema(
        slug=form.slug,
        name=form.name,
        fields=[FieldDef.model_validate(f) for f in form.fields],
        honeypot_field=form.honeypot_field,
        min_submit_seconds=form.min_submit_seconds,
        require_consent=form.require_consent,
        form_token=make_form_token(settings.secret_key, form.slug),
    )
    return JSONResponse(body.model_dump(mode="json"), headers=cors)


@router.post(
    "/{slug}/submit",
    response_model=SubmitResponse,
    status_code=201,
    summary="Submit a lead",
    openapi_extra=SUBMIT_OPENAPI,
    responses={
        303: {"description": "Redirect to the form's success URL (browser form posts only)."},
        403: {"description": "Origin not on the form's allow-list."},
        404: {"description": "Unknown or inactive form."},
        422: {"description": "Validation failed, form token missing, or submitted too fast."},
        429: {"description": "Rate limit exceeded. See the `Retry-After` header."},
    },
)
async def submit(slug: str, request: Request, background: BackgroundTasks) -> Response:
    """Accepts JSON or form-encoded data. Silent spam handling: honeypot hits and spam-scored
    submissions return the same success response so bots learn nothing."""
    app = request.app
    settings: Settings = app.state.settings
    form = await _load_form(request, slug)
    cors = _enforce_origin(form, request)

    ip = client_ip(request, settings.trust_proxy)
    ip_hash = hash_ip(ip, settings.ip_hash_salt)
    window = settings.rate_limit_window_seconds
    for key, limit in (
        (f"ip:{form.slug}:{ip_hash}", form.rate_limit_per_ip),
        (f"form:{form.slug}", form.rate_limit_per_form),
    ):
        result = await app.state.limiter.hit(key, limit, window)
        if not result.allowed:
            logger.warning("rate limited", extra={"form": form.slug, "scope": key.split(":")[0]})
            raise ApiError(
                429,
                "rate_limited",
                "Too many submissions. Try again later.",
                headers={**cors, "Retry-After": str(result.retry_after)},
            )

    data = await _parse_body(request, settings.max_body_bytes, cors)
    is_form_post = (
        request.headers.get("content-type", "").split(";")[0].strip().lower() in FORM_CONTENT_TYPES
    )

    # Honeypot: humans never see this field. Pretend success, store nothing.
    if str(data.get(form.honeypot_field) or "").strip():
        logger.info("honeypot triggered", extra={"form": form.slug})
        return _success(form, str(uuid.uuid4()), is_form_post, cors)

    _check_form_token(form, data, settings, cors)

    fields = [FieldDef.model_validate(f) for f in form.fields]
    clean, errors = validate_submission(fields, data)

    consent = parse_bool(data.get("consent")) is True
    if form.require_consent and not consent:
        errors["consent"] = "Consent is required."
    if errors:
        raise ApiError(
            422, "validation_failed", "Some fields are invalid.", fields=errors, headers=cors
        )

    name, email = _extract_identity(fields, clean)
    texts = [
        str(clean[f.name])
        for f in fields
        if f.type in (FieldType.text, FieldType.textarea) and f.name in clean
    ]
    verdict = spam_service.analyze(
        texts, email, extra_blocked_words=form.blocked_words, threshold=form.spam_threshold
    )

    source_url = data.get(SOURCE_URL_FIELD) or request.headers.get("referer")
    now = utcnow()
    lead = Lead(
        form_id=form.id,
        data=clean,
        name=name,
        email=email,
        consent=consent,
        consent_at=now if consent else None,
        ip_hash=ip_hash,
        user_agent=(request.headers.get("user-agent") or "")[:255] or None,
        source_url=str(source_url)[:500] if source_url else None,
        spam_score=verdict.score,
        is_spam=verdict.is_spam,
        spam_reasons=verdict.reasons,
        created_at=now,
    )
    event_ids: list[str] = []
    async with app.state.session_factory() as session:
        session.add(lead)
        await session.flush()
        if not verdict.is_spam:
            events = enqueue_notifications(session, form, lead, settings)
            await session.flush()
            event_ids = [e.id for e in events]
        await session.commit()

    logger.info(
        "lead stored",
        extra={
            "form": form.slug,
            "lead_id": lead.id,
            "spam_score": verdict.score,
            "spam": verdict.is_spam,
        },
    )
    if event_ids:
        background.add_task(deliver_events, app.state.session_factory, settings, event_ids)
    return _success(form, lead.id, is_form_post, cors, background)


def _success(
    form: Form,
    lead_id: str,
    is_form_post: bool,
    cors: dict[str, str],
    background: BackgroundTasks | None = None,
) -> Response:
    response: Response
    if is_form_post and form.success_redirect_url:
        response = RedirectResponse(form.success_redirect_url, status_code=303, headers=cors)
    else:
        response = JSONResponse(
            SubmitResponse(id=lead_id).model_dump(), status_code=201, headers=cors
        )
    if background is not None:
        response.background = background
    return response

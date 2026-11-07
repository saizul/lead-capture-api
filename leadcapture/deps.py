from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from leadcapture.config import Settings
from leadcapture.db import utcnow
from leadcapture.errors import ApiError
from leadcapture.models import ApiKey
from leadcapture.security import hash_api_key


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _extract_key(request: Request) -> str | None:
    header = request.headers.get("x-api-key")
    if header:
        return header.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


async def require_admin(request: Request) -> ApiKey:
    raw = _extract_key(request)
    if not raw:
        raise ApiError(
            401,
            "unauthorized",
            "Provide an API key via the X-API-Key header or Authorization: Bearer.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    async with request.app.state.session_factory() as session:
        key = (
            await session.execute(
                select(ApiKey).where(
                    ApiKey.key_hash == hash_api_key(raw), ApiKey.is_active.is_(True)
                )
            )
        ).scalar_one_or_none()
        if key is None:
            raise ApiError(
                401,
                "unauthorized",
                "Invalid or revoked API key.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        now = utcnow()
        if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=1):
            key.last_used_at = now
            await session.commit()
        return key

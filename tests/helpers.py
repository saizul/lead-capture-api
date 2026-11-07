from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from sqlalchemy import select

from leadcapture.models import ApiKey, Form, Lead
from leadcapture.security import generate_api_key

DEFAULT_FIELDS: list[dict[str, Any]] = [
    {"name": "name", "type": "text", "required": True, "max_length": 50},
    {"name": "email", "type": "email", "required": True},
    {"name": "phone", "type": "phone"},
    {"name": "message", "type": "textarea", "max_length": 500},
]

VALID_SUBMISSION = {
    "name": "Ada Lovelace",
    "email": "ada@example.com",
    "message": "I would like a quote for a landing page.",
}


async def create_form(app: FastAPI, **overrides: Any) -> Form:
    values: dict[str, Any] = {
        "slug": "contact",
        "name": "Contact",
        "fields": DEFAULT_FIELDS,
        "allowed_origins": [],
        "notifiers": [],
        "min_submit_seconds": 0,
    }
    values.update(overrides)
    async with app.state.session_factory() as session:
        form = Form(**values)
        session.add(form)
        await session.commit()
        return form


async def create_lead(app: FastAPI, form: Form, **overrides: Any) -> Lead:
    values: dict[str, Any] = {
        "form_id": form.id,
        "data": {"name": "Test Person", "email": "test@example.com"},
        "name": "Test Person",
        "email": "test@example.com",
    }
    values.update(overrides)
    async with app.state.session_factory() as session:
        lead = Lead(**values)
        session.add(lead)
        await session.commit()
        return lead


async def create_api_key(app: FastAPI, name: str = "test", active: bool = True) -> str:
    raw, prefix, key_hash = generate_api_key()
    async with app.state.session_factory() as session:
        session.add(ApiKey(name=name, prefix=prefix, key_hash=key_hash, is_active=active))
        await session.commit()
    return raw


async def fetch_all(app: FastAPI, model: Any) -> list[Any]:
    async with app.state.session_factory() as session:
        return list((await session.execute(select(model))).scalars().all())

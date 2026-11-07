from __future__ import annotations

import asyncio
import sqlite3
from datetime import timedelta

import pytest
import typer
from sqlalchemy import select
from typer.testing import CliRunner

from leadcapture.cli import app as cli_app
from leadcapture.cli import parse_field_spec
from leadcapture.config import Settings
from leadcapture.db import Base, make_engine, make_session_factory, utcnow
from leadcapture.fields import FieldType
from leadcapture.main import create_app
from leadcapture.models import Form, Lead
from leadcapture.security import hash_api_key

runner = CliRunner()


def sqlite_rows(path, sql):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_migrate_creates_schema_matching_models(cli_env):
    result = runner.invoke(cli_app, ["migrate"])
    assert result.exit_code == 0, result.output
    tables = {
        r[0] for r in sqlite_rows(cli_env, "SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert set(Base.metadata.tables) <= tables and "alembic_version" in tables
    for name, table in Base.metadata.tables.items():
        cols = {r[1] for r in sqlite_rows(cli_env, f"PRAGMA table_info({name})")}
        assert cols == {c.name for c in table.columns}, name


def test_migrate_is_idempotent(cli_env):
    assert runner.invoke(cli_app, ["migrate"]).exit_code == 0
    assert runner.invoke(cli_app, ["migrate"]).exit_code == 0


def test_create_admin_key_stores_only_hash(cli_env):
    runner.invoke(cli_app, ["migrate"])
    result = runner.invoke(cli_app, ["create-admin-key", "ci"])
    assert result.exit_code == 0
    raw = next(line for line in result.output.splitlines() if line.startswith("lc_"))
    rows = sqlite_rows(cli_env, "SELECT name, key_hash, prefix FROM api_keys")
    assert rows == [("ci", hash_api_key(raw), raw[:8])]
    assert raw not in str(sqlite_rows(cli_env, "SELECT * FROM api_keys"))


def test_create_form_with_defaults(cli_env):
    runner.invoke(cli_app, ["migrate"])
    result = runner.invoke(
        cli_app,
        [
            "create-form",
            "contact",
            "Contact",
            "--origin",
            "https://example.com/",
            "--webhook-url",
            "https://h.example.com/x",
        ],
    )
    assert result.exit_code == 0, result.output
    slug, fields, origins, notifiers = sqlite_rows(
        cli_env, "SELECT slug, fields, allowed_origins, notifiers FROM forms"
    )[0]
    assert slug == "contact"
    assert "email" in fields and origins == '["https://example.com"]' and "webhook" in notifiers


def test_create_form_custom_fields_and_duplicate(cli_env):
    runner.invoke(cli_app, ["migrate"])
    args = [
        "create-form",
        "quote",
        "Quote",
        "-f",
        "name:text:required:max=80",
        "-f",
        "budget:number",
    ]
    assert runner.invoke(cli_app, args).exit_code == 0
    again = runner.invoke(cli_app, args)
    assert again.exit_code == 1 and "already exists" in again.output


def test_list_forms(cli_env):
    runner.invoke(cli_app, ["migrate"])
    runner.invoke(cli_app, ["create-form", "contact", "Contact"])
    result = runner.invoke(cli_app, ["list-forms"])
    assert "contact" in result.output and "active" in result.output


def test_purge_old_leads(cli_env):
    runner.invoke(cli_app, ["migrate"])
    runner.invoke(cli_app, ["create-form", "contact", "Contact"])

    async def seed() -> None:
        engine = make_engine(f"sqlite+aiosqlite:///{cli_env}")
        sf = make_session_factory(engine)
        async with sf() as session:
            form = (await session.execute(select(Form))).scalar_one()
            session.add(Lead(form_id=form.id, data={}, created_at=utcnow() - timedelta(days=400)))
            session.add(Lead(form_id=form.id, data={}))
            await session.commit()
        await engine.dispose()

    asyncio.run(seed())
    result = runner.invoke(cli_app, ["purge-leads", "--older-than-days", "365", "--yes"])
    assert result.exit_code == 0 and "Deleted 1" in result.output
    assert sqlite_rows(cli_env, "SELECT count(*) FROM leads") == [(1,)]


def test_purge_requires_confirmation(cli_env):
    runner.invoke(cli_app, ["migrate"])
    result = runner.invoke(cli_app, ["purge-leads", "--older-than-days", "30"], input="n\n")
    assert result.exit_code != 0


def test_version_command():
    assert runner.invoke(cli_app, ["version"]).output.strip() == "1.0.0"


def test_parse_field_spec():
    f = parse_field_spec("email:email:required")
    assert f.type is FieldType.email and f.required
    g = parse_field_spec("bio:textarea:max=300:min=5")
    assert g.max_length == 300 and g.min_length == 5
    h = parse_field_spec("plan:select:options=a|b")
    assert h.options == ["a", "b"]
    assert parse_field_spec("nickname").type is FieldType.text


def test_parse_field_spec_rejects_unknown_type():
    with pytest.raises(typer.BadParameter):
        parse_field_spec("x:hologram")


def test_settings_normalise_database_urls():
    def url(raw: str) -> str:
        return Settings(_env_file=None, database_url=raw).async_database_url  # type: ignore[call-arg]

    assert url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert url("sqlite:///./x.db") == "sqlite+aiosqlite:///./x.db"
    assert url("sqlite+aiosqlite:///./x.db") == "sqlite+aiosqlite:///./x.db"


def test_production_refuses_default_secrets():
    s = Settings(_env_file=None, environment="production")  # type: ignore[call-arg]
    with pytest.raises(RuntimeError):
        s.assert_safe_for_production()
    ok = Settings(
        _env_file=None, environment="production", secret_key="a" * 40, ip_hash_salt="b" * 40
    )  # type: ignore[call-arg]
    ok.assert_safe_for_production()


async def test_healthz(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"


async def test_readyz_ok(client):
    resp = await client.get("/readyz")
    assert resp.status_code == 200 and resp.json()["checks"]["database"] == "ok"


async def test_readyz_reports_database_failure(app, client):
    await app.state.engine.dispose()
    app.state.engine = make_engine("sqlite+aiosqlite:////nonexistent-dir-xyz/db.sqlite")
    resp = await client.get("/readyz")
    assert resp.status_code == 503 and resp.json()["status"] == "unavailable"
    await app.state.engine.dispose()


async def test_openapi_documents_public_and_admin_routes(client):
    spec = (await client.get("/openapi.json")).json()
    assert "/v1/forms/{slug}/submit" in spec["paths"]
    assert "/v1/admin/leads/export.csv" in spec["paths"]
    submit = spec["paths"]["/v1/forms/{slug}/submit"]["post"]
    assert "application/x-www-form-urlencoded" in submit["requestBody"]["content"]
    assert (await client.get("/docs")).status_code == 200


class TestDocsDisabled:
    @pytest.fixture
    def settings_overrides(self):
        return {"docs_enabled": False}

    async def test_docs_hidden(self, client):
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404


def test_app_factory_builds_without_touching_the_database(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path}/x.db")  # type: ignore[call-arg]
    application = create_app(settings)
    assert application.title == "Lead Capture API"

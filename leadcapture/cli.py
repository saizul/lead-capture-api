"""Command line interface: migrations, admin keys, forms, retention."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Optional, TypeVar

import typer
from sqlalchemy import delete, select

from leadcapture import __version__
from leadcapture.config import get_settings
from leadcapture.db import make_engine, make_session_factory, utcnow
from leadcapture.fields import FieldDef, FieldType
from leadcapture.models import ApiKey, Form, Lead
from leadcapture.security import generate_api_key

app = typer.Typer(help="Lead Capture API command line tools.", no_args_is_help=True)

T = TypeVar("T")

DEFAULT_FIELDS = ["name:text:required", "email:email:required", "message:textarea"]


def _run(coro_factory: Any) -> Any:
    """Run `coro_factory(session_factory)` against the configured database."""

    async def runner() -> Any:
        engine = make_engine(get_settings().async_database_url)
        try:
            return await coro_factory(make_session_factory(engine))
        finally:
            await engine.dispose()

    return asyncio.run(runner())


def parse_field_spec(spec: str) -> FieldDef:
    """Parse `name:type[:required][:max=N]` into a FieldDef."""
    parts = [p.strip() for p in spec.split(":") if p.strip()]
    if not parts:
        raise typer.BadParameter(f"empty field spec: {spec!r}")
    name = parts[0]
    ftype = FieldType.text
    required = False
    extras: dict[str, Any] = {}
    for part in parts[1:]:
        if part == "required":
            required = True
        elif part.startswith("max="):
            extras["max_length"] = int(part[4:])
        elif part.startswith("min="):
            extras["min_length"] = int(part[4:])
        elif part.startswith("options="):
            extras["options"] = part[8:].split("|")
        else:
            try:
                ftype = FieldType(part)
            except ValueError:
                raise typer.BadParameter(f"unknown field type {part!r} in {spec!r}") from None
    try:
        return FieldDef(name=name, type=ftype, required=required, **extras)
    except ValueError as exc:
        raise typer.BadParameter(f"{spec!r}: {exc}") from None


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def migrate(
    revision: Annotated[str, typer.Argument(help="Target revision")] = "head",
) -> None:
    """Apply database migrations (alembic upgrade)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().async_database_url.replace("%", "%%"))
    command.upgrade(cfg, revision)
    typer.echo(f"Database is at revision: {revision}")


@app.command("create-admin-key")
def create_admin_key(name: Annotated[str, typer.Argument(help="Label for the key")]) -> None:
    """Create an admin API key. The key is displayed once; only its hash is stored."""
    raw, prefix, key_hash = generate_api_key()

    async def work(sf: Any) -> None:
        async with sf() as session:
            session.add(ApiKey(name=name, prefix=prefix, key_hash=key_hash))
            await session.commit()

    _run(work)
    typer.echo("Store this key now, it cannot be shown again:")
    typer.echo(raw)


@app.command("create-form")
def create_form(
    slug: Annotated[str, typer.Argument(help="URL slug, e.g. contact")],
    name: Annotated[str, typer.Argument(help="Display name")],
    field: Annotated[
        Optional[list[str]],  # noqa: UP045 - typer needs typing.Optional on 3.10
        typer.Option("--field", "-f", help="name:type[:required][:max=N]; repeatable"),
    ] = None,
    fields_file: Annotated[
        Optional[Path],  # noqa: UP045
        typer.Option(help="JSON file with a list of field definitions"),
    ] = None,
    origin: Annotated[
        Optional[list[str]],  # noqa: UP045
        typer.Option("--origin", "-o", help="Allowed CORS origin; repeatable"),
    ] = None,
    webhook_url: Annotated[Optional[str], typer.Option(help="Add a webhook notifier")] = None,  # noqa: UP045
    webhook_secret: Annotated[Optional[str], typer.Option(help="HMAC secret")] = None,  # noqa: UP045
    min_seconds: Annotated[int, typer.Option(help="Minimum time-to-submit")] = 3,
    require_consent: Annotated[bool, typer.Option(help="Require a consent checkbox")] = False,
) -> None:
    """Create a form. Defaults to name, email and message fields."""
    if fields_file:
        fields = [FieldDef.model_validate(f) for f in json.loads(fields_file.read_text())]
    else:
        fields = [parse_field_spec(s) for s in (field or DEFAULT_FIELDS)]
    notifiers: list[dict[str, Any]] = []
    if webhook_url:
        notifiers.append({"type": "webhook", "url": webhook_url, "secret": webhook_secret})

    async def work(sf: Any) -> bool:
        async with sf() as session:
            exists = (await session.execute(select(Form.id).where(Form.slug == slug))).first()
            if exists:
                return False
            session.add(
                Form(
                    slug=slug,
                    name=name,
                    fields=[f.model_dump(mode="json") for f in fields],
                    allowed_origins=[o.rstrip("/") for o in (origin or [])],
                    notifiers=notifiers,
                    min_submit_seconds=min_seconds,
                    require_consent=require_consent,
                )
            )
            await session.commit()
            return True

    if not _run(work):
        typer.echo(f"A form with slug {slug!r} already exists.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Created form {slug!r} with {len(fields)} fields.")


@app.command("list-forms")
def list_forms() -> None:
    """List forms."""

    async def work(sf: Any) -> list[tuple[str, str, bool]]:
        async with sf() as session:
            rows = (await session.execute(select(Form).order_by(Form.created_at))).scalars().all()
            return [(f.slug, f.name, f.is_active) for f in rows]

    for slug, name, active in _run(work):
        typer.echo(f"{slug}\t{name}\t{'active' if active else 'inactive'}")


@app.command("purge-leads")
def purge_leads(
    older_than_days: Annotated[int, typer.Option(min=1, help="Delete leads older than N days")],
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation")] = False,
) -> None:
    """Delete old leads (data retention). Related outbox rows and delivery logs go too."""
    cutoff = utcnow() - timedelta(days=older_than_days)
    if not yes:
        typer.confirm(f"Permanently delete leads created before {cutoff:%Y-%m-%d}?", abort=True)

    async def work(sf: Any) -> int:
        async with sf() as session:
            result = await session.execute(delete(Lead).where(Lead.created_at < cutoff))
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0)

    typer.echo(f"Deleted {_run(work)} lead(s).")


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
) -> None:
    """Run the API with uvicorn."""
    import uvicorn

    uvicorn.run("leadcapture.main:create_app", factory=True, host=host, port=port, reload=reload)


if __name__ == "__main__":  # pragma: no cover
    app()

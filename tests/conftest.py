from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from leadcapture.config import Settings, get_settings
from leadcapture.db import Base
from leadcapture.main import create_app
from tests.helpers import create_api_key


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    """Override in a test module/class to tweak settings."""
    return {}


@pytest.fixture
def settings(tmp_path: Path, settings_overrides: dict[str, Any]) -> Settings:
    values: dict[str, Any] = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        "secret_key": "test-secret",
        "ip_hash_salt": "test-salt",
        "environment": "test",
        "outbox_worker_enabled": False,
        "log_level": "WARNING",
        "log_format": "console",
    }
    values.update(settings_overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest_asyncio.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield application
    await application.state.engine.dispose()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def admin(app: FastAPI) -> dict[str, str]:
    return {"X-API-Key": await create_api_key(app)}


@pytest.fixture
def mock_http() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    db_path = tmp_path / "cli.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("SECRET_KEY", "cli-secret")
    get_settings.cache_clear()
    yield db_path
    get_settings.cache_clear()

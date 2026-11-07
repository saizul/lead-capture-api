from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from leadcapture import __version__
from leadcapture.config import Settings, get_settings
from leadcapture.db import make_engine, make_session_factory
from leadcapture.errors import ApiError, api_error_handler
from leadcapture.logging_config import request_id_var, setup_logging
from leadcapture.routers import admin, health, public
from leadcapture.services.outbox import run_worker
from leadcapture.services.ratelimit import build_rate_limiter

logger = logging.getLogger("leadcapture")

DESCRIPTION = """
Lead intake for websites and landing pages.

* **Public**: `POST /v1/forms/{slug}/submit` (JSON or form-encoded), `GET /v1/forms/{slug}/schema`.
* **Admin**: API-key protected CRUD for forms, lead search, CSV export, delivery logs.
* **Delivery**: webhook (HMAC signed), email, Telegram and Slack notifiers via a retrying outbox.

Authenticate admin calls with `X-API-Key: <key>` or `Authorization: Bearer <key>`.
"""

TAGS = [
    {"name": "Public submissions", "description": "Called by your website or landing page."},
    {"name": "Admin", "description": "Requires an API key created with the CLI."},
    {"name": "Health", "description": "Liveness and readiness probes."},
]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.assert_safe_for_production()
    setup_logging(settings.log_level, settings.log_format)

    engine = make_engine(settings.async_database_url)
    session_factory = make_session_factory(engine)
    limiter = build_rate_limiter(settings.redis_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        worker: asyncio.Task[None] | None = None
        if settings.outbox_worker_enabled:
            worker = asyncio.create_task(run_worker(session_factory, settings))
        logger.info("started", extra={"version": __version__, "env": settings.environment})
        try:
            yield
        finally:
            if worker:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            await limiter.close()
            await engine.dispose()

    app = FastAPI(
        title="Lead Capture API",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        contact={"name": "Saizul Amin", "url": "https://saizul.com"},
        license_info={"name": "MIT"},
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.limiter = limiter

    app.add_exception_handler(ApiError, api_error_handler)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > settings.max_body_bytes:
                response: Response = JSONResponse(
                    {
                        "error": {
                            "code": "payload_too_large",
                            "message": "Request body is too large.",
                        }
                    },
                    status_code=413,
                )
            else:
                response = await call_next(request)
        except Exception:
            logger.exception("unhandled error", extra={"path": request.url.path})
            response = JSONResponse(
                {"error": {"code": "internal_error", "message": "Internal server error."}},
                status_code=500,
            )
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path not in ("/healthz", "/readyz"):
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
        request_id_var.reset(token)
        return response

    app.include_router(health.router)
    app.include_router(public.router)
    app.include_router(admin.router)
    return app

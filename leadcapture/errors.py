from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Raised anywhere in a request; rendered as a consistent JSON error body."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        fields: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.fields = fields
        self.headers = headers or {}


async def api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    body: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.fields:
        body["fields"] = exc.fields
    return JSONResponse(status_code=exc.status_code, content={"error": body}, headers=exc.headers)

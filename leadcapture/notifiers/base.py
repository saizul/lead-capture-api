from __future__ import annotations

import builtins
import html
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel

from leadcapture import __version__
from leadcapture.config import Settings


class DeliveryError(Exception):
    """A delivery attempt failed. `retryable` decides whether the outbox schedules another try."""

    def __init__(
        self, message: str, *, retryable: bool = True, status_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass
class DeliveryResult:
    status_code: int | None = None
    detail: str | None = None


class Notifier(ABC):
    type: ClassVar[str]
    config_model: ClassVar[builtins.type[BaseModel]]

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @abstractmethod
    async def send(
        self, config: dict[str, Any], payload: dict[str, Any], delivery_id: str
    ) -> DeliveryResult:
        """Deliver one lead notification. Raise DeliveryError on failure."""


USER_AGENT = f"leadcapture/{__version__}"


def is_retryable_status(status: int) -> bool:
    return status >= 500 or status in (408, 425, 429)


def check_response(response: httpx.Response) -> DeliveryResult:
    if response.is_success:
        return DeliveryResult(status_code=response.status_code)
    snippet = response.text[:200].replace("\n", " ")
    raise DeliveryError(
        f"HTTP {response.status_code}: {snippet}",
        retryable=is_retryable_status(response.status_code),
        status_code=response.status_code,
    )


async def post_json(
    url: str,
    *,
    content: bytes,
    headers: dict[str, str],
    timeout: float,
) -> DeliveryResult:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, content=content, headers=headers)
    except httpx.TimeoutException as exc:
        raise DeliveryError(f"timeout: {exc.__class__.__name__}") from exc
    except httpx.HTTPError as exc:
        raise DeliveryError(f"network error: {exc.__class__.__name__}: {exc}") from exc
    return check_response(response)


def lead_lines(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Flatten a payload into (label, value) rows for human-readable channels."""
    rows: list[tuple[str, str]] = []
    for key, value in (payload.get("data") or {}).items():
        rows.append((key.replace("_", " ").title(), str(value)))
    return rows


def plain_text(payload: dict[str, Any], limit: int = 3500) -> str:
    form = payload.get("form", {})
    lines = [f"New lead from {form.get('name', 'form')}", ""]
    lines += [f"{label}: {value}" for label, value in lead_lines(payload)]
    if payload.get("source_url"):
        lines += ["", f"Source: {payload['source_url']}"]
    lines += [f"Lead ID: {payload.get('id')}"]
    return "\n".join(lines)[:limit]


def escape_html(value: str) -> str:
    return html.escape(value, quote=False)

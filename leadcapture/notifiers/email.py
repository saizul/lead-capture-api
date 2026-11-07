from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from leadcapture.config import Settings
from leadcapture.notifiers.base import (
    DeliveryError,
    DeliveryResult,
    Notifier,
    plain_text,
)
from leadcapture.notifiers.configs import EmailConfig


def build_message(settings: Settings, cfg: EmailConfig, payload: dict[str, Any]) -> EmailMessage:
    form_name = str(payload.get("form", {}).get("name", "form"))
    subject = cfg.subject.replace("{form_name}", form_name)
    msg = EmailMessage()
    msg["Subject"] = subject.replace("\r", " ").replace("\n", " ")
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(cfg.to)
    reply_to = payload.get("email")
    if reply_to and "\n" not in reply_to and "\r" not in reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(plain_text(payload))
    return msg


def _send_sync(settings: Settings, msg: EmailMessage) -> None:
    timeout = settings.http_timeout_seconds
    try:
        if settings.smtp_use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout)
        with server:
            if settings.smtp_starttls and not settings.smtp_use_ssl:
                server.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)
    except smtplib.SMTPResponseException as exc:
        # 4xx replies are transient; 5xx are permanent.
        raise DeliveryError(
            f"SMTP {exc.smtp_code}: {exc.smtp_error!r}",
            retryable=400 <= exc.smtp_code < 500,
            status_code=exc.smtp_code,
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise DeliveryError(f"SMTP error: {exc.__class__.__name__}: {exc}") from exc


class EmailNotifier(Notifier):
    type = "email"
    config_model = EmailConfig

    async def send(
        self, config: dict[str, Any], payload: dict[str, Any], delivery_id: str
    ) -> DeliveryResult:
        cfg = EmailConfig.model_validate(config)
        if not self.settings.smtp_host:
            raise DeliveryError("SMTP_HOST is not configured", retryable=False)
        msg = build_message(self.settings, cfg, payload)
        await asyncio.to_thread(_send_sync, self.settings, msg)
        return DeliveryResult(detail=f"sent to {len(cfg.to)} recipient(s)")

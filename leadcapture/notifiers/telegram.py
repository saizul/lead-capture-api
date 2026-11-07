from __future__ import annotations

import json
from typing import Any

from leadcapture.notifiers.base import (
    USER_AGENT,
    DeliveryError,
    DeliveryResult,
    Notifier,
    escape_html,
    lead_lines,
    post_json,
)
from leadcapture.notifiers.configs import TelegramConfig

TELEGRAM_API = "https://api.telegram.org"


def build_telegram_text(payload: dict[str, Any]) -> str:
    form_name = escape_html(str(payload.get("form", {}).get("name", "form")))
    lines = [f"<b>New lead: {form_name}</b>", ""]
    for label, value in lead_lines(payload):
        lines.append(f"<b>{escape_html(label)}:</b> {escape_html(value)}")
    if payload.get("source_url"):
        lines += ["", f"Source: {escape_html(str(payload['source_url']))}"]
    return "\n".join(lines)[:4000]


class TelegramNotifier(Notifier):
    type = "telegram"
    config_model = TelegramConfig

    async def send(
        self, config: dict[str, Any], payload: dict[str, Any], delivery_id: str
    ) -> DeliveryResult:
        cfg = TelegramConfig.model_validate(config)
        token = cfg.bot_token or self.settings.telegram_bot_token
        if not token:
            raise DeliveryError("Telegram bot token is not configured", retryable=False)
        body = json.dumps(
            {
                "chat_id": cfg.chat_id,
                "text": build_telegram_text(payload),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
        ).encode()
        return await post_json(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            content=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            timeout=self.settings.http_timeout_seconds,
        )

from __future__ import annotations

import json
import time
from typing import Any

from leadcapture.notifiers.base import (
    USER_AGENT,
    DeliveryResult,
    Notifier,
    post_json,
)
from leadcapture.notifiers.configs import WebhookConfig
from leadcapture.security import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign_payload


def encode_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode()


class WebhookNotifier(Notifier):
    """Generic JSON webhook (n8n, Zapier, Make, custom). Signed when a secret is configured."""

    type = "webhook"
    config_model = WebhookConfig

    async def send(
        self, config: dict[str, Any], payload: dict[str, Any], delivery_id: str
    ) -> DeliveryResult:
        cfg = WebhookConfig.model_validate(config)
        body = encode_body(payload)
        timestamp = int(time.time())
        headers = {
            **cfg.headers,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-LeadCapture-Event": str(payload.get("event", "lead.created")),
            "X-LeadCapture-Delivery": delivery_id,
            TIMESTAMP_HEADER: str(timestamp),
        }
        if cfg.secret:
            headers[SIGNATURE_HEADER] = sign_payload(cfg.secret, timestamp, body)
        return await post_json(
            cfg.url, content=body, headers=headers, timeout=self.settings.http_timeout_seconds
        )

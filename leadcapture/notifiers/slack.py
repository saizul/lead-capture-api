from __future__ import annotations

import json
from typing import Any

from leadcapture.notifiers.base import (
    USER_AGENT,
    DeliveryResult,
    Notifier,
    lead_lines,
    post_json,
)
from leadcapture.notifiers.configs import SlackConfig


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_slack_message(payload: dict[str, Any]) -> dict[str, Any]:
    form_name = _esc(str(payload.get("form", {}).get("name", "form")))
    fields = [
        {"type": "mrkdwn", "text": f"*{_esc(k)}*\n{_esc(v)[:300]}"} for k, v in lead_lines(payload)
    ]
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"New lead: {form_name}"[:150]}}
    ]
    # Slack allows at most 10 fields per section block.
    for i in range(0, len(fields), 10):
        blocks.append({"type": "section", "fields": fields[i : i + 10]})
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"Lead ID `{payload.get('id')}`"}],
        }
    )
    return {"text": f"New lead from {form_name}", "blocks": blocks}


class SlackNotifier(Notifier):
    type = "slack"
    config_model = SlackConfig

    async def send(
        self, config: dict[str, Any], payload: dict[str, Any], delivery_id: str
    ) -> DeliveryResult:
        cfg = SlackConfig.model_validate(config)
        body = json.dumps(build_slack_message(payload)).encode()
        return await post_json(
            cfg.webhook_url,
            content=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            timeout=self.settings.http_timeout_seconds,
        )

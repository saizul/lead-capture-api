from __future__ import annotations

from leadcapture.config import Settings
from leadcapture.notifiers.base import DeliveryError, DeliveryResult, Notifier
from leadcapture.notifiers.email import EmailNotifier
from leadcapture.notifiers.slack import SlackNotifier
from leadcapture.notifiers.telegram import TelegramNotifier
from leadcapture.notifiers.webhook import WebhookNotifier

REGISTRY: dict[str, type[Notifier]] = {
    cls.type: cls for cls in (WebhookNotifier, EmailNotifier, TelegramNotifier, SlackNotifier)
}


def get_notifier(notifier_type: str, settings: Settings) -> Notifier:
    try:
        return REGISTRY[notifier_type](settings)
    except KeyError:
        raise DeliveryError(f"unknown notifier type: {notifier_type!r}", retryable=False) from None


__all__ = ["REGISTRY", "DeliveryError", "DeliveryResult", "Notifier", "get_notifier"]

"""Pydantic models describing each notifier's per-form configuration."""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import BaseModel, Field, field_validator

MASK = "***"


def _check_http_url(value: str, *, https_only: bool = False) -> str:
    allowed = ("https://",) if https_only else ("http://", "https://")
    if not value.startswith(allowed) or len(value) < 12 or " " in value:
        raise ValueError("must be an absolute " + ("https" if https_only else "http(s)") + " URL")
    return value


class _Base(BaseModel):
    enabled: bool = True
    SECRET_FIELDS: ClassVar[tuple[str, ...]] = ()


class WebhookConfig(_Base):
    type: Literal["webhook"] = "webhook"
    url: str = Field(examples=["https://n8n.example.com/webhook/leads"])
    secret: str | None = Field(default=None, description="Used to sign requests (HMAC-SHA256).")
    headers: dict[str, str] = Field(default_factory=dict)
    SECRET_FIELDS: ClassVar[tuple[str, ...]] = ("secret",)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _check_http_url(v)


class EmailConfig(_Base):
    type: Literal["email"] = "email"
    to: list[str] = Field(min_length=1, examples=[["sales@example.com"]])
    subject: str = Field(default="New lead from {form_name}", max_length=200)

    @field_validator("to")
    @classmethod
    def _addresses(cls, v: list[str]) -> list[str]:
        for addr in v:
            if "@" not in addr or any(ch in addr for ch in "\r\n ,;"):
                raise ValueError(f"invalid recipient: {addr!r}")
        return v


class TelegramConfig(_Base):
    type: Literal["telegram"] = "telegram"
    chat_id: str = Field(examples=["-1001234567890"])
    bot_token: str | None = Field(default=None, description="Falls back to TELEGRAM_BOT_TOKEN.")
    SECRET_FIELDS: ClassVar[tuple[str, ...]] = ("bot_token",)


class SlackConfig(_Base):
    type: Literal["slack"] = "slack"
    webhook_url: str = Field(examples=["https://hooks.slack.com/services/T000/B000/XXXX"])
    SECRET_FIELDS: ClassVar[tuple[str, ...]] = ("webhook_url",)

    @field_validator("webhook_url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _check_http_url(v, https_only=True)


NotifierConfig = Annotated[
    Union[WebhookConfig, EmailConfig, TelegramConfig, SlackConfig],  # noqa: UP007
    Field(discriminator="type"),
]

CONFIG_MODELS: dict[str, type[_Base]] = {
    "webhook": WebhookConfig,
    "email": EmailConfig,
    "telegram": TelegramConfig,
    "slack": SlackConfig,
}


def secret_fields(notifier_type: str) -> tuple[str, ...]:
    model = CONFIG_MODELS.get(notifier_type)
    return model.SECRET_FIELDS if model else ()


def mask_config(config: dict[str, Any]) -> dict[str, Any]:
    """Hide secret values before returning notifier config through the API."""
    masked = dict(config)
    for name in secret_fields(str(config.get("type"))):
        if masked.get(name):
            masked[name] = MASK
    return masked


def restore_masked(
    new_configs: list[dict[str, Any]], old_configs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Replace `***` placeholders in an update with the stored secret at the same position."""
    restored: list[dict[str, Any]] = []
    for index, cfg in enumerate(new_configs):
        item = dict(cfg)
        for name in secret_fields(str(item.get("type"))):
            if item.get(name) == MASK:
                old = old_configs[index] if index < len(old_configs) else {}
                if old.get("type") != item.get("type") or not old.get(name):
                    raise ValueError(
                        f"notifiers[{index}].{name} is masked but no stored value exists"
                    )
                item[name] = old[name]
        restored.append(item)
    return restored

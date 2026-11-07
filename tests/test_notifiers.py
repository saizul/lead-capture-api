from __future__ import annotations

import smtplib
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from leadcapture.config import Settings
from leadcapture.notifiers import REGISTRY, DeliveryError, get_notifier
from leadcapture.notifiers.configs import (
    MASK,
    EmailConfig,
    SlackConfig,
    WebhookConfig,
    mask_config,
    restore_masked,
)
from leadcapture.notifiers.slack import build_slack_message
from leadcapture.notifiers.telegram import build_telegram_text
from leadcapture.notifiers.webhook import encode_body
from leadcapture.security import verify_signature

PAYLOAD: dict[str, Any] = {
    "event": "lead.created",
    "id": "lead-1",
    "form": {"slug": "contact", "name": "Contact <Form>"},
    "name": "Ada",
    "email": "ada@example.com",
    "data": {"name": "Ada", "email": "ada@example.com", "message": "Hi <b>there</b> & bye"},
    "source_url": "https://example.com/p",
}


@pytest.fixture
def cfg() -> Settings:
    return Settings(_env_file=None, telegram_bot_token="", smtp_host="")  # type: ignore[call-arg]


def webhook(cfg: Settings):
    return get_notifier("webhook", cfg)


async def test_webhook_posts_signed_json(cfg, mock_http):
    route = mock_http.post("https://hook.example.com/in").respond(200)
    await webhook(cfg).send(
        {"type": "webhook", "url": "https://hook.example.com/in", "secret": "s3cret"},
        PAYLOAD,
        "delivery-1",
    )
    request = route.calls.last.request
    body = request.content
    assert body == encode_body(PAYLOAD)
    assert request.headers["content-type"] == "application/json"
    assert request.headers["x-leadcapture-delivery"] == "delivery-1"
    assert request.headers["x-leadcapture-event"] == "lead.created"
    ts = int(request.headers["x-leadcapture-timestamp"])
    assert verify_signature("s3cret", ts, body, request.headers["x-leadcapture-signature"])


async def test_webhook_without_secret_is_unsigned(cfg, mock_http):
    route = mock_http.post("https://hook.example.com/in").respond(204)
    await webhook(cfg).send({"type": "webhook", "url": "https://hook.example.com/in"}, PAYLOAD, "d")
    assert "x-leadcapture-signature" not in route.calls.last.request.headers


async def test_webhook_custom_headers(cfg, mock_http):
    route = mock_http.post("https://hook.example.com/in").respond(200)
    await webhook(cfg).send(
        {"type": "webhook", "url": "https://hook.example.com/in", "headers": {"X-Token": "abc"}},
        PAYLOAD,
        "d",
    )
    assert route.calls.last.request.headers["x-token"] == "abc"


@pytest.mark.parametrize("status", [500, 502, 503, 429, 408])
async def test_retryable_http_statuses(cfg, mock_http, status):
    mock_http.post("https://hook.example.com/in").respond(status)
    with pytest.raises(DeliveryError) as exc:
        await webhook(cfg).send(
            {"type": "webhook", "url": "https://hook.example.com/in"}, PAYLOAD, "d"
        )
    assert exc.value.retryable and exc.value.status_code == status


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
async def test_permanent_http_statuses(cfg, mock_http, status):
    mock_http.post("https://hook.example.com/in").respond(status)
    with pytest.raises(DeliveryError) as exc:
        await webhook(cfg).send(
            {"type": "webhook", "url": "https://hook.example.com/in"}, PAYLOAD, "d"
        )
    assert not exc.value.retryable


async def test_timeout_is_retryable(cfg, mock_http):
    mock_http.post("https://hook.example.com/in").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(DeliveryError) as exc:
        await webhook(cfg).send(
            {"type": "webhook", "url": "https://hook.example.com/in"}, PAYLOAD, "d"
        )
    assert exc.value.retryable and "timeout" in str(exc.value)


async def test_connection_error_is_retryable(cfg, mock_http):
    mock_http.post("https://hook.example.com/in").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(DeliveryError) as exc:
        await webhook(cfg).send(
            {"type": "webhook", "url": "https://hook.example.com/in"}, PAYLOAD, "d"
        )
    assert exc.value.retryable


async def test_slack_posts_blocks(cfg, mock_http):
    url = "https://hooks.slack.com/services/T/B/X"
    route = mock_http.post(url).respond(200, text="ok")
    await get_notifier("slack", cfg).send({"type": "slack", "webhook_url": url}, PAYLOAD, "d")
    sent = route.calls.last.request
    import json

    message = json.loads(sent.content)
    assert message["blocks"][0]["type"] == "header"
    assert "Contact &lt;Form&gt;" in message["text"]


def test_slack_escapes_user_content():
    message = build_slack_message(PAYLOAD)
    dumped = str(message)
    assert "&lt;b&gt;there&lt;/b&gt;" in dumped and "<b>there</b>" not in dumped


async def test_slack_server_error_retryable(cfg, mock_http):
    url = "https://hooks.slack.com/services/T/B/X"
    mock_http.post(url).respond(500, text="oops")
    with pytest.raises(DeliveryError) as exc:
        await get_notifier("slack", cfg).send({"type": "slack", "webhook_url": url}, PAYLOAD, "d")
    assert exc.value.retryable


async def test_telegram_sends_html_message(cfg, mock_http):
    route = mock_http.post("https://api.telegram.org/botTOKEN123/sendMessage").respond(
        200, json={"ok": True}
    )
    await get_notifier("telegram", cfg).send(
        {"type": "telegram", "chat_id": "-100", "bot_token": "TOKEN123"}, PAYLOAD, "d"
    )
    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent["chat_id"] == "-100" and sent["parse_mode"] == "HTML"
    assert "&lt;b&gt;there&lt;/b&gt;" in sent["text"]


async def test_telegram_uses_global_token_fallback(mock_http):
    settings = Settings(_env_file=None, telegram_bot_token="GLOBAL")  # type: ignore[call-arg]
    route = mock_http.post("https://api.telegram.org/botGLOBAL/sendMessage").respond(200)
    await get_notifier("telegram", settings).send(
        {"type": "telegram", "chat_id": "1"}, PAYLOAD, "d"
    )
    assert route.called


async def test_telegram_without_token_is_permanent_failure(cfg):
    with pytest.raises(DeliveryError) as exc:
        await get_notifier("telegram", cfg).send({"type": "telegram", "chat_id": "1"}, PAYLOAD, "d")
    assert not exc.value.retryable


def test_telegram_text_is_escaped_and_truncated():
    big = {**PAYLOAD, "data": {"message": "x" * 10000}}
    text = build_telegram_text(big)
    assert len(text) <= 4000
    assert "Contact &lt;Form&gt;" in build_telegram_text(PAYLOAD)


class FakeSMTP:
    sent: list[Any] = []
    fail_with: Exception | None = None
    instances: list[FakeSMTP] = []

    def __init__(self, host: str, port: int, timeout: float = 0) -> None:
        self.host, self.port = host, port
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        FakeSMTP.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self, context: Any = None) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.logged_in = (user, password)

    def send_message(self, msg: Any) -> None:
        if FakeSMTP.fail_with:
            raise FakeSMTP.fail_with
        FakeSMTP.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail_with, FakeSMTP.instances = [], None, []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        smtp_host="smtp.test",
        smtp_port=2525,
        smtp_username="user",
        smtp_password="pw",
        smtp_from="leads@example.com",
    )


async def test_email_is_sent(smtp):
    await get_notifier("email", smtp).send(
        {"type": "email", "to": ["sales@example.com", "ceo@example.com"]}, PAYLOAD, "d"
    )
    msg = FakeSMTP.sent[0]
    assert msg["To"] == "sales@example.com, ceo@example.com"
    assert msg["Subject"] == "New lead from Contact <Form>"
    assert msg["Reply-To"] == "ada@example.com"
    assert "ada@example.com" in msg.get_content()
    server = FakeSMTP.instances[0]
    assert server.started_tls and server.logged_in == ("user", "pw")


async def test_email_subject_cannot_inject_headers(smtp):
    payload = {**PAYLOAD, "form": {"name": "Evil\r\nBcc: x@y.z"}}
    await get_notifier("email", smtp).send({"type": "email", "to": ["a@b.co"]}, payload, "d")
    assert "\n" not in FakeSMTP.sent[0]["Subject"]
    assert FakeSMTP.sent[0]["Bcc"] is None


async def test_email_requires_smtp_host(cfg):
    with pytest.raises(DeliveryError) as exc:
        await get_notifier("email", cfg).send({"type": "email", "to": ["a@b.co"]}, PAYLOAD, "d")
    assert not exc.value.retryable


async def test_email_permanent_smtp_error(smtp):
    FakeSMTP.fail_with = smtplib.SMTPResponseException(550, b"mailbox unavailable")
    with pytest.raises(DeliveryError) as exc:
        await get_notifier("email", smtp).send({"type": "email", "to": ["a@b.co"]}, PAYLOAD, "d")
    assert not exc.value.retryable


async def test_email_temporary_smtp_error(smtp):
    FakeSMTP.fail_with = smtplib.SMTPResponseException(451, b"try later")
    with pytest.raises(DeliveryError) as exc:
        await get_notifier("email", smtp).send({"type": "email", "to": ["a@b.co"]}, PAYLOAD, "d")
    assert exc.value.retryable


async def test_email_connection_error_is_retryable(smtp):
    FakeSMTP.fail_with = ConnectionRefusedError("nope")
    with pytest.raises(DeliveryError) as exc:
        await get_notifier("email", smtp).send({"type": "email", "to": ["a@b.co"]}, PAYLOAD, "d")
    assert exc.value.retryable


def test_registry_contains_all_notifiers(cfg):
    assert set(REGISTRY) == {"webhook", "email", "telegram", "slack"}


def test_unknown_notifier_type(cfg):
    with pytest.raises(DeliveryError) as exc:
        get_notifier("carrier-pigeon", cfg)
    assert not exc.value.retryable


def test_config_validation():
    with pytest.raises(ValidationError):
        WebhookConfig(url="ftp://x.example.com/hook")
    with pytest.raises(ValidationError):
        SlackConfig(webhook_url="http://hooks.slack.com/insecure")
    with pytest.raises(ValidationError):
        EmailConfig(to=["not-an-address"])
    with pytest.raises(ValidationError):
        EmailConfig(to=[])


def test_mask_and_restore_secrets():
    stored = [{"type": "webhook", "url": "https://h.example.com/x", "secret": "topsecret"}]
    masked = [mask_config(stored[0])]
    assert masked[0]["secret"] == MASK
    restored = restore_masked(masked, stored)
    assert restored[0]["secret"] == "topsecret"


def test_restore_masked_without_stored_value_fails():
    with pytest.raises(ValueError):
        restore_masked([{"type": "webhook", "url": "https://h.example.com", "secret": MASK}], [])

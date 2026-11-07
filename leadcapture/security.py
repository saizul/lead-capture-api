from __future__ import annotations

import hashlib
import hmac
import secrets
import time

API_KEY_PREFIX = "lc_"
SIGNATURE_HEADER = "X-LeadCapture-Signature"
TIMESTAMP_HEADER = "X-LeadCapture-Timestamp"


def hash_ip(ip: str, salt: str) -> str:
    """Salted HMAC of the client IP. The raw address is never stored."""
    return hmac.new(salt.encode(), ip.encode(), hashlib.sha256).hexdigest()


def hash_api_key(raw_key: str) -> str:
    # API keys are 256-bit random values, so a fast hash is appropriate here.
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Return (raw_key, display_prefix, key_hash). The raw key is shown once."""
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[:8], hash_api_key(raw)


def sign_payload(secret: str, timestamp: int, body: bytes) -> str:
    """Signature scheme: HMAC-SHA256 over `"<timestamp>.<raw body>"`, hex, `sha256=` prefixed."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify_signature(
    secret: str,
    timestamp: int,
    body: bytes,
    signature: str,
    *,
    tolerance: int = 300,
    now: float | None = None,
) -> bool:
    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance:
        return False
    expected = sign_payload(secret, timestamp, body)
    return hmac.compare_digest(expected, signature)


def make_form_token(secret: str, slug: str, now: float | None = None) -> str:
    """Signed render timestamp used for the minimum time-to-submit check."""
    ts = int(time.time() if now is None else now)
    mac = hmac.new(secret.encode(), f"form:{slug}:{ts}".encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{mac[:32]}"


def parse_form_token(secret: str, slug: str, token: str) -> int | None:
    """Return the issue timestamp if the token is authentic, else None."""
    try:
        ts_part, mac_part = token.split(".", 1)
        ts = int(ts_part)
    except (ValueError, AttributeError):
        return None
    expected = hmac.new(secret.encode(), f"form:{slug}:{ts}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected[:32], mac_part):
        return None
    return ts

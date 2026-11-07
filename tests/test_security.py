from __future__ import annotations

import time

from leadcapture.security import (
    generate_api_key,
    hash_api_key,
    hash_ip,
    make_form_token,
    parse_form_token,
    sign_payload,
    verify_signature,
)


def test_hash_ip_is_deterministic_and_salted():
    assert hash_ip("1.2.3.4", "s") == hash_ip("1.2.3.4", "s")
    assert hash_ip("1.2.3.4", "s") != hash_ip("1.2.3.4", "other")
    assert hash_ip("1.2.3.4", "s") != hash_ip("1.2.3.5", "s")
    assert "1.2.3.4" not in hash_ip("1.2.3.4", "s")


def test_api_key_generation():
    raw, prefix, key_hash = generate_api_key()
    assert raw.startswith("lc_") and raw.startswith(prefix)
    assert key_hash == hash_api_key(raw) and raw not in key_hash
    assert generate_api_key()[0] != raw


def test_signature_roundtrip():
    body = b'{"a":1}'
    sig = sign_payload("secret", 1000, body)
    assert sig.startswith("sha256=")
    assert verify_signature("secret", 1000, body, sig, now=1010)


def test_signature_rejects_tampering():
    sig = sign_payload("secret", 1000, b'{"a":1}')
    assert not verify_signature("secret", 1000, b'{"a":2}', sig, now=1000)
    assert not verify_signature("wrong", 1000, b'{"a":1}', sig, now=1000)
    assert not verify_signature("secret", 1001, b'{"a":1}', sig, now=1000)


def test_signature_rejects_stale_timestamp():
    sig = sign_payload("secret", 1000, b"x")
    assert not verify_signature("secret", 1000, b"x", sig, tolerance=300, now=2000)


def test_form_token_roundtrip():
    now = time.time()
    token = make_form_token("k", "contact", now)
    assert parse_form_token("k", "contact", token) == int(now)


def test_form_token_is_bound_to_slug_and_secret():
    token = make_form_token("k", "contact")
    assert parse_form_token("k", "other", token) is None
    assert parse_form_token("other", "contact", token) is None


def test_form_token_garbage():
    assert parse_form_token("k", "contact", "garbage") is None
    assert parse_form_token("k", "contact", "abc.def") is None
    assert parse_form_token("k", "contact", "") is None

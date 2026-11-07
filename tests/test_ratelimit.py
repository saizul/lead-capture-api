from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from leadcapture.main import create_app
from leadcapture.services.ratelimit import (
    MemoryRateLimiter,
    RedisRateLimiter,
    build_rate_limiter,
)
from tests.helpers import VALID_SUBMISSION, create_form


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_memory_allows_up_to_limit_then_blocks():
    limiter = MemoryRateLimiter(Clock())
    results = [await limiter.hit("k", 3, 60) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[0].remaining == 2


async def test_memory_retry_after_reflects_oldest_hit():
    clock = Clock()
    limiter = MemoryRateLimiter(clock)
    await limiter.hit("k", 1, 60)
    clock.now += 20
    blocked = await limiter.hit("k", 1, 60)
    assert not blocked.allowed and blocked.retry_after == 40


async def test_memory_window_slides():
    clock = Clock()
    limiter = MemoryRateLimiter(clock)
    for _ in range(2):
        await limiter.hit("k", 2, 60)
    assert not (await limiter.hit("k", 2, 60)).allowed
    clock.now += 61
    assert (await limiter.hit("k", 2, 60)).allowed


async def test_memory_keys_are_independent():
    limiter = MemoryRateLimiter(Clock())
    await limiter.hit("a", 1, 60)
    assert (await limiter.hit("b", 1, 60)).allowed
    assert not (await limiter.hit("a", 1, 60)).allowed


async def test_blocked_hits_do_not_extend_the_window():
    clock = Clock()
    limiter = MemoryRateLimiter(clock)
    await limiter.hit("k", 1, 10)
    for _ in range(5):
        clock.now += 1
        await limiter.hit("k", 1, 10)
    clock.now += 5
    assert (await limiter.hit("k", 1, 10)).allowed


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expiry: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.expiry[key] = seconds

    async def aclose(self) -> None:
        return None


class BrokenRedis(FakeRedis):
    async def incr(self, key: str) -> int:
        raise ConnectionError("redis down")


async def test_redis_limiter_counts_in_fixed_window():
    fake = FakeRedis()
    limiter = RedisRateLimiter(fake, clock=lambda: 125.0)
    results = [await limiter.hit("k", 2, 60) for _ in range(3)]
    assert [r.allowed for r in results] == [True, True, False]
    assert results[2].retry_after == 55
    assert list(fake.expiry.values()) == [61]


async def test_redis_limiter_new_window_resets():
    fake = FakeRedis()
    now = [125.0]
    limiter = RedisRateLimiter(fake, clock=lambda: now[0])
    await limiter.hit("k", 1, 60)
    assert not (await limiter.hit("k", 1, 60)).allowed
    now[0] = 190.0
    assert (await limiter.hit("k", 1, 60)).allowed


async def test_redis_limiter_fails_open():
    limiter = RedisRateLimiter(BrokenRedis())
    assert (await limiter.hit("k", 1, 60)).allowed


def test_factory_defaults_to_memory():
    assert isinstance(build_rate_limiter(None), MemoryRateLimiter)
    assert isinstance(build_rate_limiter(""), MemoryRateLimiter)


def test_factory_builds_redis_limiter():
    assert isinstance(build_rate_limiter("redis://localhost:6379/0"), RedisRateLimiter)


async def test_per_ip_limit_returns_429_with_retry_after(app, client):
    await create_form(app, rate_limit_per_ip=2)
    codes = [
        (await client.post("/v1/forms/contact/submit", json=VALID_SUBMISSION)).status_code
        for _ in range(3)
    ]
    assert codes == [201, 201, 429]
    blocked = await client.post("/v1/forms/contact/submit", json=VALID_SUBMISSION)
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["Retry-After"]) >= 1


async def test_rate_limit_applies_before_validation(app, client):
    await create_form(app, rate_limit_per_ip=1)
    await client.post("/v1/forms/contact/submit", json={})
    resp = await client.post("/v1/forms/contact/submit", json={})
    assert resp.status_code == 429


async def test_ip_limits_are_per_form(app, client):
    await create_form(app, slug="a", rate_limit_per_ip=1)
    await create_form(app, slug="b", rate_limit_per_ip=1)
    assert (await client.post("/v1/forms/a/submit", json=VALID_SUBMISSION)).status_code == 201
    assert (await client.post("/v1/forms/b/submit", json=VALID_SUBMISSION)).status_code == 201
    assert (await client.post("/v1/forms/a/submit", json=VALID_SUBMISSION)).status_code == 429


@pytest.fixture
def settings_overrides():
    return {"trust_proxy": True}


async def test_per_form_limit_spans_ips(app, client):
    await create_form(app, rate_limit_per_form=2, rate_limit_per_ip=100)
    codes = []
    for i in range(3):
        resp = await client.post(
            "/v1/forms/contact/submit",
            json=VALID_SUBMISSION,
            headers={"X-Forwarded-For": f"10.0.0.{i}"},
        )
        codes.append(resp.status_code)
    assert codes == [201, 201, 429]


async def test_forwarded_for_is_used_when_proxy_trusted(app, client):
    await create_form(app, rate_limit_per_ip=1)
    first = await client.post(
        "/v1/forms/contact/submit", json=VALID_SUBMISSION, headers={"X-Forwarded-For": "9.9.9.9"}
    )
    other = await client.post(
        "/v1/forms/contact/submit", json=VALID_SUBMISSION, headers={"X-Forwarded-For": "8.8.8.8"}
    )
    again = await client.post(
        "/v1/forms/contact/submit",
        json=VALID_SUBMISSION,
        headers={"X-Forwarded-For": "9.9.9.9, 1.1.1.1"},
    )
    assert (first.status_code, other.status_code, again.status_code) == (201, 201, 429)


async def test_forwarded_for_ignored_when_proxy_not_trusted(settings, tmp_path):
    untrusted = settings.model_copy(update={"trust_proxy": False})
    app = create_app(untrusted)
    from leadcapture.db import Base

    async with app.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await create_form(app, rate_limit_per_ip=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        a = await c.post(
            "/v1/forms/contact/submit",
            json=VALID_SUBMISSION,
            headers={"X-Forwarded-For": "1.1.1.1"},
        )
        b = await c.post(
            "/v1/forms/contact/submit",
            json=VALID_SUBMISSION,
            headers={"X-Forwarded-For": "2.2.2.2"},
        )
    await app.state.engine.dispose()
    assert (a.status_code, b.status_code) == (201, 429)

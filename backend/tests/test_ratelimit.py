"""Rate limiting (Phase 3.11 / ADR-0015): fixed-window unit behaviour + the
middleware's 429 on an auth brute-force burst."""
import pytest

from app.core import ratelimit
from app.core.ratelimit import FixedWindowLimiter


def test_allows_up_to_limit_then_blocks():
    lim = FixedWindowLimiter(limit=3, window_seconds=60)
    now = 1000.0
    assert lim.allow("k", now=now) == (True, 0)
    assert lim.allow("k", now=now)[0] is True
    assert lim.allow("k", now=now)[0] is True     # 3rd — still allowed
    allowed, retry = lim.allow("k", now=now)       # 4th — over the limit
    assert allowed is False
    assert 0 < retry <= 61


def test_window_resets_after_it_elapses():
    lim = FixedWindowLimiter(limit=1, window_seconds=60)
    assert lim.allow("k", now=100.0)[0] is True
    assert lim.allow("k", now=100.0)[0] is False   # blocked within the window
    assert lim.allow("k", now=161.0)[0] is True    # new window → allowed again


def test_keys_are_independent():
    lim = FixedWindowLimiter(limit=1, window_seconds=60)
    assert lim.allow("a", now=100.0)[0] is True
    assert lim.allow("b", now=100.0)[0] is True    # different key, own budget
    assert lim.allow("a", now=100.0)[0] is False


def test_zero_or_negative_limit_disables_the_tier():
    lim = FixedWindowLimiter(limit=0, window_seconds=60)
    for _ in range(1000):
        assert lim.allow("k")[0] is True


def test_prune_keeps_map_bounded():
    lim = FixedWindowLimiter(limit=1, window_seconds=10)
    # Seed >10k stale keys in a past window, then a fresh call triggers the prune.
    for i in range(10_050):
        lim.allow(f"stale-{i}", now=0.0)
    lim.allow("fresh", now=1000.0)
    assert len(lim._hits) < 100      # stale keys pruned, only recent survive


@pytest.mark.asyncio
async def test_auth_brute_force_returns_429(client):
    """A burst of failed logins from one client trips the strict auth tier."""
    original = ratelimit.auth_limiter.limit
    ratelimit.auth_limiter.limit = 3          # tighten for a short, deterministic burst
    try:
        codes = []
        for _ in range(5):
            r = await client.post(
                "/api/v1/auth/login",
                json={"email": "nope@nope.io", "password": "wrong"},
            )
            codes.append(r.status_code)
        assert codes.count(429) >= 1
        last = await client.post(
            "/api/v1/auth/login", json={"email": "nope@nope.io", "password": "wrong"}
        )
        assert last.status_code == 429
        assert int(last.headers["retry-after"]) >= 1
    finally:
        ratelimit.auth_limiter.limit = original


@pytest.mark.asyncio
async def test_health_probe_is_never_limited(client):
    original = ratelimit.general_limiter.limit
    ratelimit.general_limiter.limit = 1
    try:
        for _ in range(5):
            r = await client.get("/health")
            assert r.status_code == 200
    finally:
        ratelimit.general_limiter.limit = original


@pytest.mark.asyncio
async def test_disabled_flag_bypasses_the_limiter(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    original = ratelimit.auth_limiter.limit
    ratelimit.auth_limiter.limit = 1
    try:
        for _ in range(4):
            r = await client.post(
                "/api/v1/auth/login", json={"email": "x@x.io", "password": "wrong"}
            )
            assert r.status_code != 429
    finally:
        ratelimit.auth_limiter.limit = original

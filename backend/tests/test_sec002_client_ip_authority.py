"""SEC-002 (audit 2026-09-05) — ONE client-IP authority.

The app resolves the caller's address in `ratelimit._client_ip` with a
spoofing-resistant rule: honour `X-Forwarded-For` only when
`TRUSTED_PROXY_COUNT > 0`, and then take the value that many hops from the
RIGHT (what the outermost trusted proxy saw), never the client-supplied
leftmost value. Both the auth rate limit and the audit trail's `ip` column use
it. That rule was neutralised one layer below: every deployment file ran
uvicorn with `--proxy-headers --forwarded-allow-ips '*'`, and uvicorn then
wrote the LEFTMOST X-Forwarded-For value into `scope["client"]` — the very
field `_client_ip` falls back to as "the socket peer" when the proxy count is
0 (the default nothing set). A forged header minted a fresh rate-limit bucket
per request and forged audit attribution.

These tests pin both halves of the fix:
- structurally, no deployment file may hand the client address to uvicorn's
  wildcard trust again, and every production deployment file states its hop
  count;
- behaviourally, the app's rule takes the right-hand value and ignores the
  spoofed one.
"""

from __future__ import annotations

import pathlib

import pytest

from app.core import ratelimit
from app.core.config import settings

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

DEPLOYMENT_FILES = (
    "backend/Dockerfile",
    "docker-compose.yml",
    "docker-compose.prod.yml",
    "docker-compose.hostinger.yml",
    "deploy/k8s/10-config.yaml",
    "deploy/k8s/30-backend.yaml",
)

# Files that put the app in production mode must say how many proxy hops sit in
# front of it — otherwise the default (0) keys on the socket peer, which behind
# nginx is nginx, and every user shares one rate-limit bucket.
PRODUCTION_FILES = (
    "docker-compose.prod.yml",
    "docker-compose.hostinger.yml",
    "deploy/k8s/10-config.yaml",
)


def test_sec002_no_deployment_file_hands_the_client_address_to_uvicorns_wildcard_trust():
    # The flag itself is forbidden; a mention inside an explanatory comment is not.
    offenders = [
        rel
        for rel in DEPLOYMENT_FILES
        if any(
            "forwarded-allow-ips" in line and not line.lstrip().startswith("#")
            for line in (REPO / rel).read_text().splitlines()
        )
    ]
    assert offenders == [], offenders


def test_sec002_every_production_deployment_file_states_its_proxy_hop_count():
    missing = [
        rel for rel in PRODUCTION_FILES if "TRUSTED_PROXY_COUNT" not in (REPO / rel).read_text()
    ]
    assert missing == [], missing


def _scope(xff: str | None, peer: str = "10.0.0.9") -> dict:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return {"type": "http", "headers": headers, "client": (peer, 12345)}


def test_sec002_with_one_trusted_hop_the_spoofed_leftmost_value_is_ignored(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    # nginx APPENDS the real peer: the attacker's value stays leftmost.
    assert ratelimit.client_ip(_scope("6.6.6.6, 203.0.113.7")) == "203.0.113.7"
    # A different forged value must land in the SAME bucket.
    assert ratelimit.client_ip(_scope("7.7.7.7, 203.0.113.7")) == "203.0.113.7"
    # Two hops (CDN + nginx): the second from the right.
    monkeypatch.setattr(settings, "trusted_proxy_count", 2)
    assert ratelimit.client_ip(_scope("6.6.6.6, 203.0.113.7, 198.51.100.2")) == "203.0.113.7"


def test_sec002_with_no_trusted_hop_the_header_is_ignored_entirely(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_count", 0)
    assert ratelimit.client_ip(_scope("6.6.6.6, 203.0.113.7", peer="10.0.0.9")) == "10.0.0.9"
    assert ratelimit.client_ip(_scope(None, peer="10.0.0.9")) == "10.0.0.9"


@pytest.mark.asyncio
async def test_sec002_a_forged_header_cannot_mint_a_fresh_auth_bucket(client, monkeypatch):
    """The whole point, at the route: with one trusted hop and the limiter on,
    N+1 login attempts that each forge a DIFFERENT leftmost address are still
    one client and hit the limit."""
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    # The auth limiter is built at import from the configured limit; swap in a
    # three-per-minute one for the duration of this test.
    monkeypatch.setattr(ratelimit, "auth_limiter", ratelimit.FixedWindowLimiter(3, 60))
    try:
        codes = []
        for i in range(5):
            r = await client.post(
                "/api/v1/auth/login",
                json={"email": f"nobody{i}@corp.example", "password": "x" * 12},
                headers={"X-Forwarded-For": f"6.6.6.{i}, 203.0.113.7"},
            )
            codes.append(r.status_code)
        assert 429 in codes, codes
        assert codes[-1] == 429, codes
    finally:
        ratelimit.reset_all()

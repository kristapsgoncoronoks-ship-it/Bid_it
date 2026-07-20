"""Security response headers + HSTS behaviour behind a TLS proxy."""
import pytest

from app.core.config import settings


@pytest.mark.asyncio
async def test_baseline_security_headers_present(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
async def test_hsts_absent_by_default(client):
    r = await client.get("/health")
    assert "strict-transport-security" not in r.headers


@pytest.mark.asyncio
async def test_hsts_emitted_on_https_when_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "hsts_enabled", True)
    # Simulate Cloudflare/nginx terminating TLS and forwarding the scheme.
    r = await client.get("/health", headers={"X-Forwarded-Proto": "https"})
    assert "strict-transport-security" in r.headers
    assert "includeSubDomains" in r.headers["strict-transport-security"]


@pytest.mark.asyncio
async def test_hsts_not_emitted_on_plain_http_even_if_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "hsts_enabled", True)
    r = await client.get("/health", headers={"X-Forwarded-Proto": "http"})
    assert "strict-transport-security" not in r.headers

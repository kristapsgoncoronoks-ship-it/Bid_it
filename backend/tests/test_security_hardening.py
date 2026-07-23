"""Security-hardening pass: login lockout, webhook SSRF guard, API CSP/permissions
headers, and the invoice-attachment malware/executable gate."""

import io

import pytest

from app.core.config import settings


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _register(client, email="lockme@corp.io", password="supersecret"):
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "LockCo",
            "name": "Owner",
            "email": email,
            "password": password,
        },
    )
    assert r.status_code == 201, r.text
    return email, password


# --------------------------------------------------------------------------- #
# H1 — account lockout
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_login_locks_after_repeated_failures(client, monkeypatch):
    monkeypatch.setattr(settings, "login_max_failed_attempts", 3)
    email, password = await _register(client)
    for _ in range(3):
        bad = await client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
        assert bad.status_code == 401
    # Account now locked — even the CORRECT password is refused with 429.
    locked = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert locked.status_code == 429
    assert "Retry-After" in locked.headers


@pytest.mark.asyncio
async def test_successful_login_resets_failure_count(client, monkeypatch):
    monkeypatch.setattr(settings, "login_max_failed_attempts", 3)
    email, password = await _register(client, email="reset@corp.io")
    # Two failures (below threshold) then a success clears the counter…
    for _ in range(2):
        await client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
    ok = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert ok.status_code == 200
    # …so a later single failure is a plain 401, not a lockout.
    again = await client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
    assert again.status_code == 401


# --------------------------------------------------------------------------- #
# M2 — webhook SSRF guard
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hook",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://localhost:8000/x",
        "https://10.0.0.5/hook",  # RFC1918
        "http://[::1]/hook",  # IPv6 loopback
        "ftp://example.test/x",  # non-http scheme
    ],
)
async def test_webhook_rejects_unsafe_urls(auth_client, url):
    r = await auth_client.post("/api/v1/webhooks", json={"url": url, "events": "*"})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_webhook_accepts_public_url_and_rechecks_on_update(auth_client):
    ok = await auth_client.post(
        "/api/v1/webhooks", json={"url": "https://example.test/hook", "events": "*"}
    )
    assert ok.status_code == 201, ok.text
    wid = ok.json()["id"]
    # Repointing at an internal host must be refused too.
    bad = await auth_client.patch(f"/api/v1/webhooks/{wid}", json={"url": "http://127.0.0.1/x"})
    assert bad.status_code == 422


# --------------------------------------------------------------------------- #
# M4 — security headers
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_api_responses_carry_csp_and_permissions_policy(client):
    r = await client.get("/health")
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    assert "permissions-policy" in r.headers
    assert r.headers["cross-origin-resource-policy"] == "same-origin"


@pytest.mark.asyncio
async def test_docs_are_exempt_from_strict_csp(client):
    # The OpenAPI schema (dev only) must not carry the API's locked-down CSP, or
    # Swagger UI's own assets would be blocked.
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    assert "content-security-policy" not in r.headers


# --------------------------------------------------------------------------- #
# M1 — invoice-attachment executable/malware gate
# --------------------------------------------------------------------------- #
async def _make_invoice(auth_client):
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Acme Supplies",
            "invoice_number": "INV-SEC-1",
            "issue_date": "2026-05-01",
            "currency": "EUR",
            "line_items": [
                {"description": "W", "quantity": "1", "unit_price": "100", "tax_rate": "0"}
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_invoice_attachment_rejects_executable(auth_client):
    iid = await _make_invoice(auth_client)
    exe = await auth_client.post(
        f"/api/v1/invoices/{iid}/attachments",
        files={
            "file": ("payload.exe", io.BytesIO(b"MZ\x90\x00malware"), "application/octet-stream")
        },
    )
    assert exe.status_code == 415


@pytest.mark.asyncio
async def test_invoice_attachment_rejects_eicar(auth_client):
    from app.services.filesec import EICAR

    iid = await _make_invoice(auth_client)
    virus = await auth_client.post(
        f"/api/v1/invoices/{iid}/attachments",
        files={"file": ("po.txt", io.BytesIO(EICAR), "text/plain")},
    )
    assert virus.status_code == 415


@pytest.mark.asyncio
async def test_invoice_attachment_allows_plain_document(auth_client):
    iid = await _make_invoice(auth_client)
    ok = await auth_client.post(
        f"/api/v1/invoices/{iid}/attachments",
        files={"file": ("po.txt", io.BytesIO(b"purchase order 42"), "text/plain")},
    )
    assert ok.status_code == 201

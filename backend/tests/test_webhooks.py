"""Outbound webhooks: registration, signed delivery via the job queue, retries,
and the delivery log — plus the metered upload usage limit."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
from sqlalchemy import select

from app.models import job as jobmodel
from app.models import webhook as wh
from app.models.organization import Organization
from app.models.webhook import WebhookDelivery
from app.services import jobs, webhooks


async def _org(db_session) -> str:
    return await db_session.scalar(select(Organization.id).limit(1))


def test_signature_is_hmac_sha256():
    body = b'{"event":"ping"}'
    sig = webhooks.sign("topsecret", body)
    expected = "sha256=" + hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
    assert sig == expected


@pytest.mark.asyncio
async def test_register_endpoint_returns_secret_once(auth_client):
    r = await auth_client.post(
        "/api/v1/webhooks", json={"url": "https://example.test/hook", "events": "*"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["secret"]  # returned at creation
    assert body["url"] == "https://example.test/hook"

    # The list view never exposes the secret.
    lst = (await auth_client.get("/api/v1/webhooks")).json()
    assert lst[0]["id"] == body["id"]
    assert "secret" not in lst[0]


@pytest.mark.asyncio
async def test_bad_url_rejected(auth_client):
    r = await auth_client.post("/api/v1/webhooks", json={"url": "ftp://nope"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_emit_enqueues_delivery_and_delivers(auth_client, db_session, monkeypatch):
    org = await _org(db_session)
    ep = (
        await auth_client.post(
            "/api/v1/webhooks",
            json={"url": "https://example.test/hook", "events": "invoice.created"},
        )
    ).json()

    # Capture what the delivery would POST, and pretend the receiver returns 200.
    captured = {}

    async def _fake_post(url, body, headers):
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        return 200, "ok", None

    monkeypatch.setattr(webhooks, "_http_post", _fake_post)

    # Emit an event (as a domain action would), then commit.
    n = await webhooks.emit(db_session, org, "invoice.created", {"invoice_number": "INV-9"})
    await db_session.commit()
    assert n == 1

    # A delivery row + a queued job exist.
    deliv = await db_session.scalar(select(WebhookDelivery).where(WebhookDelivery.org_id == org))
    assert deliv.status == wh.PENDING

    # Run the queue → the webhook.deliver job fires.
    job = await jobs.run_once(db_session, "w1")
    assert job is not None and job.kind == webhooks.WEBHOOK_DELIVER
    assert job.status == jobmodel.SUCCEEDED

    # The delivery is recorded as delivered, and the POST was signed correctly.
    await db_session.refresh(deliv)
    assert deliv.status == wh.DELIVERED
    assert deliv.response_code == 200
    assert captured["url"] == "https://example.test/hook"
    assert captured["headers"]["X-InvoiceIQ-Event"] == "invoice.created"
    expected_sig = (
        "sha256=" + hmac.new(ep["secret"].encode(), captured["body"], hashlib.sha256).hexdigest()
    )
    assert captured["headers"]["X-InvoiceIQ-Signature"] == expected_sig
    assert json.loads(captured["body"])["data"]["invoice_number"] == "INV-9"


@pytest.mark.asyncio
async def test_delivery_failure_retries_then_dead(auth_client, db_session, monkeypatch):
    org = await _org(db_session)
    await auth_client.post(
        "/api/v1/webhooks", json={"url": "https://example.test/hook", "events": "*"}
    )

    async def _fail_post(url, body, headers):
        return 500, "server error", None

    monkeypatch.setattr(webhooks, "_http_post", _fail_post)

    await webhooks.emit(db_session, org, "ping", {"x": 1})
    await db_session.commit()

    from datetime import datetime

    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    # Force the job's attempt budget down to 1 so it dead-letters fast.
    job = await db_session.scalar(select(jobs.Job).where(jobs.Job.kind == webhooks.WEBHOOK_DELIVER))
    job.max_attempts = 1
    job.run_after = now
    await db_session.commit()

    done = await jobs.run_once(db_session, "w1", now=now)
    assert done.status == jobmodel.DEAD

    deliv = await db_session.scalar(select(WebhookDelivery).where(WebhookDelivery.org_id == org))
    assert deliv.status == wh.FAILED
    assert deliv.response_code == 500
    assert "HTTP 500" in deliv.last_error


@pytest.mark.asyncio
async def test_ping_and_deliveries_log(auth_client, monkeypatch):
    async def _ok(url, body, headers):
        return 204, "", None

    monkeypatch.setattr(webhooks, "_http_post", _ok)
    ep = (await auth_client.post("/api/v1/webhooks", json={"url": "https://example.test/h"})).json()

    ping = await auth_client.post(f"/api/v1/webhooks/{ep['id']}/ping")
    assert ping.status_code == 202
    assert ping.json()["enqueued"] == 1

    log = (await auth_client.get(f"/api/v1/webhooks/{ep['id']}/deliveries")).json()
    assert len(log) == 1 and log[0]["event_type"] == "ping"


@pytest.mark.asyncio
async def test_webhooks_tenant_isolated(auth_client, client):
    ep = (await auth_client.post("/api/v1/webhooks", json={"url": "https://a.test/h"})).json()
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other Co",
            "name": "O",
            "email": "ow@o.io",
            "password": "supersecret2",
        },
    )
    client.headers["Authorization"] = f"Bearer {reg.json()['token']['access_token']}"
    assert (await client.get("/api/v1/webhooks")).json() == []
    assert (await client.get(f"/api/v1/webhooks/{ep['id']}/deliveries")).status_code == 404


@pytest.mark.asyncio
async def test_upload_quota_enforced(auth_client, db_session):
    """WO-47: the org's PLAN monthly upload limit blocks once it's reached —
    org-wide, regardless of the caller's role (the default org plan is
    "trial")."""
    from app.services import access

    # Set the (default "trial") plan's upload limit to 1.
    await access.set_limits(db_session, "trial", invoice_limit=0, upload_limit=1)

    # A tiny valid CSV upload counts as one upload (accepted → queued for parse).
    csv = b"description,amount\nCoffee,3.50\n"
    r1 = await auth_client.post(
        "/api/v1/invoices/upload", files={"file": ("a.csv", csv, "text/csv")}
    )
    assert r1.status_code == 202, r1.text

    # The second upload hits the limit.
    r2 = await auth_client.post(
        "/api/v1/invoices/upload", files={"file": ("b.csv", csv, "text/csv")}
    )
    assert r2.status_code == 402
    assert "upload limit" in r2.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Reference integration 2026-09-07 — PAT-004 (Scrapling) and PAT-028
# (Paperless-ngx): Retry-After parsing and the connect-time public-address pin.


def test_retry_after_parser_supports_seconds_date_and_invalid_values():
    now = datetime(2026, 9, 6, 16, 0, tzinfo=UTC)
    assert webhooks._parse_retry_after("120", now=now) == 120.0
    assert webhooks._parse_retry_after("-5", now=now) == 0.0
    assert (
        webhooks._parse_retry_after(format_datetime(now + timedelta(seconds=90)), now=now) == 90.0
    )
    # A date already in the past means "now", not a negative delay.
    assert webhooks._parse_retry_after(format_datetime(now - timedelta(seconds=90)), now=now) == 0.0
    for bad in (None, "", "   ", "soon", "12 seconds", "nan", "inf", "-inf"):
        assert webhooks._parse_retry_after(bad, now=now) is None


@pytest.mark.asyncio
async def test_receiver_retry_after_floors_the_next_attempt(auth_client, db_session, monkeypatch):
    """A 503 with `Retry-After: 900` reschedules the delivery no sooner than
    900 s out — later than the queue's own first-attempt backoff (30 s)."""
    from datetime import datetime as _dt

    from app.models import job as jobmodel
    from app.services import job_handlers, jobs  # noqa: F401 — registers webhook.deliver

    org = (await auth_client.get("/api/v1/auth/me")).json()["organization"]["id"]
    await auth_client.post(
        "/api/v1/webhooks", json={"url": "https://example.test/hook", "events": "*"}
    )

    async def _busy(url, body, headers):
        return 503, "busy", "900"

    monkeypatch.setattr(webhooks, "_http_post", _busy)
    await webhooks.emit(db_session, org, "ping", {"x": 1})
    now = _dt.now(UTC) + timedelta(seconds=1)  # after the delivery's run_after
    job = await jobs.run_once(db_session, "w1", now=now)
    assert job is not None and job.kind == webhooks.WEBHOOK_DELIVER
    assert job.status == jobmodel.QUEUED
    assert job.run_after == now + timedelta(seconds=900)
    assert "RetryAfterError" in (job.last_error or "")


@pytest.mark.asyncio
async def test_connect_time_private_answer_is_terminal_not_retried(
    auth_client, db_session, monkeypatch
):
    """`assert_public_url` passed (the resolver answered a public address) but
    the connect-time resolution answered a private one: the delivery is FAILED
    with a `blocked:` reason and the job succeeds (nothing to retry)."""
    from sqlalchemy import select

    from app.core import outbound_http
    from app.models import job as jobmodel
    from app.models.webhook import WebhookDelivery
    from app.services import job_handlers, jobs  # noqa: F401

    org = (await auth_client.get("/api/v1/auth/me")).json()["organization"]["id"]
    await auth_client.post(
        "/api/v1/webhooks", json={"url": "https://example.test/hook", "events": "*"}
    )

    async def _rebound(url, body, headers):
        raise outbound_http.UnsafeOutboundUrl(
            "connection blocked: example.test resolves to a non-public address"
        )

    monkeypatch.setattr(webhooks, "_http_post", _rebound)
    await webhooks.emit(db_session, org, "ping", {"x": 1})
    job = await jobs.run_once(db_session, "w1")
    assert job is not None and job.status == jobmodel.SUCCEEDED
    deliv = await db_session.scalar(select(WebhookDelivery))
    assert deliv.status == "failed"
    assert deliv.last_error.startswith("blocked:")


@pytest.mark.asyncio
async def test_the_real_post_goes_through_the_pinned_transport(monkeypatch):
    """`_http_post` itself (not a fake) builds its client on the pinning
    transport, so a hostname that resolves to a private address is refused
    BEFORE any socket opens — the seam is not bypassable by configuration."""
    from app.core import outbound_http

    opened = []

    class _Refuse(outbound_http.PinnedPublicAsyncHTTPTransport):
        async def handle_async_request(self, request):
            opened.append(request.url.host)
            return await super().handle_async_request(request)

    monkeypatch.setattr(outbound_http, "PinnedPublicAsyncHTTPTransport", _Refuse)
    monkeypatch.setattr(outbound_http, "resolve_hostname_ips", lambda host, port: ["10.0.0.9"])
    with pytest.raises(outbound_http.UnsafeOutboundUrl):
        await webhooks._http_post("https://example.test/hook", b"{}", {"X-A": "1"})
    assert opened == ["example.test"]  # reached the transport, refused inside it


@pytest.mark.asyncio
async def test_the_real_post_does_not_follow_redirects(monkeypatch):
    """A 3xx to an internal URL would be a second destination nobody vetted:
    `_http_post` returns the 3xx as a non-2xx outcome and sends ONE request."""
    from app.core import outbound_http

    seen: list[str] = []

    class _Redirecting(outbound_http.PinnedPublicAsyncHTTPTransport):
        async def handle_async_request(self, request):
            seen.append(str(request.url))
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8080/admin"})

    import httpx

    monkeypatch.setattr(outbound_http, "PinnedPublicAsyncHTTPTransport", _Redirecting)
    code, _text, retry_after = await webhooks._http_post("https://example.test/hook", b"{}", {})
    assert code == 302 and retry_after is None
    assert len(seen) == 1 and seen[0].startswith("https://example.test/hook")


def test_registration_and_connect_time_share_one_definition_of_public():
    """`assert_public_url` (registration) and the connect-time pin must agree, or
    a URL accepted today is blocked forever at delivery."""
    import ipaddress

    from app.core import outbound_http

    for raw in (
        "8.8.8.8",
        "127.0.0.1",
        "100.64.0.1",
        "64:ff9b::7f00:1",
        "ff02::1",
        "169.254.169.254",
    ):
        addr = ipaddress.ip_address(raw)
        assert webhooks._addr_is_public(addr) is outbound_http.is_public_address(addr)
    with pytest.raises(webhooks.UnsafeWebhookUrl):
        webhooks.assert_public_url("https://100.64.0.1/hook")

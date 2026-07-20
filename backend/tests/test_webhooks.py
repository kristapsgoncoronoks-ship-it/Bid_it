"""Outbound webhooks: registration, signed delivery via the job queue, retries,
and the delivery log — plus the metered upload usage limit."""
import hashlib
import hmac
import json

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
    r = await auth_client.post("/api/v1/webhooks", json={"url": "https://example.test/hook", "events": "*"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["secret"]                       # returned at creation
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
    ep = (await auth_client.post("/api/v1/webhooks",
                                 json={"url": "https://example.test/hook", "events": "invoice.created"})).json()

    # Capture what the delivery would POST, and pretend the receiver returns 200.
    captured = {}

    async def _fake_post(url, body, headers):
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        return 200, "ok"

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
    expected_sig = "sha256=" + hmac.new(ep["secret"].encode(), captured["body"], hashlib.sha256).hexdigest()
    assert captured["headers"]["X-InvoiceIQ-Signature"] == expected_sig
    assert json.loads(captured["body"])["data"]["invoice_number"] == "INV-9"


@pytest.mark.asyncio
async def test_delivery_failure_retries_then_dead(auth_client, db_session, monkeypatch):
    org = await _org(db_session)
    await auth_client.post("/api/v1/webhooks", json={"url": "https://example.test/hook", "events": "*"})

    async def _fail_post(url, body, headers):
        return 500, "server error"

    monkeypatch.setattr(webhooks, "_http_post", _fail_post)

    await webhooks.emit(db_session, org, "ping", {"x": 1})
    await db_session.commit()

    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
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
        return 204, ""

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
    reg = await client.post("/api/v1/auth/register", json={
        "organization_name": "Other Co", "name": "O", "email": "ow@o.io", "password": "supersecret2",
    })
    client.headers["Authorization"] = f"Bearer {reg.json()['token']['access_token']}"
    assert (await client.get("/api/v1/webhooks")).json() == []
    assert (await client.get(f"/api/v1/webhooks/{ep['id']}/deliveries")).status_code == 404


@pytest.mark.asyncio
async def test_upload_quota_enforced(auth_client, db_session):
    """A 'user' role with a monthly upload limit is blocked once it's reached."""
    from app.models.user import User, UserRole
    from app.services import access
    from sqlalchemy import update as _update

    org = await _org(db_session)
    # Set this role's upload limit to 1 and downgrade the caller to a 'user'.
    await access.set_limits(db_session, UserRole.user, invoice_limit=0, upload_limit=1)
    await db_session.execute(_update(User).where(User.org_id == org).values(role=UserRole.user))
    await db_session.commit()

    # A tiny valid CSV upload counts as one upload.
    csv = b"description,amount\nCoffee,3.50\n"
    r1 = await auth_client.post("/api/v1/invoices/upload", files={"file": ("a.csv", csv, "text/csv")})
    assert r1.status_code == 200, r1.text

    # The second upload hits the limit.
    r2 = await auth_client.post("/api/v1/invoices/upload", files={"file": ("b.csv", csv, "text/csv")})
    assert r2.status_code == 402
    assert "upload limit" in r2.json()["detail"].lower()

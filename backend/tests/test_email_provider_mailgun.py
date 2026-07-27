"""Mailgun inbound-parse provider adapter (E1.6): a real provider's native
webhook, mapped onto the SAME `email_intake.process_attachment` pipeline the
generic `/email/inbound` JSON endpoint already uses (see `test_email_intake.py`
for that endpoint's own coverage — this file only proves the adapter layer:
signature verification, freshness, and that tenant resolution/enumeration-
safety/fail-closed behaviour carry over unchanged)."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from app.services.email_providers import mailgun

SIGNING_KEY = "test-mailgun-signing-key"

CSV = (
    "vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
    "Globex Ltd,INV-MG-1,2026-06-01,Widgets,10,5.00,50.00,21\n"
)


@pytest.fixture(autouse=True)
def _mailgun_key(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "mailgun_signing_key", SIGNING_KEY)


def _sign(token: str, timestamp: str, key: str = SIGNING_KEY) -> str:
    return hmac.new(key.encode(), (timestamp + token).encode(), hashlib.sha256).hexdigest()


def _form(
    *,
    recipient: str,
    sender: str | None = None,
    subject: str | None = None,
    token: str = "mg-token-abc123",
    timestamp: str | None = None,
    signature: str | None = None,
    key: str = SIGNING_KEY,
) -> dict:
    ts = timestamp if timestamp is not None else str(int(time.time()))
    sig = signature if signature is not None else _sign(token, ts, key)
    data = {
        "recipient": recipient,
        "timestamp": ts,
        "token": token,
        "signature": sig,
        "attachment-count": "1",
    }
    if sender:
        data["sender"] = sender
    if subject:
        data["subject"] = subject
    return data


def _files() -> dict:
    return {"attachment-1": ("invoice.csv", CSV, "text/csv")}


async def _activate(auth_client):
    r = await auth_client.put("/api/v1/modules/email_intake", json={"enabled": True})
    assert r.status_code == 200, r.text


async def _address(auth_client) -> str:
    r = await auth_client.get("/api/v1/email/settings")
    assert r.status_code == 200, r.text
    return r.json()["address"]


async def _row_count(db_session) -> int:
    from sqlalchemy import func, select

    from app.models.email_intake import InboundInvoice

    return await db_session.scalar(select(func.count(InboundInvoice.id))) or 0


# --------------------------------------------------------------------------- #
# Pure unit tests over the signature/freshness primitives (no I/O)
# --------------------------------------------------------------------------- #


def test_verify_signature_accepts_correct_hmac():
    ts, tok = "1700000000", "abc123"
    right = hmac.new(SIGNING_KEY.encode(), (ts + tok).encode(), hashlib.sha256).hexdigest()
    assert mailgun.verify_signature(
        token=tok, timestamp=ts, signature=right, signing_key=SIGNING_KEY
    )

    # A naive (non-HMAC) concat-then-hash is NOT the scheme Mailgun uses — proves
    # the function checks the real construction, not merely "some hash matches".
    naive = hashlib.sha256((ts + tok + SIGNING_KEY).encode()).hexdigest()
    assert naive != right
    assert not mailgun.verify_signature(
        token=tok, timestamp=ts, signature=naive, signing_key=SIGNING_KEY
    )


def test_verify_signature_rejects_one_byte_off():
    ts, tok = "1700000000", "abc123"
    right = hmac.new(SIGNING_KEY.encode(), (ts + tok).encode(), hashlib.sha256).hexdigest()
    wrong = ("0" if right[0] != "0" else "1") + right[1:]
    assert not mailgun.verify_signature(
        token=tok, timestamp=ts, signature=wrong, signing_key=SIGNING_KEY
    )


def test_verify_signature_rejects_missing_fields():
    assert not mailgun.verify_signature(
        token="", timestamp="1700000000", signature="x", signing_key="k"
    )
    assert not mailgun.verify_signature(token="t", timestamp="", signature="x", signing_key="k")
    assert not mailgun.verify_signature(
        token="t", timestamp="1700000000", signature="", signing_key="k"
    )
    assert not mailgun.verify_signature(
        token="t", timestamp="1700000000", signature="x", signing_key=""
    )


def test_is_fresh_boundary():
    now = 1_700_000_300.0
    assert mailgun.is_fresh("1700000000", max_age_seconds=300, now=now)  # exactly at the boundary
    assert not mailgun.is_fresh("1699999999", max_age_seconds=300, now=now)  # one second past
    assert mailgun.is_fresh(str(int(now)), max_age_seconds=300, now=now)  # right now
    assert mailgun.is_fresh(str(int(now) + 60), max_age_seconds=300, now=now)  # small clock skew ok
    assert not mailgun.is_fresh(
        str(int(now) + 120), max_age_seconds=300, now=now
    )  # too far in the future
    assert not mailgun.is_fresh("", now=now)
    assert not mailgun.is_fresh("not-a-number", now=now)


# --------------------------------------------------------------------------- #
# Route-level behaviour
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_mailgun_valid_signature_queues_attachment(auth_client, client, db_session):
    await _activate(auth_client)
    address = await _address(auth_client)

    r = await client.post(
        "/api/v1/email/inbound/mailgun",
        data=_form(recipient=address, sender="supplier@globex.io", subject="Your invoice"),
        files=_files(),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"received": 1, "queued": 1, "rejected": 0}

    inbox = (await auth_client.get("/api/v1/email/inbox")).json()
    assert inbox["total"] == 1
    row = inbox["items"][0]
    assert row["status"] == "queued"
    assert row["from_addr"] == "supplier@globex.io"
    assert row["subject"] == "Your invoice"
    assert row["filename"] == "invoice.csv"


@pytest.mark.asyncio
async def test_mailgun_bad_signature_rejected(auth_client, client, db_session):
    await _activate(auth_client)
    address = await _address(auth_client)

    r = await client.post(
        "/api/v1/email/inbound/mailgun",
        data=_form(recipient=address, key="totally-wrong-key"),
        files=_files(),
    )
    assert r.status_code == 401
    assert r.json()["code"] == "inbound_auth_failed"
    assert await _row_count(db_session) == 0


@pytest.mark.asyncio
async def test_mailgun_stale_timestamp_rejected(auth_client, client, db_session):
    await _activate(auth_client)
    address = await _address(auth_client)
    stale_ts = str(int(time.time()) - 400)  # older than the 300s freshness window

    r = await client.post(
        "/api/v1/email/inbound/mailgun",
        data=_form(recipient=address, timestamp=stale_ts),
        files=_files(),
    )
    assert r.status_code == 401
    assert r.json()["code"] == "inbound_auth_failed"
    assert await _row_count(db_session) == 0


@pytest.mark.asyncio
async def test_mailgun_signing_key_unset_fails_closed(auth_client, client, db_session, monkeypatch):
    from app.core.config import settings

    await _activate(auth_client)
    address = await _address(auth_client)
    monkeypatch.setattr(settings, "mailgun_signing_key", None)

    # An otherwise-perfect, correctly-signed-under-the-OLD-key request.
    r = await client.post(
        "/api/v1/email/inbound/mailgun",
        data=_form(recipient=address),
        files=_files(),
    )
    assert r.status_code == 401
    assert r.json()["code"] == "inbound_auth_failed"
    assert await _row_count(db_session) == 0


@pytest.mark.asyncio
async def test_mailgun_unknown_recipient_token_rejected(auth_client, client, db_session):
    await _activate(auth_client)

    bad_sig = await client.post(
        "/api/v1/email/inbound/mailgun",
        data=_form(recipient="unknown-nobody@in.invoiceiq.app", key="wrong-key"),
        files=_files(),
    )
    unknown_recipient = await client.post(
        "/api/v1/email/inbound/mailgun",
        data=_form(recipient="unknown-nobody@in.invoiceiq.app"),
        files=_files(),
    )
    assert bad_sig.status_code == unknown_recipient.status_code == 401
    # Enumeration safety: byte-identical body across the two failure modes.
    assert bad_sig.json() == unknown_recipient.json()
    assert await _row_count(db_session) == 0


@pytest.mark.asyncio
async def test_mailgun_sender_is_never_the_tenant_signal(auth_client, client):
    """Tenant A's recipient token routes to A even when `sender` is spoofed to
    look like tenant B — `sender` must never be trusted as an identity."""
    await _activate(auth_client)
    addr_a = await _address(auth_client)

    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Beta Logistics",
            "name": "Bea",
            "email": "bea@beta-logistics.io",
            "password": "supersecret",
        },
    )
    hb = {"Authorization": f"Bearer {reg.json()['token']['access_token']}"}
    await client.put("/api/v1/modules/email_intake", json={"enabled": True}, headers=hb)

    r = await client.post(
        "/api/v1/email/inbound/mailgun",
        data=_form(recipient=addr_a, sender="bea@beta-logistics.io"),
        files=_files(),
    )
    assert r.status_code == 200, r.text

    inbox_a = (await auth_client.get("/api/v1/email/inbox")).json()
    assert inbox_a["total"] == 1  # landed in A (the recipient), …
    inbox_b = (await client.get("/api/v1/email/inbox", headers=hb)).json()
    assert inbox_b["total"] == 0  # … never in B (the forgeable sender)


@pytest.mark.asyncio
async def test_mailgun_module_disabled_returns_403(auth_client, client):
    await _activate(auth_client)
    address = await _address(auth_client)
    off = await auth_client.put("/api/v1/modules/email_intake", json={"enabled": False})
    assert off.status_code == 200

    r = await client.post(
        "/api/v1/email/inbound/mailgun",
        data=_form(recipient=address),
        files=_files(),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_mailgun_confirms_into_a_real_invoice(auth_client, client, db_session):
    """End to end: the Mailgun-delivered attachment parses and confirms exactly
    like the generic-endpoint path (same downstream pipeline, not reimplemented)."""
    from app.services import jobs

    await _activate(auth_client)
    address = await _address(auth_client)

    r = await client.post(
        "/api/v1/email/inbound/mailgun",
        data=_form(recipient=address, sender="supplier@globex.io"),
        files=_files(),
    )
    assert r.status_code == 200, r.text

    while await jobs.run_once(db_session, "test-worker") is not None:
        pass

    inbox = (await auth_client.get("/api/v1/email/inbox")).json()
    row = inbox["items"][0]
    assert row["status"] == "pending"

    conf = await auth_client.post(f"/api/v1/email/inbox/{row['id']}/confirm", json={})
    assert conf.status_code == 201, conf.text
    assert conf.json()["invoice_number"] == "INV-MG-1"

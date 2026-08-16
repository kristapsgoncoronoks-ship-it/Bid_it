"""Audit events record FROM WHERE, inside the hash chain (P2-2 of the bug scan).

The trail recorded who, what and when but never the location — the owner's
deletion-trail requirement ("what was deleted, when, by whom, from what
location / IP address") names it explicitly, and the IP already stored on
`sessions` could not be tied to an event, so one actor with two live sessions
was unresolvable.

The properties pinned here, in order of how much they matter:

1. **The fields sit INSIDE the hash chain.** Recorded outside it, the IP would
   be the one editable column in a tamper-evident table — an attacker covering
   their tracks would edit exactly this field.
2. **Legacy chains still verify.** Events written before the columns existed
   hashed nine fields; the hash appends ip/session only when present, so a
   pre-existing chain recomputes byte-for-byte. A migration that silently broke
   every existing tenant's chain verification would be indistinguishable from
   tampering — the exact alarm it must never cause falsely.
3. **The context flows from deps.** An authenticated mutation carries the
   caller's IP and session id with no route-level code.

Synthetic fixtures only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.organization import Organization
from app.services import audit
from app.services.audit import _hash


async def _org(db_session) -> str:
    return await db_session.scalar(select(Organization.id).where(Organization.name == "Acme"))


async def _append_legacy(db_session, org_id: str, action: str) -> AuditEvent:
    """Append one event EXACTLY as the pre-P2-2 code wrote it: chained onto the
    current tip (the auth fixture has already written register/login events),
    ip/session NULL, hash over the original nine fields."""
    tip = await db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.org_id == org_id)
        .order_by(AuditEvent.seq.desc())
        .limit(1)
    )
    seq = (tip.seq + 1) if tip else 1
    prev = tip.hash if tip else None
    at_ms = int(datetime.now(UTC).timestamp() * 1000)
    event = AuditEvent(
        id=str(uuid.uuid4()),
        org_id=org_id,
        seq=seq,
        actor_id="actor-1",
        action=action,
        target_type="invoice",
        target_id="inv-1",
        at_ms=at_ms,
        prev_hash=prev,
        hash=_hash(prev, seq, org_id, "actor-1", action, "invoice", "inv-1", at_ms, None),
    )
    db_session.add(event)
    await db_session.commit()
    return event


# --------------------------------------------------------------------------- #
# 1. Inside the chain
# --------------------------------------------------------------------------- #


def test_the_hash_covers_ip_and_session_when_present():
    base = ("prev", 3, "org-1", "actor-1", "invoice.delete", "invoice", "inv-1", 1755, None)
    with_ip = _hash(*base, "203.0.113.7", "sess-1")
    assert with_ip != _hash(*base), "location present must change the hash"
    assert with_ip != _hash(*base, "203.0.113.8", "sess-1"), "editing the IP must change the hash"
    assert with_ip != _hash(*base, "203.0.113.7", "sess-2"), "editing the session must change it"


@pytest.mark.asyncio
async def test_editing_a_recorded_ip_breaks_the_chain(auth_client, db_session):
    org_id = await _org(db_session)
    event = await audit.record(
        db_session, "invoice.delete", org_id=org_id, ip="203.0.113.7", session_id="sess-1"
    )
    await db_session.commit()
    assert (await audit.verify_chain(db_session, org_id)).ok

    # The attacker's edit: point the deletion at someone else's address.
    event.ip = "198.51.100.9"
    await db_session.commit()

    status = await audit.verify_chain(db_session, org_id)
    assert not status.ok, "an edited IP verified clean — the location is outside the chain"
    assert status.broken_at_seq == event.seq


@pytest.mark.asyncio
async def test_blanking_a_recorded_ip_breaks_the_chain(auth_client, db_session):
    """Deleting the location is the other way to cover tracks. NULLing both
    fields changes the payload SHAPE (nine fields, not eleven), so the stored
    hash — computed with them — no longer matches."""
    org_id = await _org(db_session)
    event = await audit.record(
        db_session, "invoice.delete", org_id=org_id, ip="203.0.113.7", session_id="sess-1"
    )
    await db_session.commit()

    event.ip = None
    event.session_id = None
    await db_session.commit()

    assert not (await audit.verify_chain(db_session, org_id)).ok


# --------------------------------------------------------------------------- #
# 2. Legacy chains
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_chain_written_before_the_columns_existed_still_verifies(auth_client, db_session):
    """A legacy event hashed nine fields. Reconstruct one exactly as the old
    code wrote it (ip/session NULL, hash over nine), then append a NEW event
    with a location — the mixed chain must verify end to end."""
    org_id = await _org(db_session)
    legacy = await _append_legacy(db_session, org_id, "invoice.create")

    await audit.record(
        db_session, "invoice.delete", org_id=org_id, ip="203.0.113.7", session_id="sess-1"
    )
    await db_session.commit()

    status = await audit.verify_chain(db_session, org_id)
    assert status.ok, (
        f"legacy+new mixed chain failed at seq {status.broken_at_seq}: {status.detail}"
    )
    assert status.events > legacy.seq


@pytest.mark.asyncio
async def test_planting_a_location_on_a_legacy_event_is_detected(auth_client, db_session):
    """The inverse forgery: back-filling an IP onto an event that never had one
    ("they were at this address all along"). The stored hash covers nine fields;
    recomputation with the planted IP covers eleven — mismatch, detected."""
    org_id = await _org(db_session)
    event = await _append_legacy(db_session, org_id, "invoice.delete")
    assert (await audit.verify_chain(db_session, org_id)).ok

    event.ip = "203.0.113.7"
    await db_session.commit()

    assert not (await audit.verify_chain(db_session, org_id)).ok


# --------------------------------------------------------------------------- #
# 3. The context flows from deps
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_authenticated_mutation_records_ip_and_session(auth_client, db_session):
    """End to end through the app: create an invoice over HTTP, then read its
    audit event. deps sets the request context next to the actor, audit.record
    reads it — no route passes anything. The ASGI test transport presents as
    127.0.0.1; the SESSION id must match a real row for this user."""
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Fictional Fuels OU",
            "invoice_number": "INV-AUD-IP-1",
            "issue_date": "2026-06-01",
            "currency": "EUR",
            "line_items": [
                {
                    "description": "Diesel",
                    "quantity": "1",
                    "unit_price": "100.00",
                    "amount": "100.00",
                    "tax_rate": "0",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text

    event = await db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "invoice.create")
        .order_by(AuditEvent.seq.desc())
        .limit(1)
    )
    assert event is not None
    assert event.ip == "127.0.0.1"
    assert event.session_id, "the event must name the session that performed it"

    from app.models.session import Session

    session = await db_session.get(Session, event.session_id)
    assert session is not None, "session_id must reference a real session row"

    org_id = await _org(db_session)
    assert (await audit.verify_chain(db_session, org_id)).ok


@pytest.mark.asyncio
async def test_a_login_event_carries_the_ip(client, auth_client, db_session):
    """Login happens before any bearer token exists, so the route passes the
    location explicitly — the one event an investigator starts from."""
    r = await client.post(
        "/api/v1/auth/login", json={"email": "owner@acme.io", "password": "supersecret"}
    )
    assert r.status_code == 200, r.text

    event = await db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "auth.login")
        .order_by(AuditEvent.seq.desc())
        .limit(1)
    )
    assert event is not None
    assert event.ip == "127.0.0.1"

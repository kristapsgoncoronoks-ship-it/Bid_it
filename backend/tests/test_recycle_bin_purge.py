"""The 30-day purge (docs/design/deletion-and-archive.md, step 4).

Without it the bin leaks in the direction nobody watches for: a deleted record
sits invisible AND immortal, while the client has been told it goes after 30
days. That is a storage-limitation problem rather than a data-loss one, but it is
still the system saying something untrue.

With it, the risk inverts — this is the one path in the whole feature that
destroys a row for good. So the tests here are mostly about what it must NOT
take: anything still inside its window, anything under a legal hold, and anything
belonging to another tenant.

NOTE: until the platform archive (step 6) exists, "purge" means destroy. The
audit event's snapshot is the only remaining trace, which is why it is asserted
here in detail rather than by count.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core import tenant
from app.models.audit import AuditEvent
from app.models.invoice import Invoice, LineItem
from app.services import invoices as invoice_service
from app.services import retention


async def _invoice(client, number: str) -> str:
    r = await client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Fictional Fuels OU",
            "invoice_number": number,
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
    return r.json()["id"]


async def _bin_it(client, db_session, invoice_id: str, *, days_ago: int) -> None:
    """Delete through the real route, then age the bin entry."""
    assert (await client.delete(f"/api/v1/invoices/{invoice_id}")).status_code == 204
    with tenant.include_deleted():
        inv = await db_session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    inv.deleted_at = datetime.now(UTC) - timedelta(days=days_ago)
    await db_session.commit()


async def _exists(db_session, invoice_id: str) -> bool:
    with tenant.include_deleted():
        return (
            await db_session.scalar(select(Invoice.id).where(Invoice.id == invoice_id))
        ) is not None


@pytest.mark.asyncio
async def test_a_record_past_its_window_is_destroyed_and_one_inside_it_is_not(
    auth_client, db_session
):
    """The whole rule in one test: 31 days goes, 29 days stays."""
    expired = await _invoice(auth_client, "INV-PURGE-OLD")
    fresh = await _invoice(auth_client, "INV-PURGE-NEW")
    await _bin_it(auth_client, db_session, expired, days_ago=31)
    await _bin_it(auth_client, db_session, fresh, days_ago=29)

    result = await invoice_service.purge_expired_bin(db_session, (await _org(db_session)))
    await db_session.commit()

    assert result["purged"] == 1
    assert not await _exists(db_session, expired)
    assert await _exists(db_session, fresh), "a record still inside its 30 days was destroyed"


@pytest.mark.asyncio
async def test_a_LIVE_invoice_is_never_touched(auth_client, db_session):
    """The purge selects with `include_deleted()` on. A missing `deleted_at IS
    NOT NULL` filter there would quietly make it delete the entire ledger — the
    single most destructive mistake available in this codebase."""
    live = await _invoice(auth_client, "INV-PURGE-LIVE")

    result = await invoice_service.purge_expired_bin(db_session, (await _org(db_session)))
    await db_session.commit()

    assert result["purged"] == 0
    assert await _exists(db_session, live), "the purge destroyed a live invoice"


@pytest.mark.asyncio
async def test_a_legal_hold_stops_the_purge_entirely(auth_client, db_session):
    """A preservation duty overrides data minimisation. The bin is a second
    deletion path, and a hold that governed only `retention.purge` would be
    silently bypassed by it — which is exactly how records under a preservation
    order get destroyed."""
    org_id = await _org(db_session)
    expired = await _invoice(auth_client, "INV-PURGE-HELD")
    await _bin_it(auth_client, db_session, expired, days_ago=90)
    await retention.place_hold(db_session, org_id, reason="Litigation", actor_email="legal@test.io")

    result = await invoice_service.purge_expired_bin(db_session, org_id)
    await db_session.commit()

    assert result["held"] is True
    assert result["purged"] == 0
    assert await _exists(db_session, expired), "a record under legal hold was destroyed"


@pytest.mark.asyncio
async def test_releasing_the_hold_lets_the_purge_proceed(auth_client, db_session):
    """The hold suspends, it does not exempt."""
    org_id = await _org(db_session)
    expired = await _invoice(auth_client, "INV-PURGE-RELEASE")
    await _bin_it(auth_client, db_session, expired, days_ago=90)
    hold = await retention.place_hold(
        db_session, org_id, reason="Litigation", actor_email="legal@test.io"
    )
    await retention.release_hold(db_session, org_id, hold.id, actor_email="legal@test.io")

    result = await invoice_service.purge_expired_bin(db_session, org_id)
    await db_session.commit()

    assert result["purged"] == 1
    assert not await _exists(db_session, expired)


@pytest.mark.asyncio
async def test_the_purge_does_not_reach_into_another_tenant(auth_client, role_client, db_session):
    """The purge runs unscoped-by-context on a worker, so its org filter is the
    ONLY thing keeping tenants apart — and it is destroying rows."""
    other = await role_client("owner")
    theirs = await _invoice(other, "INV-PURGE-THEIRS")
    await _bin_it(other, db_session, theirs, days_ago=90)

    result = await invoice_service.purge_expired_bin(db_session, (await _org(db_session)))
    await db_session.commit()

    assert result["purged"] == 0
    assert await _exists(db_session, theirs), "the purge crossed a tenant boundary"


@pytest.mark.asyncio
async def test_the_line_items_go_with_the_invoice(auth_client, db_session):
    """Children first, then the parent — the same order `retention.purge` uses,
    so behaviour is identical whether or not FK cascades are enforced. Orphaned
    line items would keep the totals of a record that no longer exists."""
    expired = await _invoice(auth_client, "INV-PURGE-LINES")
    await _bin_it(auth_client, db_session, expired, days_ago=31)

    await invoice_service.purge_expired_bin(db_session, (await _org(db_session)))
    await db_session.commit()

    orphans = list(
        await db_session.scalars(select(LineItem.id).where(LineItem.invoice_id == expired))
    )
    assert orphans == [], "line items outlived the invoice they belong to"


@pytest.mark.asyncio
async def test_what_was_destroyed_is_recorded_not_just_how_many(auth_client, db_session):
    """Until the platform archive exists this event is the ONLY remaining trace
    of the record, so a count would leave nothing able to answer what went."""
    from app.models.job import Job
    from app.services import job_handlers

    org_id = await _org(db_session)
    expired = await _invoice(auth_client, "INV-PURGE-AUDIT")
    await _bin_it(auth_client, db_session, expired, days_ago=31)

    job = Job(org_id=org_id, kind=job_handlers.BIN_PURGE, payload_json="{}")
    out = await job_handlers._bin_purge(db_session, {}, job)

    assert out == {"held": False, "purged": 1}
    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "invoice.bin_purge")
    )
    assert event is not None, "a destructive purge left no audit event"
    meta = json.loads(event.meta)
    assert meta["purged"] == 1
    assert meta["records"][0]["invoice_number"] == "INV-PURGE-AUDIT"
    assert meta["records"][0]["total"] is not None


@pytest.mark.asyncio
async def test_a_purge_that_removes_nothing_writes_no_audit_event(auth_client, db_session):
    from app.models.job import Job
    from app.services import job_handlers

    org_id = await _org(db_session)
    await _invoice(auth_client, "INV-PURGE-QUIET")

    job = Job(org_id=org_id, kind=job_handlers.BIN_PURGE, payload_json="{}")
    await job_handlers._bin_purge(db_session, {}, job)

    events = list(
        await db_session.scalars(select(AuditEvent).where(AuditEvent.action == "invoice.bin_purge"))
    )
    assert events == []


def test_the_purge_is_scheduled_for_EVERY_tenant_not_just_ones_with_a_policy():
    """It sits in DAILY_KINDS rather than beside the retention purge, which only
    runs for tenants that configured a policy. 30 days is a promise made to every
    client the moment they delete something, not an opt-in setting — and a tenant
    with no retention policy would otherwise hold binned records forever."""
    from app.services import job_handlers, scheduler

    assert job_handlers.BIN_PURGE in scheduler.DAILY_KINDS


async def _org(db_session) -> str:
    """The org id of the `auth_client` fixture's workspace."""
    from app.models.organization import Organization

    return await db_session.scalar(select(Organization.id).where(Organization.name == "Acme"))

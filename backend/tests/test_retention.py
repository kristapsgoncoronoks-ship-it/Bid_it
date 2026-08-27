"""Data retention + legal hold (Phase 4, ADR-0019): policies, preview, purge,
legal-hold gating, object-byte cleanup, audit, scheduler, and route gating."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from app.models.audit import AuditEvent
from app.models.email_intake import InboundInvoice
from app.models.expense import ExpenseItem, ExpenseReport
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.vendor import Vendor
from app.services import documents, retention

OLD = datetime(2026, 1, 1, tzinfo=UTC)
RECENT = datetime(2026, 7, 20, tzinfo=UTC)
TODAY = date(2026, 7, 21)


async def _org_id(db) -> str:
    return await db.scalar(select(Organization.id))


async def _invoice(db, org_id, number, created_at):
    vendor = await db.scalar(select(Vendor).where(Vendor.org_id == org_id))
    if vendor is None:
        vendor = Vendor(org_id=org_id, name="Acme Supplier")
        db.add(vendor)
        await db.flush()
    inv = Invoice(
        org_id=org_id,
        vendor_id=vendor.id,
        invoice_number=number,
        issue_date=date(2026, 1, 1),
        created_at=created_at,
    )
    db.add(inv)
    await db.flush()
    return inv


async def _inbound(db, org_id, created_at, *, sha=None):
    row = InboundInvoice(
        org_id=org_id,
        received_at=created_at,
        filename="a.pdf",
        status="pending",
        sha256=sha,
        created_at=created_at,
    )
    db.add(row)
    await db.flush()
    return row


# --- policies --------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_get_policy(auth_client, db_session):
    org_id = await _org_id(db_session)
    await retention.set_policy(db_session, org_id, "invoices", 90)
    assert await retention.get_policies(db_session, org_id) == {"invoices": 90}
    # 0 removes it.
    await retention.set_policy(db_session, org_id, "invoices", 0)
    assert await retention.get_policies(db_session, org_id) == {}


@pytest.mark.asyncio
async def test_unknown_category_rejected(auth_client, db_session):
    org_id = await _org_id(db_session)
    with pytest.raises(ValueError):
        await retention.set_policy(db_session, org_id, "audit_events", 30)


# --- preview + purge -------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_deletes_old_keeps_recent(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _invoice(db_session, org_id, "OLD", OLD)
    await _invoice(db_session, org_id, "NEW", RECENT)
    await db_session.commit()
    await retention.set_policy(db_session, org_id, "invoices", 30)

    prev = await retention.preview(db_session, org_id, today=TODAY)
    assert prev.counts["invoices"] == 1 and prev.on_hold is False

    res = await retention.purge(db_session, org_id, today=TODAY)
    assert res == {"held": False, "purged": {"invoices": 1}}

    numbers = list(await db_session.scalars(select(Invoice.invoice_number)))
    assert numbers == ["NEW"]


@pytest.mark.asyncio
async def test_expense_purge_routes_through_the_bin_and_the_bin_destroys_the_bytes(
    auth_client, db_session
):
    """WO-V: the whole chain, not one step of it.

    This test used to assert that the retention purge hard-deleted an expense
    report, its items and its receipt bytes on the spot. That was true, and it
    was the defect: every other delete in this product goes through a 30-day
    recycle bin, and the retention path was a second destruction route with
    strictly weaker guarantees — exactly what WO-M fixed for invoices and left
    open here.

    Now the policy purge BINS the report (recoverable, items intact, bytes
    intact) and the bin's own clock destroys it later — including the bytes,
    which the generic bin purge did not touch until WO-V. Routing the category
    before fixing that would have silently orphaned every receipt file.
    """
    from datetime import timedelta

    from app.core import storage, tenant
    from app.services import bin as bin_svc

    org_id = await _org_id(db_session)
    sha, _ = await documents.store(documents.RECEIPTS, org_id, b"receipt-bytes", "application/pdf")
    report = ExpenseReport(
        org_id=org_id, employee_id="e1", employee_name="E", title="Trip", created_at=OLD
    )
    db_session.add(report)
    await db_session.flush()
    db_session.add(
        ExpenseItem(
            report_id=report.id,
            spend_date=date(2026, 1, 1),
            description="Fuel",
            receipt_sha256=sha,
            created_at=OLD,
        )
    )
    await db_session.commit()
    await retention.set_policy(db_session, org_id, "expenses", 30)

    res = await retention.purge(db_session, org_id, today=TODAY)
    assert res["purged"] == {"expenses": 1}

    # BINNED, not destroyed: hidden from every ordinary read…
    assert await db_session.scalar(select(func.count()).select_from(ExpenseReport)) == 0
    # …but the row, its item and its receipt are all still there.
    with tenant.include_deleted():
        binned = await db_session.scalar(select(ExpenseReport).where(ExpenseReport.id == report.id))
    assert binned is not None and binned.deleted_at is not None
    assert binned.deleted_by == retention.RETENTION_ACTOR
    assert await db_session.scalar(select(func.count()).select_from(ExpenseItem)) == 1
    assert await documents.load(documents.RECEIPTS, org_id, sha) == b"receipt-bytes", (
        "a binned report is restorable, so its receipts must survive the binning"
    )

    # …and it appears on the Trash screen like any other binned record.
    assert any(r["id"] == report.id for r in await bin_svc.list_binned(db_session, org_id))

    # Now let the bin's own clock run out.
    later = datetime.now(UTC) + timedelta(days=bin_svc.BIN_RETENTION_DAYS + 1)
    purged = await bin_svc.purge_expired(db_session, org_id, now=later)
    await db_session.commit()
    assert any(r["id"] == report.id for r in purged["records"])

    with tenant.include_deleted():
        assert await db_session.scalar(select(func.count()).select_from(ExpenseReport)) == 0
    with pytest.raises(storage.StorageError):  # WO-V: the bin destroys bytes too
        await documents.load(documents.RECEIPTS, org_id, sha)


@pytest.mark.asyncio
async def test_email_intake_still_hard_deletes_and_says_why(auth_client, db_session):
    """The honest other half. `InboundInvoice` has no `deleted_at` column, so
    the bin cannot hold it — routing it would be a hard delete wearing a bin's
    name. This pins the CURRENT truth and the reason, so the day the columns
    land, whoever adds them finds this test asking to be updated."""
    assert not hasattr(InboundInvoice, "deleted_at"), (
        "InboundInvoice gained a soft-delete column — add it to bin.KINDS and "
        "retention._BINNED_CATEGORIES, then rewrite this test as the chain"
    )
    assert "email_intake" not in retention._BINNED_CATEGORIES

    org_id = await _org_id(db_session)
    sha, _ = await documents.store(
        documents.EMAIL_ATTACHMENTS, org_id, b"attachment", "application/pdf"
    )
    db_session.add(
        InboundInvoice(
            org_id=org_id,
            from_addr="a@b.test",
            subject="Bill",
            received_at=OLD,
            filename="bill.pdf",
            sha256=sha,
            created_at=OLD,
        )
    )
    await db_session.commit()
    await retention.set_policy(db_session, org_id, "email_intake", 30)

    res = await retention.purge(db_session, org_id, today=TODAY)
    assert res["purged"] == {"email_intake": 1}
    assert await db_session.scalar(select(func.count()).select_from(InboundInvoice)) == 0

    from app.core import storage

    with pytest.raises(storage.StorageError):
        await documents.load(documents.EMAIL_ATTACHMENTS, org_id, sha)


@pytest.mark.asyncio
async def test_purge_is_audited(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _invoice(db_session, org_id, "OLD", OLD)
    await db_session.commit()
    await retention.set_policy(db_session, org_id, "invoices", 30)
    await retention.purge(db_session, org_id, today=TODAY)

    actions = list(
        await db_session.scalars(select(AuditEvent.action).where(AuditEvent.org_id == org_id))
    )
    assert "retention.purge" in actions


# --- legal hold ------------------------------------------------------------


@pytest.mark.asyncio
async def test_legal_hold_blocks_purge(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _invoice(db_session, org_id, "OLD", OLD)
    await db_session.commit()
    await retention.set_policy(db_session, org_id, "invoices", 30)
    hold = await retention.place_hold(
        db_session, org_id, reason="Litigation X", actor_email="a@a.io"
    )

    assert await retention.is_on_hold(db_session, org_id) is True
    prev = await retention.preview(db_session, org_id, today=TODAY)
    assert prev.on_hold is True and prev.counts["invoices"] == 1  # counted but blocked

    res = await retention.purge(db_session, org_id, today=TODAY)
    assert res == {"held": True, "purged": {}}
    assert (
        await db_session.scalar(select(func.count()).select_from(Invoice)) == 1
    )  # nothing deleted

    # Release → purge proceeds.
    assert await retention.release_hold(db_session, org_id, hold.id, actor_email="a@a.io") is True
    res2 = await retention.purge(db_session, org_id, today=TODAY)
    assert res2 == {"held": False, "purged": {"invoices": 1}}


@pytest.mark.asyncio
async def test_release_unknown_hold(auth_client, db_session):
    org_id = await _org_id(db_session)
    assert await retention.release_hold(db_session, org_id, "nope", actor_email="a@a.io") is False


# --- scheduler -------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_enqueues_purge_only_with_policy(auth_client, db_session):
    from app.models.job import Job
    from app.services import scheduler

    org_id = await _org_id(db_session)
    # No policy yet → no purge job.
    await scheduler.enqueue_daily(db_session, today=TODAY)
    kinds = list(await db_session.scalars(select(Job.kind)))
    assert "retention.purge" not in kinds

    await retention.set_policy(db_session, org_id, "invoices", 30)
    await scheduler.enqueue_daily(db_session, today=date(2026, 7, 22))
    kinds = list(await db_session.scalars(select(Job.kind)))
    assert "retention.purge" in kinds


# --- routes ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_routes_full_cycle(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _invoice(db_session, org_id, "OLD", OLD)
    await db_session.commit()

    # Set a policy via the API.
    r = await auth_client.put(
        "/api/v1/retention/policy", json={"category": "invoices", "retain_days": 30}
    )
    assert r.status_code == 200
    body = r.json()
    inv_cat = next(c for c in body["categories"] if c["key"] == "invoices")
    assert inv_cat["retain_days"] == 30 and inv_cat["purgeable_now"] == 1

    # Place a hold → purge is blocked.
    h = await auth_client.post("/api/v1/retention/holds", json={"reason": "audit"})
    assert h.status_code == 201
    hold_id = h.json()["id"]
    p = await auth_client.post("/api/v1/retention/purge")
    assert p.json() == {"held": True, "purged": {}}

    # Release, then purge for real.
    rel = await auth_client.post(f"/api/v1/retention/holds/{hold_id}/release")
    assert rel.status_code == 200 and rel.json()["on_hold"] is False
    p2 = await auth_client.post("/api/v1/retention/purge")
    assert p2.json() == {"held": False, "purged": {"invoices": 1}}


@pytest.mark.asyncio
async def test_routes_require_admin(auth_client, db_session):
    from app.models.user import User, UserRole

    user = await db_session.scalar(select(User))
    user.role = UserRole.user
    await db_session.commit()
    assert (await auth_client.get("/api/v1/retention")).status_code == 403
    assert (await auth_client.post("/api/v1/retention/purge")).status_code == 403


@pytest.mark.asyncio
async def test_bad_category_rejected_by_route(auth_client):
    r = await auth_client.put(
        "/api/v1/retention/policy", json={"category": "nope", "retain_days": 30}
    )
    assert r.status_code == 400

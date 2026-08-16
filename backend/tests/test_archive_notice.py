"""The archive pre-expiry notice + the paid retention extension.

"Nothing may leave the archive without the owner having been told first" was a
sentence in the module docstring; these tests make it enforceable. And the
extension has one property worth more than the rest together: buying it AFTER
the notice must protect the records the notice was about — an extension that
only covered future deletions would be worthless at exactly the moment it is
bought.

Synthetic fixtures throughout, seeded directly into `archived_invoices` (the
journey in is `test_platform_archive.py`'s subject, not this file's).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.archived_invoice import ArchivedInvoice
from app.models.email_message import EmailMessage
from app.models.organization import Organization
from app.services import archive, scheduler

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


async def _org(db_session) -> str:
    return await db_session.scalar(select(Organization.id).where(Organization.name == "Acme"))


def _row(org_id: str, number: str, *, expires_in_days: int) -> ArchivedInvoice:
    return ArchivedInvoice(
        org_id=org_id,
        original_invoice_id=f"00000000-0000-0000-0000-{abs(hash(number)) % 10**12:012d}",
        invoice_number=number,
        vendor_name="Fictional Fuels OU",
        line_items_json="[]",
        archived_at=NOW - timedelta(days=1000),
        expires_at=NOW + timedelta(days=expires_in_days),
    )


@pytest.mark.asyncio
async def test_records_entering_the_window_are_noticed_once_in_one_email(auth_client, db_session):
    """Two in the window → ONE email to the owner naming both; a second run
    sends nothing (the stamp is the idempotency). One far-out record stays
    un-noticed — warning about everything is warning about nothing."""
    org_id = await _org(db_session)
    db_session.add(_row(org_id, "NOTICE-1", expires_in_days=10))
    db_session.add(_row(org_id, "NOTICE-2", expires_in_days=40))
    db_session.add(_row(org_id, "NOTICE-FAR", expires_in_days=400))
    await db_session.commit()

    first = await archive.send_expiry_notices(db_session, org_id, now=NOW)
    await db_session.commit()

    assert first["records"] == 2
    assert first["sent"] == 1  # one owner in the fixture org → one email
    mails = list(
        await db_session.scalars(select(EmailMessage).where(EmailMessage.kind == "archive_expiry"))
    )
    assert len(mails) == 1
    assert "NOTICE-1" in mails[0].body and "NOTICE-2" in mails[0].body
    assert "NOTICE-FAR" not in mails[0].body

    second = await archive.send_expiry_notices(db_session, org_id, now=NOW)
    assert second == {"sent": 0, "records": 0, "skipped_no_email": 0}


@pytest.mark.asyncio
async def test_an_undeliverable_notice_stays_owed(auth_client, db_session):
    """No owner address → the rows stay UNSTAMPED and the skip is reported.
    Marking them noticed would convert 'we could not tell them' into 'we told
    them', which is the one lie this feature exists to prevent."""
    from app.models.membership import Membership

    org_id = await _org(db_session)
    db_session.add(_row(org_id, "NOTICE-ORPHAN", expires_in_days=10))
    # No ACTIVE owner left to address (email is NOT NULL, so "no address" in
    # practice means no active owner membership — an offboarded company).
    memberships = list(
        await db_session.scalars(select(Membership).where(Membership.org_id == org_id))
    )
    for m in memberships:
        m.status = "suspended"
    await db_session.commit()

    result = await archive.send_expiry_notices(db_session, org_id, now=NOW)

    assert result["sent"] == 0
    assert result["skipped_no_email"] == 1
    stamped = await db_session.scalar(
        select(ArchivedInvoice.expiry_notified_at).where(
            ArchivedInvoice.invoice_number == "NOTICE-ORPHAN"
        )
    )
    assert stamped is None, "an undelivered notice must not be marked done"


@pytest.mark.asyncio
async def test_the_extension_reaches_back_to_existing_records(auth_client, db_session):
    """The property that makes the extension purchasable AFTER the notice:
    granting it re-stamps existing rows to archived_at + 365*years and CLEARS
    their notice stamp, so a fresh notice precedes the new expiry."""
    org_id = await _org(db_session)
    row = _row(org_id, "EXT-1", expires_in_days=10)
    row.expiry_notified_at = NOW - timedelta(days=1)  # notice already sent
    db_session.add(row)
    await db_session.commit()

    grant = await archive.apply_retention_override(db_session, org_id, 10)
    await db_session.commit()

    assert grant["effective_years"] == 10
    assert grant["rows_extended"] == 1
    # The Core UPDATE bypassed the ORM; drop the identity-mapped instance's
    # stale attributes before reading, and normalise tz (SQLite reads naive).
    db_session.expire_all()
    row = await db_session.scalar(
        select(ArchivedInvoice).where(ArchivedInvoice.invoice_number == "EXT-1")
    )
    naive = lambda d: d.replace(tzinfo=None)  # noqa: E731
    assert (naive(row.expires_at) - naive(row.archived_at)).days == 365 * 10
    assert row.expiry_notified_at is None, "an extended record is owed a FRESH notice"


@pytest.mark.asyncio
async def test_clearing_or_lowering_never_shortens_an_existing_record(auth_client, db_session):
    """Lowering must never reach backwards: a record already kept under a longer
    promise keeps it. Clearing the override re-stamps nothing; a below-included
    override is ignored outright by `retention_years`."""
    org_id = await _org(db_session)
    db_session.add(_row(org_id, "KEEP-LONG", expires_in_days=10))
    await db_session.commit()
    await archive.apply_retention_override(db_session, org_id, 10)
    await db_session.commit()
    long_expiry = await db_session.scalar(
        select(ArchivedInvoice.expires_at).where(ArchivedInvoice.invoice_number == "KEEP-LONG")
    )

    await archive.apply_retention_override(db_session, org_id, None)  # clear
    await db_session.commit()
    assert (
        await db_session.scalar(
            select(ArchivedInvoice.expires_at).where(ArchivedInvoice.invoice_number == "KEEP-LONG")
        )
        == long_expiry
    )

    assert await archive.retention_years(db_session, org_id) == archive.INCLUDED_RETENTION_YEARS
    await archive.apply_retention_override(db_session, org_id, 1)  # below included
    await db_session.commit()
    assert await archive.retention_years(db_session, org_id) == archive.INCLUDED_RETENTION_YEARS


@pytest.mark.asyncio
async def test_new_archives_are_stamped_with_the_extended_window(auth_client, db_session):
    org_id = await _org(db_session)
    await archive.apply_retention_override(db_session, org_id, 7)
    await db_session.commit()

    n = await archive.archive_records(
        db_session,
        org_id,
        [
            archive.ArchivedRecord(
                original_invoice_id="00000000-0000-0000-0000-000000000042",
                invoice_number="EXT-NEW",
                vendor_id=None,
                vendor_name=None,
                issue_date=None,
                currency=None,
                subtotal=None,
                tax_amount=None,
                total=None,
                line_items=[],
                source_sha256=None,
                source_filename=None,
                original_deleted_at=None,
                original_deleted_by=None,
            )
        ],
        now=NOW,
    )
    await db_session.commit()

    assert n == 1
    row = await db_session.scalar(
        select(ArchivedInvoice).where(ArchivedInvoice.invoice_number == "EXT-NEW")
    )
    assert (row.expires_at - row.archived_at).days == 365 * 7


@pytest.mark.asyncio
async def test_the_operator_grants_the_extension_over_the_platform_api(auth_client, db_session):
    """The grant surface: PATCH /platform/tenants/{id} — operator-only like the
    rest of that router, audited via its `changes` meta, 0 clears back to the
    included tier."""
    from app.models.user import User

    org_id = await _org(db_session)
    assert (
        await auth_client.patch(
            f"/api/v1/platform/tenants/{org_id}", json={"archive_retention_years": 10}
        )
    ).status_code == 403  # a company owner is NOT a platform operator

    owner = await db_session.scalar(select(User).where(User.email == "owner@acme.io"))
    owner.is_platform_admin = True
    await db_session.commit()

    r = await auth_client.patch(
        f"/api/v1/platform/tenants/{org_id}", json={"archive_retention_years": 10}
    )
    assert r.status_code == 200, r.text
    assert r.json()["archive_retention_years"] == 10
    assert await archive.retention_years(db_session, org_id) == 10

    r = await auth_client.patch(
        f"/api/v1/platform/tenants/{org_id}", json={"archive_retention_years": 0}
    )
    assert r.status_code == 200
    assert r.json()["archive_retention_years"] is None


def test_the_notice_is_scheduled_daily_for_every_tenant():
    from app.services import job_handlers

    assert job_handlers.ARCHIVE_NOTICE in scheduler.DAILY_KINDS

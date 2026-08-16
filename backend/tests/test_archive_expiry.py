"""The archive's expiry purge — the end of the deletion chain.

Before `archive.purge_expired` existed, `expires_at` was stamped, indexed,
published by the API and printed on the client screen, and NOTHING enforced it:
"kept for three years, then removed" was true only up to the comma
(docs/audit/2026-08-16-bug-scan.md, P0-1). These tests pin the promise from both
sides — expired records go, in-window records stay — and pin the two properties
that make destroying data safe to automate: a legal hold stops it entirely, and
the document bytes are collected only when nothing else references them.

Synthetic fixtures throughout: rows are seeded directly into `archived_invoices`
because this module's subject is the archive's own lifecycle, not the journey in.
The journey (delete → bin → purge → archive) is `test_platform_archive.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core import storage
from app.models.archived_invoice import ArchivedInvoice
from app.models.audit import AuditEvent
from app.models.extraction_run import ExtractionRun
from app.models.organization import Organization
from app.services import archive, documents, retention, scheduler

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


async def _org(db_session) -> str:
    return await db_session.scalar(select(Organization.id).where(Organization.name == "Acme"))


def _row(org_id: str, number: str, *, expired: bool, sha: str | None = None) -> ArchivedInvoice:
    stamp = NOW - timedelta(days=400)
    return ArchivedInvoice(
        org_id=org_id,
        original_invoice_id=f"00000000-0000-0000-0000-{abs(hash(number)) % 10**12:012d}",
        invoice_number=number,
        vendor_name="Fictional Fuels OU",
        currency="EUR",
        line_items_json="[]",
        source_sha256=sha,
        archived_at=stamp,
        expires_at=NOW - timedelta(days=1) if expired else NOW + timedelta(days=365),
    )


async def _store(org_id: str, data: bytes) -> str:
    sha, _ = await documents.store(documents.UPLOADS, org_id, data)
    return sha


async def _stored(org_id: str, sha: str) -> bool:
    key = storage.content_key(documents.UPLOADS, org_id, sha)
    return storage.get_storage().exists(key)


# --------------------------------------------------------------------------- #
# The promise, from both sides
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_expired_record_is_destroyed_and_an_in_window_record_survives(
    auth_client, db_session
):
    org_id = await _org(db_session)
    db_session.add(_row(org_id, "ARC-EXPIRED", expired=True))
    db_session.add(_row(org_id, "ARC-KEEP", expired=False))
    await db_session.commit()

    result = await archive.purge_expired(db_session, org_id, now=NOW)
    await db_session.commit()

    assert result["held"] is False
    assert result["purged"] == 1
    left = list(await db_session.scalars(select(ArchivedInvoice.invoice_number)))
    assert left == ["ARC-KEEP"]
    # The audit meta must name what was destroyed, not just count it — after
    # this commit, that event is the only remaining trace of the record.
    assert [r["invoice_number"] for r in result["records"]] == ["ARC-EXPIRED"]


@pytest.mark.asyncio
async def test_a_record_expiring_today_is_not_taken_early(auth_client, db_session):
    """`expires_at <= now` at the boundary: one second past goes, one second shy
    stays. Being a day early on a legal retention promise is the failure mode;
    being a day late is slack the client never notices."""
    org_id = await _org(db_session)
    row = _row(org_id, "ARC-EDGE", expired=False)
    row.expires_at = NOW + timedelta(seconds=1)
    db_session.add(row)
    await db_session.commit()

    result = await archive.purge_expired(db_session, org_id, now=NOW)
    assert result["purged"] == 0


@pytest.mark.asyncio
async def test_a_legal_hold_refuses_the_entire_run(auth_client, db_session):
    """The same rule as the bin purge, on the same grounds: a preservation duty
    overrides a retention window on EVERY destruction path — including one added
    a year after the hold was wired."""
    org_id = await _org(db_session)
    db_session.add(_row(org_id, "ARC-HELD", expired=True))
    await retention.place_hold(db_session, org_id, reason="Litigation", actor_email="legal@t.io")
    await db_session.commit()

    result = await archive.purge_expired(db_session, org_id, now=NOW)

    assert result == {"held": True, "purged": 0, "records": [], "collectable_shas": []}
    assert await db_session.scalar(select(ArchivedInvoice.id)) is not None


@pytest.mark.asyncio
async def test_the_delete_asserts_the_org_itself(auth_client, db_session):
    """Purging tenant A must not touch tenant B's expired rows. The tenant guard
    does not apply to a DELETE, so the WHERE clause here IS the boundary."""
    org_a = await _org(db_session)
    other = Organization(name="Other Haulier OU")
    db_session.add(other)
    await db_session.flush()
    db_session.add(_row(org_a, "ARC-A", expired=True))
    db_session.add(_row(other.id, "ARC-B", expired=True))
    await db_session.commit()

    result = await archive.purge_expired(db_session, org_a, now=NOW)
    await db_session.commit()

    assert result["purged"] == 1
    survivor = await db_session.scalar(
        select(ArchivedInvoice.invoice_number).where(ArchivedInvoice.org_id == other.id)
    )
    assert survivor == "ARC-B", "another tenant's expired record was destroyed"


# --------------------------------------------------------------------------- #
# Byte collection — content-addressed, so reference-counted
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_unreferenced_sha_is_collectable_and_its_bytes_are_removed(
    auth_client, db_session
):
    org_id = await _org(db_session)
    sha = await _store(org_id, b"%PDF-1.4 expired-original")
    db_session.add(_row(org_id, "ARC-BYTES", expired=True, sha=sha))
    await db_session.commit()

    result = await archive.purge_expired(db_session, org_id, now=NOW)
    await db_session.commit()

    assert result["collectable_shas"] == [sha]
    assert await archive.collect_bytes(org_id, result["collectable_shas"]) == 1
    assert not await _stored(org_id, sha)


@pytest.mark.asyncio
async def test_a_sha_shared_with_a_surviving_archive_row_is_not_collected(auth_client, db_session):
    """Content-addressed storage: the same PDF uploaded twice is ONE object.
    Collecting it on the first expiry would silently break the second client
    record while it is still inside its promised window."""
    org_id = await _org(db_session)
    sha = await _store(org_id, b"%PDF-1.4 shared-original")
    db_session.add(_row(org_id, "ARC-GONE", expired=True, sha=sha))
    db_session.add(_row(org_id, "ARC-STAYS", expired=False, sha=sha))
    await db_session.commit()

    result = await archive.purge_expired(db_session, org_id, now=NOW)
    await db_session.commit()

    assert result["purged"] == 1
    assert result["collectable_shas"] == []
    assert await _stored(org_id, sha)


@pytest.mark.asyncio
async def test_a_sha_still_referenced_by_a_live_invoice_is_not_collected(auth_client, db_session):
    """The uploads store is shared with LIVE invoices via their extraction runs.
    A client can delete one copy of a duplicate upload and keep the other; the
    archive expiring its copy must not take the live invoice's document with it."""
    org_id = await _org(db_session)
    sha = await _store(org_id, b"%PDF-1.4 still-live-original")
    db_session.add(_row(org_id, "ARC-DUP", expired=True, sha=sha))
    db_session.add(ExtractionRun(org_id=org_id, method="text-layer", source_sha256=sha))
    await db_session.commit()

    result = await archive.purge_expired(db_session, org_id, now=NOW)
    await db_session.commit()

    assert result["purged"] == 1
    assert result["collectable_shas"] == []
    assert await _stored(org_id, sha)


@pytest.mark.asyncio
async def test_two_rows_expiring_together_release_their_shared_sha(auth_client, db_session):
    """The reference check runs AFTER the deletes in the same transaction, so a
    sha whose every referent expires in this run IS collectable — the check must
    not be fooled by rows that are already gone."""
    org_id = await _org(db_session)
    sha = await _store(org_id, b"%PDF-1.4 both-expiring")
    db_session.add(_row(org_id, "ARC-T1", expired=True, sha=sha))
    db_session.add(_row(org_id, "ARC-T2", expired=True, sha=sha))
    await db_session.commit()

    result = await archive.purge_expired(db_session, org_id, now=NOW)
    await db_session.commit()

    assert result["purged"] == 2
    assert result["collectable_shas"] == [sha]


# --------------------------------------------------------------------------- #
# The wiring — a purge that never runs enforces nothing
# --------------------------------------------------------------------------- #


def test_the_archive_purge_is_scheduled_daily_for_every_tenant():
    """In DAILY_KINDS, like BIN_PURGE — not behind the opt-in retention policy.
    Expiry is stamped on every archived record; a tenant who never configured
    retention was still shown the promise."""
    from app.services import job_handlers

    assert job_handlers.ARCHIVE_PURGE in scheduler.DAILY_KINDS


@pytest.mark.asyncio
async def test_the_job_handler_audits_what_it_destroyed(auth_client, db_session):
    """Run the handler the way the worker does. The audit event must carry the
    destroyed records: after this commit it is the only trace they existed."""
    from app.models.job import Job
    from app.services.job_handlers import _archive_purge

    org_id = await _org(db_session)
    sha = await _store(org_id, b"%PDF-1.4 handler-original")
    db_session.add(_row(org_id, "ARC-JOB", expired=True, sha=sha))
    await db_session.commit()

    out = await _archive_purge(
        db_session, {}, Job(org_id=org_id, kind="archive.purge_expired", payload_json="{}")
    )

    assert out["purged"] == 1
    assert out["bytes_collected"] == 1
    assert not await _stored(org_id, sha)
    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.org_id == org_id, AuditEvent.action == "archive.purge")
    )
    assert event is not None
    assert "ARC-JOB" in (event.meta or "")

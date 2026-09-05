"""The platform archive — writing to it, and reading it back.

`docs/design/platform-archive.md` is the design; the owner decisions it records
are the reason this module looks the way it does.

WHAT THIS IS FOR
----------------
A client deletes an invoice, it sits in the recycle bin for 30 days, and then the
bin purge destroys the row. Before this module existed, that was the end of it:
the record was gone and only an audit snapshot remained. Now the purge hands the
record here first, and the client's company owner can still view and download it
for the retention period.

That changes what retention IS. A platform-only store is something done TO a
client — a store they did not know about, which is the failure mode the design
doc was written to avoid. A client-visible archive is something they USE, and
"your records are kept for N years and you can look at them" is a sentence that
goes in a DPA and a sales deck rather than one that has to be explained.

READ-ONLY BY DESIGN
-------------------
There is no restore-from-archive. The bin restores into live books; the archive
only shows. Pulling a three-year-old invoice back into the ledger would reopen a
closed accounting period and can collide with invoice numbers issued since —
download is most of the value, re-entering the books is not.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.archived_invoice import ArchivedInvoice
from app.models.extraction_run import ExtractionRun
from app.models.organization import Organization

log = logging.getLogger("invoiceiq.archive")

INCLUDED_RETENTION_YEARS = 3
"""What every plan includes (owner decision). Longer is a PAID extension.

Deliberately a number the operator can change rather than a constant compiled
into a sweep — but note that changing it only affects invoices archived AFTERWARDS
(`ArchivedInvoice.expires_at` is stamped at write time), because lowering a
setting must never reach backwards and destroy records already kept under a
longer promise.

NOT VERIFIED AGAINST ANY STATUTE. Baltic accounting law commonly requires source
documents to be kept LONGER than three years, so a client on the included tier
may have records leave the archive while they were still obliged to hold them.
That obligation is theirs rather than the platform's, but it lands on them at the
worst possible moment — which is why nothing may leave the archive without the
owner having been told first, and why the extension must be purchasable after
that notice as well as before it.
"""

EXPIRY_NOTICE_DAYS = 60
"""How long before expiry the owner is warned. Long enough to read an email, talk
to an accountant, and buy the extension without hurrying — the entire point is
that the deadline is never a surprise."""


@dataclass
class ArchivedRecord:
    """One invoice being handed to the archive by the purge.

    A plain value object rather than an ORM row: the purge reads columns (not
    entities) precisely so it does not hydrate rows it is about to destroy, and
    this keeps that property intact across the hand-off.
    """

    original_invoice_id: str
    invoice_number: str | None
    vendor_id: str | None
    vendor_name: str | None
    issue_date: object | None
    currency: str | None
    subtotal: object | None
    tax_amount: object | None
    total: object | None
    line_items: list[dict]
    source_sha256: str | None
    source_filename: str | None
    original_deleted_at: datetime | None
    original_deleted_by: str | None


async def retention_years(db: AsyncSession, org_id: str) -> int:
    """How long THIS organisation's archive keeps a record.

    The MAX of three things, and deliberately never a min:

    1. the included tier (`INCLUDED_RETENTION_YEARS`) — the floor every client
       was promised on the archive screen and in the DPA;
    2. the org's PLAN attribute (`plans.Plan.archive_retention_years`) — WO-AD,
       DECISIONS §1.B: longer retention rides the ladder, so Business and
       Enterprise carry 7 years as part of the plan;
    3. the per-org staff override (`organizations.archive_retention_years`).

    A MAX because a misconfigured plan or override must never quietly shorten a
    promise already made — a downgrade from Business does not reach backwards
    into records kept under 7 years either, since `expires_at` is stamped on the
    row and this function is only ever consulted to EXTEND (see
    `restamp_to_effective`).
    """
    row = await db.execute(
        select(Organization.plan, Organization.archive_retention_years).where(
            Organization.id == org_id
        )
    )
    plan_key, override = row.one_or_none() or (None, None)
    from app.services import plans

    candidates = [INCLUDED_RETENTION_YEARS, plans.plan_for(plan_key).archive_retention_years]
    if override is not None:
        candidates.append(int(override))
    return max(candidates)


async def restamp_to_effective(db: AsyncSession, org_id: str) -> int:
    """Re-stamp every archived row's `expires_at` to `archived_at + the org's
    EFFECTIVE retention`, EXTEND-ONLY, and clear the notice stamp on each row
    that moved so a fresh notice precedes the new expiry. Returns how many rows
    were extended.

    This is what makes a longer retention MEAN something at the moment it is
    acquired. `expires_at` is stamped at write time, so a plan upgrade (or a
    staff grant) that changed nothing on existing rows would protect only
    invoices deleted AFTERWARDS — worthless exactly when it is bought, which is
    right after a pre-expiry notice about records already archived. Both the
    plan-change paths (`billing`) and the staff override
    (`apply_retention_override`) call THIS, so there is one re-stamp
    implementation and not two that could disagree.

    ONLY EVER EXTENDS: a row whose current expiry is already later keeps it.
    Per-row, in Python: `column + timedelta` does not translate to portable SQL
    (SQLite silently matches nothing). A plan change is a rare action over one
    tenant's archive, so row-at-a-time is fine and the extend-only comparison
    stays explicit rather than buried in dialect date arithmetic.

    Does NOT commit.
    """
    effective = await retention_years(db, org_id)
    if effective <= INCLUDED_RETENTION_YEARS:
        # Nothing above the floor; every row was stamped with at least the
        # floor already. Skipping the scan is not an optimisation — it is the
        # statement that the included tier never re-stamps anything.
        return 0
    window = timedelta(days=365 * effective)
    rows = list(
        await db.execute(
            select(
                ArchivedInvoice.id, ArchivedInvoice.archived_at, ArchivedInvoice.expires_at
            ).where(ArchivedInvoice.org_id == org_id)
        )
    )
    extended = 0
    for r in rows:
        new_expiry = r.archived_at + window
        if r.expires_at < new_expiry:
            await db.execute(
                sa_update(ArchivedInvoice)
                .where(ArchivedInvoice.org_id == org_id, ArchivedInvoice.id == r.id)
                .values(expires_at=new_expiry, expiry_notified_at=None)
            )
            extended += 1
    return extended


async def archive_records(
    db: AsyncSession, org_id: str, records: list[ArchivedRecord], *, now: datetime | None = None
) -> int:
    """Write records to the archive. Returns how many landed.

    Called by `invoices.purge_expired_bin` INSIDE the same transaction as the
    delete, so a record can never be destroyed without being archived — the two
    halves commit together or not at all. That single property is what makes the
    archive a backstop rather than a best-effort copy.

    Does NOT commit; the caller owns the transaction.
    """
    if not records:
        return 0

    stamp = now or datetime.now(UTC)
    years = await retention_years(db, org_id)
    # timedelta has no "years", and calendar arithmetic on 29 February is a
    # famous way to raise ValueError once every four years. 365-day years are
    # close enough for a retention window measured in years and never throw.
    expires = stamp + timedelta(days=365 * years)

    for r in records:
        db.add(
            ArchivedInvoice(
                org_id=org_id,
                original_invoice_id=r.original_invoice_id,
                invoice_number=r.invoice_number,
                vendor_id=r.vendor_id,
                vendor_name=r.vendor_name,
                issue_date=r.issue_date,
                currency=r.currency,
                subtotal=r.subtotal,
                tax_amount=r.tax_amount,
                total=r.total,
                line_items_json=json.dumps(r.line_items, default=str),
                source_sha256=r.source_sha256,
                source_filename=r.source_filename,
                original_deleted_at=r.original_deleted_at,
                original_deleted_by=r.original_deleted_by,
                archived_at=stamp,
                expires_at=expires,
            )
        )
    return len(records)


@dataclass
class ArchivePage:
    items: list[ArchivedInvoice]
    total: int
    retention_years: int
    expiry_notice_days: int


async def page(db: AsyncSession, org_id: str, *, limit: int = 50, offset: int = 0) -> ArchivePage:
    """One page of an organisation's archive, newest first.

    Ordinary tenant-scoped reads: this table is in `TENANT_MODELS`, so the
    central guard applies its org filter as well as the explicit one here. That
    belt-and-braces matters more on this table than on most — it is the one
    holding records clients believe they deleted.
    """
    total = await db.scalar(
        select(func.count()).select_from(ArchivedInvoice).where(ArchivedInvoice.org_id == org_id)
    )
    rows = await db.scalars(
        select(ArchivedInvoice)
        .where(ArchivedInvoice.org_id == org_id)
        .order_by(ArchivedInvoice.archived_at.desc(), ArchivedInvoice.id)
        .limit(limit)
        .offset(offset)
    )
    return ArchivePage(
        items=list(rows),
        total=int(total or 0),
        retention_years=await retention_years(db, org_id),
        expiry_notice_days=EXPIRY_NOTICE_DAYS,
    )


async def get(db: AsyncSession, org_id: str, archive_id: str) -> ArchivedInvoice | None:
    """One archived record, org-scoped. `None` rather than raising, so the caller
    chooses the 404 shape (opaque, §4.4)."""
    return await db.scalar(
        select(ArchivedInvoice).where(
            ArchivedInvoice.org_id == org_id, ArchivedInvoice.id == archive_id
        )
    )


async def expiring_soon(
    db: AsyncSession, org_id: str, *, now: datetime | None = None
) -> list[ArchivedInvoice]:
    """Records due to leave the archive within `EXPIRY_NOTICE_DAYS`.

    The notice this feeds is not a nicety. Three years is likely BELOW the
    statutory floor in the markets this product serves, so a client who does not
    extend will lose records they were obliged to keep. Telling them first is
    what makes that survivable — and it is also the moment the extension sells
    itself, which is why the same query serves both.
    """
    stamp = now or datetime.now(UTC)
    horizon = stamp + timedelta(days=EXPIRY_NOTICE_DAYS)
    rows = await db.scalars(
        select(ArchivedInvoice)
        .where(
            ArchivedInvoice.org_id == org_id,
            ArchivedInvoice.expires_at <= horizon,
            ArchivedInvoice.expires_at > stamp,
        )
        .order_by(ArchivedInvoice.expires_at)
    )
    return list(rows)


# --------------------------------------------------------------------------- #
# Expiry — the promise the read side was already making
# --------------------------------------------------------------------------- #

PURGE_BATCH = 500
"""Same figure and same reason as `invoices.PURGE_BATCH`: an unbounded
`id.in_(...)` hits Postgres's 65535 bind-parameter ceiling (SQLite's is 32766),
and a job that fails on size is re-enqueued daily and fails forever."""


async def purge_expired(db: AsyncSession, org_id: str, *, now: datetime | None = None) -> dict:
    """Destroy archive rows past `expires_at` — the end of the deletion chain.

    Until this existed, `expires_at` was stamped, indexed, published by the API
    and printed on the client screen, and NOTHING enforced it: "kept for three
    years, then removed" was true only up to the comma. Personal data retained
    indefinitely past a stated period is the failure a retention window exists
    to prevent (storage limitation), and it was happening on exactly the store
    that holds records clients believe they deleted.

    REFUSES (no-op) under an active legal hold, exactly as the bin purge does
    and for the same reason: a preservation duty overrides a retention window on
    EVERY destruction path, including the ones added after the hold was wired.

    Batched, columns-not-entities, and the org and expiry predicates are
    RE-ASSERTED on the DELETE itself rather than inherited from the select — the
    tenant guard does not touch a non-SELECT statement, so on an irreversible
    path the WHERE clause is the entire tenant boundary.

    DOCUMENT BYTES ARE NOT DELETED HERE. The store is content-addressed and the
    same sha can be referenced by another archive row still inside its window,
    or by a LIVE invoice's extraction run (the same PDF uploaded twice is one
    object). Deleting bytes inside this transaction would also mean a rollback
    left surviving rows pointing at nothing. Instead the shas that are safe to
    collect — referenced by NO remaining archive row and NO extraction run of
    this org — are returned as `collectable_shas`, and the job handler deletes
    them best-effort AFTER the commit.

    Does NOT commit; the caller commits the destruction with its audit event so
    the two are one transaction. Returns
    `{"held": bool, "purged": int, "records": [...], "collectable_shas": [...]}`.
    """
    from app.services import retention

    if await retention.is_on_hold(db, org_id):
        return {"held": True, "purged": 0, "records": [], "collectable_shas": []}

    stamp = now or datetime.now(UTC)
    records: list[dict] = []
    candidate_shas: set[str] = set()
    purged = 0

    while True:
        batch = list(
            await db.execute(
                select(
                    ArchivedInvoice.id,
                    ArchivedInvoice.original_invoice_id,
                    ArchivedInvoice.invoice_number,
                    ArchivedInvoice.vendor_name,
                    ArchivedInvoice.total,
                    ArchivedInvoice.currency,
                    ArchivedInvoice.source_sha256,
                    ArchivedInvoice.archived_at,
                    ArchivedInvoice.expires_at,
                )
                .where(
                    ArchivedInvoice.org_id == org_id,
                    ArchivedInvoice.expires_at <= stamp,
                )
                .limit(PURGE_BATCH)
            )
        )
        if not batch:
            break

        ids = [row.id for row in batch]
        # After this runs, the audit event is the only remaining trace of the
        # record anywhere in the platform — so it records what was destroyed,
        # not just how many.
        records += [
            {
                "archive_id": row.id,
                "original_invoice_id": row.original_invoice_id,
                "invoice_number": row.invoice_number,
                "vendor_name": row.vendor_name,
                "total": str(row.total) if row.total is not None else None,
                "currency": row.currency,
                "archived_at": row.archived_at.isoformat() if row.archived_at else None,
                "expired_at": row.expires_at.isoformat() if row.expires_at else None,
            }
            for row in batch
        ]
        candidate_shas.update(row.source_sha256 for row in batch if row.source_sha256)

        result = await db.execute(
            delete(ArchivedInvoice).where(
                ArchivedInvoice.org_id == org_id,
                ArchivedInvoice.id.in_(ids),
                ArchivedInvoice.expires_at <= stamp,
            )
        )
        purged += int(getattr(result, "rowcount", 0) or 0)

        if len(batch) < PURGE_BATCH:
            break

    collectable: list[str] = []
    if candidate_shas:
        # Both checks run AFTER the deletes above (same transaction), so a sha
        # shared only among rows expiring together is correctly collectable,
        # while one referenced by a survivor — or by a live invoice — is not.
        still_archived = set(
            await db.scalars(
                select(ArchivedInvoice.source_sha256).where(
                    ArchivedInvoice.org_id == org_id,
                    ArchivedInvoice.source_sha256.in_(sorted(candidate_shas)),
                )
            )
        )
        still_live = set(
            await db.scalars(
                select(ExtractionRun.source_sha256).where(
                    ExtractionRun.org_id == org_id,
                    ExtractionRun.source_sha256.in_(sorted(candidate_shas)),
                )
            )
        )
        collectable = sorted(candidate_shas - still_archived - still_live)

    return {"held": False, "purged": purged, "records": records, "collectable_shas": collectable}


async def collect_bytes(org_id: str, shas: list[str]) -> int:
    """Best-effort removal of expired documents' bytes, AFTER the row commit.

    A failure here leaves an orphaned object nothing references — logged, and
    strictly better than the inverse (rows surviving a rollback while their
    bytes are already gone). Returns how many were removed."""
    from app.services import documents

    removed = 0
    for sha in shas:
        try:
            await documents.delete(documents.UPLOADS, org_id, sha)
            removed += 1
        except Exception as exc:  # noqa: BLE001 - cleanup must not fail the purge
            log.warning("archive byte collection failed for %s/%s: %s", org_id, sha, exc)
    return removed


# --------------------------------------------------------------------------- #
# The pre-expiry notice, and the paid extension it sells
# --------------------------------------------------------------------------- #


async def send_expiry_notices(
    db: AsyncSession, org_id: str, *, now: datetime | None = None
) -> dict:
    """Warn the company's owners about records due to leave the archive.

    The notice is not a nicety (module docstring): three years is likely BELOW
    the statutory floor in this product's markets, so a client who does not
    extend loses records they were obliged to keep — and the notice is both what
    makes that survivable and the moment the paid extension sells itself.

    One EMAIL PER OWNER per run, covering every un-noticed record inside the
    window — never one email per record (a 200-invoice expiry as 200 emails is a
    notice nobody reads). Sent to every ACTIVE OWNER membership: for a live
    tenant that is the accountable audience; for an ex-client it degrades to the
    last recorded owner address, which is exactly the owner's 2026-08-16
    decision. Records are STAMPED (`expiry_notified_at`) so tomorrow's run does
    not repeat them; granting an extension clears the stamp, so a fresh notice
    precedes the NEW expiry too.

    No owner has an email → records are left UNSTAMPED and the count is
    reported, not swallowed: a notice that cannot be delivered must stay
    visibly owed, not silently marked done.

    Does NOT commit; the job handler commits the stamps, the audit event and
    the recorded emails as one transaction.
    """
    stamp = now or datetime.now(UTC)
    horizon = stamp + timedelta(days=EXPIRY_NOTICE_DAYS)
    rows = list(
        await db.execute(
            select(
                ArchivedInvoice.id,
                ArchivedInvoice.invoice_number,
                ArchivedInvoice.vendor_name,
                ArchivedInvoice.expires_at,
            )
            .where(
                ArchivedInvoice.org_id == org_id,
                ArchivedInvoice.expiry_notified_at.is_(None),
                ArchivedInvoice.expires_at <= horizon,
                ArchivedInvoice.expires_at > stamp,
            )
            .order_by(ArchivedInvoice.expires_at)
        )
    )
    if not rows:
        return {"sent": 0, "records": 0, "skipped_no_email": 0}

    from app.models.membership import Membership
    from app.models.user import User, UserRole
    from app.services import mailer

    recipients = [
        e
        for e in await db.scalars(
            select(User.email)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.org_id == org_id,
                Membership.role == UserRole.owner,
                Membership.status == "active",
                User.email.is_not(None),
            )
        )
        if e
    ]
    if not recipients:
        return {"sent": 0, "records": len(rows), "skipped_no_email": 1}

    earliest = rows[0].expires_at
    years = await retention_years(db, org_id)
    subject, body = mailer.archive_expiry_email(
        count=len(rows),
        earliest=earliest,
        notice_days=EXPIRY_NOTICE_DAYS,
        retention_years=years,
        examples=[(r.invoice_number or "(no number)", r.vendor_name or "") for r in rows[:5]],
    )
    for to_email in recipients:
        await mailer.send(
            db, org_id, kind="archive_expiry", to_email=to_email, subject=subject, body=body
        )

    await db.execute(
        sa_update(ArchivedInvoice)
        .where(
            ArchivedInvoice.org_id == org_id,
            ArchivedInvoice.id.in_([r.id for r in rows]),
            # Only stamp what is still un-noticed: a concurrent grant of an
            # extension may have cleared/queued differently mid-run.
            ArchivedInvoice.expiry_notified_at.is_(None),
        )
        .values(expiry_notified_at=stamp)
    )
    return {
        "sent": len(recipients),
        "records": len(rows),
        "skipped_no_email": 0,
        "record_ids": [r.id for r in rows],
        "earliest": earliest.isoformat() if earliest else None,
    }


async def apply_retention_override(db: AsyncSession, org_id: str, years: int | None) -> dict:
    """Grant (or clear) the paid retention extension, and make it MEAN something.

    `expires_at` is stamped at write time, so changing the org's years without
    touching existing rows would sell an extension that only protects invoices
    deleted AFTERWARDS — worthless at exactly the moment it is bought, which is
    right after a pre-expiry notice about records already archived. So granting
    an extension also RE-STAMPS existing rows to `archived_at + 365*years`,
    and clears their notice stamp so a fresh notice precedes the new expiry.

    ONLY EVER EXTENDS. A row whose current expiry is already later keeps it,
    and clearing the override (years=None) re-stamps NOTHING: lowering a
    setting must never reach backwards and destroy records already kept under
    a longer promise (the module docstring's rule, enforced here).

    Does NOT commit; the caller (the platform route) commits the grant with
    its audit event.
    """
    org = await db.get(Organization, org_id)
    if org is None:
        raise ValueError(f"unknown organization: {org_id}")
    old = org.archive_retention_years
    org.archive_retention_years = years
    # Flush before recomputing: `retention_years` reads via a SELECT, and the
    # session may not autoflush the pending attribute change into it.
    await db.flush()

    # One re-stamp implementation, shared with the plan-change paths (WO-AD).
    # Clearing the override (years=None) re-stamps nothing UNLESS the org's plan
    # itself carries more than the floor — in which case the plan's promise
    # still holds and the rows are extended to it, never shortened.
    extended = await restamp_to_effective(db, org_id)
    effective = await retention_years(db, org_id)
    return {"old": old, "new": years, "effective_years": effective, "rows_extended": extended}

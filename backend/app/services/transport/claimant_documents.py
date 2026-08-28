"""The claimant document store (G2.10 slice 2; `BA_fleet_fuel.md` §3.E's
`check_type="document"` rules, §3.F F3, R45).

WHAT THIS MODULE IS FOR
------------------------
`checklist.submission_checklist` has raised `unsupported_check_type` on every
`document` rule since WO-60, because there was nothing to ask. `has_valid` is
the thing to ask: does THIS claimant hold a currently-valid document of THIS
kind, for THIS refund country. That one function is what turns four of the six
harvested checklist rules from a documented deferral into an evaluated gate.

WHY "NOT HELD" AND "EXPIRED" ARE DIFFERENT ANSWERS
----------------------------------------------------
`has_valid` returns `(ok, reason)`, not a bool. A checklist that said only
"Power of attorney: failed" for both cases would be describing two different
jobs with one sentence — one operator has to request a document that was never
obtained, the other has to renew one that lapsed on a date the record knows.
`_state` therefore reports the expiry it found. This is the same discipline
`excise.py` applies to a missing rate versus a placeholder rate: collapsing two
states into one is how a surface starts lying quietly.

WHY EXPIRY IS COMPARED AGAINST A `today` SEAM
-----------------------------------------------
Every date-sensitive transport check in this codebase takes `today` (see
`deadline.period_ended`, `lock.submit_claim`, `status.derive_stage`), because a
gate whose verdict depends on the wall clock cannot be asserted deterministically.
`has_valid` and `expiring` take the same seam and default to `date.today()`.

WHY `valid_until` IS INCLUSIVE
--------------------------------
A power of attorney valid until 2026-03-31 is valid ON 2026-03-31. `>= today`,
not `> today` — the off-by-one here would refuse a claim on the last day the
document actually covers.

WHY A NULL `valid_until` PASSES
--------------------------------
NULL means "no stated expiry", which is the normal case for a trade-register
extract or a signed contract. Treating it as expired would block every claimant
holding a document that genuinely never lapses; treating it as *unknown* and
failing closed would do the same thing while sounding more careful. The record
says there is no expiry, and that is a fact, not an absence.

WHO WRITES
-----------
`record` is the only inserter and the only writer of a validity window;
`remove` is the only deleter. Both audit. The BYTES are not written here — the
caller stores them through `documents.store` (the one binary choke point, which
also registers them in the Slice 5d registry) and passes the resulting digest
in, so this module never becomes a second vault.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionError, ValidationError
from app.models.transport.claimant_document import DOC_KINDS, VatClaimantDocument
from app.services import audit, issuer, modules

# §3.E's PoA-expiry bullet, verbatim: `expiring_documents(within_days=60)`.
EXPIRY_HORIZON_DAYS = 60


@dataclass(frozen=True)
class DocumentState:
    """The answer to "is this document held and valid", with the reason the
    checklist needs to say which of the two failure modes it is."""

    ok: bool
    reason: str | None = None
    valid_until: date | None = None


async def _gate(db: AsyncSession, org_id: str) -> None:
    """The WO-49 convention: no transport service entry point trusts its caller
    to have checked the entitlement (ADR-P3 rule 3)."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")


def normalize_country(country: str | None) -> str:
    """`""` for a customer-scope document, an upper-cased ISO-2 code otherwise.
    A one-character fragment is refused here rather than by the CHECK
    constraint, so the caller gets a sentence instead of an IntegrityError."""
    if country is None or not country.strip():
        return ""
    code = country.strip().upper()
    if len(code) != 2:
        raise ValidationError(f"'{country}' is not a 2-letter country code", code="invalid_country")
    return code


async def record(
    db: AsyncSession,
    org_id: str,
    entity_id: str,
    *,
    kind: str,
    sha256: str,
    size: int,
    country: str | None = None,
    mime: str | None = None,
    filename: str | None = None,
    valid_from: date | None = None,
    valid_until: date | None = None,
    uploaded_by: str | None = None,
) -> VatClaimantDocument:
    """Register one held document. Re-recording the SAME bytes for the same
    (entity, kind, country) UPDATES the existing row's validity window rather
    than duplicating it — correcting a mistyped expiry must not leave two rows
    disagreeing about one document. Different bytes are a renewal and get their
    own row, which is what keeps the lapsed one visible beside its replacement.
    """
    await _gate(db, org_id)
    if kind not in DOC_KINDS:
        raise ValidationError(
            f"'{kind}' is not a document kind ({', '.join(DOC_KINDS)})",
            code="unknown_document_kind",
        )
    code = normalize_country(country)
    if valid_from and valid_until and valid_until < valid_from:
        raise ValidationError(
            "The document's validity ends before it starts", code="invalid_validity_window"
        )

    entity = await issuer.get_by_id(db, org_id, entity_id)
    if entity is None:
        raise NotFoundError("Entity not found", code="entity_not_found")

    row = await db.scalar(
        select(VatClaimantDocument).where(
            VatClaimantDocument.org_id == org_id,
            VatClaimantDocument.entity_id == entity_id,
            VatClaimantDocument.kind == kind,
            VatClaimantDocument.country == code,
            VatClaimantDocument.sha256 == sha256,
        )
    )
    created = row is None
    if row is None:
        row = VatClaimantDocument(
            org_id=org_id,
            entity_id=entity_id,
            kind=kind,
            country=code,
            sha256=sha256,
            size=size,
        )
        db.add(row)
    row.mime = mime
    row.filename = filename
    row.valid_from = valid_from
    row.valid_until = valid_until
    row.uploaded_by = uploaded_by
    await db.flush()

    await audit.record(
        db,
        audit.A.TRANSPORT_CLAIMANT_DOCUMENT_RECORDED,
        target_type="vat_claimant_document",
        target_id=row.id,
        meta={
            "entity_id": entity_id,
            "kind": kind,
            "country": code,
            "created": created,
            "valid_until": valid_until.isoformat() if valid_until else None,
        },
        org_id=org_id,
    )
    return row


async def remove(db: AsyncSession, org_id: str, document_id: str) -> VatClaimantDocument:
    """Delete one held document. Org-scoped: a cross-tenant id is
    indistinguishable from an unknown one (master-context §4.4 — opaque 404,
    never a 403 that would confirm a stranger's row exists)."""
    await _gate(db, org_id)
    row = await db.scalar(
        select(VatClaimantDocument).where(
            VatClaimantDocument.org_id == org_id, VatClaimantDocument.id == document_id
        )
    )
    if row is None:
        raise NotFoundError("Document not found", code="claimant_document_not_found")

    await audit.record(
        db,
        audit.A.TRANSPORT_CLAIMANT_DOCUMENT_REMOVED,
        target_type="vat_claimant_document",
        target_id=row.id,
        meta={"entity_id": row.entity_id, "kind": row.kind, "country": row.country},
        org_id=org_id,
    )
    await db.delete(row)
    await db.flush()
    return row


async def list_documents(
    db: AsyncSession, org_id: str, entity_id: str
) -> list[VatClaimantDocument]:
    await _gate(db, org_id)
    stmt = (
        select(VatClaimantDocument)
        .where(
            VatClaimantDocument.org_id == org_id,
            VatClaimantDocument.entity_id == entity_id,
        )
        .order_by(
            VatClaimantDocument.kind,
            VatClaimantDocument.country,
            VatClaimantDocument.created_at.desc(),
        )
    )
    return list(await db.scalars(stmt))


def _state(rows: list[VatClaimantDocument], *, today: date) -> DocumentState:
    """The verdict over every row held for one (kind, country). The BEST row
    wins — a claimant holding a lapsed PoA and its renewal holds a valid PoA.
    Only when none is valid does the reason describe the failure, and it
    describes the LATEST expiry, because that is the one that lapsed most
    recently and the one an operator is renewing."""
    if not rows:
        return DocumentState(ok=False, reason="No document on file")
    valid = [r for r in rows if r.valid_until is None or r.valid_until >= today]
    if valid:
        # The soonest real expiry among the valid ones — an unexpiring document
        # reports None, which is not "expiring today".
        expiries = sorted(r.valid_until for r in valid if r.valid_until is not None)
        return DocumentState(ok=True, valid_until=expiries[0] if expiries else None)
    latest = max(r.valid_until for r in rows if r.valid_until is not None)
    return DocumentState(ok=False, reason=f"Expired on {latest.isoformat()}", valid_until=latest)


async def has_valid(
    db: AsyncSession,
    org_id: str,
    entity_id: str,
    *,
    kind: str,
    country: str | None = None,
    today: date | None = None,
) -> DocumentState:
    """The one question `checklist` asks. Module gate deliberately NOT re-run
    here: `submission_checklist` gates before it reaches this, and this is a
    per-rule call inside a loop — see `list_rules`'s own note on the same
    redundancy."""
    day = today or date.today()
    code = normalize_country(country)
    rows = list(
        await db.scalars(
            select(VatClaimantDocument).where(
                VatClaimantDocument.org_id == org_id,
                VatClaimantDocument.entity_id == entity_id,
                VatClaimantDocument.kind == kind,
                VatClaimantDocument.country == code,
            )
        )
    )
    return _state(rows, today=day)


async def expiring(
    db: AsyncSession,
    org_id: str,
    *,
    within_days: int = EXPIRY_HORIZON_DAYS,
    today: date | None = None,
) -> list[VatClaimantDocument]:
    """§3.E's `expiring_documents(within_days=60)`: every document whose stated
    expiry falls inside the horizon, ALREADY-EXPIRED ONES INCLUDED. A chase
    board that dropped a document the day it lapsed would go quiet at exactly
    the moment the claim it covers starts being refused — the same defect
    `capture_failures.failure_seq` exists to prevent.

    A document with no stated expiry never appears: it is not expiring."""
    await _gate(db, org_id)
    day = today or date.today()
    horizon = day + timedelta(days=within_days)
    stmt = (
        select(VatClaimantDocument)
        .where(
            VatClaimantDocument.org_id == org_id,
            VatClaimantDocument.valid_until.is_not(None),
            VatClaimantDocument.valid_until <= horizon,
        )
        .order_by(VatClaimantDocument.valid_until, VatClaimantDocument.kind)
    )
    return list(await db.scalars(stmt))

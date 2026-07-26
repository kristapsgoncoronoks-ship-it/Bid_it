"""Partner (counterparty) workflow — pre-invoicing document gates + penalty invoices.

A partner may require formal documents signed before any invoice is issued to it
(a framework contract and/or an acceptance act). `readiness()` reports whether the
required documents are signed; `create_issued` refuses to issue to a partner that
is not ready.

Penalty invoicing bills accrued late-payment interest aggregated across a
partner's OVERDUE invoices. It is opt-in per partner and — per policy — may only
be generated once a contract is signed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import money
from app.core.errors import NotFoundError
from app.models.issued_invoice import IssuedInvoice, IssuedInvoiceLine
from app.models.partner import Partner, PartnerDocument
from app.schemas.partner import DocumentSignIn, PartnerDocumentIn, PartnerIn, PartnerUpdate
from app.services import audit, issued_service, issued_status
from app.services import issuer as issuer_svc

CONTRACT = "contract"
ACCEPTANCE = "acceptance_act"
DOC_LABELS = {CONTRACT: "Contract", ACCEPTANCE: "Acceptance act"}

_ZERO = Decimal("0")


def required_kinds(p: Partner) -> list[str]:
    """Ordered prerequisite document kinds for this partner (contract first)."""
    kinds = []
    if p.requires_contract:
        kinds.append(CONTRACT)
    if p.requires_acceptance:
        kinds.append(ACCEPTANCE)
    return kinds


def _signed_kinds(p: Partner) -> set[str]:
    # Assumes p.documents is loaded (selectinload).
    return {d.kind for d in p.documents if d.status == "signed"}


def has_signed_contract(p: Partner) -> bool:
    return CONTRACT in _signed_kinds(p)


def missing_signed(p: Partner) -> list[str]:
    """Required document kinds that do not yet have a SIGNED document."""
    signed = _signed_kinds(p)
    return [k for k in required_kinds(p) if k not in signed]


def is_ready(p: Partner) -> bool:
    """True when every prerequisite document is signed (or none is required)."""
    return not missing_signed(p)


@dataclass
class Readiness:
    ready: bool
    required: list[str]
    signed: list[str]
    missing: list[str]


def readiness(p: Partner) -> Readiness:
    signed = _signed_kinds(p)
    req = required_kinds(p)
    return Readiness(
        ready=is_ready(p),
        required=req,
        signed=[k for k in req if k in signed],
        missing=missing_signed(p),
    )


async def get_partner(db: AsyncSession, org_id: str, partner_id: str) -> Partner | None:
    return await db.scalar(
        select(Partner)
        .where(Partner.id == partner_id, Partner.org_id == org_id)
        .options(selectinload(Partner.documents))
    )


# --- Mutations (WO-3) ----------------------------------------------------------
#
# All partner writes live HERE (not in the route) so every mutation is audited in
# the same transaction as the change it describes (invariant §4.16). Each function
# flushes and audits; the CALLER commits — the audit row persists atomically with
# the operation. The audit actor comes from the request context set by
# `app.api.deps` (audit.record reads it), so events are always attributable.


def _jsonable(value: object) -> object:
    """Audit-meta-safe rendering: JSON-native values pass through; Decimal/date/
    anything else becomes its string form so `audit.record`'s json.dumps can
    never fail (a failed dump would silently drop the event — best-effort audit
    must not be defeated by a serialization detail)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    return str(value)


async def create_partner(db: AsyncSession, org_id: str, body: PartnerIn) -> Partner:
    """Create a partner (the issuing counterparty master). Flushes + audits
    `partner.create`; caller commits."""
    p = Partner(org_id=org_id, **body.model_dump())
    if p.country:
        p.country = p.country.upper()
    db.add(p)
    await db.flush()
    await audit.record(
        db, audit.A.PARTNER_CREATE, target_type="partner", target_id=p.id, meta={"name": p.name}
    )
    return p


async def update_partner(db: AsyncSession, p: Partner, body: PartnerUpdate) -> Partner:
    """Apply a partial update to an (already tenant-loaded) partner, auditing
    old→new for every field that actually changed. A no-op patch records no
    event — nothing mutated, so there is nothing to attest. Flushes + audits;
    caller commits."""
    changed: dict[str, dict[str, object]] = {}
    for name, value in body.model_dump(exclude_unset=True).items():
        if name == "country" and value:
            value = value.upper()
        old = getattr(p, name)
        if value == old:
            continue
        setattr(p, name, value)
        changed[name] = {"old": _jsonable(old), "new": _jsonable(value)}
    if changed:
        await db.flush()
        await audit.record(
            db,
            audit.A.PARTNER_UPDATE,
            target_type="partner",
            target_id=p.id,
            meta={"name": p.name, "changes": changed},
        )
    return p


async def add_document(
    db: AsyncSession, org_id: str, p: Partner, body: PartnerDocumentIn
) -> PartnerDocument:
    """Attach a prerequisite document (contract / acceptance act) in `draft`
    status. Audited as `partner.document_upload` — the document's existence is
    the first half of the evidence trail that later justifies issuing. Flushes +
    audits; caller commits."""
    doc = PartnerDocument(
        org_id=org_id,
        partner_id=p.id,
        kind=body.kind,
        title=body.title,
        reference=body.reference,
        note=body.note,
        status="draft",
    )
    db.add(doc)
    await db.flush()
    await audit.record(
        db,
        audit.A.PARTNER_DOC_UPLOAD,
        target_type="partner_document",
        target_id=doc.id,
        meta={"partner_id": p.id, "kind": doc.kind, "title": doc.title},
    )
    return doc


async def sign_document(
    db: AsyncSession, org_id: str, partner_id: str, doc_id: str, body: DocumentSignIn
) -> PartnerDocument:
    """Mark a partner document as signed — the commercial assertion that can
    unlock issuing, so the audit event carries the full attribution: the actor
    (from request context), the partner, the document kind and the signature
    date. Opaque 404 (`NotFoundError`) for a cross-tenant or nonexistent id
    (invariant §4.4). Flushes + audits; caller commits."""
    doc = await db.scalar(
        select(PartnerDocument).where(
            PartnerDocument.id == doc_id,
            PartnerDocument.partner_id == partner_id,
            PartnerDocument.org_id == org_id,
        )
    )
    if doc is None:
        raise NotFoundError("Document not found")
    doc.status = "signed"
    doc.signed_by = body.signed_by
    doc.signed_date = body.signed_date or date.today()
    await db.flush()
    await audit.record(
        db,
        audit.A.PARTNER_DOC_SIGN,
        target_type="partner_document",
        target_id=doc.id,
        meta={
            "partner_id": partner_id,
            "kind": doc.kind,
            "signed_by": doc.signed_by,
            "signed_date": doc.signed_date.isoformat(),
        },
    )
    return doc


async def delete_document(
    db: AsyncSession, org_id: str, partner_id: str, doc_id: str
) -> PartnerDocument:
    """Delete a partner document. Deleting a SIGNED contract/acceptance act
    silently flips the partner readiness gate (issuing may become blocked — or,
    for an already-ready partner, the evidence that justified past issuing
    disappears), so the deletion is audited with the document's kind, title and
    status BEFORE removal (invariant §4.16 — the row is gone afterwards, the
    audit event is the only remaining record). Opaque 404 (`NotFoundError`) for
    a cross-tenant or nonexistent id (invariant §4.4). Flushes + audits; caller
    commits."""
    doc = await db.scalar(
        select(PartnerDocument).where(
            PartnerDocument.id == doc_id,
            PartnerDocument.partner_id == partner_id,
            PartnerDocument.org_id == org_id,
        )
    )
    if doc is None:
        raise NotFoundError("Document not found")
    meta = {
        "partner_id": partner_id,
        "kind": doc.kind,
        "title": doc.title,
        "status": doc.status,
    }
    await db.delete(doc)
    await db.flush()
    await audit.record(
        db, audit.A.PARTNER_DOC_DELETE, target_type="partner_document", target_id=doc_id, meta=meta
    )
    return doc


# --- Penalty invoicing ---------------------------------------------------------


@dataclass
class PenaltyLine:
    invoice_id: str
    number: str
    days_overdue: int
    outstanding: Decimal
    penalty: Decimal


@dataclass
class PenaltySummary:
    currency: str
    total_penalty: Decimal
    total_outstanding: Decimal
    max_days_overdue: int
    lines: list[PenaltyLine] = field(default_factory=list)


async def penalty_summary(
    db: AsyncSession, org_id: str, partner_id: str, today: date | None = None
) -> PenaltySummary:
    """Accrued late-payment interest across a partner's OVERDUE invoices.

    Overdue days and penalty are computed per invoice, then aggregated by partner.
    Penalty invoices themselves (kind='penalty') are excluded so interest never
    compounds on interest.
    """
    today = today or date.today()
    rows = list(
        await db.scalars(
            select(IssuedInvoice).where(
                IssuedInvoice.org_id == org_id,
                IssuedInvoice.partner_id == partner_id,
                IssuedInvoice.kind == "standard",
            )
        )
    )
    lines: list[PenaltyLine] = []
    total_pen, total_out, max_days = _ZERO, _ZERO, 0
    currency = "EUR"
    for inv in rows:
        if issued_status.status_of(inv, today) != issued_status.OVERDUE:
            continue
        if inv.number is None:  # unissued invoices carry no number — never overdue
            continue
        pen = issued_status.penalty_of(inv, today)
        if pen <= _ZERO:
            continue
        days = issued_status.days_overdue_of(inv, today)
        out = issued_status.outstanding_of(inv)
        currency = inv.currency
        lines.append(PenaltyLine(inv.id, inv.number, days, out, pen))
        total_pen += pen
        total_out += out
        max_days = max(max_days, days)
    return PenaltySummary(currency, money.q2(total_pen), money.q2(total_out), max_days, lines)


class PenaltyBlocked(Exception):
    """Raised when a penalty invoice cannot be generated (policy/state)."""


async def generate_penalty_invoice(
    db: AsyncSession, org_id: str, partner: Partner, today: date | None = None
) -> IssuedInvoice:
    """Create a penalty (late-interest) invoice for a partner. Requires the
    partner to allow penalties AND to have a signed contract."""
    today = today or date.today()
    if not partner.penalty_enabled:
        raise PenaltyBlocked("Penalty invoicing is not enabled for this partner.")
    if not has_signed_contract(partner):
        raise PenaltyBlocked(
            "A signed contract is required before a penalty invoice can be generated."
        )

    summary = await penalty_summary(db, org_id, partner.id, today)
    if summary.total_penalty <= _ZERO:
        raise PenaltyBlocked("This partner has no accrued late-payment interest to bill.")

    # Lock the issuer row FOR UPDATE + use the single shared number allocator so a
    # penalty invoice can never collide with a concurrent standard issue.
    profile = await issuer_svc.lock(db, org_id)
    number = issued_service._next_invoice_number(profile, today)

    detail = "; ".join(
        f"{ln.number} ({ln.days_overdue}d, {summary.currency} {ln.penalty})" for ln in summary.lines
    )
    note = f"Late-payment interest on overdue invoices: {detail}."

    inv = IssuedInvoice(
        org_id=org_id,
        partner_id=partner.id,
        kind="penalty",
        number=number,
        issue_date=today,
        due_date=today,
        currency=summary.currency,
        buyer_name=partner.name,
        buyer_email=partner.email,
        buyer_vat_number=partner.vat_number,
        buyer_address_line1=partner.address_line1,
        buyer_city=partner.city,
        buyer_postal_code=partner.postal_code,
        buyer_country=partner.country,
        seller_json=json.dumps(issuer_svc.seller_snapshot(profile)),
        vat_scheme="exempt",  # late-payment interest is outside the scope of VAT
        note=note,
        subtotal=summary.total_penalty,
        tax_total=_ZERO,
        total=summary.total_penalty,
        lines=[
            IssuedInvoiceLine(
                position=1,
                description=f"Late-payment interest on {len(summary.lines)} overdue invoice(s)",
                quantity=Decimal("1"),
                unit="C62",
                unit_price=summary.total_penalty,
                vat_rate=_ZERO,
                net_amount=summary.total_penalty,
            )
        ],
    )
    db.add(inv)
    return inv

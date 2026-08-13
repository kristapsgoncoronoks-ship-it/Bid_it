from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.models.partner import Partner
from app.schemas.issued import IssuedInvoiceDetail
from app.schemas.partner import (
    DocumentSignIn,
    PartnerDetail,
    PartnerDocumentIn,
    PartnerDocumentOut,
    PartnerIn,
    PartnerOut,
    PartnerUpdate,
    PenaltyLineOut,
    PenaltySummaryOut,
    ReadinessOut,
)
from app.services import audit, modules, partners

# Structural authorization (ADR-0024): partners are the issuing counterparty
# master — router-level ISSUED_READ; mutations (incl. document sign-off, which
# unlocks invoicing, and penalty-invoice generation) declare ISSUED_WRITE.
# (Previously open to any member with the module enabled.)
router = APIRouter(
    prefix="/partners",
    tags=["partners"],
    dependencies=[Depends(require_perm(authz.Permission.ISSUED_READ))],
)
_WRITE = [Depends(require_perm(authz.Permission.ISSUED_WRITE))]


async def _guard(db: DbSession, org_id: str):
    await modules.require_enabled(db, org_id, "issuing")


def _detail(p: Partner) -> PartnerDetail:
    r = partners.readiness(p)
    return PartnerDetail(
        **PartnerOut.model_validate(p).model_dump(),
        documents=[PartnerDocumentOut.model_validate(doc) for doc in p.documents],
        readiness=ReadinessOut(
            ready=r.ready, required=r.required, signed=r.signed, missing=r.missing
        ),
        has_signed_contract=partners.has_signed_contract(p),
    )


async def _load(db: DbSession, org_id: str, partner_id: str) -> Partner:
    p = await partners.get_partner(db, org_id, partner_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Partner not found")
    return p


@router.get("", response_model=list[PartnerOut])
async def list_partners(current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    rows = await db.scalars(
        select(Partner).where(Partner.org_id == current.org_id).order_by(Partner.name)
    )
    return [PartnerOut.model_validate(p) for p in rows]


@router.post(
    "", response_model=PartnerDetail, status_code=status.HTTP_201_CREATED, dependencies=_WRITE
)
async def create_partner(body: PartnerIn, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    p = await partners.create_partner(db, current.org_id, body)
    await db.commit()
    await db.refresh(p, attribute_names=["documents"])
    return _detail(p)


@router.get("/{partner_id}", response_model=PartnerDetail)
async def get_partner(partner_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    return _detail(await _load(db, current.org_id, partner_id))


@router.patch("/{partner_id}", response_model=PartnerDetail, dependencies=_WRITE)
async def update_partner(partner_id: str, body: PartnerUpdate, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    p = await _load(db, current.org_id, partner_id)
    await partners.update_partner(db, p, body)
    await db.commit()
    await db.refresh(p, attribute_names=["documents"])
    return _detail(p)


# --- Documents (contract / acceptance act) -------------------------------------


@router.post(
    "/{partner_id}/documents",
    response_model=PartnerDocumentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=_WRITE,
)
async def add_document(
    partner_id: str, body: PartnerDocumentIn, current: CurrentUser, db: DbSession
):
    await _guard(db, current.org_id)
    p = await _load(db, current.org_id, partner_id)
    doc = await partners.add_document(db, current.org_id, p, body)
    await db.commit()
    await db.refresh(doc)
    return PartnerDocumentOut.model_validate(doc)


@router.post(
    "/{partner_id}/documents/{doc_id}/sign", response_model=PartnerDetail, dependencies=_WRITE
)
async def sign_document(
    partner_id: str, doc_id: str, body: DocumentSignIn, current: CurrentUser, db: DbSession
):
    """Mark a partner document as signed — this can unlock invoicing. The
    service audits the sign (actor, partner, kind, signature date) and raises an
    opaque 404 for a cross-tenant/unknown document."""
    await _guard(db, current.org_id)
    await partners.sign_document(db, current.org_id, partner_id, doc_id, body)
    await db.commit()
    return _detail(await _load(db, current.org_id, partner_id))


@router.delete(
    "/{partner_id}/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_WRITE,
)
async def delete_document(partner_id: str, doc_id: str, current: CurrentUser, db: DbSession):
    """Delete a partner document. Deleting a signed contract can flip the
    partner readiness gate, so the service audits the deletion (kind, title,
    status at deletion) and raises an opaque 404 for a cross-tenant/unknown id."""
    await _guard(db, current.org_id)
    await partners.delete_document(db, current.org_id, partner_id, doc_id)
    await db.commit()


# --- Penalty invoicing ---------------------------------------------------------


@router.get("/{partner_id}/penalty", response_model=PenaltySummaryOut)
async def partner_penalty(
    partner_id: str, current: CurrentUser, db: DbSession, currency: str | None = None
):
    """Late-payment interest still BILLABLE across the partner's overdue invoices.

    `currency` selects which one to summarise; omitted, the largest is used and
    `currencies` reports the rest. Totals cover one currency only — they are
    never summed across.
    """
    await _guard(db, current.org_id)
    p = await _load(db, current.org_id, partner_id)
    s = await partners.penalty_summary(db, current.org_id, p.id, currency=currency)

    blocked = None
    if not p.penalty_enabled:
        blocked = "Penalty invoicing is not enabled for this partner."
    elif not partners.has_signed_contract(p):
        blocked = "A signed contract is required before a penalty invoice can be generated."
    elif s.total_penalty <= 0:
        blocked = "This partner has no unbilled late-payment interest to bill."

    return PenaltySummaryOut(
        currency=s.currency,
        total_penalty=s.total_penalty,
        total_outstanding=s.total_outstanding,
        max_days_overdue=s.max_days_overdue,
        lines=[
            PenaltyLineOut(
                invoice_id=ln.invoice_id,
                number=ln.number,
                days_overdue=ln.days_overdue,
                outstanding=ln.outstanding,
                penalty=ln.penalty,
            )
            for ln in s.lines
        ],
        can_generate=blocked is None,
        blocked_reason=blocked,
        currencies=s.currencies,
    )


@router.post(
    "/{partner_id}/penalty-invoice",
    response_model=IssuedInvoiceDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=_WRITE,
)
async def generate_penalty_invoice(
    partner_id: str, current: CurrentUser, db: DbSession, currency: str | None = None
):
    """Generate a penalty (late-interest) invoice for the partner. Requires the
    partner to allow penalties and to have a signed contract."""
    await _guard(db, current.org_id)
    p = await _load(db, current.org_id, partner_id)
    try:
        inv = await partners.generate_penalty_invoice(db, current.org_id, p, currency=currency)
    except partners.PenaltyBlocked as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    await audit.record(
        db,
        audit.A.ISSUED_PENALTY,
        target_type="issued_invoice",
        target_id=inv.id,
        meta={"partner": p.name, "number": inv.number, "amount": str(inv.total)},
    )
    await db.commit()
    await db.refresh(inv, attribute_names=["lines"])

    # Reuse the issued serializer for a consistent response.
    from app.api.routes.issued import _detail as issued_detail

    return issued_detail(inv)

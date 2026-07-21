from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.partner import Partner, PartnerDocument
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

router = APIRouter(prefix="/partners", tags=["partners"])


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


@router.post("", response_model=PartnerDetail, status_code=status.HTTP_201_CREATED)
async def create_partner(body: PartnerIn, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    p = Partner(org_id=current.org_id, **body.model_dump())
    if p.country:
        p.country = p.country.upper()
    db.add(p)
    await audit.record(
        db, audit.A.PARTNER_CREATE, target_type="partner", target_id=p.id, meta={"name": p.name}
    )
    await db.commit()
    await db.refresh(p, attribute_names=["documents"])
    return _detail(p)


@router.get("/{partner_id}", response_model=PartnerDetail)
async def get_partner(partner_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    return _detail(await _load(db, current.org_id, partner_id))


@router.patch("/{partner_id}", response_model=PartnerDetail)
async def update_partner(partner_id: str, body: PartnerUpdate, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    p = await _load(db, current.org_id, partner_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "country" and value:
            value = value.upper()
        setattr(p, field, value)
    await db.commit()
    await db.refresh(p, attribute_names=["documents"])
    return _detail(p)


# --- Documents (contract / acceptance act) -------------------------------------


@router.post(
    "/{partner_id}/documents",
    response_model=PartnerDocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_document(
    partner_id: str, body: PartnerDocumentIn, current: CurrentUser, db: DbSession
):
    await _guard(db, current.org_id)
    p = await _load(db, current.org_id, partner_id)
    doc = PartnerDocument(
        org_id=current.org_id,
        partner_id=p.id,
        kind=body.kind,
        title=body.title,
        reference=body.reference,
        note=body.note,
        status="draft",
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return PartnerDocumentOut.model_validate(doc)


@router.post("/{partner_id}/documents/{doc_id}/sign", response_model=PartnerDetail)
async def sign_document(
    partner_id: str, doc_id: str, body: DocumentSignIn, current: CurrentUser, db: DbSession
):
    """Mark a partner document as signed — this can unlock invoicing."""
    from datetime import date

    await _guard(db, current.org_id)
    doc = await db.scalar(
        select(PartnerDocument).where(
            PartnerDocument.id == doc_id,
            PartnerDocument.partner_id == partner_id,
            PartnerDocument.org_id == current.org_id,
        )
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    doc.status = "signed"
    doc.signed_by = body.signed_by
    doc.signed_date = body.signed_date or date.today()
    await audit.record(
        db,
        audit.A.PARTNER_DOC_SIGN,
        target_type="partner_document",
        target_id=doc.id,
        meta={"partner_id": partner_id, "kind": doc.kind},
    )
    await db.commit()
    return _detail(await _load(db, current.org_id, partner_id))


@router.delete("/{partner_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(partner_id: str, doc_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    doc = await db.scalar(
        select(PartnerDocument).where(
            PartnerDocument.id == doc_id,
            PartnerDocument.partner_id == partner_id,
            PartnerDocument.org_id == current.org_id,
        )
    )
    if doc is not None:
        await db.delete(doc)
        await db.commit()


# --- Penalty invoicing ---------------------------------------------------------


@router.get("/{partner_id}/penalty", response_model=PenaltySummaryOut)
async def partner_penalty(partner_id: str, current: CurrentUser, db: DbSession):
    """Accrued late-payment interest across the partner's overdue invoices."""
    await _guard(db, current.org_id)
    p = await _load(db, current.org_id, partner_id)
    s = await partners.penalty_summary(db, current.org_id, p.id)

    blocked = None
    if not p.penalty_enabled:
        blocked = "Penalty invoicing is not enabled for this partner."
    elif not partners.has_signed_contract(p):
        blocked = "A signed contract is required before a penalty invoice can be generated."
    elif s.total_penalty <= 0:
        blocked = "This partner has no accrued late-payment interest to bill."

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
    )


@router.post(
    "/{partner_id}/penalty-invoice",
    response_model=IssuedInvoiceDetail,
    status_code=status.HTTP_201_CREATED,
)
async def generate_penalty_invoice(partner_id: str, current: CurrentUser, db: DbSession):
    """Generate a penalty (late-interest) invoice for the partner. Requires the
    partner to allow penalties and to have a signed contract."""
    await _guard(db, current.org_id)
    p = await _load(db, current.org_id, partner_id)
    try:
        inv = await partners.generate_penalty_invoice(db, current.org_id, p)
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

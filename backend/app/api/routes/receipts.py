"""Incoming-payment receipts + allocation across issued invoices (Slice 5c).

A receipt (money received) can be split across several receivables; each
allocation records a payment ledger entry, so an invoice's amount_paid stays the
single source of truth. Gated on the issuing module.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.issued_invoice import IssuedInvoice
from app.schemas.receipt import (
    AllocateRequest,
    ReceiptAllocationOut,
    ReceiptCreate,
    ReceiptDetail,
    ReceiptOut,
)
from app.services import modules, receipts

router = APIRouter(prefix="/receipts", tags=["receipts"])


async def _guard(db: DbSession, org_id: str) -> None:
    await modules.require_enabled(db, org_id, "issuing")


async def _out(db: DbSession, org_id: str, r) -> ReceiptOut:
    allocated = await receipts.allocated_total(db, org_id, r.id)
    return ReceiptOut(
        id=r.id,
        amount=r.amount,
        received_on=r.received_on,
        method=r.method,
        reference=r.reference,
        note=r.note,
        allocated=allocated,
        unallocated=(r.amount - allocated),
        created_at=r.created_at,
    )


async def _detail(db: DbSession, org_id: str, r) -> ReceiptDetail:
    base = await _out(db, org_id, r)
    allocs = await receipts.allocations(db, org_id, r.id)
    return ReceiptDetail(
        **base.model_dump(),
        allocations=[ReceiptAllocationOut.model_validate(p) for p in allocs],
    )


async def _load(db: DbSession, org_id: str, receipt_id: str):
    r = await receipts.get(db, org_id, receipt_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt not found")
    return r


@router.post("", response_model=ReceiptOut, status_code=status.HTTP_201_CREATED)
async def create_receipt(body: ReceiptCreate, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    r = await receipts.create(
        db,
        current.org_id,
        amount=body.amount,
        received_on=body.received_on,
        method=body.method,
        reference=body.reference,
        note=body.note,
    )
    return await _out(db, current.org_id, r)


@router.get("", response_model=list[ReceiptOut])
async def list_receipts(current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    rows = await receipts.list_receipts(db, current.org_id)
    return [await _out(db, current.org_id, r) for r in rows]


@router.get("/{receipt_id}", response_model=ReceiptDetail)
async def get_receipt(receipt_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    r = await _load(db, current.org_id, receipt_id)
    return await _detail(db, current.org_id, r)


@router.post("/{receipt_id}/allocate", response_model=ReceiptDetail)
async def allocate_receipt(
    receipt_id: str, body: AllocateRequest, current: CurrentUser, db: DbSession
):
    await _guard(db, current.org_id)
    r = await _load(db, current.org_id, receipt_id)
    inv = await db.scalar(
        select(IssuedInvoice).where(
            IssuedInvoice.org_id == current.org_id, IssuedInvoice.id == body.invoice_id
        )
    )
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Issued invoice not found")
    try:
        await receipts.allocate(db, current.org_id, r, inv, body.amount)
    except receipts.ReceiptError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return await _detail(db, current.org_id, r)

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession
from app.api.routes.vendors import get_or_create_vendor
from app.models.invoice import Invoice, InvoiceStatus, LineItem
from app.models.vendor import Vendor
from app.schemas.invoice import (
    InvoiceCreate,
    InvoiceDetailOut,
    InvoiceListOut,
    InvoiceOut,
    InvoiceUpdate,
    LineItemOut,
    ParsedInvoiceDraft,
)
from app.services.parser import parse_invoice_file

router = APIRouter(prefix="/invoices", tags=["invoices"])

_CENTS = Decimal("0.01")
_MAX_UPLOAD = 5 * 1024 * 1024  # 5 MB


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


async def _resolve_vendor(db: DbSession, org_id: str, body: InvoiceCreate) -> Vendor:
    if body.vendor_id:
        vendor = await db.get(Vendor, body.vendor_id)
        if vendor is None or vendor.org_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Vendor not found")
        return vendor
    if body.vendor_name and body.vendor_name.strip():
        return await get_or_create_vendor(db, org_id, body.vendor_name)
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY, "vendor_id or vendor_name is required"
    )


def _detail(inv: Invoice, vendor_name: str) -> InvoiceDetailOut:
    return InvoiceDetailOut(
        id=inv.id,
        vendor_id=inv.vendor_id,
        vendor_name=vendor_name,
        invoice_number=inv.invoice_number,
        issue_date=inv.issue_date,
        due_date=inv.due_date,
        currency=inv.currency,
        status=inv.status,
        subtotal=inv.subtotal,
        tax_amount=inv.tax_amount,
        total=inv.total,
        source_filename=inv.source_filename,
        notes=inv.notes,
        line_items=[LineItemOut.model_validate(li) for li in inv.line_items],
    )


@router.post("", response_model=InvoiceDetailOut, status_code=status.HTTP_201_CREATED)
async def create_invoice(body: InvoiceCreate, current: CurrentUser, db: DbSession):
    vendor = await _resolve_vendor(db, current.org_id, body)

    subtotal = Decimal("0")
    tax_total = Decimal("0")
    items: list[LineItem] = []
    for li in body.line_items:
        amount = li.amount if li.amount is not None else (li.quantity * li.unit_price)
        amount = _q(amount)
        line_tax = _q(amount * li.tax_rate / Decimal("100"))
        subtotal += amount
        tax_total += line_tax
        items.append(
            LineItem(
                description=li.description,
                category=li.category or "uncategorized",
                quantity=li.quantity,
                unit_price=li.unit_price,
                amount=amount,
                tax_rate=li.tax_rate,
            )
        )

    invoice = Invoice(
        org_id=current.org_id,
        vendor_id=vendor.id,
        invoice_number=body.invoice_number,
        issue_date=body.issue_date,
        due_date=body.due_date,
        currency=body.currency.upper(),
        status=body.status,
        subtotal=_q(subtotal),
        tax_amount=_q(tax_total),
        total=_q(subtotal + tax_total),
        notes=body.notes,
        source_filename=body.source_filename,
        line_items=items,
    )
    db.add(invoice)
    await db.commit()
    await db.refresh(invoice, attribute_names=["line_items"])
    return _detail(invoice, vendor.name)


@router.get("", response_model=InvoiceListOut)
async def list_invoices(
    current: CurrentUser,
    db: DbSession,
    vendor_id: str | None = None,
    status_: InvoiceStatus | None = Query(default=None, alias="status"),
    start: date | None = None,
    end: date | None = None,
    q: str | None = Query(default=None, description="search invoice number"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    filters = [Invoice.org_id == current.org_id]
    if vendor_id:
        filters.append(Invoice.vendor_id == vendor_id)
    if status_:
        filters.append(Invoice.status == status_)
    if start:
        filters.append(Invoice.issue_date >= start)
    if end:
        filters.append(Invoice.issue_date <= end)
    if q:
        filters.append(Invoice.invoice_number.ilike(f"%{q.strip()}%"))

    total = await db.scalar(select(func.count(Invoice.id)).where(*filters))
    rows = await db.scalars(
        select(Invoice)
        .where(*filters)
        .order_by(Invoice.issue_date.desc(), Invoice.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return InvoiceListOut(
        items=[InvoiceOut.model_validate(r) for r in rows],
        total=total or 0,
        page=page,
        page_size=page_size,
    )


async def _load_scoped(db: DbSession, org_id: str, invoice_id: str) -> Invoice:
    invoice = await db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.org_id == org_id)
        .options(selectinload(Invoice.line_items), selectinload(Invoice.vendor))
    )
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return invoice


@router.get("/{invoice_id}", response_model=InvoiceDetailOut)
async def get_invoice(invoice_id: str, current: CurrentUser, db: DbSession):
    invoice = await _load_scoped(db, current.org_id, invoice_id)
    return _detail(invoice, invoice.vendor.name)


@router.patch("/{invoice_id}", response_model=InvoiceDetailOut)
async def update_invoice(
    invoice_id: str, body: InvoiceUpdate, current: CurrentUser, db: DbSession
):
    invoice = await _load_scoped(db, current.org_id, invoice_id)
    if body.status is not None:
        invoice.status = body.status
    if body.due_date is not None:
        invoice.due_date = body.due_date
    if body.notes is not None:
        invoice.notes = body.notes
    await db.commit()
    await db.refresh(invoice, attribute_names=["line_items"])
    return _detail(invoice, invoice.vendor.name)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(invoice_id: str, current: CurrentUser, db: DbSession):
    invoice = await _load_scoped(db, current.org_id, invoice_id)
    await db.delete(invoice)
    await db.commit()


@router.post("/upload", response_model=ParsedInvoiceDraft)
async def upload_invoice(current: CurrentUser, file: UploadFile):
    """Parse an uploaded CSV/JSON into a draft. Does NOT persist — the client
    reviews the draft and POSTs it to `/invoices` to save."""
    content = await file.read()
    if len(content) > _MAX_UPLOAD:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (max 5 MB)")
    try:
        return parse_invoice_file(file.filename or "upload", content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

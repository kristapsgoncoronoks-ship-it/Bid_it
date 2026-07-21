from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession
from app.api.routes.vendors import get_or_create_vendor
from app.core.dimensions import DIMENSION_KEYS
from app.core.money import q2 as _q
from app.models.invoice import Invoice, InvoiceStatus, LineItem
from app.models.organization import Organization
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
from app.schemas.validation import ValidationDecision, ValidationFinding
from app.services import access, audit, costing, filesec, fx, validation, webhooks
from app.services.parser import parse_invoice_file

router = APIRouter(prefix="/invoices", tags=["invoices"])

_MAX_UPLOAD = 15 * 1024 * 1024  # 15 MB (scanned PDFs run larger)


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
        total_eur=inv.total_eur,
        fx_rate=inv.fx_rate,
        fx_source=inv.fx_source,
        validation_status=inv.validation_status,
        source_filename=inv.source_filename,
        notes=inv.notes,
        cost_center=inv.cost_center,
        department=inv.department,
        project=inv.project,
        vehicle=inv.vehicle,
        property_ref=inv.property_ref,
        line_items=[LineItemOut.model_validate(li) for li in inv.line_items],
        validation_findings=_parse_findings(inv.validation_findings),
        validated_by=inv.validated_by,
        validated_at=inv.validated_at,
    )


def _parse_findings(raw: str | None) -> list[ValidationFinding]:
    if not raw:
        return []
    try:
        return [ValidationFinding(**x) for x in json.loads(raw)]
    except (ValueError, TypeError):
        return []


async def persist_invoice(db: DbSession, org_id: str, body: InvoiceCreate) -> tuple[Invoice, str]:
    """Create and persist an invoice from a draft (vendor resolve → line math →
    FX-to-EUR → validation). Shared by the manual create route and the email-intake
    confirm route so an emailed invoice is saved *identically* to an uploaded one.
    Returns the refreshed invoice and its vendor name."""
    vendor = await _resolve_vendor(db, org_id, body)

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

    total = _q(subtotal + tax_total)
    currency = body.currency.upper()
    # Convert to EUR: use the invoice-stated rate if given, else the ECB rate for
    # the issue date (cached in ecb_rates). EUR invoices are 1:1.
    total_eur, fx_source = await fx.eur_total(db, total, currency, body.issue_date, body.fx_rate)

    invoice = Invoice(
        org_id=org_id,
        vendor_id=vendor.id,
        invoice_number=body.invoice_number,
        issue_date=body.issue_date,
        due_date=body.due_date,
        currency=currency,
        status=body.status,
        subtotal=_q(subtotal),
        tax_amount=_q(tax_total),
        total=total,
        total_eur=total_eur,
        fx_rate=body.fx_rate,
        fx_source=fx_source,
        notes=body.notes,
        source_filename=body.source_filename,
        cost_center=body.cost_center,
        department=body.department,
        project=body.project,
        vehicle=body.vehicle,
        property_ref=body.property_ref,
        line_items=items,
    )
    # Link the cost-allocation tags to master data at write time (Slice 3a).
    await costing.apply_links(db, org_id, invoice, DIMENSION_KEYS)

    # Data validation (AI / human) — runs only for the options the org enabled.
    org = await db.get(Organization, org_id)
    await validation.apply_validation(
        db, invoice, org.ai_validation_enabled, org.human_validation_enabled, date.today()
    )

    db.add(invoice)
    await db.commit()
    await db.refresh(invoice, attribute_names=["line_items"])
    return invoice, vendor.name


@router.post("", response_model=InvoiceDetailOut, status_code=status.HTTP_201_CREATED)
async def create_invoice(body: InvoiceCreate, current: CurrentUser, db: DbSession):
    # System-matrix usage limit for the caller's access level.
    await access.enforce_invoice_quota(db, current.org_id, current.role)
    invoice, vendor_name = await persist_invoice(db, current.org_id, body)
    await audit.record(
        db,
        audit.A.INVOICE_CREATE,
        target_type="invoice",
        target_id=invoice.id,
        meta={
            "number": invoice.invoice_number,
            "total": str(invoice.total),
            "currency": invoice.currency,
        },
    )
    await webhooks.emit(
        db,
        current.org_id,
        "invoice.created",
        {
            "id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "vendor_name": vendor_name,
            "total": str(invoice.total),
            "currency": invoice.currency,
            "status": invoice.status.value,
        },
    )
    await db.commit()
    return _detail(invoice, vendor_name)


@router.get("", response_model=InvoiceListOut)
async def list_invoices(
    current: CurrentUser,
    db: DbSession,
    vendor_id: str | None = None,
    status_: InvoiceStatus | None = Query(default=None, alias="status"),
    start: date | None = None,
    end: date | None = None,
    q: str | None = Query(default=None, description="search invoice number"),
    validation_status: str | None = Query(
        default=None, description="none|passed|flagged|pending|approved|rejected"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    filters = [Invoice.org_id == current.org_id]
    if vendor_id:
        filters.append(Invoice.vendor_id == vendor_id)
    if status_:
        filters.append(Invoice.status == status_)
    if validation_status:
        filters.append(Invoice.validation_status == validation_status)
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
async def update_invoice(invoice_id: str, body: InvoiceUpdate, current: CurrentUser, db: DbSession):
    invoice = await _load_scoped(db, current.org_id, invoice_id)
    if body.status is not None:
        invoice.status = body.status
    if body.due_date is not None:
        invoice.due_date = body.due_date
    if body.notes is not None:
        invoice.notes = body.notes
    # Cost dimensions: apply only those explicitly present in the request (a
    # provided null clears the tag; an absent field is left unchanged).
    changed = [key for key in DIMENSION_KEYS if key in body.model_fields_set]
    for key in changed:
        setattr(invoice, key, getattr(body, key))
    # Re-resolve the master link only for the dimensions that changed (Slice 3a).
    await costing.apply_links(db, current.org_id, invoice, changed)
    await db.commit()
    await db.refresh(invoice, attribute_names=["line_items"])
    return _detail(invoice, invoice.vendor.name)


@router.post("/{invoice_id}/validate", response_model=InvoiceDetailOut)
async def human_validate(
    invoice_id: str, body: ValidationDecision, current: CurrentUser, db: DbSession
):
    """Human review gate: approve or reject an invoice pending validation."""
    invoice = await _load_scoped(db, current.org_id, invoice_id)
    invoice.validation_status = (
        validation.APPROVED if body.action == "approve" else validation.REJECTED
    )
    invoice.validated_by = current.email
    invoice.validated_at = datetime.now(UTC)
    if body.note:
        invoice.notes = ((invoice.notes + "\n") if invoice.notes else "") + f"[review] {body.note}"
    await db.commit()
    await db.refresh(invoice, attribute_names=["line_items"])
    return _detail(invoice, invoice.vendor.name)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(invoice_id: str, current: CurrentUser, db: DbSession):
    invoice = await _load_scoped(db, current.org_id, invoice_id)
    await audit.record(
        db,
        audit.A.INVOICE_DELETE,
        target_type="invoice",
        target_id=invoice_id,
        meta={"number": invoice.invoice_number},
    )
    await db.delete(invoice)
    await db.commit()


@router.post("/upload", response_model=ParsedInvoiceDraft)
async def upload_invoice(current: CurrentUser, db: DbSession, file: UploadFile):
    """Parse an uploaded PDF/CSV/JSON into a draft. Does NOT persist — the client
    reviews the draft and POSTs it to `/invoices` to save. PDFs use the text layer
    when present and fall back to OCR for scanned documents."""
    # Metered usage: the acting role's monthly upload limit (0 = unlimited).
    await access.enforce_upload_quota(db, current.org_id, current.role)
    content = await file.read()
    if len(content) > _MAX_UPLOAD:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (max 15 MB)")
    # Security gate: type-validate + malware-scan before any parsing/OCR.
    try:
        filesec.check(file.filename or "upload", content, allowed=filesec.INVOICE_KINDS)
    except filesec.FileRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))
    try:
        # OCR/PDF parsing is CPU-bound — run it in a threadpool so the event loop
        # isn't blocked while one upload is parsed.
        draft = await run_in_threadpool(parse_invoice_file, file.filename or "upload", content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    await access.record_usage(db, current.org_id, "upload")
    return draft

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.core.dimensions import DIMENSION_KEYS
from app.core.money import q2 as _q
from app.models.extraction_field import ExtractionField
from app.models.extraction_run import ExtractionRun
from app.models.invoice import Invoice, InvoiceStatus, LineItem, WorkflowState
from app.models.organization import Organization
from app.models.vendor import Vendor
from app.schemas.ap_payment import SupplierPaymentOut, SupplierPaymentRecord
from app.schemas.invoice import (
    CaptureReviewIn,
    CaptureReviewItem,
    CaptureReviewQueueOut,
    DuplicateCandidateOut,
    DuplicateReportOut,
    ExtractionResult,
    ExtractionRunOut,
    FieldProvenanceOut,
    InvoiceCreate,
    InvoiceDetailOut,
    InvoiceListOut,
    InvoiceOut,
    InvoiceUpdate,
    LineItemOut,
    ParsedInvoiceDraft,
    UploadAccepted,
)
from app.schemas.validation import ValidationDecision, ValidationFinding
from app.services import (
    access,
    ap_payments,
    ap_status,
    audit,
    costing,
    documents,
    duplicates,
    extraction,
    filesec,
    fx,
    invoice_workflow,
    jobs,
    validation,
    webhooks,
)
from app.services.vendors import get_or_create_vendor

# Structural authorization (ADR-0024): every invoice route needs at least
# INVOICE_READ (router-level — held by EVERY business role, so the metered
# capture flow stays open to every tier exactly as documented on
# `create_invoice`/`upload_invoice`). The privileged operations declare their
# stricter permission per-route below.
router = APIRouter(
    prefix="/invoices",
    tags=["invoices"],
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_READ))],
)

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
        status.HTTP_422_UNPROCESSABLE_CONTENT, "vendor_id or vendor_name is required"
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
        workflow_state=inv.workflow_state.value,
        amount_paid=inv.amount_paid,
        paid_date=inv.paid_date,
        outstanding=ap_status.outstanding_of(inv),
        payment_status=ap_status.status_of(inv),
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
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    await validation.apply_validation(
        db, invoice, org.ai_validation_enabled, org.human_validation_enabled, date.today()
    )

    db.add(invoice)
    await db.flush()  # the invoice row must exist before the lineage FK is set
    # Slice 5b: link the capture run this invoice was saved from (if any).
    if body.extraction_run_id:
        await extraction.link_to_invoice(db, org_id, body.extraction_run_id, invoice.id)
    await db.commit()
    await db.refresh(invoice, attribute_names=["line_items"])
    return invoice, vendor.name


@router.post("", response_model=InvoiceDetailOut, status_code=status.HTTP_201_CREATED)
async def create_invoice(body: InvoiceCreate, current: CurrentUser, db: DbSession):
    # NOTE: invoice capture (create/upload) is the metered data-entry flow open to
    # every billing tier — including `user_free` — and is governed by the usage
    # quota below, NOT by INVOICE_WRITE. Only the privileged operations (approve/
    # reject and delete) are permission-gated. See test_access (free-tier limits).
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


@router.get("/duplicate-candidates", response_model=DuplicateReportOut)
async def duplicate_candidates(
    current: CurrentUser,
    db: DbSession,
    invoice_number: str = Query(..., min_length=1),
    vendor_id: str | None = Query(default=None),
    exclude_invoice_id: str | None = Query(default=None),
):
    """Same-number invoices in this org, split into likely duplicates (same
    supplier) vs cross-supplier candidates (a different supplier with the same
    number). Advisory — never blocks a save."""
    report = await duplicates.candidates(
        db,
        current.org_id,
        invoice_number=invoice_number,
        vendor_id=vendor_id,
        exclude_invoice_id=exclude_invoice_id,
    )

    def _out(c: duplicates.Candidate) -> DuplicateCandidateOut:
        return DuplicateCandidateOut(
            invoice_id=c.invoice_id,
            vendor_id=c.vendor_id,
            vendor_name=c.vendor_name,
            invoice_number=c.invoice_number,
            issue_date=c.issue_date,
            total=c.total,
            currency=c.currency,
            status=c.status,
        )

    return DuplicateReportOut(
        invoice_number=report.invoice_number,
        exact=[_out(c) for c in report.exact],
        cross_supplier=[_out(c) for c in report.cross_supplier],
    )


@router.get("/captures/review", response_model=CaptureReviewQueueOut)
async def capture_review_queue(
    current: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """Human-review queue for intake: captures that PARSED but aren't saved to an
    invoice yet, each with its low-confidence field count + a duplicate flag, so a
    reviewer triages what needs a look. Newest first. Tenant-scoped."""
    base = select(ExtractionRun).where(
        ExtractionRun.org_id == current.org_id,
        ExtractionRun.status == "parsed",
        ExtractionRun.invoice_id.is_(None),
    )
    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    runs = list(
        await db.scalars(
            base.order_by(ExtractionRun.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    items: list[CaptureReviewItem] = []
    for run in runs:
        fields = await extraction.fields_for_run(db, current.org_id, run.id)
        low = sum(1 for f in fields if f.low_confidence)
        number: str | None = None
        vendor_name: str | None = None
        if run.draft_json:
            try:
                d = ParsedInvoiceDraft.model_validate_json(run.draft_json)
                number = d.draft.invoice_number
                vendor_name = d.draft.vendor_name
            except Exception:  # noqa: BLE001 - a bad cached draft must not break the queue
                pass
        dup = False
        if number:
            dup = (
                await db.scalar(
                    select(Invoice.id)
                    .where(Invoice.org_id == current.org_id, Invoice.invoice_number == number)
                    .limit(1)
                )
            ) is not None
        items.append(
            CaptureReviewItem(
                extraction_run_id=run.id,
                method=run.method,
                status=run.status,
                source_filename=run.source_filename,
                invoice_number=number,
                vendor_name=vendor_name,
                warning_count=run.warning_count,
                total_fields=len(fields),
                low_confidence_fields=low,
                duplicate_candidate=dup,
                created_at=run.created_at,
            )
        )
    return CaptureReviewQueueOut(items=items, total=total or 0)


@router.post("/captures/{run_id}/review", response_model=list[FieldProvenanceOut])
async def review_capture_fields(
    run_id: str, body: CaptureReviewIn, current: CurrentUser, db: DbSession
):
    """Record human corrections for a capture's fields — stores the reviewed value
    and clears the low-confidence flag. The reviewed value is preserved alongside
    the original/normalized capture, so the correction is auditable."""
    run = await extraction.get_capture(db, current.org_id, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Capture not found")
    fields = await extraction.fields_for_run(db, current.org_id, run_id)
    by_name = {f.field: f for f in fields}
    for item in body.fields:
        f = by_name.get(item.field)
        if f is not None:
            f.reviewed_value = item.reviewed_value[:500]
            f.low_confidence = False
    await db.commit()
    fields = await extraction.fields_for_run(db, current.org_id, run_id)
    return [FieldProvenanceOut.model_validate(f) for f in fields]


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


@router.get("/{invoice_id}/extraction", response_model=list[ExtractionRunOut])
async def invoice_extraction(invoice_id: str, current: CurrentUser, db: DbSession):
    """The capture lineage (how this invoice was read) — Slice 5b — with each
    run's per-field provenance (Slice 5f)."""
    await _load_scoped(db, current.org_id, invoice_id)  # tenant-scoped existence check
    runs = await extraction.list_for_invoice(db, current.org_id, invoice_id)
    out: list[ExtractionRunOut] = []
    for run in runs:
        fields = await extraction.fields_for_run(db, current.org_id, run.id)
        run_out = ExtractionRunOut.model_validate(run)
        run_out.fields = [FieldProvenanceOut.model_validate(f) for f in fields]
        out.append(run_out)
    return out


@router.patch(
    "/{invoice_id}",
    response_model=InvoiceDetailOut,
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_WRITE))],
)
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


# States from which a supplier invoice can be paid (see invoice_workflow.TRANSITIONS).
_PAYABLE = frozenset({WorkflowState.scheduled_for_payment, WorkflowState.partially_paid})


@router.get(
    "/{invoice_id}/payments",
    response_model=list[SupplierPaymentOut],
    dependencies=[Depends(require_perm(authz.Permission.PAYMENT_READ))],
)
async def list_supplier_payments(invoice_id: str, current: CurrentUser, db: DbSession):
    """The AP payment-ledger history for one supplier invoice (Phase 13)."""
    await _load_scoped(db, current.org_id, invoice_id)  # tenant-scoped existence check
    rows = await ap_payments.list_for(db, current.org_id, invoice_id)
    return [SupplierPaymentOut.model_validate(p) for p in rows]


@router.patch(
    "/{invoice_id}/payment",
    response_model=InvoiceDetailOut,
    dependencies=[Depends(require_perm(authz.Permission.PAYMENT_WRITE))],
)
async def record_supplier_payment(
    invoice_id: str, body: SupplierPaymentRecord, current: CurrentUser, db: DbSession
):
    """Record a payment against an APPROVED, scheduled-for-payment supplier invoice.
    The body carries the new CUMULATIVE amount paid; the ledger records the delta
    (a downward figure is an auditable correction). The amount is capped at the
    invoice total, and settling it (partially/fully) advances the workflow state to
    partially_paid / paid."""
    inv = await _load_scoped(db, current.org_id, invoice_id)
    if inv.workflow_state not in _PAYABLE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invoice must be scheduled for payment before it can be paid",
        )
    total = _q(Decimal(inv.total))
    new_total = _q(Decimal(body.amount_paid))
    if new_total > total:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"amount exceeds the invoice total ({total})"
        )

    await ap_payments.set_cumulative(
        db,
        current.org_id,
        inv,
        new_total=new_total,
        paid_date=body.paid_date,
        method=body.method,
        reference=body.reference,
    )
    # Advance the approval workflow to match the settlement.
    target: WorkflowState | None = None
    if new_total >= total and total > 0:
        target = WorkflowState.paid
    elif new_total > 0 and inv.workflow_state == WorkflowState.scheduled_for_payment:
        target = WorkflowState.partially_paid
    if target is not None and target != inv.workflow_state:
        invoice_workflow.assert_transition(inv.workflow_state, target)
        inv.workflow_state = target
        if target == WorkflowState.paid:
            inv.status = InvoiceStatus.paid  # sync the legacy aging status
        inv.version = (inv.version or 0) + 1

    await audit.record(
        db,
        audit.A.AP_PAYMENT,
        target_type="invoice",
        target_id=inv.id,
        meta={"amount_paid": str(new_total), "status": ap_status.status_of(inv)},
    )
    if target == WorkflowState.paid:
        await webhooks.emit(
            db,
            current.org_id,
            "invoice.paid",
            {"id": inv.id, "invoice_number": inv.invoice_number},
        )
    await db.commit()
    await db.refresh(inv, attribute_names=["line_items", "vendor"])
    return _detail(inv, inv.vendor.name)


@router.post(
    "/{invoice_id}/validate",
    response_model=InvoiceDetailOut,
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_APPROVE))],
)
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


@router.delete(
    "/{invoice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_DELETE))],
)
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


@router.post("/upload", response_model=UploadAccepted, status_code=status.HTTP_202_ACCEPTED)
async def upload_invoice(current: CurrentUser, db: DbSession, file: UploadFile):
    """Accept an uploaded supplier invoice (PDF / JPEG / PNG / XML / CSV / JSON) and
    QUEUE it for parsing (Stage B). Images and scanned PDFs go through OCR.

    The security scan runs inline (a bad file is rejected immediately), but the
    CPU-heavy parse/OCR runs on the WORKER tier — so a burst of large uploads
    never ties up the API. Returns 202 + an `extraction_run_id`; poll
    GET /invoices/upload/{extraction_run_id} for the draft, then POST it to
    /invoices to save."""
    # Metered usage: the acting role's monthly upload limit (0 = unlimited). Upload
    # is metered data capture, open to every tier (see create_invoice) — not gated.
    await access.enforce_upload_quota(db, current.org_id, current.role)
    content = await file.read()
    if len(content) > _MAX_UPLOAD:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (max 15 MB)")
    # Security gate: type-validate + malware-scan BEFORE storing or queuing.
    try:
        filesec.check(file.filename or "upload", content, allowed=filesec.SUPPLIER_UPLOAD_KINDS)
    except filesec.FileRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))
    sha = extraction.sha256_hex(content)
    # Persist the original so the worker can parse it off-tier, then queue a run.
    await documents.store(
        documents.UPLOADS,
        current.org_id,
        content,
        file.content_type,
        db=db,
        filename=file.filename,
        uploaded_by=current.email,
    )
    run = await extraction.start_capture(db, current.org_id, filename=file.filename, sha256=sha)
    await jobs.enqueue(
        db,
        extraction.UPLOAD_EXTRACT_KIND,
        {"run_id": run.id},
        org_id=current.org_id,
        idempotency_key=f"upload-extract:{run.id}",
    )
    await access.record_usage(db, current.org_id, "upload")
    return UploadAccepted(extraction_run_id=run.id, status=run.status)


@router.get("/upload/{run_id}", response_model=ExtractionResult)
async def upload_status(run_id: str, current: CurrentUser, db: DbSession):
    """Poll an async upload capture. While queued/running, `draft` is null; once
    parsed it carries the reviewable `ParsedInvoiceDraft`; on failure, `error`."""
    run = await extraction.get_capture(db, current.org_id, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    draft = None
    if run.status == "parsed" and run.draft_json:
        draft = ParsedInvoiceDraft.model_validate_json(run.draft_json)
    return ExtractionResult(
        extraction_run_id=run.id,
        status=run.status,
        method=(run.method if run.method not in (None, "pending") else None),
        draft=draft,
        error=(run.note if run.status == "failed" else None),
    )


@router.post(
    "/upload/{run_id}/retry", response_model=UploadAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def retry_upload(run_id: str, current: CurrentUser, db: DbSession):
    """Manually re-run extraction for a FAILED or PARSED-but-unsaved capture — e.g.
    after a transient OCR failure, or to re-parse with an updated provider. Re-queues
    the SAME stored bytes (the original file is never re-uploaded or mutated) and
    clears the previous parse's provenance. Tenant-scoped."""
    run = await extraction.get_capture(db, current.org_id, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    if run.invoice_id is not None or run.status == "saved":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This capture was already saved to an invoice"
        )
    if not run.source_sha256:
        raise HTTPException(status.HTTP_409_CONFLICT, "No stored file to re-extract")

    run.status = "queued"
    run.method = "pending"
    run.note = None
    run.draft_json = None
    await db.execute(
        delete(ExtractionField).where(
            ExtractionField.org_id == current.org_id,
            ExtractionField.extraction_run_id == run.id,
        )
    )
    await db.flush()
    # A fresh (unkeyed) job so a prior finished upload-extract job doesn't dedupe
    # this deliberate retry; the handler re-runs because status is "queued".
    await jobs.enqueue(
        db,
        extraction.UPLOAD_EXTRACT_KIND,
        {"run_id": run.id},
        org_id=current.org_id,
        idempotency_key=None,
    )
    await db.commit()
    return UploadAccepted(extraction_run_id=run.id, status=run.status)

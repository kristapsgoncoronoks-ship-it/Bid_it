from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentOrg, CurrentUser, DbSession, require_perm
from app.core import authz, storage
from app.core.dimensions import DIMENSION_KEYS
from app.core.errors import ConflictError
from app.core.money import q2 as _q
from app.core.security_headers import content_disposition
from app.models.document import Document
from app.models.extraction_run import ExtractionRun
from app.models.invoice import Invoice, InvoiceStatus, LineItem, WorkflowState
from app.models.organization import Organization
from app.models.vendor import Vendor
from app.schemas.ap_payment import SupplierPaymentOut, SupplierPaymentRecord
from app.schemas.invoice import (
    BatchUploadAccepted,
    BatchUploadOutcome,
    BinListOut,
    BinnedInvoiceOut,
    BulkAcknowledgeIn,
    BulkAcknowledgeOut,
    BulkDeleteIn,
    BulkDeleteOut,
    BulkOutcomeOut,
    CaptureAcknowledgeIn,
    CaptureFailureGroup,
    CaptureFailureItem,
    CaptureFailureWorklistOut,
    CaptureReviewIn,
    CaptureReviewItem,
    CaptureReviewQueueOut,
    DeleteInvoiceIn,
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
    ScoredCandidateOut,
    UploadAccepted,
)
from app.schemas.project_profit import AllocationIn
from app.schemas.validation import ValidationDecision, ValidationFinding
from app.services import (
    access,
    ap_payments,
    ap_status,
    audit,
    bulk,
    capture_failures,
    capture_memory,
    capture_progress,
    costing,
    documents,
    duplicates,
    extraction,
    filesec,
    fx,
    invoice_workflow,
    jobs,
    validation,
    vendor_resolution,
    webhooks,
)
from app.services import bin as bin_svc
from app.services import (
    invoices as invoice_service,
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


async def _resolve_vendor(db: DbSession, org_id: str, body: InvoiceCreate) -> Vendor:
    if body.vendor_id:
        vendor = await db.get(Vendor, body.vendor_id)
        if vendor is None or vendor.org_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Vendor not found")
        return vendor
    if body.vendor_name and body.vendor_name.strip():
        # H-3: resolving a supplier from a NAME is an automation decision, so it
        # goes on the permanent record with its reason. The audit chain is where
        # "why did the machine do this" belongs — immutable, and already the
        # thing someone reads when they ask months later. Storing the sentence on
        # the invoice instead would go stale the moment a supplier is renamed,
        # and would then be a confident false account of history.
        #
        # Read BEFORE the create, or the resolution would describe the row it
        # just made and always report an exact match.
        res = await vendor_resolution.resolve(db, org_id, body.vendor_name)
        vendor = await get_or_create_vendor(db, org_id, body.vendor_name)
        await audit.record(
            db,
            action="vendor.auto_resolved",
            target_type="vendor",
            target_id=vendor.id,
            meta=vendor_resolution.audit_meta(res),
        )
        return vendor
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
async def create_invoice(
    body: InvoiceCreate, current: CurrentUser, current_org: CurrentOrg, db: DbSession
):
    # NOTE: invoice capture (create/upload) is the metered data-entry flow open to
    # every billing tier — including `user_free` — and is governed by the usage
    # quota below, NOT by INVOICE_WRITE. Only the privileged operations (approve/
    # reject and delete) are permission-gated. See test_access (free-tier limits).
    # System-matrix usage limit — WO-47: keyed by the ORG's plan (every member
    # of the org shares this one cap), not the caller's own role.
    await access.enforce_invoice_quota(db, current.org_id, current_org.plan)
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
    workflow_state: str | None = Query(
        default=None,
        description="a WorkflowState value, or `in_approval` = submitted|partially_approved",
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
    if workflow_state:
        # `in_approval` is a virtual alias so the dashboard's approvals tile can
        # deep-link ONE worklist covering both live-chain states (WO-16).
        if workflow_state == "in_approval":
            filters.append(
                Invoice.workflow_state.in_(
                    (WorkflowState.submitted, WorkflowState.partially_approved)
                )
            )
        else:
            try:
                filters.append(Invoice.workflow_state == WorkflowState(workflow_state))
            except ValueError:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "Unknown workflow_state filter.",
                ) from None
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
    total: Decimal | None = Query(default=None, ge=0),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    issue_date: date | None = Query(default=None),
):
    """Same-number invoices in this org, split into likely duplicates (same
    supplier) vs cross-supplier candidates (a different supplier with the same
    number), plus (E1.4) `scored`: same-VENDOR invoices with a DIFFERENT number
    but a close amount and issue date — only computed when `vendor_id`, `total`,
    `currency` and `issue_date` are all supplied (backward compatible: a caller
    checking only by number gets `scored: []`, unchanged from before E1.4).
    Advisory throughout — never blocks a save."""
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

    scored: list[ScoredCandidateOut] = []
    if vendor_id and total is not None and currency and issue_date:
        scored_rows = await duplicates.scored_candidates(
            db,
            current.org_id,
            vendor_id=vendor_id,
            total=total,
            currency=currency.upper(),
            issue_date=issue_date,
            exclude_invoice_id=exclude_invoice_id,
            exclude_invoice_number=invoice_number,
        )
        scored = [
            ScoredCandidateOut(
                invoice_id=c.invoice_id,
                vendor_id=c.vendor_id,
                vendor_name=c.vendor_name,
                invoice_number=c.invoice_number,
                issue_date=c.issue_date,
                total=c.total,
                currency=c.currency,
                status=c.status,
                score=c.score,
                reason=c.reason,
            )
            for c in scored_rows
        ]

    return DuplicateReportOut(
        invoice_number=report.invoice_number,
        exact=[_out(c) for c in report.exact],
        cross_supplier=[_out(c) for c in report.cross_supplier],
        scored=scored,
    )


def _vendor_name_from_run(run: ExtractionRun) -> str | None:
    """The raw captured header `vendor_name` off a run's cached draft — used
    both to flag a would-be duplicate (existing) and, since E1.5, as the key
    into the capture-memory learning loop. A malformed/missing cached draft
    yields `None`, never an exception — a queue row or a hint lookup must not
    break because one cached draft is unreadable."""
    if not run.draft_json:
        return None
    try:
        return ParsedInvoiceDraft.model_validate_json(run.draft_json).draft.vendor_name
    except Exception:  # noqa: BLE001 - a bad cached draft must not break the caller
        return None


async def _attach_hints(
    db: DbSession, org_id: str, run: ExtractionRun, out: list[FieldProvenanceOut]
) -> list[FieldProvenanceOut]:
    """E1.5: attach the vendor's current advisory hints to each field's live
    provenance, post-validation. Never touches `value`/`reviewed_value` — see
    `capture_memory.hints_for`'s docstring for the §4.19 guarantee."""
    vendor_name = _vendor_name_from_run(run)
    if vendor_name is None:
        return out
    hints = await capture_memory.hints_for(db, org_id, vendor_name, [o.field for o in out])
    for o in out:
        h = hints.get(o.field)
        if h is not None:
            o.suggested_value = h.suggested_value
            o.suggestion_observed_count = h.observed_count
            o.suggestion_corrected_count = h.corrected_count
    return out


@router.get("/captures/review", response_model=CaptureReviewQueueOut)
async def capture_review_queue(
    current: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """Human-review queue for intake: captures that PARSED but aren't saved to an
    invoice yet, each with its low-confidence field count + a duplicate flag, so a
    reviewer triages what needs a look. Newest first. Tenant-scoped.

    Field counts include LINE-scoped rows (E1.2). Deterministic line captures
    contribute zero flags (see extraction_provider._line_flag); OCR/text captures
    flag their genuinely-uncertain cells — so the count stays a signal."""
    # One queue definition (WO-16): the dashboard's counts use the same filter.
    base = select(ExtractionRun).where(*extraction.pending_review_filters(current.org_id))
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


@router.get("/captures/failures", response_model=CaptureFailureWorklistOut)
async def capture_failure_worklist(
    current: CurrentUser,
    db: DbSession,
    include_acknowledged: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    """Every capture in this tenant that FAILED, from both channels (direct upload
    and emailed attachment), newest first — H-1.

    Before this route a failed capture was reachable only by polling its own id,
    which you had to already know. That is the worst shape a document pipeline can
    take: the customer believes the document was processed, it was not, and no
    screen disagrees.

    Each item carries a stable `code` plus the `summary`/`remediation` written for
    a finance operator, `retry_helps` (so a screen never offers a retry that
    cannot work) and `document_retained` (what DID survive). `groups` folds
    repeats of one cause into a single line. Acknowledged items are hidden by
    default — but an acknowledgement covers only the failure it was made against,
    so a capture that fails again comes back. Read-only, tenant-scoped."""
    wl = await capture_failures.worklist_page(
        db,
        current.org_id,
        page=page,
        page_size=page_size,
        include_acknowledged=include_acknowledged,
    )
    return CaptureFailureWorklistOut(
        items=[CaptureFailureItem.model_validate(i, from_attributes=True) for i in wl.items],
        groups=[CaptureFailureGroup.model_validate(g, from_attributes=True) for g in wl.groups],
        total=wl.total,
        unacknowledged=wl.unacknowledged,
    )


@router.post(
    "/captures/failures/{channel}/{ref_id}/acknowledge",
    response_model=CaptureFailureWorklistOut,
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_WRITE))],
)
async def acknowledge_capture_failure(
    channel: str,
    ref_id: str,
    body: CaptureAcknowledgeIn,
    current: CurrentUser,
    db: DbSession,
):
    """Record that a human has seen this failure and decided what to do about it.

    An acknowledgement is a RECORD (who, when, an optional note) appended to a
    history, not a boolean flipped on the capture — and it is pinned to the
    failure it was made against, so acknowledging cannot silence a LATER failure
    of the same document. Returns the refreshed worklist so the caller does not
    have to re-request it. Opaque 404 (§4.4) for a reference that is not a failed
    capture in this tenant."""
    ack = await capture_failures.acknowledge(
        db,
        current.org_id,
        channel=channel,
        ref_id=ref_id,
        actor=current.email,
        note=body.note,
    )
    if ack is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Failed capture not found")
    # §4.16: the acknowledgement and its audit event commit together.
    await audit.record(
        db,
        action="capture.failure_acknowledged",
        target_type="capture_failure",
        target_id=ref_id,
        meta={"channel": channel, "note": body.note},
    )
    await db.commit()
    wl = await capture_failures.worklist(db, current.org_id)
    return CaptureFailureWorklistOut(
        items=[CaptureFailureItem.model_validate(i, from_attributes=True) for i in wl.items],
        groups=[CaptureFailureGroup.model_validate(g, from_attributes=True) for g in wl.groups],
        total=wl.total,
        unacknowledged=wl.unacknowledged,
    )


@router.post(
    "/captures/failures/acknowledge",
    response_model=BulkAcknowledgeOut,
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_WRITE))],
)
async def bulk_acknowledge_capture_failures(
    body: BulkAcknowledgeIn, current: CurrentUser, db: DbSession
):
    """Acknowledge many failed captures in one action — L-4, under its guards.

    `agreed_count` is the count the CLIENT displayed. If it disagrees with what
    arrived, the list moved under the operator and the whole batch is refused
    (409 `bulk_count_mismatch`) rather than applied to a set they never saw.

    Every record comes back with its own outcome. A SKIP is an ordinary result
    carrying a reason ("already acknowledged since this failure") — not a failure,
    because burying the system working correctly in an error count is how error
    counts stop being read.

    `applied_ids` is derived from what the write actually did, not from what was
    requested, so an undo is mechanically correct rather than hand-authored.

    A filter-based selection is permitted here because acknowledging is
    reversible — the record stands and a LATER failure resurfaces the item
    regardless. Irreversible bulk actions must call
    `bulk.require_explicit_selection`; reserving that guard for the cases that
    need it is what keeps it meaningful."""
    try:
        selection = bulk.Selection(body.selection)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown selection mode"
        ) from None

    result = await capture_failures.bulk_acknowledge(
        db,
        current.org_id,
        items=[(i.channel, i.ref_id) for i in body.items],
        selection=selection,
        agreed_count=body.agreed_count,
        actor=current.email,
        note=body.note,
    )
    if result.applied:
        # ONE audit event for the batch, carrying the ids that actually changed —
        # the mechanical reversal record (§4.16: same transaction as the writes).
        await audit.record(
            db,
            action="capture.failures_bulk_acknowledged",
            target_type="capture_failure",
            target_id=None,
            meta={
                "applied_ids": result.applied_ids,
                "applied": result.applied,
                "skipped": result.skipped,
                "selection": selection.value,
                "note": body.note,
            },
        )
    await db.commit()
    wl = await capture_failures.worklist(db, current.org_id)
    return BulkAcknowledgeOut(
        applied=result.applied,
        skipped=result.skipped,
        failed=result.failed,
        outcomes=[BulkOutcomeOut.model_validate(o, from_attributes=True) for o in result.outcomes],
        applied_ids=result.applied_ids,
        worklist=CaptureFailureWorklistOut(
            items=[CaptureFailureItem.model_validate(i, from_attributes=True) for i in wl.items],
            groups=[CaptureFailureGroup.model_validate(g, from_attributes=True) for g in wl.groups],
            total=wl.total,
            unacknowledged=wl.unacknowledged,
        ),
    )


@router.post("/captures/{run_id}/review", response_model=list[FieldProvenanceOut])
async def review_capture_fields(
    run_id: str, body: CaptureReviewIn, current: CurrentUser, db: DbSession
):
    """Record human corrections for a capture's fields — stores the reviewed value
    and clears the low-confidence flag. The reviewed value is preserved alongside
    the original/normalized capture, so the correction is auditable. A body item
    without `line_index` targets a header row; with `line_index = n` it targets
    that field of line_items[n] (E1.2). Unknown (field, line_index) pairs are
    ignored — no mutation, no audit event."""
    run = await extraction.get_capture(db, current.org_id, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Capture not found")
    fields = await extraction.fields_for_run(db, current.org_id, run_id)
    by_key = {(f.field, f.line_index): f for f in fields}
    # E1.5: the raw captured header vendor_name keys the learning-loop memory
    # (see capture_memory.py) — resolved once, from the run's own cached
    # draft, never from a correction made in THIS request.
    vendor_name = _vendor_name_from_run(run)
    changes: dict[str, dict[str, str | None]] = {}
    for item in body.fields:
        f = by_key.get((item.field, item.line_index))
        if f is not None:
            key = f.field if f.line_index is None else f"line_items[{f.line_index}].{f.field}"
            old = f.reviewed_value if f.reviewed_value is not None else f.value
            new = item.reviewed_value[:500]
            changes[key] = {"old": old, "new": new}
            if new != old:
                # A GENUINE correction feeds the learning loop; a same-value
                # resubmit (old == new) is still recorded/audited above but
                # teaches the memory nothing (§4.19: advisory signal only).
                await capture_memory.record_correction(
                    db,
                    current.org_id,
                    vendor_name=vendor_name,
                    field=f.field,
                    original_value=old,
                    corrected_value=new,
                )
            f.reviewed_value = new
            f.low_confidence = False
    # §4.16: the correction is a mutation, so it is audited (old→new per field)
    # in the SAME transaction as the field update (and, since E1.5, the same
    # transaction as any capture_memory row above — both commit together).
    if changes:
        await audit.record(
            db,
            audit.A.CAPTURE_REVIEW,
            target_type="extraction_run",
            target_id=run_id,
            meta={"fields": changes},
        )
    await db.commit()
    fields = await extraction.fields_for_run(db, current.org_id, run_id)
    out = [FieldProvenanceOut.model_validate(f) for f in fields]
    return await _attach_hints(db, current.org_id, run, out)


@router.get("/captures/{run_id}/fields", response_model=list[FieldProvenanceOut])
async def capture_fields(run_id: str, current: CurrentUser, db: DbSession):
    """The LIVE per-field provenance rows for a capture run (E1.1) — including any
    human `reviewed_value` recorded so far, plus (E1.5) an ADVISORY
    `suggested_value` per field drawn from this vendor's past corrections, when
    any exist. The poll endpoint replays the parse-time draft; this is the
    endpoint the review screen re-reads after a correction or a page reload.
    Read-only, tenant-scoped, opaque 404."""
    run = await extraction.get_capture(db, current.org_id, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Capture not found")
    fields = await extraction.fields_for_run(db, current.org_id, run_id)
    out = [FieldProvenanceOut.model_validate(f) for f in fields]
    return await _attach_hints(db, current.org_id, run, out)


@router.get("/captures/{run_id}/source")
async def capture_source(run_id: str, current: CurrentUser, db: DbSession):
    """The original uploaded document for a capture run, served INERT (nosniff +
    content-disposition) so the review screen can show it side by side with the
    extracted fields (E1.1). The mime comes from the document registry — the
    server never sniffs or guesses a renderable type. Tenant-scoped, opaque 404."""
    run = await extraction.get_capture(db, current.org_id, run_id)
    if run is None or not run.source_sha256:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Capture not found")
    try:
        data = await documents.load(documents.UPLOADS, current.org_id, run.source_sha256)
    except storage.StorageError:
        # A referenced-but-missing object is an integrity fault; to this caller it
        # is simply "no document" — 404, without leaking storage internals.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stored document missing")
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stored document missing")
    doc = await db.scalar(
        select(Document).where(
            Document.org_id == current.org_id,
            Document.sha256 == run.source_sha256,
            Document.kind == documents.UPLOADS,
        )
    )
    return Response(
        content=data,
        media_type=(doc.mime if doc and doc.mime else "application/octet-stream"),
        headers={
            "Content-Disposition": content_disposition(
                run.source_filename or "document", fallback="attachment"
            ),
            "X-Content-Type-Options": "nosniff",
        },
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


@router.get(
    "/trash",
    response_model=BinListOut,
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_DELETE))],
)
async def list_binned_invoices(
    current: CurrentUser,
    db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """The recycle bin — what this workspace has deleted and how long is left.

    Declared BEFORE `/{invoice_id}`: FastAPI matches in declaration order, so
    below it this path would be swallowed by the id parameter and answer 404.

    Gated on INVOICE_DELETE rather than INVOICE_RESTORE deliberately: whoever can
    delete needs to SEE what they deleted — that is how they discover the mistake
    — even though only an admin or the owner can act on it.
    """
    items, total = await invoice_service.binned_page(db, current.org_id, limit=limit, offset=offset)
    return BinListOut(
        items=[BinnedInvoiceOut.model_validate(i, from_attributes=True) for i in items],
        total=total,
        retention_days=invoice_service.BIN_RETENTION_DAYS,
    )


@router.get(
    "/trash/other",
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_DELETE))],
)
async def list_binned_other(current: CurrentUser, db: DbSession):
    """WO-M: the generic bin — every non-invoice entity the owner extended the
    bin to (expense reports, inbox transactions, recurring schedules, invoice
    attachments), with days left on the same 30-day promise."""
    return {
        "items": await bin_svc.list_binned(db, current.org_id),
        "retention_days": invoice_service.BIN_RETENTION_DAYS,
    }


@router.post(
    "/trash/other/{kind}/{row_id}/restore",
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_RESTORE))],
)
async def restore_binned_other(kind: str, row_id: str, current: CurrentUser, db: DbSession):
    """WO-M: bring a generic-bin record back. Same INVOICE_RESTORE gate as the
    invoice restore — putting records back into the books is the consequential
    half of the pair regardless of the record's kind."""
    try:
        out = await bin_svc.restore(db, current.org_id, kind, row_id)
    except bin_svc.BinError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    await audit.record(
        db,
        "bin.restore",
        target_type=kind,
        target_id=row_id,
        meta=out["summary"],
    )
    await db.commit()
    return out


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
async def delete_invoice(
    invoice_id: str,
    current: CurrentUser,
    db: DbSession,
    body: DeleteInvoiceIn | None = None,
):
    """Move ONE invoice to the recycle bin.

    The row is no longer destroyed. It is stamped `deleted_at`, disappears from
    every read, and an admin or the org owner can restore it for 30 days
    (docs/design/deletion-and-archive.md). Deleting one already in the bin is an
    opaque 404, the same as one that never existed.

    A CLEAN DRAFT deletes with no ceremony. Anything past draft, paid, or in a
    payment run is the client's decision to make — the owner's explicit call —
    but only after being warned every time: without a body carrying the current
    `acknowledged_warning_version`, this answers 409 `deletion_consent_required`
    WITH the warning to display, and the client re-submits with the version it
    was given. The server, not the browser dialog, is the gate; a stale version
    is refused, because consent to different words is not consent to these.

    Bulk delete stays drafts-only. "Warn every time" is exactly what one checkbox
    over two hundred invoices cannot deliver."""
    snap, consent = await invoice_service.delete_one(
        db,
        current.org_id,
        invoice_id,
        actor=current.email,
        acknowledged_warning_version=body.acknowledged_warning_version if body else None,
    )
    meta = {"bulk": False, "deleted": 1, "records": [asdict(snap)], "reversible": True}
    if consent:
        # The owner's requirement, in the permanent record: WHAT the client was
        # told, not merely that something was shown.
        meta["consent"] = consent
    await audit.record(
        db,
        audit.A.INVOICE_DELETE,
        target_type="invoice",
        target_id=invoice_id,
        meta=meta,
    )
    await db.commit()


@router.post(
    "/{invoice_id}/restore",
    response_model=InvoiceDetailOut,
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_RESTORE))],
)
async def restore_invoice(invoice_id: str, current: CurrentUser, db: DbSession):
    """Bring a binned invoice back into the books — admin or org owner only.

    A narrower permission than deleting it (owner decision): putting a record
    back into the books is the more consequential half of the pair, and a
    finance manager who can delete deliberately cannot restore.

    Refuses with 409 `invoice_number_in_use` when a live invoice already carries
    the same number — otherwise a delete-then-re-key-then-restore sequence leaves
    two live copies of one supplier bill, which is how a bill gets paid twice."""
    invoice = await invoice_service.restore_one(db, current.org_id, invoice_id)
    await audit.record(
        db,
        audit.A.INVOICE_RESTORE,
        target_type="invoice",
        target_id=invoice_id,
        meta={"invoice_number": invoice.invoice_number},
    )
    await db.commit()
    invoice = await _load_scoped(db, current.org_id, invoice_id)
    return _detail(invoice, invoice.vendor.name)


@router.post(
    "/bulk-delete",
    response_model=BulkDeleteOut,
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_DELETE))],
)
async def bulk_delete_invoices(body: BulkDeleteIn, current: CurrentUser, db: DbSession):
    """Move many DRAFT invoices to the recycle bin in one action.

    Guard 4 still fires: `selection` must be `explicit`. The bin has made this
    reversible, but "everything matching this filter" is still a set the operator
    never enumerated, and hundreds of invoices vanishing from the books is a real
    incident for however long it takes to work out which ones they were.

    Only a draft with no payment and no payment run is binned. Anything else is
    SKIPPED with a reason naming its state — an approved or paid invoice being
    left alone is the system working, not an error.

    The audit event freezes what each invoice looked like at the moment of the
    decision, so reading the trail does not require looking up rows whose values
    may since have changed."""
    try:
        selection = bulk.Selection(body.selection)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown selection mode"
        ) from None

    result, snapshots = await invoice_service.bulk_delete_drafts(
        db,
        current.org_id,
        ids=body.invoice_ids,
        selection=selection,
        agreed_count=body.agreed_count,
        actor=current.email,
    )
    records = [asdict(s) for s in snapshots]
    if records:
        await audit.record(
            db,
            audit.A.INVOICE_DELETE,
            target_type="invoice",
            target_id=None,
            meta={"bulk": True, "deleted": len(records), "records": records},
        )
    await db.commit()
    return BulkDeleteOut(
        deleted=result.applied,
        skipped=result.skipped,
        failed=result.failed,
        outcomes=[BulkOutcomeOut.model_validate(o, from_attributes=True) for o in result.outcomes],
        deleted_records=records,
    )


class _UploadRefused(Exception):  # noqa: N818 — a refusal, not an error condition
    """One file was not admitted, with the reason as a stable code.

    Carries the HTTP status the SINGLE-file endpoint has always returned for
    this refusal, so the batch endpoint can report per-file reasons without a
    second, drifting copy of the admission rules (WO-X)."""

    def __init__(self, code: str, status_code: int, message: str):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message


#: How many files one batch upload may carry. A cap the caller can see beats an
#: unbounded loop holding a request open while it scans and stores; anything
#: larger is a folder sync, which is a different product decision.
MAX_BATCH_FILES = 25


async def _admit_one_upload(
    db: DbSession,
    *,
    org_id: str,
    plan: str,
    actor: str,
    file: UploadFile,
    override: bool,
) -> ExtractionRun:
    """Run the full admission sequence for ONE file and queue its capture.

    Quota → size → security scan → duplicate advisory → store → queue → meter,
    in that order, because each step is cheaper than the next and none of them
    should run for a file the previous one would have refused.

    Raises `_UploadRefused` (quota / size / type) or `ConflictError` (the
    duplicate advisory, which already carries its own code). This is the ONLY
    implementation of the sequence: `/upload` re-raises as HTTP, `/upload/batch`
    records the reason per file. A second copy of these rules would eventually
    admit a file one door refuses.
    """
    # Metered usage: the ORG's plan monthly upload limit (0 = unlimited; WO-47:
    # keyed by the org's plan, not the caller's role). Upload is metered data
    # capture, open to every tier (see create_invoice) — not gated.
    #
    # WO-X: called per FILE. A batch is N uploads that arrived together, not one
    # upload — checking once for the request would let a 40-file drop through a
    # plan with 3 uploads left, which is the whole point of having a limit.
    try:
        await access.enforce_upload_quota(db, org_id, plan)
    except HTTPException as exc:
        raise _UploadRefused("upload_quota_reached", exc.status_code, str(exc.detail)) from exc
    content = await file.read()
    # WO-94/N3 — the ONE configured cap (`settings.max_upload_mb`), read from
    # `filesec` rather than re-typed here. The check stays at the boundary so
    # an over-sized body is refused as 413 before anything is read further;
    # the limit itself is the service's.
    if len(content) > filesec.max_bytes():
        raise _UploadRefused(
            "file_too_large",
            status.HTTP_413_CONTENT_TOO_LARGE,
            filesec.too_large_message(),
        )
    # Security gate: type-validate + malware-scan BEFORE storing or queuing.
    try:
        filesec.check(file.filename or "upload", content, allowed=filesec.SUPPLIER_UPLOAD_KINDS)
    except filesec.FileRejected as exc:
        raise _UploadRefused(
            "unsupported_file", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)
        ) from exc
    sha = extraction.sha256_hex(content)
    # Advisory re-upload guard (E1.3): blocks (409, code=duplicate_upload) on a
    # byte-identical prior capture unless the caller explicitly overrides — before
    # anything is stored, queued, or metered.
    await extraction.check_duplicate_upload(db, org_id, sha, override=override)
    # Persist the original so the worker can parse it off-tier, then queue a run.
    await documents.store(
        documents.UPLOADS,
        org_id,
        content,
        file.content_type,
        db=db,
        filename=file.filename,
        uploaded_by=actor,
    )
    run = await extraction.start_capture(db, org_id, filename=file.filename, sha256=sha)
    await jobs.enqueue(
        db,
        extraction.UPLOAD_EXTRACT_KIND,
        {"run_id": run.id},
        org_id=org_id,
        idempotency_key=f"upload-extract:{run.id}",
    )
    await access.record_usage(db, org_id, "upload")
    return run


@router.post("/upload", response_model=UploadAccepted, status_code=status.HTTP_202_ACCEPTED)
async def upload_invoice(
    current: CurrentUser,
    current_org: CurrentOrg,
    db: DbSession,
    file: UploadFile,
    override: bool = False,
):
    """Accept an uploaded supplier invoice (PDF / JPEG / PNG / XML / CSV / JSON) and
    QUEUE it for parsing (Stage B). Images and scanned PDFs go through OCR.

    The security scan runs inline (a bad file is rejected immediately), but the
    CPU-heavy parse/OCR runs on the WORKER tier — so a burst of large uploads
    never ties up the API. Returns 202 + an `extraction_run_id`; poll
    GET /invoices/upload/{extraction_run_id} for the draft, then POST it to
    /invoices to save.

    `override=true` bypasses the hash-based re-upload advisory (E1.3) — see
    `extraction.check_duplicate_upload`.

    For several files at once see POST /invoices/upload/batch, which runs this
    exact admission sequence per file and reports each outcome separately."""
    try:
        run = await _admit_one_upload(
            db,
            org_id=current.org_id,
            plan=current_org.plan,
            actor=current.email,
            file=file,
            override=override,
        )
    except _UploadRefused as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return UploadAccepted(extraction_run_id=run.id, status=run.status)


@router.post(
    "/upload/batch", response_model=BatchUploadAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def upload_invoices_batch(
    current: CurrentUser,
    current_org: CurrentOrg,
    db: DbSession,
    files: list[UploadFile],
    override: bool = False,
):
    """Accept SEVERAL supplier invoices in one request: N files in, N outcomes out.

    AP does not arrive one document at a time. The single-file door made the
    daily reality — an envelope, a supplier's monthly batch, a folder someone
    scanned — into N separate round trips with N page states to shepherd.

    Each file goes through `_admit_one_upload` independently, so a batch is
    PARTIAL by design: one duplicate among nine good invoices leaves nine
    captures queued and reports the tenth with its reason. Failing the whole
    request on the first bad file would discard real work to report a refusal
    the caller can act on file by file.

    Because admission runs per file, so does the plan's upload quota: a batch of
    forty against a plan with three uploads left queues three and refuses
    thirty-seven with `upload_quota_reached`. The limit is a count of documents,
    and a request is not a document.

    `override=true` applies to every file in the request. Returns 202 with the
    outcomes in the order the files were sent."""
    if not files:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Attach at least one file to upload"
        )
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Upload at most {MAX_BATCH_FILES} files at a time ({len(files)} were attached).",
        )
    outcomes: list[BatchUploadOutcome] = []
    for file in files:
        name = file.filename or "upload"
        try:
            run = await _admit_one_upload(
                db,
                org_id=current.org_id,
                plan=current_org.plan,
                actor=current.email,
                file=file,
                override=override,
            )
        except _UploadRefused as exc:
            outcomes.append(
                BatchUploadOutcome(
                    filename=name, accepted=False, code=exc.code, message=exc.message
                )
            )
        except ConflictError as exc:
            # The duplicate advisory. It already carries its own code and the
            # sentence naming when the file was seen before; re-wording it here
            # would give the same refusal two voices.
            outcomes.append(
                BatchUploadOutcome(filename=name, accepted=False, code=exc.code, message=str(exc))
            )
        else:
            outcomes.append(
                BatchUploadOutcome(filename=name, accepted=True, extraction_run_id=run.id)
            )
    accepted = sum(1 for o in outcomes if o.accepted)
    return BatchUploadAccepted(
        accepted=accepted, rejected=len(outcomes) - accepted, outcomes=outcomes
    )


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
        # WO-X — what the parser is doing right now. `percent` is derived here
        # and never stored, so it cannot come to disagree with the counts it is
        # computed from.
        stage=run.stage,
        pages_done=run.pages_done or 0,
        pages_total=run.pages_total,
        percent=capture_progress.percent(
            run.stage, run.pages_done or 0, run.pages_total, status=run.status
        ),
    )


@router.post(
    "/upload/{run_id}/retry", response_model=UploadAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def retry_upload(
    run_id: str, current: CurrentUser, db: DbSession, discard_review: bool = False
):
    """Manually re-run extraction for a FAILED or PARSED-but-unsaved capture — e.g.
    after a transient OCR failure, or to re-parse with an updated provider. Re-queues
    the SAME stored bytes (the original file is never re-uploaded or mutated) and
    clears the previous parse's provenance. Tenant-scoped.

    A capture someone has already CORRECTED is refused (`capture_has_review`)
    unless `discard_review=true`: re-extracting replaces a human's typed values
    with a fresh machine read, and that must be asked for rather than assumed.
    The discard is audited."""
    run = await extraction.get_capture(db, current.org_id, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    if run.invoice_id is not None or run.status == "saved":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This capture was already saved to an invoice"
        )
    if not run.source_sha256:
        raise HTTPException(status.HTTP_409_CONFLICT, "No stored file to re-extract")

    # Refuses BEFORE anything is mutated, so a rejected retry is a true no-op —
    # a guard that rejects after deleting the rows would be worse than none.
    discarded = await extraction.clear_fields_for_retry(
        db, current.org_id, run, discard_review=discard_review
    )
    run.status = "queued"
    run.method = "pending"
    run.note = None
    run.failure_code = None
    run.draft_json = None
    # WO-X: progress belongs to ONE attempt. Left at `done` from the previous
    # one, the re-parse could never report a phase again (progress only moves
    # forward), so the retry would look instantly finished and then hang.
    run.stage = capture_progress.QUEUED
    run.pages_done = 0
    run.pages_total = None
    if discarded:
        await audit.record(
            db,
            action="capture.review_discarded",
            target_type="extraction_run",
            target_id=run.id,
            meta={"discarded_reviews": discarded},
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


@router.put(
    "/{invoice_id}/allocation",
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_WRITE))],
)
async def set_project_allocation(
    invoice_id: str, body: AllocationIn, current: CurrentUser, db: DbSession
):
    """Replace this invoice's project allocation — all three levels in one write
    (project-profitability phase 2, precedence line > split > whole-invoice).
    Splits must sum to exactly 100; every referenced project must be this
    org's (opaque 404 otherwise)."""
    from app.services import project_profit

    try:
        result = await project_profit.set_allocation(
            db,
            current.org_id,
            invoice_id,
            project_id=body.project_id,
            splits=[(s.project_id, s.percent) for s in body.splits]
            if body.splits is not None
            else None,
            lines=body.lines,
        )
    except project_profit.NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except project_profit.ProjectProfitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    await audit.record(
        db,
        "invoice.project_allocation",
        target_type="invoice",
        target_id=invoice_id,
        meta=result,
    )
    await db.commit()
    return result


@router.get("/{invoice_id}/allocation")
async def get_project_allocation(invoice_id: str, current: CurrentUser, db: DbSession):
    """The invoice's current project allocation, in the same shape the PUT
    accepts — read, edit, PUT back."""
    from app.services import project_profit

    try:
        return await project_profit.get_allocation(db, current.org_id, invoice_id)
    except project_profit.NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None

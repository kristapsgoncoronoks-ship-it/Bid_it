from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz, money
from app.core.security_headers import content_disposition
from app.models.customer import Customer
from app.models.email_message import EmailMessage
from app.models.issued_invoice import (
    IssuedInvoice,
    IssuedInvoiceAttachment,
    IssuedInvoiceLine,
)
from app.schemas.issued import (
    BulkReminderResult,
    CreditNoteCreate,
    DisputeRequest,
    EmailMessageOut,
    IssuedAttachmentOut,
    IssuedInvoiceCreate,
    IssuedInvoiceDetail,
    IssuedInvoiceListOut,
    IssuedInvoiceOut,
    IssuedLineOut,
    IssueRequest,
    PaymentOut,
    PaymentUpdate,
    ReminderRequest,
    SendRequest,
    SendResult,
    VatBucketOut,
    VoidRequest,
    WriteOffRequest,
)
from app.schemas.issued_reports import (
    PartnerReportOut,
    ReceivablesReportOut,
    SummaryReportOut,
    VatReportOut,
)
from app.services import (
    audit,
    documents,
    dunning,
    facturx,
    filesec,
    invoice_pdf,
    issued_lifecycle,
    issued_reports,
    issued_service,
    issued_status,
    issuer,
    mailer,
    modules,
    partners,
    payments,
    tax_codes,
    vat,
    webhooks,
)

# Structural authorization (ADR-0024): every issuing route needs at least
# ISSUED_READ (router-level); write/send routes declare the stricter
# ISSUED_WRITE / ISSUED_SEND per-route below.
router = APIRouter(
    prefix="/issued",
    tags=["issuing"],
    dependencies=[Depends(require_perm(authz.Permission.ISSUED_READ))],
)
_WRITE = [Depends(require_perm(authz.Permission.ISSUED_WRITE))]
_SEND = [Depends(require_perm(authz.Permission.ISSUED_SEND))]


async def _guard(db: DbSession, org_id: str):
    await modules.require_enabled(db, org_id, "issuing")
    profile = await issuer.get_or_create(db, org_id)
    missing = issuer.missing_fields(profile)
    if missing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Complete your company registration details first (missing: {', '.join(missing)}).",
        )
    return profile


def _vat_of(inv: IssuedInvoice) -> vat.VatResult:
    raw = [
        {
            "description": li.description,
            "quantity": li.quantity,
            "unit": li.unit,
            "unit_price": li.unit_price,
            "discount_percent": li.discount_percent,
            "vat_rate": li.vat_rate,
        }
        for li in inv.lines
    ]
    return vat.compute(raw, inv.vat_scheme)


def _with_status(out: IssuedInvoiceOut, inv: IssuedInvoice) -> IssuedInvoiceOut:
    out.status = issued_status.status_of(inv)
    out.outstanding = issued_status.outstanding_of(inv)
    out.penalty_accrued = issued_status.penalty_of(inv)
    out.days_overdue = issued_status.days_overdue_of(inv)
    return out


def _out(inv: IssuedInvoice) -> IssuedInvoiceOut:
    return _with_status(IssuedInvoiceOut.model_validate(inv), inv)


def _detail(inv: IssuedInvoice) -> IssuedInvoiceDetail:
    result = _vat_of(inv)
    d = IssuedInvoiceDetail.model_validate(inv)
    _with_status(d, inv)
    d.lines = [IssuedLineOut.model_validate(li) for li in inv.lines]
    d.vat_breakdown = [VatBucketOut(rate=b.rate, base=b.base, vat=b.vat) for b in result.breakdown]
    return d


async def _resolve_issuer(db: DbSession, org_id: str, issuer_id: str | None):
    """The issuer entity to invoice as (named or default), validated to belong to
    the org and to be Art. 226-complete."""
    try:
        chosen = await issuer.resolve(db, org_id, issuer_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Issuer not found")
    missing = issuer.missing_fields(chosen)
    if missing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Complete the issuer's registration details first (missing: {', '.join(missing)}).",
        )
    return chosen


def _enforce_partner_gate(partner) -> None:
    """A partner-linked invoice can only be ISSUED once its pre-invoicing workflow
    (contract / acceptance act) is signed. Enforced at issue time, not on a draft."""
    if partner is None:
        return
    missing = partners.missing_signed(partner)
    if missing:
        labels = ", ".join(partners.DOC_LABELS.get(k, k) for k in missing)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot issue to {partner.name}: awaiting signed {labels}.",
        )


async def _resolve_links(db: DbSession, org_id: str, body: IssuedInvoiceCreate):
    """Resolve+validate the partner/customer links, prefill the buyer block from
    the customer master, resolve line tax-codes, and validate a buyer is present.
    Returns (partner, customer, customer_terms). Does NOT enforce the partner
    signed-gate (that is deferred to issue time)."""
    partner = None
    if body.partner_id:
        partner = await partners.get_partner(db, org_id, body.partner_id)
        if partner is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Partner not found")

    customer = None
    customer_terms = None
    if body.customer_id:
        customer = await db.scalar(
            select(Customer).where(Customer.id == body.customer_id, Customer.org_id == org_id)
        )
        if customer is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
        body.buyer_name = body.buyer_name or customer.name
        body.buyer_email = body.buyer_email or customer.email
        body.buyer_vat_number = body.buyer_vat_number or customer.vat_number
        body.buyer_address_line1 = body.buyer_address_line1 or customer.address_line1
        body.buyer_city = body.buyer_city or customer.city
        body.buyer_postal_code = body.buyer_postal_code or customer.postal_code
        body.buyer_country = body.buyer_country or customer.country
        body.currency = body.currency or customer.default_currency
        customer_terms = customer.payment_terms_days
    if not body.buyer_name:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "A buyer name (or a customer_id) is required."
        )

    # Resolve any line tax-code to its catalogue rate (Slice 4b): the code drives
    # vat_rate; the canonical code is snapshotted onto the line.
    for li in body.lines:
        if li.tax_code:
            tc = await tax_codes.resolve(db, org_id, li.tax_code)
            if tc is None or not tc.active:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, f"Unknown or inactive tax code '{li.tax_code}'"
                )
            li.vat_rate = tc.rate
            li.tax_code = tc.code
    return partner, customer, customer_terms


@router.post(
    "",
    response_model=IssuedInvoiceDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=_WRITE,
)
async def create_issued(body: IssuedInvoiceCreate, current: CurrentUser, db: DbSession):
    """Create an invoice. By default it is born FINAL (numbered, issued); pass
    `draft: true` to create an editable draft with no number and no partner
    signed-gate (finalize it later via POST /{id}/issue)."""
    await _guard(db, current.org_id)  # module + default-issuer completeness gate
    partner, customer, customer_terms = await _resolve_links(db, current.org_id, body)
    chosen = await _resolve_issuer(db, current.org_id, body.issuer_id)

    if body.draft:
        # A draft carries no gap-free number (and doesn't touch the profile
        # counter) — it is not a legal document until issued.
        profile = chosen
        inv = issued_service.build_invoice(
            profile,
            body,
            org_id=current.org_id,
            partner=partner,
            payment_terms_days=customer_terms,
            allocate_number=False,
            lifecycle=issued_lifecycle.DRAFT,
        )
    else:
        _enforce_partner_gate(partner)
        # Lock the CHOSEN issuer entity FOR UPDATE so concurrent issues on it
        # serialize on its own numbering series.
        profile = await issuer.lock(db, current.org_id, chosen.id)
        inv = issued_service.build_invoice(
            profile,
            body,
            org_id=current.org_id,
            partner=partner,
            payment_terms_days=customer_terms,
        )
        inv.issued_at = datetime.now(UTC)
    if customer is not None:
        inv.customer_id = customer.id
    db.add(inv)
    await audit.record(
        db,
        audit.A.ISSUED_CREATE,
        target_type="issued_invoice",
        target_id=inv.id,
        meta={"number": inv.number, "lifecycle": inv.lifecycle, "total": str(inv.total)},
    )
    await db.commit()
    await db.refresh(inv, attribute_names=["lines"])
    return _detail(inv)


@router.patch("/{invoice_id}", response_model=IssuedInvoiceDetail, dependencies=_WRITE)
async def edit_draft(
    invoice_id: str, body: IssuedInvoiceCreate, current: CurrentUser, db: DbSession
):
    """Replace a DRAFT invoice's contents (buyer, dates, lines, totals). Only a
    draft is editable — an issued invoice is immutable (correct it with a credit
    note). Server recomputes the tax/totals."""
    await _guard(db, current.org_id)
    inv = await _load(db, current.org_id, invoice_id)
    if not issued_lifecycle.is_editable(inv.lifecycle):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only a draft invoice can be edited (this one is {inv.lifecycle}). "
            "Issue a credit note to correct an issued invoice.",
        )
    partner, customer, customer_terms = await _resolve_links(db, current.org_id, body)
    # Keep the draft's issuer unless the edit names a different one.
    profile = await _resolve_issuer(db, current.org_id, body.issuer_id or inv.issuer_id)
    rebuilt = issued_service.build_invoice(
        profile,
        body,
        org_id=current.org_id,
        partner=partner,
        payment_terms_days=customer_terms,
        allocate_number=False,
        lifecycle=issued_lifecycle.DRAFT,
    )
    # Copy the recomputed snapshot onto the existing draft row (keep its id).
    for attr in (
        "issuer_id",
        "partner_id",
        "issue_date",
        "supply_date",
        "due_date",
        "currency",
        "buyer_name",
        "buyer_email",
        "buyer_vat_number",
        "buyer_address_line1",
        "buyer_city",
        "buyer_postal_code",
        "buyer_country",
        "seller_json",
        "vat_scheme",
        "note",
        "po_reference",
        "tax_exemption_reason",
        "penalty_rate",
        "subtotal",
        "tax_total",
        "total",
    ):
        setattr(inv, attr, getattr(rebuilt, attr))
    inv.customer_id = customer.id if customer is not None else None
    # Replace the line set with FRESH (unparented) rows so the delete-orphan cascade
    # deletes the old lines and inserts the new ones cleanly.
    new_lines = [
        IssuedInvoiceLine(
            position=li.position,
            description=li.description,
            quantity=li.quantity,
            unit=li.unit,
            unit_price=li.unit_price,
            discount_percent=li.discount_percent,
            vat_rate=li.vat_rate,
            net_amount=li.net_amount,
            tax_code=li.tax_code,
        )
        for li in rebuilt.lines
    ]
    inv.lines.clear()
    inv.lines.extend(new_lines)
    await audit.record(
        db,
        audit.A.ISSUED_EDIT,
        target_type="issued_invoice",
        target_id=inv.id,
        meta={"total": str(inv.total)},
    )
    await db.commit()
    await db.refresh(inv, attribute_names=["lines"])
    return _detail(inv)


@router.post("/{invoice_id}/approve", response_model=IssuedInvoiceDetail, dependencies=_WRITE)
async def approve_draft(invoice_id: str, current: CurrentUser, db: DbSession):
    """Move a draft to APPROVED (a review gate before it is issued). Still no
    number and still editable-free — approve is reversible only by issuing or
    cancelling."""
    await modules.require_enabled(db, current.org_id, "issuing")
    inv = await _load(db, current.org_id, invoice_id)
    issued_lifecycle.target_for("approve", inv.lifecycle)  # validates source state
    inv.lifecycle = issued_lifecycle.APPROVED
    inv.approved_at = datetime.now(UTC)
    await audit.record(
        db,
        audit.A.ISSUED_APPROVE,
        target_type="issued_invoice",
        target_id=inv.id,
        meta={},
    )
    await db.commit()
    await db.refresh(inv, attribute_names=["lines"])
    return _detail(inv)


@router.post("/{invoice_id}/issue", response_model=IssuedInvoiceDetail, dependencies=_WRITE)
async def issue_draft(invoice_id: str, body: IssueRequest, current: CurrentUser, db: DbSession):
    """Finalize a draft/approved invoice: enforce the partner signed-gate, allocate
    the gap-free number under a row lock, and set it live (immutable thereafter)."""
    await _guard(db, current.org_id)
    inv = await _load(db, current.org_id, invoice_id)
    issued_lifecycle.target_for("issue", inv.lifecycle)  # validates source state
    if inv.partner_id:
        partner = await partners.get_partner(db, current.org_id, inv.partner_id)
        _enforce_partner_gate(partner)

    # Re-stamp the issue date (default today) and recompute the due date, keeping
    # the draft's payment-term gap. The number uses the (new) issue year and comes
    # from the DRAFT'S OWN issuer entity's series.
    profile = await issuer.lock(db, current.org_id, inv.issuer_id)
    term_days = (
        (inv.due_date - inv.issue_date).days
        if inv.due_date is not None
        else profile.payment_terms_days
    )
    new_issue = body.issue_date or date.today()
    inv.issue_date = new_issue
    inv.due_date = new_issue + timedelta(days=max(0, term_days))
    inv.number = issued_service._next_invoice_number(profile, inv.issue_date)
    inv.lifecycle = issued_lifecycle.ISSUED
    inv.issued_at = datetime.now(UTC)
    await audit.record(
        db,
        audit.A.ISSUED_ISSUE,
        target_type="issued_invoice",
        target_id=inv.id,
        meta={"number": inv.number},
    )
    await webhooks.emit(
        db,
        current.org_id,
        "issued.created",
        {"id": inv.id, "number": inv.number, "total": str(inv.total), "currency": inv.currency},
    )
    await db.commit()
    await db.refresh(inv, attribute_names=["lines"])
    return _detail(inv)


@router.post(
    "/{invoice_id}/duplicate",
    response_model=IssuedInvoiceDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=_WRITE,
)
async def duplicate_invoice(invoice_id: str, current: CurrentUser, db: DbSession):
    """Copy any invoice into a fresh editable DRAFT (new dates, no number, a fresh
    seller snapshot). Credit notes can't be duplicated."""
    await _guard(db, current.org_id)
    src = await _load(db, current.org_id, invoice_id)
    if issued_status.is_credit_note(src):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot duplicate a credit note.")
    # Keep the source's issuer entity (fresh seller snapshot from it).
    profile = (
        await issuer.get_by_id(db, current.org_id, src.issuer_id) if src.issuer_id else None
    ) or await issuer.get_or_create(db, current.org_id)
    dup = IssuedInvoice(
        org_id=current.org_id,
        partner_id=src.partner_id,
        customer_id=src.customer_id,
        issuer_id=profile.id,
        doc_type="invoice",
        lifecycle=issued_lifecycle.DRAFT,
        number=None,
        issue_date=date.today(),
        supply_date=src.supply_date,
        due_date=None,  # recomputed at issue
        currency=src.currency,
        buyer_name=src.buyer_name,
        buyer_email=src.buyer_email,
        buyer_vat_number=src.buyer_vat_number,
        buyer_address_line1=src.buyer_address_line1,
        buyer_city=src.buyer_city,
        buyer_postal_code=src.buyer_postal_code,
        buyer_country=src.buyer_country,
        seller_json=json.dumps(issuer.seller_snapshot(profile)),
        vat_scheme=src.vat_scheme,
        note=src.note,
        penalty_rate=src.penalty_rate,
        subtotal=src.subtotal,
        tax_total=src.tax_total,
        total=src.total,
    )
    dup.lines = [
        IssuedInvoiceLine(
            position=li.position,
            description=li.description,
            quantity=li.quantity,
            unit=li.unit,
            unit_price=li.unit_price,
            discount_percent=li.discount_percent,
            vat_rate=li.vat_rate,
            net_amount=li.net_amount,
            tax_code=li.tax_code,
        )
        for li in src.lines
    ]
    db.add(dup)
    await audit.record(
        db,
        audit.A.ISSUED_DUPLICATE,
        target_type="issued_invoice",
        target_id=dup.id,
        meta={"source": src.number or src.id},
    )
    await db.commit()
    await db.refresh(dup, attribute_names=["lines"])
    return _detail(dup)


@router.post("/{invoice_id}/cancel", response_model=IssuedInvoiceDetail, dependencies=_WRITE)
async def cancel_draft(invoice_id: str, current: CurrentUser, db: DbSession):
    """Cancel a never-issued draft/approved invoice. (An ISSUED invoice is voided
    via /void, not cancelled.) The row is kept for the audit trail, reading VOID."""
    await modules.require_enabled(db, current.org_id, "issuing")
    inv = await _load(db, current.org_id, invoice_id)
    issued_lifecycle.target_for("cancel_draft", inv.lifecycle)  # validates source state
    inv.lifecycle = issued_lifecycle.CANCELLED
    await audit.record(
        db,
        audit.A.ISSUED_CANCEL,
        target_type="issued_invoice",
        target_id=inv.id,
        meta={},
    )
    await db.commit()
    await db.refresh(inv, attribute_names=["lines"])
    return _detail(inv)


@router.post("/{invoice_id}/mark-viewed", response_model=IssuedInvoiceDetail, dependencies=_WRITE)
async def mark_viewed(invoice_id: str, current: CurrentUser, db: DbSession):
    """Record that the buyer VIEWED the invoice (drives the 'viewed' delivery state).

    The trigger for a real deployment is an email open-tracking webhook or a public
    invoice-link open; this authenticated endpoint is the seam they call. First-wins
    and IDEMPOTENT — a second call is a no-op (the first view timestamp stands). The
    invoice must already have been sent."""
    await modules.require_enabled(db, current.org_id, "issuing")
    inv = await _load(db, current.org_id, invoice_id)
    _require_receivable(inv)
    if inv.sent_at is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Send the invoice before it can be marked viewed."
        )
    if inv.viewed_at is None:
        inv.viewed_at = datetime.now(UTC)  # idempotent: only the first view is recorded
        await audit.record(
            db,
            audit.A.ISSUED_VIEWED,
            target_type="issued_invoice",
            target_id=inv.id,
            meta={"number": inv.number},
        )
        await db.commit()
        await db.refresh(inv, attribute_names=["lines"])
    return _detail(inv)


@router.post("/{invoice_id}/dispute", response_model=IssuedInvoiceDetail, dependencies=_WRITE)
async def dispute_invoice(
    invoice_id: str, body: DisputeRequest, current: CurrentUser, db: DbSession
):
    """Flag an issued invoice as DISPUTED (the buyer contests it). It stays a
    receivable (still owed) but is surfaced separately."""
    await modules.require_enabled(db, current.org_id, "issuing")
    inv = await _load(db, current.org_id, invoice_id)
    _reject_if_voided(inv)
    issued_lifecycle.target_for("dispute", inv.lifecycle)  # validates source state
    inv.lifecycle = issued_lifecycle.DISPUTED
    inv.disputed_at = datetime.now(UTC)
    inv.dispute_reason = body.reason
    await audit.record(
        db,
        audit.A.ISSUED_DISPUTE,
        target_type="issued_invoice",
        target_id=inv.id,
        meta={"number": inv.number, "reason": body.reason},
    )
    await db.commit()
    await db.refresh(inv, attribute_names=["lines"])
    return _detail(inv)


@router.post("/{invoice_id}/undispute", response_model=IssuedInvoiceDetail, dependencies=_WRITE)
async def undispute_invoice(invoice_id: str, current: CurrentUser, db: DbSession):
    """Resolve a dispute — return the invoice to the normal issued/AR lifecycle."""
    await modules.require_enabled(db, current.org_id, "issuing")
    inv = await _load(db, current.org_id, invoice_id)
    issued_lifecycle.target_for("undispute", inv.lifecycle)  # validates source state
    inv.lifecycle = issued_lifecycle.ISSUED
    await audit.record(
        db,
        audit.A.ISSUED_UNDISPUTE,
        target_type="issued_invoice",
        target_id=inv.id,
        meta={"number": inv.number},
    )
    await db.commit()
    await db.refresh(inv, attribute_names=["lines"])
    return _detail(inv)


@router.post("/{invoice_id}/write-off", response_model=IssuedInvoiceDetail, dependencies=_WRITE)
async def write_off_invoice(
    invoice_id: str, body: WriteOffRequest, current: CurrentUser, db: DbSession
):
    """Write off an issued/disputed invoice as bad debt. It is no longer a
    collectible receivable (outstanding reads 0), but the turnover stays on record."""
    await modules.require_enabled(db, current.org_id, "issuing")
    inv = await _load(db, current.org_id, invoice_id)
    _reject_if_voided(inv)
    issued_lifecycle.target_for("write_off", inv.lifecycle)  # validates source state
    inv.lifecycle = issued_lifecycle.WRITTEN_OFF
    inv.written_off_at = datetime.now(UTC)
    inv.writeoff_reason = body.reason
    await audit.record(
        db,
        audit.A.ISSUED_WRITE_OFF,
        target_type="issued_invoice",
        target_id=inv.id,
        meta={"number": inv.number, "reason": body.reason},
    )
    await db.commit()
    await db.refresh(inv, attribute_names=["lines"])
    return _detail(inv)


@router.post(
    "/{invoice_id}/credit-note",
    response_model=IssuedInvoiceDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=_WRITE,
)
async def create_credit_note(
    invoice_id: str, body: CreditNoteCreate, current: CurrentUser, db: DbSession
):
    """Issue a credit note that corrects (reduces) an existing invoice.

    Omit `lines` to credit the whole remaining amount; pass lines for a partial
    credit. A credit note gets its own number series, reduces the corrected
    invoice's outstanding balance, and lowers reported turnover.
    """
    await _guard(db, current.org_id)  # module + issuer-completeness gate
    # Lock the invoice row: `credited_total` is a read-modify-write over the SAME
    # cumulative figure `record_payment` locks for (see `_load`'s comment) — two
    # concurrent partial credit notes reading the same stale `already` would lose
    # one write and understate credited_total, silently widening the over-credit/
    # over-payment exposure `record_payment` trusts this number to cap.
    original = await _load(db, current.org_id, invoice_id, lock=True)
    _require_receivable(original)
    if issued_status.is_credit_note(original):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot credit a credit note.")

    # Determine the lines being credited (full remaining, or the caller's lines).
    already = issued_service.already_credited(original)
    if body.lines is None:
        remaining = money.q2(Decimal(original.total) - already)
        if remaining <= Decimal("0"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "This invoice is already fully credited."
            )
        raw_lines = issued_service.credit_note_lines_for_full(original)
        # After a PARTIAL credit, credit only the still-un-credited portion: scale
        # the original lines by remaining/total. VAT is linear in the net, so the
        # credit note grosses to exactly `remaining` while keeping the rate mix.
        if already > Decimal("0") and Decimal(original.total) > Decimal("0"):
            factor = remaining / Decimal(original.total)
            for rl in raw_lines:
                rl["unit_price"] = Decimal(rl["unit_price"]) * factor
    else:
        raw_lines = [li.model_dump() for li in body.lines]

    # Lock the ORIGINAL invoice's issuer entity FOR UPDATE so the credit-note number
    # comes from the same entity's series (race-free).
    profile = await issuer.lock(db, current.org_id, original.issuer_id)
    cn = issued_service.build_credit_note(
        profile,
        original,
        raw_lines,
        org_id=current.org_id,
        issue_date=body.issue_date,
        note=body.reason,
    )
    # Enforce: total credited (existing + this) may not exceed the invoiced total.
    # Only the caller-supplied-lines path can over-credit; the omit path is bounded
    # to `remaining` by construction. Allow one cent of rounding tolerance.
    new_credited = already + Decimal(cn.total)
    if body.lines is not None and new_credited > Decimal(original.total) + Decimal("0.01"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Credit ({cn.total}) exceeds the invoice's un-credited amount "
            f"({money.q2(Decimal(original.total) - already)}).",
        )
    # Never let the running credited total drift past the invoiced total.
    original.credited_total = money.q2(min(new_credited, Decimal(original.total)))
    db.add(cn)
    await audit.record(
        db,
        audit.A.ISSUED_CREDIT_NOTE,
        target_type="issued_invoice",
        target_id=cn.id,
        meta={"number": cn.number, "corrects": original.number, "amount": str(cn.total)},
    )
    await webhooks.emit(
        db,
        current.org_id,
        "issued.credit_note",
        {
            "id": cn.id,
            "number": cn.number,
            "corrects": original.number,
            "amount": str(cn.total),
            "currency": cn.currency,
        },
    )
    await db.commit()
    await db.refresh(cn, attribute_names=["lines"])
    return _detail(cn)


@router.get("", response_model=IssuedInvoiceListOut)
async def list_issued(
    current: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
):
    total = await db.scalar(
        select(func.count(IssuedInvoice.id)).where(IssuedInvoice.org_id == current.org_id)
    )
    rows = await db.scalars(
        select(IssuedInvoice)
        .where(IssuedInvoice.org_id == current.org_id)
        .order_by(IssuedInvoice.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return IssuedInvoiceListOut(items=[_out(r) for r in rows], total=total or 0)


async def _load(
    db: DbSession, org_id: str, invoice_id: str, *, lock: bool = False
) -> IssuedInvoice:
    stmt = (
        select(IssuedInvoice)
        .where(IssuedInvoice.id == invoice_id, IssuedInvoice.org_id == org_id)
        .options(selectinload(IssuedInvoice.lines))
    )
    if lock:
        # Serialize concurrent settlement writes on this invoice so the cumulative
        # amount_paid update + ledger delta are atomic (no double-write / cap bypass).
        # SQLite ignores FOR UPDATE (writes serialize); Postgres takes the row lock.
        # The lines load via a separate selectinload query, so FOR UPDATE stays on
        # the invoice row only.
        stmt = stmt.with_for_update(of=IssuedInvoice)
    inv = await db.scalar(stmt)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Issued invoice not found")
    return inv


def _reject_if_voided(inv: IssuedInvoice) -> None:
    if inv.voided_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Invoice {inv.number} is voided and cannot be modified."
        )


def _require_receivable(inv: IssuedInvoice) -> None:
    """A live receivable (issued or disputed, not voided). Payment / credit-note /
    send / reminder only apply here — a draft/approved has no number yet and a
    cancelled/written-off invoice is no longer collectible."""
    _reject_if_voided(inv)
    if inv.lifecycle in (issued_lifecycle.DRAFT, issued_lifecycle.APPROVED):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This invoice is still a {inv.lifecycle} — issue it first.",
        )
    if inv.lifecycle in (issued_lifecycle.CANCELLED, issued_lifecycle.WRITTEN_OFF):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This invoice is {inv.lifecycle.replace('_', ' ')} and is no longer a receivable.",
        )


def _require_numbered(inv: IssuedInvoice) -> None:
    """A PDF / e-invoice XML only exists for a numbered (issued) document."""
    if inv.number is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A draft has no PDF or e-invoice XML until it is issued.",
        )


@router.patch("/{invoice_id}/payment", response_model=IssuedInvoiceDetail, dependencies=_WRITE)
async def record_payment(invoice_id: str, body: PaymentUpdate, current: CurrentUser, db: DbSession):
    """Record a payment against an issued invoice (drives the paid/overdue report)."""
    await modules.require_enabled(db, current.org_id, "issuing")
    inv = await _load(db, current.org_id, invoice_id, lock=True)
    _require_receivable(inv)
    if issued_status.is_credit_note(inv):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "A credit note is not a receivable — no payment applies."
        )
    # Payment is capped at the amount actually owed (invoice total net of credits).
    effective = issued_status.effective_total(inv)
    if body.amount_paid > effective:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Amount paid cannot exceed the amount owed ({effective}) after credit notes.",
        )
    # Record the change in the payment ledger and refresh the derived cache
    # (amount_paid / paid_date). SUM(payments) stays equal to amount_paid.
    await payments.set_cumulative(
        db,
        current.org_id,
        inv,
        new_total=body.amount_paid,
        effective=effective,
        paid_date=body.paid_date,
    )
    await audit.record(
        db,
        audit.A.ISSUED_PAYMENT,
        target_type="issued_invoice",
        target_id=inv.id,
        meta={
            "number": inv.number,
            "amount_paid": str(inv.amount_paid),
            "status": issued_status.status_of(inv),
        },
    )
    await webhooks.emit(
        db,
        current.org_id,
        "issued.payment",
        {
            "id": inv.id,
            "number": inv.number,
            "amount_paid": str(inv.amount_paid),
            "outstanding": str(issued_status.outstanding_of(inv)),
            "status": issued_status.status_of(inv),
            "currency": inv.currency,
        },
    )
    await db.commit()
    await db.refresh(inv, attribute_names=["lines"])
    return _detail(inv)


@router.post("/{invoice_id}/void", response_model=IssuedInvoiceDetail, dependencies=_WRITE)
async def void_invoice(invoice_id: str, body: VoidRequest, current: CurrentUser, db: DbSession):
    """Cancel (void) an unpaid invoice. A voided invoice reads as status VOID and
    refuses payment / credit-note / send. Refuses to void a credit note, an
    invoice with any payment recorded, or one already credited."""
    await modules.require_enabled(db, current.org_id, "issuing")
    inv = await _load(db, current.org_id, invoice_id)
    if inv.voided_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Invoice is already voided.")
    if inv.lifecycle != issued_lifecycle.ISSUED:
        # A never-issued draft/approved is cancelled (/cancel); a disputed/written-off
        # invoice is resolved through its own lifecycle, not voided.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only an issued invoice can be voided (this one is {inv.lifecycle}).",
        )
    if issued_status.is_credit_note(inv):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot void a credit note.")
    if Decimal(inv.amount_paid or 0) > Decimal("0"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot void an invoice with payments recorded — refund and remove them first.",
        )
    if Decimal(getattr(inv, "credited_total", None) or 0) > Decimal("0"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Cannot void an invoice that has credit notes against it."
        )
    inv.voided_at = datetime.now(UTC)
    inv.void_reason = body.reason
    await audit.record(
        db,
        audit.A.ISSUED_VOID,
        target_type="issued_invoice",
        target_id=inv.id,
        meta={"number": inv.number, "reason": body.reason},
    )
    await db.commit()
    await db.refresh(inv, attribute_names=["lines"])
    return _detail(inv)


@router.get("/{invoice_id}/payments", response_model=list[PaymentOut])
async def list_payments(invoice_id: str, current: CurrentUser, db: DbSession):
    """The payment ledger (settlement history) for one issued invoice."""
    await modules.require_enabled(db, current.org_id, "issuing")
    await _load(db, current.org_id, invoice_id)  # tenant-scoped existence check
    return await payments.list_for(db, current.org_id, invoice_id)


@router.get("/{invoice_id}/xml")
async def get_issued_xml(invoice_id: str, current: CurrentUser, db: DbSession):
    inv = await _load(db, current.org_id, invoice_id)
    _require_numbered(inv)
    seller = json.loads(inv.seller_json)
    xml = facturx.build_cii(inv, seller, _vat_of(inv))
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": content_disposition(f"{inv.number}.xml")},
    )


async def _render_pdf(db: DbSession, org_id: str, inv: IssuedInvoice) -> bytes:
    """Build the EN-16931 PDF (with embedded Factur-X XML) for one invoice."""
    seller = json.loads(inv.seller_json)
    result = _vat_of(inv)
    xml = facturx.build_cii(inv, seller, result)
    # The logo comes from the invoice's OWN issuer entity (fallback: default).
    profile = (
        await issuer.get_by_id(db, org_id, inv.issuer_id) if inv.issuer_id else None
    ) or await issuer.get_or_create(db, org_id)
    logo = None
    if profile.logo_sha256:
        logo_bytes = await documents.load(documents.LOGOS, org_id, profile.logo_sha256)
        if logo_bytes:
            logo = (profile.logo_mime or "image/png", logo_bytes)
    return await run_in_threadpool(invoice_pdf.build_pdf, inv, seller, result, xml, logo)


@router.get("/{invoice_id}/pdf")
async def get_issued_pdf(
    invoice_id: str,
    current: CurrentUser,
    db: DbSession,
    inline: bool = Query(default=False),
):
    """Download the invoice PDF, or view it inline in the browser (`?inline=1`)."""
    inv = await _load(db, current.org_id, invoice_id)
    _require_numbered(inv)
    try:
        pdf = await _render_pdf(db, current.org_id, inv)
    except invoice_pdf.PdfUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"PDF generation unavailable: {e}")
    disposition = "inline" if inline else "attachment"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                f"{inv.number}.pdf", disposition=disposition
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/export.zip")
async def export_period_zip(
    current: CurrentUser,
    db: DbSession,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    status_filter: str | None = Query(default=None, alias="status"),
):
    """Download a ZIP of every issued-invoice PDF in a period (max 500)."""
    await modules.require_enabled(db, current.org_id, "issuing")
    stmt = (
        select(IssuedInvoice)
        .where(
            IssuedInvoice.org_id == current.org_id,
            # A draft has no number and no legal PDF — never export one.
            IssuedInvoice.number.isnot(None),
        )
        .options(selectinload(IssuedInvoice.lines))
        .order_by(IssuedInvoice.issue_date)
    )
    if date_from is not None:
        stmt = stmt.where(IssuedInvoice.issue_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(IssuedInvoice.issue_date <= date_to)
    invoices = list(await db.scalars(stmt.limit(500)))
    if status_filter:
        invoices = [i for i in invoices if issued_status.status_of(i) == status_filter]
    if not invoices:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No issued invoices match this period")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for inv in invoices:
            try:
                pdf = await _render_pdf(db, current.org_id, inv)
            except invoice_pdf.PdfUnavailable as e:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, f"PDF generation unavailable: {e}"
                )
            zf.writestr(f"{inv.number}.pdf", pdf)
    span = f"{date_from or 'all'}_{date_to or 'all'}"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="invoices_{span}.zip"'},
    )


# --------------------------------------------------------------------------------
# Email delivery + payment reminders (SMTP when configured, else recorded to the
# outbox). Reminders include any accrued late-payment penalty.
# --------------------------------------------------------------------------------


def _seller_name(inv: IssuedInvoice) -> str:
    seller = json.loads(inv.seller_json)
    return seller.get("legal_name") or seller.get("trade_name") or "Us"


@router.post("/{invoice_id}/send", response_model=SendResult, dependencies=_SEND)
async def send_invoice(invoice_id: str, body: SendRequest, current: CurrentUser, db: DbSession):
    """Email the invoice PDF to the buyer (or an override recipient)."""
    await modules.require_enabled(db, current.org_id, "issuing")
    inv = await _load(db, current.org_id, invoice_id)
    _require_receivable(inv)
    recipient = body.to_email or inv.buyer_email
    if not recipient:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No recipient: set a customer email on the invoice or pass one.",
        )

    # Idempotency: once delivered, a repeat send is a NO-OP that returns the first
    # send (no second email, no second outbox row). `resend=true` overrides.
    if inv.sent_at is not None and not body.resend:
        last = await db.scalar(
            select(EmailMessage)
            .where(
                EmailMessage.org_id == current.org_id,
                EmailMessage.invoice_id == inv.id,
                EmailMessage.kind == "invoice",
            )
            .order_by(EmailMessage.created_at.desc())
            .limit(1)
        )
        if last is not None:
            return SendResult(
                message=EmailMessageOut.model_validate(last),
                delivered=last.status == "sent",
                already_sent=True,
            )

    try:
        pdf = await _render_pdf(db, current.org_id, inv)
    except invoice_pdf.PdfUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"PDF generation unavailable: {e}")

    subject, text = mailer.invoice_email(
        seller_name=_seller_name(inv),
        number=inv.number or "",
        buyer_name=inv.buyer_name,
        total=inv.total,
        currency=inv.currency,
        due_date=inv.due_date,
    )
    msg = await mailer.send(
        db,
        current.org_id,
        kind="invoice",
        to_email=recipient,
        subject=subject,
        body=text,
        invoice_id=inv.id,
        attachment=(f"{inv.number}.pdf", pdf),
    )
    if inv.sent_at is None:
        inv.sent_at = datetime.now(UTC)  # record the first delivery on the invoice
    await audit.record(
        db,
        audit.A.ISSUED_SENT,
        target_type="issued_invoice",
        target_id=inv.id,
        meta={"number": inv.number, "to": recipient, "status": msg.status},
    )
    await db.commit()
    await db.refresh(msg)
    return SendResult(message=EmailMessageOut.model_validate(msg), delivered=msg.status == "sent")


@router.post("/{invoice_id}/reminder", response_model=SendResult, dependencies=_SEND)
async def send_reminder(
    invoice_id: str, body: ReminderRequest, current: CurrentUser, db: DbSession
):
    """Send a payment reminder (with any accrued penalty) for an overdue invoice."""
    await modules.require_enabled(db, current.org_id, "issuing")
    inv = await _load(db, current.org_id, invoice_id)
    _require_receivable(inv)
    recipient = body.to_email or inv.buyer_email
    if not recipient:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No recipient: set a customer email on the invoice or pass one.",
        )
    if issued_status.status_of(inv) == issued_status.PAID:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invoice is already paid")

    msg = await _do_reminder(db, current.org_id, inv, recipient)
    await db.commit()
    await db.refresh(msg)
    return SendResult(message=EmailMessageOut.model_validate(msg), delivered=msg.status == "sent")


async def _do_reminder(db: DbSession, org_id: str, inv: IssuedInvoice, recipient: str):
    # A manual send uses the ladder tone for the invoice's current overdue depth and
    # advances its level, so the daily auto-run won't immediately re-send the same one.
    ladder = await dunning.load_ladder(db, org_id)
    target = dunning.resolve_level(issued_status.days_overdue_of(inv), ladder)
    return await dunning.send_reminder(
        db,
        org_id,
        inv,
        recipient,
        tone=target.tone if target else "reminder",
        level=target.level if target else None,
    )


@router.post("/reminders/run", response_model=BulkReminderResult, dependencies=_SEND)
async def run_overdue_reminders(current: CurrentUser, db: DbSession):
    """Send a reminder for every overdue invoice that has a customer email."""
    await modules.require_enabled(db, current.org_id, "issuing")
    res = await dunning.run_overdue(db, current.org_id)
    await db.commit()
    for m in res.messages:
        await db.refresh(m)
    return BulkReminderResult(
        sent=res.sent,
        skipped_no_email=res.skipped_no_email,
        skipped=res.skipped,
        messages=[EmailMessageOut.model_validate(m) for m in res.messages],
    )


@router.get("/emails", response_model=list[EmailMessageOut])
async def list_emails(current: CurrentUser, db: DbSession):
    """The outbound-mail history (invoices sent + reminders) for this workspace."""
    await modules.require_enabled(db, current.org_id, "issuing")
    rows = await mailer.list_messages(db, current.org_id, kinds=mailer.INVOICE_MAIL_KINDS)
    return [EmailMessageOut.model_validate(m) for m in rows]


# --------------------------------------------------------------------------------
# Reports (read-only, tenant-scoped, single-currency). Each endpoint returns JSON
# by default or a CSV download when `?format=csv`. NET = VAT-exclusive subtotal.
# --------------------------------------------------------------------------------


def _csv_safe(value):
    """Neutralise CSV/Excel formula injection: prefix a leading formula trigger
    (= + - @ tab CR) with a single quote so a cell (e.g. a crafted buyer name) is
    never evaluated as a formula on open. Mirrors erp_export._safe."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _csv(filename: str, header: list[str], rows: list[list]) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([_csv_safe(c) for c in r])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/summary", response_model=SummaryReportOut)
async def report_summary(
    current: CurrentUser,
    db: DbSession,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    format: str = Query(default="json", pattern="^(json|csv)$"),
):
    await modules.require_enabled(db, current.org_id, "issuing")
    rep = await issued_reports.summary(db, current.org_id, currency, date_from, date_to)
    if format == "csv":
        return _csv(
            "issued-summary.csv",
            ["period", "invoices", f"net_{rep.currency}", f"gross_{rep.currency}"],
            [[p.period, p.count, p.net, p.gross] for p in rep.series],
        )
    return rep


@router.get("/reports/receivables", response_model=ReceivablesReportOut)
async def report_receivables(
    current: CurrentUser,
    db: DbSession,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    format: str = Query(default="json", pattern="^(json|csv)$"),
    view: str = Query(default="status", pattern="^(status|aging)$"),
):
    await modules.require_enabled(db, current.org_id, "issuing")
    rep = await issued_reports.receivables(db, current.org_id, currency, date_from, date_to)
    if format == "csv":
        if view == "aging":
            return _csv(
                "issued-aging.csv",
                ["bucket", "invoices", f"outstanding_{rep.currency}"],
                [[b.label, b.count, b.outstanding] for b in rep.aging],
            )
        return _csv(
            "issued-receivables.csv",
            ["status", "invoices", f"gross_{rep.currency}", f"outstanding_{rep.currency}"],
            [[s.label, s.count, s.gross, s.outstanding] for s in rep.statuses],
        )
    return rep


@router.get("/reports/partners", response_model=PartnerReportOut)
async def report_partners(
    current: CurrentUser,
    db: DbSession,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    format: str = Query(default="json", pattern="^(json|csv)$"),
):
    await modules.require_enabled(db, current.org_id, "issuing")
    rep = await issued_reports.by_partner(db, current.org_id, currency, date_from, date_to)
    if format == "csv":
        return _csv(
            "issued-partners.csv",
            [
                "partner",
                "vat_number",
                "invoices",
                f"net_{rep.currency}",
                f"vat_{rep.currency}",
                f"gross_{rep.currency}",
                f"outstanding_{rep.currency}",
                "last_invoice",
            ],
            [
                [
                    p.partner,
                    p.vat_number or "",
                    p.count,
                    p.net,
                    p.vat,
                    p.gross,
                    p.outstanding,
                    p.last_invoice.isoformat() if p.last_invoice else "",
                ]
                for p in rep.partners
            ],
        )
    return rep


@router.get("/reports/vat", response_model=VatReportOut)
async def report_vat(
    current: CurrentUser,
    db: DbSession,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    format: str = Query(default="json", pattern="^(json|csv)$"),
):
    await modules.require_enabled(db, current.org_id, "issuing")
    rep = await issued_reports.vat_summary(db, current.org_id, currency, date_from, date_to)
    if format == "csv":
        return _csv(
            "issued-vat.csv",
            ["vat_rate", f"base_{rep.currency}", f"vat_{rep.currency}"],
            [[r.rate, r.base, r.vat] for r in rep.by_rate],
        )
    return rep


# --------------------------------------------------------------------------------
# Supporting attachments (a signed PO, a delivery note, a contract). The bytes are
# stored in object storage; the row holds the metadata + sha256 pointer. Any
# lifecycle may carry attachments; they are never part of the legal invoice PDF.
# --------------------------------------------------------------------------------

_ATTACH_MAX = 25 * 1024 * 1024  # 25 MB per attachment


@router.get("/{invoice_id}/attachments", response_model=list[IssuedAttachmentOut])
async def list_issued_attachments(invoice_id: str, current: CurrentUser, db: DbSession):
    await modules.require_enabled(db, current.org_id, "issuing")
    await _load(db, current.org_id, invoice_id)  # tenant-scoped existence check
    rows = list(
        await db.scalars(
            select(IssuedInvoiceAttachment)
            .where(
                IssuedInvoiceAttachment.org_id == current.org_id,
                IssuedInvoiceAttachment.invoice_id == invoice_id,
            )
            .order_by(IssuedInvoiceAttachment.created_at.asc())
        )
    )
    return [IssuedAttachmentOut.model_validate(a) for a in rows]


@router.post(
    "/{invoice_id}/attachments",
    response_model=IssuedAttachmentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=_WRITE,
)
async def add_issued_attachment(
    invoice_id: str,
    current: CurrentUser,
    db: DbSession,
    file: UploadFile,
    note: str | None = None,
):
    """Attach a supporting document (signed PO, delivery note, contract)."""
    await modules.require_enabled(db, current.org_id, "issuing")
    await _load(db, current.org_id, invoice_id)
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Empty file.")
    if len(data) > _ATTACH_MAX:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Attachment too large (25 MB)."
        )
    # Security gate (filesec choke point): block executables / archives / scripts
    # + malware-scan BEFORE storing — attacker-supplied bytes. Inert docs allowed.
    try:
        filesec.reject_active_content(data)
    except filesec.FileRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))
    sha, size = await documents.store(
        documents.ISSUED_ATTACHMENTS,
        current.org_id,
        data,
        file.content_type,
        db=db,
        filename=file.filename,
        uploaded_by=current.email,
    )
    row = IssuedInvoiceAttachment(
        org_id=current.org_id,
        invoice_id=invoice_id,
        filename=file.filename or "attachment",
        mime=file.content_type,
        size=size,
        sha256=sha,
        note=note,
        uploaded_by=current.id,
        uploaded_by_email=current.email,
    )
    db.add(row)
    await audit.record(
        db,
        audit.A.ISSUED_ATTACH,
        target_type="issued_invoice",
        target_id=invoice_id,
        meta={"filename": row.filename, "size": size},
    )
    await db.commit()
    await db.refresh(row)
    return IssuedAttachmentOut.model_validate(row)


@router.get("/{invoice_id}/attachments/{attachment_id}/download")
async def download_issued_attachment(
    invoice_id: str, attachment_id: str, current: CurrentUser, db: DbSession
):
    await modules.require_enabled(db, current.org_id, "issuing")
    row = await db.scalar(
        select(IssuedInvoiceAttachment).where(
            IssuedInvoiceAttachment.id == attachment_id,
            IssuedInvoiceAttachment.invoice_id == invoice_id,
            IssuedInvoiceAttachment.org_id == current.org_id,
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    data = await documents.load(documents.ISSUED_ATTACHMENTS, current.org_id, row.sha256)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment bytes missing")
    return Response(
        content=data,
        media_type=row.mime or "application/octet-stream",
        headers={
            "Content-Disposition": content_disposition(row.filename, fallback="attachment"),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/{invoice_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_WRITE,
)
async def delete_issued_attachment(
    invoice_id: str, attachment_id: str, current: CurrentUser, db: DbSession
):
    await modules.require_enabled(db, current.org_id, "issuing")
    row = await db.scalar(
        select(IssuedInvoiceAttachment).where(
            IssuedInvoiceAttachment.id == attachment_id,
            IssuedInvoiceAttachment.invoice_id == invoice_id,
            IssuedInvoiceAttachment.org_id == current.org_id,
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Registered LAST: this single-segment dynamic route must not shadow the static
# ones above (/export.zip, /emails, /reports/*).
@router.get("/{invoice_id}", response_model=IssuedInvoiceDetail)
async def get_issued(invoice_id: str, current: CurrentUser, db: DbSession):
    return _detail(await _load(db, current.org_id, invoice_id))

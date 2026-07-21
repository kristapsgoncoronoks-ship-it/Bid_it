from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession
from app.core import money
from app.models.issued_invoice import IssuedInvoice
from app.schemas.issued import (
    BulkReminderResult,
    CreditNoteCreate,
    EmailMessageOut,
    IssuedInvoiceCreate,
    IssuedInvoiceDetail,
    IssuedInvoiceListOut,
    IssuedInvoiceOut,
    IssuedLineOut,
    PaymentOut,
    PaymentUpdate,
    ReminderRequest,
    SendRequest,
    SendResult,
    VatBucketOut,
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
    invoice_pdf,
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

router = APIRouter(prefix="/issued", tags=["issuing"])


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


@router.post("", response_model=IssuedInvoiceDetail, status_code=status.HTTP_201_CREATED)
async def create_issued(body: IssuedInvoiceCreate, current: CurrentUser, db: DbSession):
    profile = await _guard(db, current.org_id)

    # If linked to a partner, enforce its pre-invoicing workflow (contract /
    # acceptance act must be signed) and inherit the partner's penalty rate.
    partner = None
    if body.partner_id:
        partner = await partners.get_partner(db, current.org_id, body.partner_id)
        if partner is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Partner not found")
        missing = partners.missing_signed(partner)
        if missing:
            labels = ", ".join(partners.DOC_LABELS.get(k, k) for k in missing)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Cannot issue to {partner.name}: awaiting signed {labels}.",
            )

    # Resolve any line tax-code to its catalogue rate (Slice 4b): the code drives
    # vat_rate; the canonical code is snapshotted onto the line.
    for li in body.lines:
        if li.tax_code:
            tc = await tax_codes.resolve(db, current.org_id, li.tax_code)
            if tc is None or not tc.active:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, f"Unknown or inactive tax code '{li.tax_code}'"
                )
            li.vat_rate = tc.rate
            li.tax_code = tc.code

    inv = issued_service.build_invoice(profile, body, org_id=current.org_id, partner=partner)
    db.add(inv)
    await db.commit()
    await db.refresh(inv, attribute_names=["lines"])
    return _detail(inv)


@router.post(
    "/{invoice_id}/credit-note",
    response_model=IssuedInvoiceDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_credit_note(
    invoice_id: str, body: CreditNoteCreate, current: CurrentUser, db: DbSession
):
    """Issue a credit note that corrects (reduces) an existing invoice.

    Omit `lines` to credit the whole remaining amount; pass lines for a partial
    credit. A credit note gets its own number series, reduces the corrected
    invoice's outstanding balance, and lowers reported turnover.
    """
    profile = await _guard(db, current.org_id)
    original = await _load(db, current.org_id, invoice_id)
    if issued_status.is_credit_note(original):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot credit a credit note.")

    # Determine the lines being credited (full remaining, or the caller's lines).
    if body.lines is None:
        remaining = money.q2(Decimal(original.total) - issued_service.already_credited(original))
        if remaining <= Decimal("0"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "This invoice is already fully credited."
            )
        raw_lines = issued_service.credit_note_lines_for_full(original)
    else:
        raw_lines = [li.model_dump() for li in body.lines]

    cn = issued_service.build_credit_note(
        profile,
        original,
        raw_lines,
        org_id=current.org_id,
        issue_date=body.issue_date,
        note=body.reason,
    )
    # Enforce: total credited (existing + this) may not exceed the invoiced total.
    new_credited = issued_service.already_credited(original) + Decimal(cn.total)
    if new_credited > Decimal(original.total) + Decimal("0.001"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Credit ({cn.total}) exceeds the invoice's un-credited amount "
            f"({money.q2(Decimal(original.total) - issued_service.already_credited(original))}).",
        )
    original.credited_total = money.q2(new_credited)
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


async def _load(db: DbSession, org_id: str, invoice_id: str) -> IssuedInvoice:
    inv = await db.scalar(
        select(IssuedInvoice)
        .where(IssuedInvoice.id == invoice_id, IssuedInvoice.org_id == org_id)
        .options(selectinload(IssuedInvoice.lines))
    )
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Issued invoice not found")
    return inv


@router.patch("/{invoice_id}/payment", response_model=IssuedInvoiceDetail)
async def record_payment(invoice_id: str, body: PaymentUpdate, current: CurrentUser, db: DbSession):
    """Record a payment against an issued invoice (drives the paid/overdue report)."""
    await modules.require_enabled(db, current.org_id, "issuing")
    inv = await _load(db, current.org_id, invoice_id)
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


@router.get("/{invoice_id}/payments", response_model=list[PaymentOut])
async def list_payments(invoice_id: str, current: CurrentUser, db: DbSession):
    """The payment ledger (settlement history) for one issued invoice."""
    await modules.require_enabled(db, current.org_id, "issuing")
    await _load(db, current.org_id, invoice_id)  # tenant-scoped existence check
    return await payments.list_for(db, current.org_id, invoice_id)


@router.get("/{invoice_id}/xml")
async def get_issued_xml(invoice_id: str, current: CurrentUser, db: DbSession):
    inv = await _load(db, current.org_id, invoice_id)
    seller = json.loads(inv.seller_json)
    xml = facturx.build_cii(inv, seller, _vat_of(inv))
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{inv.number}.xml"'},
    )


async def _render_pdf(db: DbSession, org_id: str, inv: IssuedInvoice) -> bytes:
    """Build the EN-16931 PDF (with embedded Factur-X XML) for one invoice."""
    seller = json.loads(inv.seller_json)
    result = _vat_of(inv)
    xml = facturx.build_cii(inv, seller, result)
    profile = await issuer.get_or_create(db, org_id)
    logo = None
    if profile.logo_sha256:
        logo_bytes = await documents.load(documents.LOGOS, org_id, profile.logo_sha256)
        if logo_bytes:
            logo = (profile.logo_mime, logo_bytes)
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
    try:
        pdf = await _render_pdf(db, current.org_id, inv)
    except invoice_pdf.PdfUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"PDF generation unavailable: {e}")
    disposition = "inline" if inline else "attachment"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{inv.number}.pdf"',
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
        .where(IssuedInvoice.org_id == current.org_id)
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


@router.post("/{invoice_id}/send", response_model=SendResult)
async def send_invoice(invoice_id: str, body: SendRequest, current: CurrentUser, db: DbSession):
    """Email the invoice PDF to the buyer (or an override recipient)."""
    await modules.require_enabled(db, current.org_id, "issuing")
    inv = await _load(db, current.org_id, invoice_id)
    recipient = body.to_email or inv.buyer_email
    if not recipient:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No recipient: set a customer email on the invoice or pass one.",
        )
    try:
        pdf = await _render_pdf(db, current.org_id, inv)
    except invoice_pdf.PdfUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"PDF generation unavailable: {e}")

    subject, text = mailer.invoice_email(
        seller_name=_seller_name(inv),
        number=inv.number,
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


@router.post("/{invoice_id}/reminder", response_model=SendResult)
async def send_reminder(
    invoice_id: str, body: ReminderRequest, current: CurrentUser, db: DbSession
):
    """Send a payment reminder (with any accrued penalty) for an overdue invoice."""
    await modules.require_enabled(db, current.org_id, "issuing")
    inv = await _load(db, current.org_id, invoice_id)
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
    return await dunning.send_reminder(db, org_id, inv, recipient)


@router.post("/reminders/run", response_model=BulkReminderResult)
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
        messages=[EmailMessageOut.model_validate(m) for m in res.messages],
    )


@router.get("/emails", response_model=list[EmailMessageOut])
async def list_emails(current: CurrentUser, db: DbSession):
    """The outbound-mail history (invoices sent + reminders) for this workspace."""
    await modules.require_enabled(db, current.org_id, "issuing")
    rows = await mailer.list_messages(db, current.org_id)
    return [EmailMessageOut.model_validate(m) for m in rows]


# --------------------------------------------------------------------------------
# Reports (read-only, tenant-scoped, single-currency). Each endpoint returns JSON
# by default or a CSV download when `?format=csv`. NET = VAT-exclusive subtotal.
# --------------------------------------------------------------------------------


def _csv(filename: str, header: list[str], rows: list[list]) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
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
):
    await modules.require_enabled(db, current.org_id, "issuing")
    rep = await issued_reports.receivables(db, current.org_id, currency, date_from, date_to)
    if format == "csv":
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


# Registered LAST: this single-segment dynamic route must not shadow the static
# ones above (/export.zip, /emails, /reports/*).
@router.get("/{invoice_id}", response_model=IssuedInvoiceDetail)
async def get_issued(invoice_id: str, current: CurrentUser, db: DbSession):
    return _detail(await _load(db, current.org_id, invoice_id))

from __future__ import annotations

import csv
import io
import json
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession
from app.models.issued_invoice import IssuedInvoice, IssuedInvoiceLine
from app.schemas.issued import (
    IssuedInvoiceCreate,
    IssuedInvoiceDetail,
    IssuedInvoiceListOut,
    IssuedInvoiceOut,
    IssuedLineOut,
    PaymentUpdate,
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
    facturx,
    invoice_pdf,
    issued_reports,
    issued_status,
    issuer,
    modules,
    vat,
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
    raw = [{
        "description": li.description, "quantity": li.quantity, "unit": li.unit,
        "unit_price": li.unit_price, "vat_rate": li.vat_rate,
    } for li in inv.lines]
    return vat.compute(raw, inv.vat_scheme)


def _with_status(out: IssuedInvoiceOut, inv: IssuedInvoice) -> IssuedInvoiceOut:
    out.status = issued_status.status_of(inv)
    out.outstanding = issued_status.outstanding_of(inv)
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

    result = vat.compute([li.model_dump() for li in body.lines], body.vat_scheme)
    issue_date = body.issue_date or date.today()
    due_date = body.due_date or (issue_date + timedelta(days=profile.payment_terms_days))
    currency = (body.currency or profile.default_currency or "EUR").upper()

    number = f"{profile.invoice_prefix}{issue_date.year}-{profile.next_number:04d}"
    profile.next_number += 1

    note = body.note or vat.SCHEME_NOTES.get(body.vat_scheme)

    inv = IssuedInvoice(
        org_id=current.org_id,
        number=number,
        issue_date=issue_date,
        supply_date=body.supply_date,
        due_date=due_date,
        currency=currency,
        buyer_name=body.buyer_name,
        buyer_vat_number=body.buyer_vat_number,
        buyer_address_line1=body.buyer_address_line1,
        buyer_city=body.buyer_city,
        buyer_postal_code=body.buyer_postal_code,
        buyer_country=body.buyer_country.upper() if body.buyer_country else None,
        seller_json=json.dumps(issuer.seller_snapshot(profile)),
        vat_scheme=body.vat_scheme,
        note=note,
        subtotal=result.subtotal,
        tax_total=result.tax_total,
        total=result.total,
        lines=[
            IssuedInvoiceLine(
                position=i + 1,
                description=li["description"],
                quantity=li["quantity"],
                unit=li["unit"],
                unit_price=li["unit_price"],
                vat_rate=li["vat_rate"],
                net_amount=li["net_amount"],
            )
            for i, li in enumerate(result.lines)
        ],
    )
    db.add(inv)
    await db.commit()
    await db.refresh(inv, attribute_names=["lines"])
    return _detail(inv)


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


@router.get("/{invoice_id}", response_model=IssuedInvoiceDetail)
async def get_issued(invoice_id: str, current: CurrentUser, db: DbSession):
    return _detail(await _load(db, current.org_id, invoice_id))


@router.patch("/{invoice_id}/payment", response_model=IssuedInvoiceDetail)
async def record_payment(invoice_id: str, body: PaymentUpdate, current: CurrentUser, db: DbSession):
    """Record a payment against an issued invoice (drives the paid/overdue report)."""
    await modules.require_enabled(db, current.org_id, "issuing")
    inv = await _load(db, current.org_id, invoice_id)
    if body.amount_paid > inv.total:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Amount paid cannot exceed the invoice total")
    inv.amount_paid = body.amount_paid
    # A payment date is required to settle; clear it when the balance is un-paid.
    fully = body.amount_paid >= inv.total and inv.total > 0
    inv.paid_date = (body.paid_date or date.today()) if fully else (body.paid_date if body.amount_paid > 0 else None)
    await audit.record(
        db, audit.A.ISSUED_PAYMENT, target_type="issued_invoice", target_id=inv.id,
        meta={"number": inv.number, "amount_paid": str(inv.amount_paid), "status": issued_status.status_of(inv)},
    )
    await db.commit()
    await db.refresh(inv, attribute_names=["lines"])
    return _detail(inv)


@router.get("/{invoice_id}/xml")
async def get_issued_xml(invoice_id: str, current: CurrentUser, db: DbSession):
    inv = await _load(db, current.org_id, invoice_id)
    seller = json.loads(inv.seller_json)
    xml = facturx.build_cii(inv, seller, _vat_of(inv))
    return Response(
        content=xml, media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{inv.number}.xml"'},
    )


@router.get("/{invoice_id}/pdf")
async def get_issued_pdf(invoice_id: str, current: CurrentUser, db: DbSession):
    inv = await _load(db, current.org_id, invoice_id)
    seller = json.loads(inv.seller_json)
    result = _vat_of(inv)
    xml = facturx.build_cii(inv, seller, result)
    profile = await issuer.get_or_create(db, current.org_id)
    logo = (profile.logo_mime, profile.logo_data) if profile.logo_data else None
    try:
        pdf = await run_in_threadpool(invoice_pdf.build_pdf, inv, seller, result, xml, logo)
    except invoice_pdf.PdfUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"PDF generation unavailable: {e}")
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{inv.number}.pdf"'},
    )


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
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/summary", response_model=SummaryReportOut)
async def report_summary(
    current: CurrentUser, db: DbSession,
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
    current: CurrentUser, db: DbSession,
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
    current: CurrentUser, db: DbSession,
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
            ["partner", "vat_number", "invoices", f"net_{rep.currency}", f"vat_{rep.currency}",
             f"gross_{rep.currency}", f"outstanding_{rep.currency}", "last_invoice"],
            [[p.partner, p.vat_number or "", p.count, p.net, p.vat, p.gross, p.outstanding,
              p.last_invoice.isoformat() if p.last_invoice else ""] for p in rep.partners],
        )
    return rep


@router.get("/reports/vat", response_model=VatReportOut)
async def report_vat(
    current: CurrentUser, db: DbSession,
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

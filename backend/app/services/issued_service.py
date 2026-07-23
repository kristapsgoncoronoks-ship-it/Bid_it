"""Shared construction of ISSUED documents (invoices + credit notes).

Extracted so the create route AND the recurring-invoice generator produce a byte
-identical invoice from the same numbering/VAT/penalty rules — there is exactly
one place that assigns an invoice number and snapshots the seller.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

from app.models.issued_invoice import IssuedInvoice, IssuedInvoiceLine
from app.models.issuer import IssuerProfile
from app.schemas.issued import IssuedInvoiceCreate
from app.services import issuer as issuer_svc
from app.services import vat


def _next_invoice_number(profile: IssuerProfile, issue_date: date) -> str:
    number = f"{profile.invoice_prefix}{issue_date.year}-{profile.next_number:04d}"
    profile.next_number += 1
    return number


def _next_credit_note_number(profile: IssuerProfile, issue_date: date) -> str:
    number = f"{profile.credit_note_prefix}{issue_date.year}-{profile.next_credit_number:04d}"
    profile.next_credit_number += 1
    return number


def build_invoice(
    profile: IssuerProfile,
    body: IssuedInvoiceCreate,
    *,
    org_id: str,
    partner=None,
    issue_date: date | None = None,
    payment_terms_days: int | None = None,
    allocate_number: bool = True,
    lifecycle: str = "issued",
) -> IssuedInvoice:
    """Construct (but do NOT persist) an issued INVOICE from a create payload.

    When `allocate_number` is True (the default), assigns the next number and
    mutates `profile.next_number` (the caller commits so numbering + row land
    atomically). A DRAFT passes allocate_number=False and gets no number until it
    is issued.
    """
    result = vat.compute([li.model_dump() for li in body.lines], body.vat_scheme)
    issue_date = issue_date or body.issue_date or date.today()
    terms = payment_terms_days if payment_terms_days is not None else profile.payment_terms_days
    due_date = body.due_date or (issue_date + timedelta(days=terms))
    currency = (body.currency or profile.default_currency or "EUR").upper()

    # Penalty rate precedence: explicit > partner default > issuer default.
    penalty_rate = body.penalty_rate
    if penalty_rate is None and partner is not None:
        penalty_rate = partner.penalty_rate
    if penalty_rate is None:
        penalty_rate = profile.default_penalty_rate

    note = body.note or vat.SCHEME_NOTES.get(body.vat_scheme)

    return IssuedInvoice(
        org_id=org_id,
        partner_id=partner.id if partner else None,
        doc_type="invoice",
        lifecycle=lifecycle,
        number=_next_invoice_number(profile, issue_date) if allocate_number else None,
        issue_date=issue_date,
        supply_date=body.supply_date,
        due_date=due_date,
        currency=currency,
        buyer_name=body.buyer_name,
        buyer_email=body.buyer_email,
        penalty_rate=penalty_rate,
        buyer_vat_number=body.buyer_vat_number,
        buyer_address_line1=body.buyer_address_line1,
        buyer_city=body.buyer_city,
        buyer_postal_code=body.buyer_postal_code,
        buyer_country=body.buyer_country.upper() if body.buyer_country else None,
        seller_json=json.dumps(issuer_svc.seller_snapshot(profile)),
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
                # Carry the chosen catalogue code through as a snapshot label
                # (result.lines is 1:1 with body.lines, in order).
                tax_code=src.tax_code,
            )
            for i, (li, src) in enumerate(zip(result.lines, body.lines, strict=False))
        ],
    )


def build_credit_note(
    profile: IssuerProfile,
    original: IssuedInvoice,
    lines: list[dict],
    *,
    org_id: str,
    issue_date: date | None = None,
    note: str | None = None,
) -> IssuedInvoice:
    """Construct (but do NOT persist) a CREDIT NOTE that corrects `original`.

    `lines` are raw line dicts (description/quantity/unit_price/vat_rate). Amounts
    are stored POSITIVE; the credit's effect on receivables/turnover is applied by
    the caller (bump `original.credited_total`) and by the reporting sign rules.
    """
    result = vat.compute(lines, original.vat_scheme)
    issue_date = issue_date or date.today()
    default_note = f"Credit note for invoice {original.number}."
    return IssuedInvoice(
        org_id=org_id,
        partner_id=original.partner_id,
        doc_type="credit_note",
        corrected_invoice_id=original.id,
        number=_next_credit_note_number(profile, issue_date),
        issue_date=issue_date,
        supply_date=original.supply_date,
        due_date=None,  # a credit note is not a receivable — nothing falls due
        currency=original.currency,
        buyer_name=original.buyer_name,
        buyer_email=original.buyer_email,
        penalty_rate=None,
        buyer_vat_number=original.buyer_vat_number,
        buyer_address_line1=original.buyer_address_line1,
        buyer_city=original.buyer_city,
        buyer_postal_code=original.buyer_postal_code,
        buyer_country=original.buyer_country,
        seller_json=original.seller_json,  # same frozen seller as the invoice it corrects
        vat_scheme=original.vat_scheme,
        note=note or default_note,
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


def credit_note_lines_for_full(original: IssuedInvoice) -> list[dict]:
    """The line payload that credits the ENTIRE original invoice (mirror its lines)."""
    return [
        {
            "description": li.description,
            "quantity": li.quantity,
            "unit": li.unit,
            "unit_price": li.unit_price,
            "vat_rate": li.vat_rate,
        }
        for li in original.lines
    ]


def already_credited(original: IssuedInvoice) -> Decimal:
    return Decimal(getattr(original, "credited_total", None) or Decimal("0"))

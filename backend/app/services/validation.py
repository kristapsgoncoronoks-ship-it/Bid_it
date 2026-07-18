"""Data validation for invoices — AI (automated checks) and/or human review.

Both are OPT-IN, OFF by default, and turned on per-organization by the user:
  • ai_validation_enabled    → run the automated validator (below) and record its
    findings. On its own it auto-resolves to `passed` or `flagged`.
  • human_validation_enabled → route the invoice to a human review gate
    (`pending`) until someone approves/rejects it.

With both on, the AI findings are computed to ASSIST the human, and the invoice
still waits at `pending` for a person. With neither on, status is `none` and the
invoice behaves exactly as before.

The "AI" validator here is a deterministic rule engine (fast, offline, testable).
It is the seam for a real LLM: `ai_enrich()` is where a model would add findings;
the default provider is a no-op, so nothing leaves the server unless wired up.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.schemas.validation import ValidationFinding
from app.services import fx

_TOL = Decimal("0.01")
_TAX_TOL = Decimal("0.02")
_FX_DEVIATION_PCT = Decimal("3")

# status values
NONE = "none"
PASSED = "passed"
FLAGGED = "flagged"
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"

_KNOWN_CCY = {"EUR", "USD", "GBP", "CHF", "PLN", "SEK", "NOK", "DKK", "CZK",
              "JPY", "CAD", "AUD", "RON", "HUF", "BGN"}


async def run_checks(db: AsyncSession, invoice: Invoice, today: date) -> list[ValidationFinding]:
    """The automated (AI) validator. Read-only; returns findings, never mutates."""
    f: list[ValidationFinding] = []
    lines = list(invoice.line_items)

    # Required fields
    if not invoice.invoice_number or not invoice.invoice_number.strip():
        f.append(ValidationFinding(severity="error", code="missing_number",
                                   message="Invoice number is missing.", field="invoice_number"))
    if not lines:
        f.append(ValidationFinding(severity="warning", code="no_lines",
                                   message="Invoice has no line items."))

    # Money consistency
    line_sum = sum((li.amount for li in lines), start=Decimal("0"))
    if lines and abs(line_sum - Decimal(invoice.subtotal)) > _TOL:
        f.append(ValidationFinding(severity="error", code="subtotal_mismatch",
                                   message=f"Subtotal {invoice.subtotal} ≠ sum of lines {line_sum}.",
                                   field="subtotal"))
    if abs(Decimal(invoice.subtotal) + Decimal(invoice.tax_amount) - Decimal(invoice.total)) > _TOL:
        f.append(ValidationFinding(severity="error", code="total_mismatch",
                                   message=f"Total {invoice.total} ≠ subtotal + tax "
                                           f"({invoice.subtotal} + {invoice.tax_amount}).",
                                   field="total"))
    line_tax = sum(
        ((li.amount * li.tax_rate / Decimal("100")) for li in lines), start=Decimal("0")
    )
    if lines and abs(line_tax - Decimal(invoice.tax_amount)) > _TAX_TOL:
        f.append(ValidationFinding(severity="warning", code="tax_mismatch",
                                   message=f"Tax {invoice.tax_amount} ≠ sum of line taxes "
                                           f"({line_tax.quantize(_TOL)}).", field="tax_amount"))

    # Per-line arithmetic (only when a real quantity is present, to avoid noise on
    # OCR lines that default to qty 1 / unit = amount).
    for li in lines:
        if li.quantity and li.quantity > 1 and li.unit_price > 0:
            expected = li.quantity * li.unit_price
            tol = max(_TOL, abs(expected) * Decimal("0.01"))
            if abs(expected - li.amount) > tol:
                f.append(ValidationFinding(
                    severity="warning", code="line_math",
                    message=f"Line '{li.description[:40]}': {li.quantity}×{li.unit_price} "
                            f"= {expected.quantize(_TOL)} ≠ amount {li.amount}."))

    # Non-positive total
    if Decimal(invoice.total) <= 0:
        f.append(ValidationFinding(severity="error", code="non_positive_total",
                                   message="Invoice total is zero or negative.", field="total"))

    # Dates
    if invoice.issue_date and invoice.issue_date > today:
        f.append(ValidationFinding(severity="warning", code="future_date",
                                   message=f"Issue date {invoice.issue_date} is in the future.",
                                   field="issue_date"))
    if invoice.issue_date and invoice.issue_date < today - timedelta(days=365 * 3):
        f.append(ValidationFinding(severity="info", code="old_date",
                                   message=f"Issue date {invoice.issue_date} is over 3 years old.",
                                   field="issue_date"))
    if invoice.due_date and invoice.issue_date and invoice.due_date < invoice.issue_date:
        f.append(ValidationFinding(severity="warning", code="due_before_issue",
                                   message="Due date is before the issue date.", field="due_date"))

    # Currency
    if invoice.currency and invoice.currency.upper() not in _KNOWN_CCY:
        f.append(ValidationFinding(severity="warning", code="unknown_currency",
                                   message=f"Unrecognised currency '{invoice.currency}'.",
                                   field="currency"))

    # Duplicate invoice number for the same vendor
    dup = await db.scalar(
        select(Invoice.id).where(
            Invoice.org_id == invoice.org_id,
            Invoice.vendor_id == invoice.vendor_id,
            Invoice.invoice_number == invoice.invoice_number,
            Invoice.id != invoice.id,
        ).limit(1)
    )
    if dup is not None:
        f.append(ValidationFinding(severity="error", code="duplicate",
                                   message=f"Invoice number '{invoice.invoice_number}' already "
                                           "exists for this vendor.", field="invoice_number"))

    # FX rate vs ECB (only when foreign + a stated rate)
    if invoice.currency and invoice.currency.upper() != "EUR" and invoice.fx_rate:
        ecb = await fx.rate_for(db, invoice.currency, invoice.issue_date or today)
        if ecb and ecb > 0:
            dev = (Decimal(invoice.fx_rate) - ecb) / ecb * Decimal("100")
            if abs(dev) > _FX_DEVIATION_PCT:
                f.append(ValidationFinding(
                    severity="warning", code="fx_deviation",
                    message=f"Stated FX rate deviates {dev.quantize(Decimal('0.1'))}% from the "
                            f"ECB rate ({ecb}).", field="fx_rate"))

    return ai_enrich(invoice, f)


def ai_enrich(invoice: Invoice, findings: list[ValidationFinding]) -> list[ValidationFinding]:
    """Seam for an LLM validator. Default provider is a no-op (nothing leaves the
    server). A real provider would append model-generated findings here."""
    return findings


async def apply_validation(
    db: AsyncSession, invoice: Invoice, ai_enabled: bool, human_enabled: bool, today: date
) -> list[ValidationFinding]:
    """Set invoice.validation_status/findings per the org's enabled options."""
    import json

    findings: list[ValidationFinding] = []
    if not ai_enabled and not human_enabled:
        invoice.validation_status = NONE
        invoice.validation_findings = None
        return findings

    if ai_enabled:
        findings = await run_checks(db, invoice, today)
        invoice.validation_findings = json.dumps([x.model_dump() for x in findings])

    has_error = any(x.severity == "error" for x in findings)
    if human_enabled:
        invoice.validation_status = PENDING
    else:  # AI only
        invoice.validation_status = FLAGGED if has_error else PASSED
    return findings

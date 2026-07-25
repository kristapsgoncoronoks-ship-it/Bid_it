"""The ONE invoice validation engine (WO-7 / board C1.1).

Two validators used to disagree about whether an invoice reconciles: an
advisory, org-toggled rule engine here, and a zero-tolerance blocking
`_reconcile` living inside the review *route* module. They are now a single
service-owned registry (`RULES`): every rule carries an explicit
``block | advise`` policy and its own tolerance, and no rule is implemented
twice. The route layer only calls in and shapes the response.

Two families of rules:

• BLOCKING (policy="block", tolerance exactly 0) — the AP submit gate.
  `reconcile()` recomputes the money from the lines and compares it to the
  stated header totals; any finding refuses `POST /invoices/{id}/submit`.
  Zero tolerance is deliberate: a submitted invoice enters the approval
  chain and, once approved, locks — a cent of drift there is a control
  failure, not noise. Fail-closed.

• ADVISORY (policy="advise") — the opt-in data-validation findings, OFF by
  default and turned on per-organization by the user:
    ai_validation_enabled    → run `run_checks` and record findings; on its
                               own it auto-resolves to `passed` / `flagged`.
    human_validation_enabled → route the invoice to a human review gate
                               (`pending`) until someone approves/rejects.
  With both on, the AI findings assist the human and the invoice still waits
  at `pending`. With neither on, status is `none` and nothing changes.
  Advisory tolerances are looser (a cent of rounding, two cents of tax) —
  these findings inform capture review, they never gate. Fail-open.

The "AI" validator is a deterministic rule engine (fast, offline, testable).
`ai_enrich()` is the seam for a real LLM; the default provider is a no-op,
so nothing leaves the server unless wired up.

Findings persist as JSON `{severity, code, message, field}` — the SPA renders
that shape; do not change it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import q2
from app.models.invoice import Invoice
from app.schemas.validation import ValidationFinding
from app.services import fx

# status values
NONE = "none"
PASSED = "passed"
FLAGGED = "flagged"
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"


# --------------------------------------------------------------------------- #
# The rule registry — single source of truth for policy and tolerance
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Rule:
    """One validation rule. `policy` decides whether a finding blocks the AP
    submit gate ("block") or merely informs ("advise"); `tolerance` is the
    rule's own comparison slack — explicit per rule, never a module-level
    constant reused by accident. Every "block" rule has tolerance exactly 0
    (see the module docstring for why)."""

    code: str  # stable, unique, machine-readable slug
    policy: Literal["block", "advise"]
    tolerance: Decimal
    scope: Literal["header", "line", "document"]


RULES: tuple[Rule, ...] = (
    # -- blocking reconciliation (the former route-level `_reconcile`) --------
    Rule("recon_tax_rate_range", "block", Decimal("0"), "line"),
    Rule("recon_line_amount", "block", Decimal("0"), "line"),
    Rule("recon_subtotal", "block", Decimal("0"), "header"),
    Rule("recon_tax", "block", Decimal("0"), "header"),
    Rule("recon_total", "block", Decimal("0"), "header"),
    # -- advisory data-validation checks --------------------------------------
    Rule("missing_number", "advise", Decimal("0"), "header"),
    Rule("no_lines", "advise", Decimal("0"), "document"),
    Rule("subtotal_mismatch", "advise", Decimal("0.01"), "header"),
    Rule("total_mismatch", "advise", Decimal("0.01"), "header"),
    Rule("tax_mismatch", "advise", Decimal("0.02"), "header"),
    # line_math: tolerance is the FLOOR — the effective slack is
    # max(0.01, 1% of the expected amount), relative part applied in the check.
    Rule("line_math", "advise", Decimal("0.01"), "line"),
    Rule("non_positive_total", "advise", Decimal("0"), "header"),
    Rule("future_date", "advise", Decimal("0"), "header"),
    Rule("old_date", "advise", Decimal("0"), "header"),
    Rule("due_before_issue", "advise", Decimal("0"), "header"),
    Rule("unknown_currency", "advise", Decimal("0"), "header"),
    Rule("duplicate", "advise", Decimal("0"), "document"),
    Rule("duplicate_cross_supplier", "advise", Decimal("0"), "document"),
    # fx_deviation: tolerance is a PERCENT deviation from the ECB rate, not EUR.
    Rule("fx_deviation", "advise", Decimal("3"), "header"),
)

_BY_CODE: dict[str, Rule] = {r.code: r for r in RULES}


def rule(code: str) -> Rule:
    """Registry lookup; raises KeyError for an unknown code (a programming
    error — every emitted finding must be a registered rule)."""
    return _BY_CODE[code]


def _policy_of(code: str) -> str:
    """Defensive policy lookup for PERSISTED findings: a finding read back from
    an old row whose code predates the registry is treated as advisory
    ("legacy" — it never gains blocking power retroactively)."""
    r = _BY_CODE.get(code)
    return r.policy if r is not None else "advise"


# formatting quantum for messages (2 dp display) — not a tolerance.
_CENT = Decimal("0.01")

_KNOWN_CCY = {
    "EUR",
    "USD",
    "GBP",
    "CHF",
    "PLN",
    "SEK",
    "NOK",
    "DKK",
    "CZK",
    "JPY",
    "CAD",
    "AUD",
    "RON",
    "HUF",
    "BGN",
}


# --------------------------------------------------------------------------- #
# Blocking reconciliation (the AP submit gate) — pure, sync, zero tolerance
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Reconciliation:
    """Result of the blocking money reconciliation: the recomputed totals, the
    stated header totals, and the findings. `blocking` derives from the
    registry policy; the submit gate refuses when it is non-empty."""

    computed_subtotal: Decimal
    computed_tax: Decimal
    computed_total: Decimal
    stated_subtotal: Decimal
    stated_tax: Decimal
    stated_total: Decimal
    findings: list[ValidationFinding] = dc_field(default_factory=list)

    @property
    def blocking(self) -> list[ValidationFinding]:
        return [f for f in self.findings if _policy_of(f.code) == "block"]

    @property
    def balanced(self) -> bool:
        return not self.blocking


def reconcile(invoice: Invoice) -> Reconciliation:
    """Recompute the money from the lines and compare to the stated header
    totals — the AP total-reconciliation + tax-validation check surfaced in
    review and enforced at submit. The server's numbers are authoritative; a
    stated header total is compared, never trusted (§4.10). Zero tolerance:
    the tolerances come from the registry's `recon_*` rules, all Decimal("0")."""

    def _f(code: str, message: str, fld: str | None = None) -> ValidationFinding:
        return ValidationFinding(severity="error", code=code, message=message, field=fld)

    findings: list[ValidationFinding] = []
    csub = Decimal("0")
    ctax = Decimal("0")
    for li in invoice.line_items:
        amount = q2(Decimal(li.amount or 0))
        rate = Decimal(li.tax_rate or 0)
        csub += amount
        ctax += q2(amount * rate / Decimal("100"))
        if rate < 0 or rate > 100:
            findings.append(
                _f(
                    "recon_tax_rate_range",
                    f"Line '{li.description}': tax rate {rate}% is out of range 0–100",
                )
            )
        expected = q2(Decimal(li.quantity or 0) * Decimal(li.unit_price or 0))
        if amount != expected:
            findings.append(
                _f(
                    "recon_line_amount",
                    f"Line '{li.description}': amount {amount} ≠ quantity × unit price "
                    f"({expected})",
                )
            )
    csub = q2(csub)
    ctax = q2(ctax)
    ctot = q2(csub + ctax)
    ssub, stax, stot = (
        q2(Decimal(invoice.subtotal or 0)),
        q2(Decimal(invoice.tax_amount or 0)),
        q2(Decimal(invoice.total or 0)),
    )
    if csub != ssub:
        findings.append(
            _f(
                "recon_subtotal",
                f"Subtotal mismatch: lines total {csub} vs stated {ssub}",
                "subtotal",
            )
        )
    if ctax != stax:
        findings.append(
            _f("recon_tax", f"Tax mismatch: lines tax {ctax} vs stated {stax}", "tax_amount")
        )
    if ctot != stot:
        findings.append(
            _f("recon_total", f"Total mismatch: computed {ctot} vs stated {stot}", "total")
        )
    return Reconciliation(
        computed_subtotal=csub,
        computed_tax=ctax,
        computed_total=ctot,
        stated_subtotal=ssub,
        stated_tax=stax,
        stated_total=stot,
        findings=findings,
    )


# --------------------------------------------------------------------------- #
# Advisory checks (opt-in AI validation) — read-only, looser tolerances
# --------------------------------------------------------------------------- #


async def run_checks(db: AsyncSession, invoice: Invoice, today: date) -> list[ValidationFinding]:
    """The automated (AI) validator. Read-only; returns findings, never mutates.
    Tolerances come from the registry — one rule, one slack, declared once."""
    f: list[ValidationFinding] = []
    lines = list(invoice.line_items)

    # Required fields
    if not invoice.invoice_number or not invoice.invoice_number.strip():
        f.append(
            ValidationFinding(
                severity="error",
                code="missing_number",
                message="Invoice number is missing.",
                field="invoice_number",
            )
        )
    if not lines:
        f.append(
            ValidationFinding(
                severity="warning", code="no_lines", message="Invoice has no line items."
            )
        )

    # Money consistency
    line_sum = sum((li.amount for li in lines), start=Decimal("0"))
    if lines and abs(line_sum - Decimal(invoice.subtotal)) > rule("subtotal_mismatch").tolerance:
        f.append(
            ValidationFinding(
                severity="error",
                code="subtotal_mismatch",
                message=f"Subtotal {invoice.subtotal} ≠ sum of lines {line_sum}.",
                field="subtotal",
            )
        )
    if (
        abs(Decimal(invoice.subtotal) + Decimal(invoice.tax_amount) - Decimal(invoice.total))
        > rule("total_mismatch").tolerance
    ):
        f.append(
            ValidationFinding(
                severity="error",
                code="total_mismatch",
                message=f"Total {invoice.total} ≠ subtotal + tax "
                f"({invoice.subtotal} + {invoice.tax_amount}).",
                field="total",
            )
        )
    line_tax = sum(((li.amount * li.tax_rate / Decimal("100")) for li in lines), start=Decimal("0"))
    if lines and abs(line_tax - Decimal(invoice.tax_amount)) > rule("tax_mismatch").tolerance:
        f.append(
            ValidationFinding(
                severity="warning",
                code="tax_mismatch",
                message=f"Tax {invoice.tax_amount} ≠ sum of line taxes "
                f"({line_tax.quantize(_CENT)}).",
                field="tax_amount",
            )
        )

    # Per-line arithmetic (only when a real quantity is present, to avoid noise on
    # OCR lines that default to qty 1 / unit = amount).
    for li in lines:
        if li.quantity and li.quantity > 1 and li.unit_price > 0:
            expected = li.quantity * li.unit_price
            tol = max(rule("line_math").tolerance, abs(expected) * Decimal("0.01"))
            if abs(expected - li.amount) > tol:
                f.append(
                    ValidationFinding(
                        severity="warning",
                        code="line_math",
                        message=f"Line '{li.description[:40]}': {li.quantity}×{li.unit_price} "
                        f"= {expected.quantize(_CENT)} ≠ amount {li.amount}.",
                    )
                )

    # Non-positive total
    if Decimal(invoice.total) <= 0:
        f.append(
            ValidationFinding(
                severity="error",
                code="non_positive_total",
                message="Invoice total is zero or negative.",
                field="total",
            )
        )

    # Dates
    if invoice.issue_date and invoice.issue_date > today:
        f.append(
            ValidationFinding(
                severity="warning",
                code="future_date",
                message=f"Issue date {invoice.issue_date} is in the future.",
                field="issue_date",
            )
        )
    if invoice.issue_date and invoice.issue_date < today - timedelta(days=365 * 3):
        f.append(
            ValidationFinding(
                severity="info",
                code="old_date",
                message=f"Issue date {invoice.issue_date} is over 3 years old.",
                field="issue_date",
            )
        )
    if invoice.due_date and invoice.issue_date and invoice.due_date < invoice.issue_date:
        f.append(
            ValidationFinding(
                severity="warning",
                code="due_before_issue",
                message="Due date is before the issue date.",
                field="due_date",
            )
        )

    # Currency
    if invoice.currency and invoice.currency.upper() not in _KNOWN_CCY:
        f.append(
            ValidationFinding(
                severity="warning",
                code="unknown_currency",
                message=f"Unrecognised currency '{invoice.currency}'.",
                field="currency",
            )
        )

    # Duplicate invoice number for the same vendor
    dup = await db.scalar(
        select(Invoice.id)
        .where(
            Invoice.org_id == invoice.org_id,
            Invoice.vendor_id == invoice.vendor_id,
            Invoice.invoice_number == invoice.invoice_number,
            Invoice.id != invoice.id,
        )
        .limit(1)
    )
    if dup is not None:
        f.append(
            ValidationFinding(
                severity="error",
                code="duplicate",
                message=f"Invoice number '{invoice.invoice_number}' already "
                "exists for this vendor.",
                field="invoice_number",
            )
        )
    else:
        # Same number under a DIFFERENT supplier — usually a coincidence, but flag
        # it (a mis-assigned supplier looks exactly like this). Advisory warning,
        # never an error; distinct from the same-supplier duplicate above.
        cross = await db.scalar(
            select(Invoice.id)
            .where(
                Invoice.org_id == invoice.org_id,
                Invoice.invoice_number == invoice.invoice_number,
                Invoice.vendor_id != invoice.vendor_id,
                Invoice.id != invoice.id,
            )
            .limit(1)
        )
        if cross is not None:
            f.append(
                ValidationFinding(
                    severity="warning",
                    code="duplicate_cross_supplier",
                    message=f"Invoice number '{invoice.invoice_number}' also exists "
                    "under a different supplier — check the supplier is correct.",
                    field="invoice_number",
                )
            )

    # FX rate vs ECB (only when foreign + a stated rate)
    if invoice.currency and invoice.currency.upper() != "EUR" and invoice.fx_rate:
        ecb = await fx.rate_for(db, invoice.currency, invoice.issue_date or today)
        if ecb and ecb > 0:
            dev = (Decimal(invoice.fx_rate) - ecb) / ecb * Decimal("100")
            if abs(dev) > rule("fx_deviation").tolerance:
                f.append(
                    ValidationFinding(
                        severity="warning",
                        code="fx_deviation",
                        message=f"Stated FX rate deviates {dev.quantize(Decimal('0.1'))}% from the "
                        f"ECB rate ({ecb}).",
                        field="fx_rate",
                    )
                )

    return ai_enrich(invoice, f)


def ai_enrich(invoice: Invoice, findings: list[ValidationFinding]) -> list[ValidationFinding]:
    """Seam for an LLM validator. Default provider is a no-op (nothing leaves the
    server). A real provider would append model-generated findings here."""
    return findings


async def apply_validation(
    db: AsyncSession, invoice: Invoice, ai_enabled: bool, human_enabled: bool, today: date
) -> list[ValidationFinding]:
    """Set invoice.validation_status/findings per the org's enabled options."""
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

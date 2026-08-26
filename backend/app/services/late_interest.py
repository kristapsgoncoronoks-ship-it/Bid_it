"""Late-payment interest on overdue receivables (WO-K, Dir. 2011/7/EU).

ADVISORY arithmetic only — this module computes what an overdue issued
invoice could carry, it never books anything and never auto-issues. The
existing penalty-invoicing machinery is the only way a figure here becomes
a document, and a human drives that.

Two bases, contractual first:

- **Contractual** — the invoice's own `penalty_rate` (% per annum) when the
  parties agreed one. It replaces the statutory default entirely, including
  the €40: Art. 6's flat recovery cost is the floor for the DEFAULT regime,
  not a bonus on top of negotiated terms.
- **Statutory (2011/7/EU)** — reference rate + 8 percentage points (Art. 2(6)
  minimum margin) on the outstanding amount, pro rata by day over a 365-day
  year, plus the Art. 6 fixed €40 recovery cost per invoice. The reference
  rate is the org-configured `late_interest_base_rate` (Settings), falling
  back to a stated default constant — ADR-0027 forbids fetching the ECB rate
  ambiently, and a semi-annual figure typed once by an admin beats a silent
  network dependency.

EUR only: the €40 and the directive's mechanics are EUR-denominated; a
non-EUR invoice gets `None` with the reason stated rather than a guessed
conversion (the FX convention forbids sums without a recorded rate).
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.models.issued_invoice import IssuedInvoice
from app.models.organization import Organization

_CENT = Decimal("0.01")

#: Art. 2(6): statutory interest = reference rate + at least eight points.
STATUTORY_MARGIN_PP = Decimal("8")
#: Art. 6(1): fixed compensation for recovery costs, per invoice.
RECOVERY_COST_EUR = Decimal("40.00")
#: Fallback when the org has not configured a reference rate. The ECB main
#: refinancing rate moves with policy decisions — configure the real current
#: value in Settings; this constant only keeps the advisory figure defined.
DEFAULT_BASE_RATE_PP = Decimal("2.15")


def _money(v: Decimal) -> str:
    return str(v.quantize(_CENT, rounding=ROUND_HALF_UP))


def compute(invoice: IssuedInvoice, org: Organization, *, today: date | None = None) -> dict | None:
    """The advisory late-interest figure for one overdue invoice, or None
    (with no side channel) when none applies. Returns a dict the route can
    serialize as-is."""
    today = today or date.today()
    if getattr(invoice, "doc_type", "invoice") != "invoice":
        return None  # a credit note owes nobody anything
    if invoice.due_date is None or invoice.due_date >= today:
        return None
    outstanding = (
        Decimal(invoice.total or 0)
        - Decimal(invoice.amount_paid or 0)
        - Decimal(getattr(invoice, "credited_total", None) or 0)
    )
    if outstanding <= 0:
        return None
    days = (today - invoice.due_date).days

    if (invoice.currency or "EUR").upper() != "EUR":
        return {
            "basis": "unavailable",
            "reason": (
                "Statutory late-payment figures are EUR-denominated; this "
                f"invoice is in {invoice.currency}. Agree a contractual rate "
                "on the invoice instead."
            ),
            "days_overdue": days,
        }

    contractual = invoice.penalty_rate
    if contractual is not None and Decimal(contractual) > 0:
        rate = Decimal(contractual)
        interest = outstanding * rate / Decimal("100") * Decimal(days) / Decimal("365")
        return {
            "basis": "contractual",
            "rate_pp": str(rate),
            "days_overdue": days,
            "outstanding": _money(outstanding),
            "interest_eur": _money(interest),
            "recovery_cost_eur": None,
            "total_eur": _money(interest),
        }

    base = Decimal(getattr(org, "late_interest_base_rate", None) or DEFAULT_BASE_RATE_PP)
    rate = base + STATUTORY_MARGIN_PP
    interest = outstanding * rate / Decimal("100") * Decimal(days) / Decimal("365")
    return {
        "basis": "statutory",
        "directive": "2011/7/EU",
        "base_rate_pp": str(base),
        "base_rate_configured": getattr(org, "late_interest_base_rate", None) is not None,
        "rate_pp": str(rate),
        "days_overdue": days,
        "outstanding": _money(outstanding),
        "interest_eur": _money(interest),
        "recovery_cost_eur": _money(RECOVERY_COST_EUR),
        "total_eur": _money(interest + RECOVERY_COST_EUR),
    }

"""VAT computation for issued invoices (EN 16931 / Directive 2006/112/EC).

Groups lines by VAT rate into a breakdown (taxable base + VAT per rate), which is
exactly what a compliant invoice must show. For reverse-charge / intra-EU /
exempt schemes the effective VAT is zero and a legal note is required.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_CENTS = Decimal("0.01")

# Schemes where the supplier charges no VAT and must state the reason.
ZERO_VAT_SCHEMES = {"reverse_charge", "intra_eu", "exempt"}

SCHEME_NOTES = {
    "reverse_charge": "Reverse charge — VAT to be accounted for by the recipient (Art. 196 Directive 2006/112/EC).",
    "intra_eu": "Intra-Community supply — exempt (Art. 138 Directive 2006/112/EC).",
    "exempt": "VAT exempt (Art. 132–137 Directive 2006/112/EC).",
}


def q(v: Decimal) -> Decimal:
    return v.quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass
class RateBucket:
    rate: Decimal
    base: Decimal
    vat: Decimal


@dataclass
class VatResult:
    lines: list[dict]          # normalised lines with net_amount
    breakdown: list[RateBucket]
    subtotal: Decimal          # taxable base total
    tax_total: Decimal
    total: Decimal


def compute(raw_lines: list[dict], scheme: str = "standard") -> VatResult:
    """`raw_lines`: [{description, quantity, unit_price, vat_rate, unit?}].

    Under a zero-VAT scheme every line's effective rate is 0 for the VAT
    calculation, regardless of the rate entered.
    """
    zero = scheme in ZERO_VAT_SCHEMES
    norm: list[dict] = []
    buckets: dict[Decimal, RateBucket] = {}
    subtotal = Decimal("0")

    for li in raw_lines:
        qty = Decimal(str(li.get("quantity", "1")))
        unit_price = Decimal(str(li.get("unit_price", "0")))
        net = q(qty * unit_price)
        rate = Decimal("0") if zero else Decimal(str(li.get("vat_rate", "0")))
        subtotal += net
        b = buckets.setdefault(rate, RateBucket(rate=rate, base=Decimal("0"), vat=Decimal("0")))
        b.base += net
        norm.append(
            {
                "description": str(li.get("description", "")).strip()[:500],
                "quantity": qty,
                "unit": str(li.get("unit", "C62"))[:8],
                "unit_price": unit_price,
                "vat_rate": rate,
                "net_amount": net,
            }
        )

    for b in buckets.values():
        b.base = q(b.base)
        b.vat = q(b.base * b.rate / Decimal("100"))

    tax_total = q(sum((b.vat for b in buckets.values()), start=Decimal("0")))
    subtotal = q(subtotal)
    breakdown = sorted(buckets.values(), key=lambda b: b.rate)
    return VatResult(
        lines=norm,
        breakdown=breakdown,
        subtotal=subtotal,
        tax_total=tax_total,
        total=q(subtotal + tax_total),
    )

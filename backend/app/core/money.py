"""Money helpers — exact Decimal rounding, defined once.

Amounts are quantized ROUND_HALF_UP so the rounding rule for currency lives in a
single place instead of being re-declared per module. Storage columns stay
Numeric(14, 2). Use `q2` for cents and `q(value, exp)` for another precision.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")


def q(value: Decimal, exp: Decimal = CENTS) -> Decimal:
    """Quantize `value` to `exp` (default cents), ROUND_HALF_UP."""
    return Decimal(value).quantize(exp, rounding=ROUND_HALF_UP)


def q2(value: Decimal) -> Decimal:
    """Quantize `value` to 2 decimals (cents), ROUND_HALF_UP."""
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)

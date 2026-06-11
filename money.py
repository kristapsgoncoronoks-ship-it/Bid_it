"""
MONEY - exact decimal arithmetic for amounts (EUR / local), VAT and thresholds.

Amounts in this system are 2-decimal currency values. Plain binary float plus
Python's round() (banker's, round-half-to-even) is wrong for accounting: a VAT
refund regime rounds half UP and compares totals against hard EUR thresholds
(e.g. the 400 EUR quarterly / 50 EUR annual minimums of Dir. 2008/9/EC).

Use Decimal here:
  * q2(x)  -> Decimal quantized to cents, ROUND_HALF_UP  (use for thresholds)
  * f2(x)  -> the same value as a float, for storage in SQLite REAL columns
  * dsum() -> exact Decimal sum of amounts; fsum() the float form

Storage columns stay REAL, but every value written is already exactly quantized
to cents, and every threshold decision is made on the Decimal form so a total
sitting exactly on a EUR boundary never flips on binary-float noise.
"""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

CENT = Decimal("0.01")


def D(x):
    """Best-effort Decimal from int / float / str / Decimal / None (None -> 0)."""
    if x is None or x == "":
        return Decimal(0)
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        # via repr so 56057.99 stays 56057.99, not 56057.98999999...
        return Decimal(repr(x))
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def q2(x):
    """Quantize to 2 decimals with ROUND_HALF_UP (accounting rounding)."""
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def f2(x):
    """q2 as a float, for storage in REAL columns / JSON responses."""
    return float(q2(x))


def dsum(values):
    """Exact Decimal sum of an iterable of amounts, quantized to cents."""
    total = Decimal(0)
    for v in values:
        total += D(v)
    return total.quantize(CENT, rounding=ROUND_HALF_UP)


def fsum(values):
    """dsum as a float."""
    return float(dsum(values))


if __name__ == "__main__":
    # inline smoke test (the project convention)
    assert f2(56057.985) == 56057.99, "half-up rounding"     # banker's would give .98
    assert f2(2.675) == 2.68, "classic float-rounding trap"
    assert f2(None) == 0.0
    assert fsum([0.1, 0.2]) == 0.30, "exact decimal sum"
    assert q2("399.994") < 400 and q2("399.995") >= 400, "threshold boundary (half-up)"
    assert str(q2(1.1)) == "1.10"
    print("money.py: all smoke tests PASS")

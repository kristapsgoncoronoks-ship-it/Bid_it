"""Tests for the Decimal money helpers."""
from decimal import Decimal

import money


def test_half_up_rounding():
    # Python's round() (banker's) would give 56057.98 / 2.67; accounting rounds up.
    assert money.f2(56057.985) == 56057.99
    assert money.f2(2.675) == 2.68
    assert money.f2(0.005) == 0.01


def test_none_and_blank():
    assert money.f2(None) == 0.0
    assert money.f2("") == 0.0
    assert money.q2(None) == Decimal("0.00")


def test_exact_sum():
    # 0.1 + 0.2 != 0.3 in binary float; dsum is exact.
    assert money.fsum([0.1, 0.2]) == 0.30
    assert money.dsum([0.1, 0.2, 0.005]) == Decimal("0.31")


def test_threshold_boundary():
    # a value rounding to exactly 400.00 must clear the 400 quarterly minimum
    assert money.q2("399.994") < 400
    assert money.q2("399.995") >= 400


def test_returns_native_types():
    assert isinstance(money.f2(1.1), float)
    assert isinstance(money.q2(1.1), Decimal)
    assert str(money.q2(1.1)) == "1.10"

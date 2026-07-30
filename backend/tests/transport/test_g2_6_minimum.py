"""G2.6 slice 1 — the Art. 17 minimum-amount gate (R8), `BA_fleet_fuel.md`
A3. See `app.services.transport.minimum`'s module docstring for the exact
case-law boundaries this file proves: `SEK 3,999` blocked / `SEK 4,000`
allowed, `€399.99` blocked / `€400.00` allowed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.transport import minimum


def test_g2_6_min_for_a_national_minimum_country_returns_local_basis():
    currency, threshold, basis = minimum.min_for("SE", is_annual=False)
    assert (currency, threshold, basis) == ("SEK", Decimal("4000"), "local")

    currency, threshold, basis = minimum.min_for("SE", is_annual=True)
    assert (currency, threshold, basis) == ("SEK", Decimal("500"), "local")


def test_g2_6_min_for_a_euro_country_returns_eur_basis():
    currency, threshold, basis = minimum.min_for("LV", is_annual=False)
    assert (currency, threshold, basis) == ("EUR", Decimal("400.00"), "eur")

    currency, threshold, basis = minimum.min_for("LV", is_annual=True)
    assert (currency, threshold, basis) == ("EUR", Decimal("50.00"), "eur")


def test_g2_6_min_for_poland_is_deliberately_absent_falls_back_to_eur():
    """`BA_fleet_fuel.md` A3: Poland is DELIBERATELY absent from
    `NATIONAL_MINIMUMS` — verified explicitly, not silently assumed."""
    currency, _threshold, basis = minimum.min_for("PL", is_annual=False)
    assert currency == "EUR"
    assert basis == "eur"


@pytest.mark.parametrize(
    "vat_local,expected_blocked",
    [
        (Decimal("3999"), True),  # SEK 3,999 — blocked, verbatim case law
        (Decimal("4000"), False),  # SEK 4,000 — allowed, the boundary itself passes
        (Decimal("4000.01"), False),
    ],
)
def test_g2_6_below_minimum_sweden_quarterly_case_law_boundary(vat_local, expected_blocked):
    assert (
        minimum.below_minimum(
            country="SE", ref_period="2026-Q2", vat_eur=Decimal("0"), vat_local=vat_local
        )
        is expected_blocked
    )


@pytest.mark.parametrize(
    "vat_eur,expected_blocked",
    [
        (Decimal("399.99"), True),  # blocked, verbatim case law
        (Decimal("400.00"), False),  # allowed, the boundary itself passes
        (Decimal("400.01"), False),
    ],
)
def test_g2_6_below_minimum_euro_country_quarterly_case_law_boundary(vat_eur, expected_blocked):
    assert (
        minimum.below_minimum(
            country="LV", ref_period="2026-Q2", vat_eur=vat_eur, vat_local=vat_eur
        )
        is expected_blocked
    )


def test_g2_6_below_minimum_annual_threshold_is_lower():
    # €50 annual vs €400 quarterly — a claim of €100 clears the annual
    # threshold but would be blocked as a quarterly claim.
    assert (
        minimum.below_minimum(
            country="LV", ref_period="2026-YEAR", vat_eur=Decimal("100.00"), vat_local=None
        )
        is False
    )
    assert (
        minimum.below_minimum(
            country="LV", ref_period="2026-Q2", vat_eur=Decimal("100.00"), vat_local=None
        )
        is True
    )


def test_g2_6_below_minimum_treats_a_none_local_amount_as_zero_when_basis_is_local():
    assert (
        minimum.below_minimum(
            country="SE", ref_period="2026-Q2", vat_eur=Decimal("999999"), vat_local=None
        )
        is True
    )

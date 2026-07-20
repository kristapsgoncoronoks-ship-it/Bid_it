"""Golden invariants for money, VAT, and FX (see ADR-0010).

These lock down the correctness properties the product sells on. If any of these
break, a financial figure somewhere is wrong. They are deliberately explicit and
example-driven ("golden files") so a regression is obvious in the diff.

Invariants covered:
  M — money is exact Decimal, ROUND_HALF_UP, 2 dp, idempotent.
  V — VAT computes per scheme; zero-VAT schemes carry a legal note; rates group;
      totals are self-consistent.
  F — FX converts at units-per-EUR with recorded PROVENANCE; unknown never masks
      as a real conversion.
  X — reports NEVER sum across currencies (single-currency by construction).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core import money
from app.services import fx, vat

# --------------------------------------------------------------------------- #
# M — Money
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected", [
    ("0.005", "0.01"),      # HALF_UP rounds the exact half up
    ("0.004", "0.00"),
    ("2.675", "2.68"),      # the classic float trap — exact with Decimal
    ("2.674", "2.67"),
    ("-0.005", "-0.01"),    # symmetric HALF_UP away from zero
    ("10", "10.00"),
    ("1.999", "2.00"),
])
def test_q2_rounds_half_up(raw, expected):
    assert money.q2(Decimal(raw)) == Decimal(expected)


def test_q2_is_idempotent_and_two_places():
    once = money.q2(Decimal("3.14159"))
    assert once == Decimal("3.14")
    assert money.q2(once) == once
    assert once.as_tuple().exponent == -2  # exactly 2 decimal places


def test_money_never_uses_float():
    # q2 accepts a Decimal and returns a Decimal; passing a float would be a bug
    # at the call site. Assert the type contract holds.
    result = money.q2(Decimal("1.005"))
    assert isinstance(result, Decimal)


# --------------------------------------------------------------------------- #
# V — VAT engine
# --------------------------------------------------------------------------- #

def _line(desc, qty, price, rate):
    return {"description": desc, "quantity": qty, "unit_price": price, "vat_rate": rate}


def test_vat_standard_multi_rate_golden():
    # 2×100 @19% + 1×50 @7%  → base 250; VAT 38.00 + 3.50 = 41.50; total 291.50
    r = vat.compute([_line("Consulting", "2", "100.00", "19"), _line("Support", "1", "50.00", "7")])
    assert r.subtotal == Decimal("250.00")
    assert r.tax_total == Decimal("41.50")
    assert r.total == Decimal("291.50")
    by_rate = {b.rate: b for b in r.breakdown}
    assert by_rate[Decimal("19")].base == Decimal("200.00")
    assert by_rate[Decimal("19")].vat == Decimal("38.00")
    assert by_rate[Decimal("7")].vat == Decimal("3.50")


def test_vat_totals_are_self_consistent():
    r = vat.compute([_line("A", "3", "33.33", "21"), _line("B", "1", "0.01", "21")])
    # total == subtotal + tax_total, always.
    assert r.total == money.q2(r.subtotal + r.tax_total)
    # tax_total == sum of per-bucket VAT.
    assert r.tax_total == money.q2(sum((b.vat for b in r.breakdown), start=Decimal("0")))


def test_vat_same_rate_lines_group_into_one_bucket():
    r = vat.compute([_line("A", "1", "100.00", "19"), _line("B", "1", "50.00", "19")])
    assert len(r.breakdown) == 1
    assert r.breakdown[0].base == Decimal("150.00")
    assert r.breakdown[0].vat == Decimal("28.50")


@pytest.mark.parametrize("scheme", ["reverse_charge", "intra_eu", "exempt"])
def test_zero_vat_schemes_charge_no_vat_and_state_a_note(scheme):
    r = vat.compute([_line("Services", "1", "1000.00", "19")], scheme)
    assert r.tax_total == Decimal("0.00")
    assert r.total == r.subtotal == Decimal("1000.00")  # no VAT added
    assert scheme in vat.SCHEME_NOTES and vat.SCHEME_NOTES[scheme]


def test_vat_rounds_each_bucket_half_up():
    # 1 × 10.10 @ 19% = 1.919 → 1.92 (per-bucket HALF_UP)
    r = vat.compute([_line("X", "1", "10.10", "19")])
    assert r.tax_total == Decimal("1.92")


# --------------------------------------------------------------------------- #
# F — FX conversion + provenance
# --------------------------------------------------------------------------- #

_ON = date(2026, 7, 18)  # conftest seeds ECB rates around this date


@pytest.mark.asyncio
async def test_eur_total_eur_is_identity_with_provenance(db_session):
    eur, source = await fx.eur_total(db_session, Decimal("123.45"), "EUR", _ON, None)
    assert eur == Decimal("123.45")
    assert source == "eur"


@pytest.mark.asyncio
async def test_eur_total_uses_stated_rate_when_given(db_session):
    # stated rate = 1.25 foreign units per EUR → 125.00 / 1.25 = 100.00
    eur, source = await fx.eur_total(db_session, Decimal("125.00"), "USD", _ON, Decimal("1.25"))
    assert eur == Decimal("100.00")
    assert source == "stated"


@pytest.mark.asyncio
async def test_eur_total_converts_at_ecb_units_per_eur(db_session):
    # Insert a deterministic rate so the golden value is exact and stable.
    await fx.load_rates(db_session, [(_ON, "ZZZ", Decimal("2.0"))])  # 2 ZZZ per EUR
    eur, source = await fx.eur_total(db_session, Decimal("50.00"), "ZZZ", _ON, None)
    assert eur == Decimal("25.00")   # 50 / 2
    assert source == "ecb"


@pytest.mark.asyncio
async def test_unknown_currency_never_masks_as_a_real_conversion(db_session):
    eur, source = await fx.eur_total(db_session, Decimal("99.99"), "QQQ", _ON, None)
    assert eur is None
    assert source == "unknown"   # provenance is explicit, never a silent wrong number


@pytest.mark.asyncio
async def test_provenance_source_is_always_a_known_value(db_session):
    for ccy, stated in [("EUR", None), ("USD", Decimal("1.1")), ("QQQ", None)]:
        _eur, source = await fx.eur_total(db_session, Decimal("10"), ccy, _ON, stated)
        assert source in {"eur", "stated", "ecb", "unknown"}


# --------------------------------------------------------------------------- #
# X — No mixed-currency aggregation (reports are single-currency by construction)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_issued_report_never_sums_across_currencies(db_session):
    import json

    from app.models.issued_invoice import IssuedInvoice
    from app.models.organization import Organization
    from app.services import issued_reports

    org = Organization(name="FX Co")
    db_session.add(org)
    await db_session.flush()

    def _inv(number, currency, total):
        return IssuedInvoice(
            org_id=org.id, number=number, issue_date=_ON, currency=currency,
            buyer_name="Buyer", seller_json=json.dumps({"legal_name": "Us"}),
            subtotal=Decimal(total), tax_total=Decimal("0"), total=Decimal(total),
        )

    db_session.add_all([
        _inv("E-1", "EUR", "100.00"),
        _inv("E-2", "EUR", "50.00"),
        _inv("U-1", "USD", "999.00"),  # must NOT be summed into the EUR total
    ])
    await db_session.commit()

    rep = await issued_reports.summary(db_session, org.id, None, None, None)
    # Picks the most-used currency (EUR) and sums ONLY that currency.
    assert rep.currency == "EUR"
    assert rep.gross == Decimal("150.00")           # 100 + 50, NOT + 999
    assert "USD" in rep.available_currencies         # the other currency is surfaced…
    assert "EUR" in rep.available_currencies
    # …but never folded into a single cross-currency total.

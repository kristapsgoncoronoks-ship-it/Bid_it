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


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0.005", "0.01"),  # HALF_UP rounds the exact half up
        ("0.004", "0.00"),
        ("2.675", "2.68"),  # the classic float trap — exact with Decimal
        ("2.674", "2.67"),
        ("-0.005", "-0.01"),  # symmetric HALF_UP away from zero
        ("10", "10.00"),
        ("1.999", "2.00"),
    ],
)
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


def test_q2_fleet_fuel_threshold_smoke():
    # Fleet Fuel harvest (WO-8): EUR-threshold decisions sit exactly at the
    # rounding boundary — 399.994 stays under 400, 399.995 crosses it.
    assert money.q2(Decimal("399.994")) < Decimal("400")
    assert money.q2(Decimal("399.995")) >= Decimal("400")


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
    assert eur == Decimal("25.00")  # 50 / 2
    assert source == "ecb"


@pytest.mark.asyncio
async def test_unknown_currency_never_masks_as_a_real_conversion(db_session):
    eur, source = await fx.eur_total(db_session, Decimal("99.99"), "QQQ", _ON, None)
    assert eur is None
    assert source == "unknown"  # provenance is explicit, never a silent wrong number


@pytest.mark.asyncio
async def test_provenance_source_is_always_a_known_value(db_session):
    for ccy, stated in [("EUR", None), ("USD", Decimal("1.1")), ("QQQ", None)]:
        _eur, source = await fx.eur_total(db_session, Decimal("10"), ccy, _ON, stated)
        assert source in {"eur", "stated", "ecb", "unknown"}


# --------------------------------------------------------------------------- #
# X — No mixed-currency aggregation (reports are single-currency by construction)
# --------------------------------------------------------------------------- #


def test_fi15_no_aggregate_sums_across_currencies_without_conversion():
    """FI-15 (WO-8): an aggregate spanning currencies either returns
    per-currency figures or converts with a recorded rate — NEVER a bare sum of
    the raw amounts. The AP aging summary is the canonical aggregate here."""
    from app.services import ap_aging

    def item(number, ccy, outstanding, status="overdue", bucket="1-30"):
        return ap_aging.WorklistItem(
            id=number,
            invoice_number=number,
            vendor_name="V",
            due_date=_ON,
            currency=ccy,
            total=Decimal(outstanding),
            outstanding=Decimal(outstanding),
            status=status,
            days_overdue=5,
            bucket=bucket,
        )

    items = [
        item("E-1", "EUR", "100.00"),
        item("E-2", "EUR", "50.00"),
        item("S-1", "SEK", "999.00"),
        item("P-1", "PLN", "777.00", status="open", bucket="due_soon"),
    ]
    s = ap_aging.summarize(items)
    raw_mixed_sum = Decimal("100.00") + Decimal("50.00") + Decimal("999.00")
    # Single labelled currency; the amount is that currency's sum ONLY.
    assert s.currency == "EUR"
    assert s.overdue_amount == Decimal("150.00")
    assert s.overdue_amount != raw_mixed_sum
    assert s.due_soon_amount == Decimal("0.00")  # the PLN line is NOT folded in
    # Counts still cover everything (a count is not a money sum)…
    assert s.overdue_count == 3 and s.due_soon_count == 1
    # …and the unfolded currencies are surfaced, never silently dropped.
    assert s.other_currencies == ("PLN", "SEK")


@pytest.mark.asyncio
async def test_invoice_path_and_expense_path_agree(db_session):
    """WO-8 acceptance: the same (amount, currency, date) yields the identical
    EUR Decimal through the invoice path (fx.eur_total) and the expense path
    (expenses.apply_item_fx on an EUR report)."""
    from datetime import date as _date

    from app.models.expense import ExpenseItem
    from app.services import expenses

    matrix = [
        ("108.50", "USD", _date(2026, 6, 5)),
        ("430.00", "PLN", _date(2026, 6, 5)),
        ("59.99", "CZK", _date(2026, 7, 10)),
        ("1000.00", "PLN", _date(2026, 2, 14)),
        ("250.00", "EUR", _date(2026, 6, 5)),
    ]
    for amount, ccy, on in matrix:
        invoice_eur, invoice_src = await fx.eur_total(db_session, Decimal(amount), ccy, on, None)
        item = ExpenseItem(
            spend_date=on,
            description="X",
            currency=ccy,
            original_amount=Decimal(amount),
            amount=Decimal("0"),
        )
        await expenses.apply_item_fx(db_session, item, "EUR")
        assert invoice_eur is not None
        assert item.amount == invoice_eur, (amount, ccy, on)
        assert item.fx_source == invoice_src, (amount, ccy, on)
        if ccy != "EUR":
            assert item.fx_rate is not None  # the conversion rate is RECORDED


@pytest.mark.asyncio
async def test_expense_path_unknown_currency_refuses_never_guesses(db_session):
    """WO-8: `unknown` never masks as a conversion — an entry that cannot be
    converted is refused at write (422, stable code), not stored at a guessed
    or silently-zero amount."""
    from datetime import date as _date

    from app.core.errors import AppError
    from app.models.expense import ExpenseItem
    from app.services import expenses

    item = ExpenseItem(
        spend_date=_date(2026, 6, 5),
        description="X",
        currency="QQQ",  # no rate cached, ever
        original_amount=Decimal("100.00"),
        amount=Decimal("0"),
    )
    with pytest.raises(AppError) as exc:
        await expenses.apply_item_fx(db_session, item, "EUR")
    assert exc.value.code == "fx_rate_unavailable"
    assert exc.value.status == 422


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
            org_id=org.id,
            number=number,
            issue_date=_ON,
            currency=currency,
            buyer_name="Buyer",
            seller_json=json.dumps({"legal_name": "Us"}),
            subtotal=Decimal(total),
            tax_total=Decimal("0"),
            total=Decimal(total),
        )

    db_session.add_all(
        [
            _inv("E-1", "EUR", "100.00"),
            _inv("E-2", "EUR", "50.00"),
            _inv("U-1", "USD", "999.00"),  # must NOT be summed into the EUR total
        ]
    )
    await db_session.commit()

    rep = await issued_reports.summary(db_session, org.id, None, None, None)
    # Picks the most-used currency (EUR) and sums ONLY that currency.
    assert rep.currency == "EUR"
    assert rep.gross == Decimal("150.00")  # 100 + 50, NOT + 999
    assert "USD" in rep.available_currencies  # the other currency is surfaced…
    assert "EUR" in rep.available_currencies
    # …but never folded into a single cross-currency total.


async def _org_vendor(db_session, org_name="FX Co", vendor_name="V"):
    from app.models.organization import Organization
    from app.models.vendor import Vendor

    org = Organization(name=org_name)
    db_session.add(org)
    await db_session.flush()
    vendor = Vendor(org_id=org.id, name=vendor_name)
    db_session.add(vendor)
    await db_session.flush()
    return org, vendor


@pytest.mark.asyncio
async def test_fi10_analytics_never_sums_across_currencies(db_session):
    """C1.7/WO-24: same shape as
    `test_issued_report_never_sums_across_currencies`, generalised to the AP
    side (`analytics.summary`) — a EUR+USD tenant's total is 150.00, never
    1149.00."""
    from app.models.invoice import Invoice
    from app.services import analytics

    org, vendor = await _org_vendor(db_session)

    def _inv(number, currency, total):
        return Invoice(
            org_id=org.id,
            vendor_id=vendor.id,
            invoice_number=number,
            issue_date=_ON,
            currency=currency,
            subtotal=Decimal(total),
            tax_amount=Decimal("0"),
            total=Decimal(total),
        )

    db_session.add_all(
        [
            _inv("E-1", "EUR", "100.00"),
            _inv("E-2", "EUR", "50.00"),
            _inv("U-1", "USD", "999.00"),  # must NOT be summed into the EUR total
        ]
    )
    await db_session.commit()

    rep = await analytics.summary(db_session, org.id, None, None, None)
    assert rep.currency == "EUR"
    assert rep.total_spend == Decimal("150.00")  # 100 + 50, NOT + 1149.00
    assert set(rep.available_currencies) == {"EUR", "USD"}


@pytest.mark.asyncio
async def test_fi10_benchmark_never_sums_across_currencies(db_session):
    """Same shape, over `benchmark.combined_benchmark` — a category's "cheapest
    vendor" verdict must never blend unit prices from different currencies."""
    from app.models.invoice import Invoice, LineItem
    from app.services import benchmark

    org, vendor = await _org_vendor(db_session)

    def _inv_with_line(number, currency, amount, qty):
        inv = Invoice(
            org_id=org.id,
            vendor_id=vendor.id,
            invoice_number=number,
            issue_date=_ON,
            currency=currency,
            subtotal=Decimal(amount),
            tax_amount=Decimal("0"),
            total=Decimal(amount),
        )
        db_session.add(inv)
        return inv

    eur1 = _inv_with_line("E-1", "EUR", "20.00", "10")
    eur2 = _inv_with_line("E-2", "EUR", "10.00", "10")
    usd1 = _inv_with_line("U-1", "USD", "9990.00", "1")  # would dominate any blended avg
    await db_session.flush()
    db_session.add_all(
        [
            LineItem(
                invoice_id=eur1.id,
                description="fuel",
                category="fuel",
                quantity="10",
                amount="20.00",
            ),
            LineItem(
                invoice_id=eur2.id,
                description="fuel",
                category="fuel",
                quantity="10",
                amount="10.00",
            ),
            LineItem(
                invoice_id=usd1.id,
                description="fuel",
                category="fuel",
                quantity="1",
                amount="9990.00",
            ),
        ]
    )
    await db_session.commit()

    rep = await benchmark.combined_benchmark(db_session, org.id, None, None)
    assert rep.summary.currency == "EUR"
    assert rep.summary.total_spend == Decimal("30.00")  # 20 + 10, NOT + 9990.00
    assert "USD" in rep.summary.available_currencies


@pytest.mark.asyncio
async def test_fi10_budget_never_guesses_unknown_fx(db_session):
    """Same shape, over `budget.overview` — an invoice in a currency with no
    recorded conversion is EXCLUDED, never guessed at a 1:1 parity (§4.15)."""
    from app.models.invoice import Invoice, LineItem
    from app.services import budget

    org, vendor = await _org_vendor(db_session)

    eur = Invoice(
        org_id=org.id,
        vendor_id=vendor.id,
        invoice_number="E-1",
        issue_date=_ON,
        currency="EUR",
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("0"),
        total=Decimal("100.00"),
        total_eur=Decimal("100.00"),
        fx_source="eur",
    )
    # "QQQ" has no cached rate anywhere (same as the expense-path fixture
    # above) — fx_source is "unknown", total_eur stays NULL.
    unknown = Invoice(
        org_id=org.id,
        vendor_id=vendor.id,
        invoice_number="Q-1",
        issue_date=_ON,
        currency="QQQ",
        subtotal=Decimal("500.00"),
        tax_amount=Decimal("0"),
        total=Decimal("500.00"),
        total_eur=None,
        fx_source="unknown",
    )
    db_session.add_all([eur, unknown])
    await db_session.flush()
    db_session.add_all(
        [
            LineItem(
                invoice_id=eur.id,
                description="x",
                category="groceries",
                quantity="1",
                amount="100.00",
            ),
            LineItem(
                invoice_id=unknown.id,
                description="x",
                category="groceries",
                quantity="1",
                amount="500.00",
            ),
        ]
    )
    await db_session.commit()

    ov = await budget.overview(db_session, org.id, _ON.year, _ON.month)
    # The old fallback would have counted the 500 QQQ as if it were 500 EUR,
    # giving 600.00. Correct: the unknown-FX invoice is excluded.
    assert ov["total_actual"] == "100.00"
    assert ov["excluded_unconverted"] == 1

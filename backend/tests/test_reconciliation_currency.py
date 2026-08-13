"""A bank line in another currency may not settle a base-currency record.

Every reconcilable target — receipts, direct issued payments, reimbursement
batches, payment runs — is denominated in the organisation's base currency; the
last two are literally stored as `total_eur`. So only the *magnitude* of a
foreign line would ever match, which is precisely how a 500.00 USD credit comes
to settle a 500.00 EUR receivable.

The camt parser reads `Amt/@Ccy`, but `bank_lines` had nowhere to keep it, so
the currency was discarded between parsing and matching.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.bank_import import BankLine
from app.services import reconciliation


def _line(**kw) -> BankLine:
    base = dict(
        org_id="org",
        statement_id="stmt",
        line_date=date(2026, 3, 2),
        description="Payment",
        amount=Decimal("500.00"),
        direction="credit",
        currency=None,
        status="unmatched",
    )
    base.update(kw)
    return BankLine(**base)


# --- what counts as foreign ----------------------------------------------------


def test_a_stated_foreign_currency_is_foreign():
    assert reconciliation.foreign_currency_of(_line(currency="USD")) == "USD"


def test_the_base_currency_is_not_foreign():
    assert reconciliation.foreign_currency_of(_line(currency="EUR")) is None


def test_an_unstated_currency_is_not_treated_as_foreign():
    """A CSV or PDF statement states no currency. Reading that silence as
    "foreign" would block every such statement ever imported — the fix would
    have broken more than the defect did."""
    assert reconciliation.foreign_currency_of(_line(currency=None)) is None


def test_case_and_padding_do_not_smuggle_a_currency_past_the_check():
    assert reconciliation.foreign_currency_of(_line(currency=" usd ")) == "USD"
    assert reconciliation.foreign_currency_of(_line(currency=" eur ")) is None


# --- the gate ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_foreign_line_is_offered_no_candidates(db_session):
    """Not a ranked list the user might accept anyway — none.

    A receipt that WOULD match on amount and date is planted first. Without it
    this test passes against the unfixed code purely because an empty database
    has nothing to suggest — the assertion would be about the fixture, not the
    gate. With it, the unfixed code returns exactly the wrong candidate: a
    500.00 EUR receipt offered to settle a 500.00 USD credit.
    """
    from app.models.receipt import Receipt

    db_session.add(
        Receipt(
            org_id="org",
            amount=Decimal("500.00"),
            received_on=date(2026, 3, 2),
            method="bank_transfer",
            reference="REF-1",
        )
    )
    await db_session.flush()

    # The same line in the base currency finds it — proving the planted receipt
    # really is a candidate, so the empty result below is the gate and not luck.
    assert await reconciliation.suggest_matches(db_session, "org", _line(currency="EUR"))
    assert await reconciliation.suggest_matches(db_session, "org", _line(currency="USD")) == []


@pytest.mark.asyncio
async def test_confirming_a_foreign_line_is_refused(db_session):
    """Confirm takes a caller-supplied target id and is reachable without ever
    asking for a suggestion, so filtering the suggestion list alone would be
    decoration."""
    with pytest.raises(reconciliation.ReconError) as e:
        await reconciliation.confirm_match(
            db_session, "org", _line(currency="USD"), "receipt", "some-receipt-id"
        )
    assert "USD" in str(e.value)


@pytest.mark.asyncio
async def test_the_refusal_names_the_currency_and_what_to_do(db_session):
    """An error a user cannot act on sends them to support."""
    with pytest.raises(reconciliation.ReconError) as e:
        await reconciliation.confirm_match(
            db_session, "org", _line(currency="GBP"), "receipt", "x"
        )
    msg = str(e.value)
    assert "GBP" in msg and "EUR" in msg
    assert "separately" in msg or "convert" in msg

"""camt.053 is a statement of what the BANK says happened. Four things it says
were being read wrong, each turning into money the account does not have.

Every test here was written against a parser that failed it. On a five-entry
statement the old reader reported 3,577.00 credited where the truthful EUR
figure is 300.00 — a reversal booked as a second payment, a USD entry summed as
euros, a pending entry treated as settled, and a batch of two payments collapsed
into one unmatchable line.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import bank_statement as bs

_NS = 'xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"'


def _stmt(entries: str) -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f"<Document {_NS}><BkToCstmrStmt><Stmt>{entries}</Stmt></BkToCstmrStmt></Document>"
    ).encode()


def _entry(
    amount: str,
    *,
    ccy: str = "EUR",
    ind: str = "CRDT",
    day: str = "2026-03-02",
    sts: str = "BOOK",
    reversal: bool = False,
    details: str = "<TxDtls><RmtInf><Ustrd>INV-1</Ustrd></RmtInf></TxDtls>",
) -> str:
    rvsl = "<RvslInd>true</RvslInd>" if reversal else ""
    return (
        f'<Ntry><Amt Ccy="{ccy}">{amount}</Amt><CdtDbtInd>{ind}</CdtDbtInd>'
        f"<Sts>{sts}</Sts>{rvsl}<BookgDt><Dt>{day}</Dt></BookgDt>"
        f"<NtryDtls>{details}</NtryDtls></Ntry>"
    )


def _parse(entries: str) -> bs.StatementResult:
    return bs.parse("statement.xml", _stmt(entries))


# --- reversals -----------------------------------------------------------------


def test_a_reversed_credit_is_money_going_out():
    """The worst of the four. A credit and its reversal both read as incoming
    payments, so the account appears to have received twice what it did — and
    reconciliation can settle an invoice against money already clawed back."""
    r = _parse(_entry("1000.00") + _entry("1000.00", reversal=True, day="2026-03-03"))

    assert [t.direction for t in r.transactions] == ["credit", "debit"]
    net = sum(t.amount if t.direction == "credit" else -t.amount for t in r.transactions)
    assert net == Decimal("0.00"), "a booking and its reversal must cancel"


def test_a_reversed_debit_is_money_coming_back():
    """Reversal flips whichever way the original went — not 'always a debit'."""
    r = _parse(_entry("50.00", ind="DBIT", reversal=True))
    assert [t.direction for t in r.transactions] == ["credit"]


def test_the_reversal_is_called_out_in_the_warnings():
    r = _parse(_entry("10.00", reversal=True))
    assert any("revers" in w.lower() for w in r.warnings)


# --- currency ------------------------------------------------------------------


def test_the_stated_currency_is_kept():
    """`Amt/@Ccy` was dropped, so a USD credit became a bare number that the
    rest of the system was free to read as euros."""
    r = _parse(_entry("500.00", ccy="USD"))
    assert r.transactions[0].currency == "USD"


def test_a_mixed_currency_statement_says_so():
    r = _parse(_entry("100.00") + _entry("100.00", ccy="USD", day="2026-03-03"))
    assert {t.currency for t in r.transactions} == {"EUR", "USD"}
    assert any("mixes currencies" in w for w in r.warnings)


def test_currency_is_never_invented_where_the_source_is_silent():
    """A CSV states no currency. None must stay None rather than become the
    organisation's currency by assumption — §4.15, refuse rather than guess."""
    csv = b"date,description,amount\n2026-03-02,Payment,100.00\n"
    r = bs.parse("statement.csv", csv)
    assert r.transactions[0].currency is None


# --- pending -------------------------------------------------------------------


def test_a_pending_entry_is_not_cash():
    """PDNG is money the bank has not settled; it may still fail."""
    r = _parse(_entry("100.00") + _entry("777.00", sts="PDNG", day="2026-03-05"))
    assert [t.amount for t in r.transactions] == [Decimal("100.00")]
    assert any("pending" in w.lower() for w in r.warnings)
    assert "1 entry was" in " ".join(r.warnings), "singular reads as a sentence"


def test_a_missing_status_still_counts_as_booked():
    """camt.053 reports booked entries, so absence means booked. Treating a
    silent `Sts` as unsettled would empty most real statements."""
    entry = (
        '<Ntry><Amt Ccy="EUR">100.00</Amt><CdtDbtInd>CRDT</CdtDbtInd>'
        "<BookgDt><Dt>2026-03-02</Dt></BookgDt></Ntry>"
    )
    assert len(_parse(entry).transactions) == 1


def test_the_nested_status_form_is_understood():
    """`<Sts><Cd>PDNG</Cd></Sts>` from camt.053.001.08 onward, where the bare
    code of .001.02 became a nested element. A booked entry rides along so this
    tests the STATUS reading rather than the empty-statement error."""
    nested_pending = (
        '<Ntry><Amt Ccy="EUR">100.00</Amt><CdtDbtInd>CRDT</CdtDbtInd>'
        "<Sts><Cd>PDNG</Cd></Sts><BookgDt><Dt>2026-03-02</Dt></BookgDt></Ntry>"
    )
    r = _parse(_entry("42.00", day="2026-03-01") + nested_pending)
    assert [t.amount for t in r.transactions] == [Decimal("42.00")]


# --- batched entries -----------------------------------------------------------

_TWO = (
    '<TxDtls><Amt Ccy="EUR">100.00</Amt><RmtInf><Ustrd>INV-A</Ustrd></RmtInf></TxDtls>'
    '<TxDtls><Amt Ccy="EUR">200.00</Amt><RmtInf><Ustrd>INV-B</Ustrd></RmtInf></TxDtls>'
)


def test_a_priced_batch_becomes_one_line_per_payment():
    """A bulk collection covering many invoices arrived as a single figure with
    every reference crammed into its description — unmatchable by construction."""
    r = _parse(_entry("300.00", details=_TWO))
    assert [(t.amount, t.description) for t in r.transactions] == [
        (Decimal("100.00"), "INV-A"),
        (Decimal("200.00"), "INV-B"),
    ]


def test_a_batch_that_does_not_add_up_is_left_whole():
    """Splitting a batch whose parts miss the total would invent a breakdown the
    bank never gave. Refuse, keep the entry, and say why."""
    r = _parse(_entry("999.00", details=_TWO))  # 100 + 200 != 999
    assert [t.amount for t in r.transactions] == [Decimal("999.00")]
    assert any("did not price" in w for w in r.warnings)


def test_an_unpriced_batch_is_left_whole():
    unpriced = (
        "<TxDtls><RmtInf><Ustrd>INV-A</Ustrd></RmtInf></TxDtls>"
        "<TxDtls><RmtInf><Ustrd>INV-B</Ustrd></RmtInf></TxDtls>"
    )
    r = _parse(_entry("300.00", details=unpriced))
    assert [t.amount for t in r.transactions] == [Decimal("300.00")]


def test_a_split_batch_inherits_the_entry_direction():
    r = _parse(_entry("300.00", ind="DBIT", details=_TWO))
    assert {t.direction for t in r.transactions} == {"debit"}


# --- the whole statement, end to end -------------------------------------------


def test_the_five_entry_statement_reports_what_the_bank_says():
    """The regression case, whole. Old reader: 3,577.00 credited. Truth: 300.00
    in EUR (a payment and its reversal cancel; the batch splits) and 500.00 USD,
    which is a different unit and is not added to it."""
    r = _parse(
        _entry("1000.00")
        + _entry("1000.00", reversal=True, day="2026-03-03")
        + _entry("500.00", ccy="USD", day="2026-03-04")
        + _entry("777.00", sts="PDNG", day="2026-03-05")
        + _entry("300.00", day="2026-03-06", details=_TWO)
    )
    eur = sum(
        t.amount if t.direction == "credit" else -t.amount
        for t in r.transactions
        if t.currency == "EUR"
    )
    usd = sum(t.amount for t in r.transactions if t.currency == "USD")
    assert eur == Decimal("300.00")
    assert usd == Decimal("500.00")


def test_a_statement_of_only_pending_entries_is_an_error_not_an_empty_success():
    """Silently importing nothing would read as 'this statement had no activity'."""
    with pytest.raises(ValueError):
        _parse(_entry("100.00", sts="PDNG"))

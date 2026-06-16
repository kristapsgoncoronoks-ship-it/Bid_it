"""Regression guard for the ONE real deterministic PDF parser, parse_eurowag.

The parser is dense, locale-specific regex over pdftotext output; without a fixture test a
refactor could silently break the only supplier that extracts for free/offline. These
fixtures are synthetic but shaped exactly like the Eurowag coversheet + country-invoice
text the regexes target (totals are NET EUR/L basis, VAT excluded)."""
import extract as EX


COVER = (
    "W.A.G. payment services a.s.\n"
    "SUMMARY OVERVIEW\n"
    "Total payment 1234567 EUR\n"
    "Buyer\n"
    "  Demo Transport SIA\n"
)

DE_INVOICE = (
    "W.A.G. payment services a.s.\n"
    "Document Number DE1234567890\n"
    "Izpildes valsts / Deutschland\n"
    "PVN specifikācija\n"
    "Total  1 000,00  190,00  1 190,00  EUR\n"
)


def test_parse_eurowag_happy_path():
    d = EX.parse_eurowag([("COVER.pdf", COVER), ("DE_invoice.pdf", DE_INVOICE)])
    assert d is not None
    assert d["supplier"] == "EUROWAG"
    assert d["backend"] == "parser" and d["confidence"] == "medium"
    assert d["statement_ref"] == "1234567"
    assert d["customer"] == "Demo Transport SIA"
    assert len(d["lines"]) == 1
    ln = d["lines"][0]
    assert ln["invoice_no"] == "DE1234567890"
    assert ln["country"] == "Germany"            # Deutschland -> normalized
    assert ln["net"] == 1000.0 and ln["vat"] == 190.0
    assert ln["currency"] == "EUR" and ln["_source"] == "DE_invoice.pdf"


def test_parse_eurowag_per_rate_fallback():
    # no "Total ..." summary line -> the parser sums the per-rate spec rows instead
    inv = ("Eurowag\nDocument Number PL1234567890\n"
           "Izpildes valsts / Polska\n"
           "\n 23  2 000,00  460,00  2 460,00  EUR\n")
    d = EX.parse_eurowag([("PL_invoice.pdf", inv)])
    assert d is not None and len(d["lines"]) == 1
    ln = d["lines"][0]
    assert ln["country"] == "Poland"
    assert ln["net"] == 2000.0 and ln["vat"] == 460.0


def test_parse_eurowag_returns_none_for_other_supplier():
    assert EX.parse_eurowag([("bp.pdf", "BP plc statement\nDocument Number XX0000000001")]) is None


def test_parse_eurowag_amount_locale_parsing():
    # money helper handles NBSP / space / dot thousands separators uniformly
    assert EX._num("7 059,83") == 7059.83
    assert EX._num("1.776,96") == 1776.96
    assert EX._num("1 000,00") == 1000.0

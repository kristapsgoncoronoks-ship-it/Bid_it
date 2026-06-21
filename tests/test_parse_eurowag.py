"""Regression guard for the ONE real deterministic PDF parser, parse_eurowag.

The parser is dense, locale-specific regex over pdftotext output; without a fixture test a
refactor could silently break the only supplier that extracts for free/offline. These
fixtures are synthetic but shaped exactly like the Eurowag coversheet + country-invoice
text the regexes target (totals are NET EUR/L basis, VAT excluded)."""
import glob
import os

import pytest

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


# ── SELLER read off the real Belgium sample PDF (the local issuing entity per country) ──
_BE_SAMPLE = sorted(glob.glob("samples/documents/*EUROWAG*BE3026001012765*"))


@pytest.mark.skipif(not _BE_SAMPLE, reason="Belgium Eurowag sample PDF not present")
def test_parse_eurowag_reads_seller_entity_off_invoice():
    # The captured legal entity must be the LOCAL Belgian issuing entity printed in the footer
    # (W.A.G. payment solutions BE BVBA / BE0648861506) — NOT the Czech factoring entity — with
    # its address, registration number, country and the invoice issue date.
    text = EX.pdf_text(open(_BE_SAMPLE[0], "rb").read())
    d = EX.parse_eurowag([(os.path.basename(_BE_SAMPLE[0]), text)])
    assert d is not None and d["supplier"] == "EUROWAG"
    assert d["supplier_legal_name"] == "W.A.G. payment solutions BE BVBA"
    assert d["supplier_vat"] == "BE0648861506"
    assert d["supplier_reg_no"] == "0648861506"
    assert "Sint-Gillis" in d["supplier_address"]
    assert d["supplier_country"] == "Belgium"
    assert d["statement_date"] == "2026-05-31"
    assert "Issuing Services" not in (d["supplier_legal_name"] or "")   # not the CZ entity


def test_eurowag_seller_helper_handles_all_locales():
    # name ends at the legal form; VAT/reg parsed across the differing footer labels
    be = ("Pardevejs / Verkoper: W.A.G. payment solutions BE BVBA, South center Titanium, "
          "Sint-Gillis, Brussel, Uzņēmuma ID / Id. Nummer: 0648861506, "
          "PVN reg. Nr. / BTW-nummer: BE0648861506")
    s = EX._eurowag_seller(be)
    assert s["name"] == "W.A.G. payment solutions BE BVBA"
    assert s["vat"] == "BE0648861506" and s["reg_no"] == "0648861506"
    de = ("Pardevejs / Verkäufer: W.A.G. payment solutions, a.s., Na Vítězné pláni 1719/4, "
          "Praha 4, Uzņēmuma ID / IDENT.-NR.: 26415623, PVN reg. Nr. / STEUER-ID.: DE256110720")
    s = EX._eurowag_seller(de)
    assert s["name"] == "W.A.G. payment solutions, a.s." and s["vat"] == "DE256110720"

"""The GENERIC, on-prem, deterministic last-resort header heuristic
(`_generic_text_draft`): a plain PDF that no per-supplier parser recognises and that no
AI backend processes must come back as a PREFILLED draft (document no / date / currency /
VAT-id hint + total/VAT recorded as TEXT HINTS), not a blank form — while staying byte-
identical to before on garbage text, and NEVER overriding a real parser."""
import extract as EX


# OCR-like text from an unrecognised invoice layout.
_OCR_TEXT = (
    "ACME FUELS GmbH\n"
    "Rechnung Nr: RE-2026-00457\n"
    "Rechnungsdatum: 2026-05-15\n"
    "VAT ID: DE123456789\n"
    "Currency: EUR\n"
    "Lieferung Diesel ...\n"
    "VAT: 234.00\n"
    "Total: 1,234.56 EUR\n"
)


def test_generic_fills_header_and_hints():
    d = EX._generic_text_draft([("scan.pdf", _OCR_TEXT)])
    assert d is not None
    assert d["statement_ref"] == "RE-2026-00457"
    assert d["statement_date"] == "2026-05-15"
    assert d["currency"] == "EUR"
    assert d["supplier"] == "DE123456789"          # VAT-id hint (no name resolved)
    assert d["backend"] == "generic"
    assert d["confidence"] == "low"
    # totals/VAT are TEXT HINTS only — never fabricated claim lines
    assert d["lines"] == []
    assert "1,234.56" in d["notes"]               # printed total surfaced as a HINT
    assert "HINT" in d["notes"]


# A buyer-first bilingual layout (FR/LV, like an E100 fuel invoice): the BUYER block
# (Client / Pircējs) and its VAT-id are printed BEFORE the SELLER block (Vendeur /
# Pārdevējs). The heuristic must capture the SELLER, not the customer.
_BUYER_FIRST = (
    "Client / Pircējs\n"
    'SIA "Iecavnieks Auto"\n'
    "Iecavnieki, Iecavas novads, LV-3913, Latvija\n"
    "TVA-NR / PVN: LV43603043473\n"
    "Facture / Rēķins Nr  BE95489/5413791\n"
    "Vendeur / Pārdevējs\n"
    "E100 International Trade sp. z o.o.\n"
    "ul. Pory 78/7, 02-757 Warszawa\n"
    "TVA-NR / PVN: BE0676647155\n"
    "Date / Datums: 2026-03-31\n"
)


def test_generic_picks_seller_not_buyer():
    name, vat = EX._seller_identity(_BUYER_FIRST)
    assert vat == "BE0676647155"                   # the SELLER's VAT, NOT the buyer's LV id
    assert name == "E100 International Trade sp. z o.o."
    d = EX._generic_text_draft([("e100.pdf", _BUYER_FIRST)])
    assert d["supplier_vat"] == "BE0676647155"
    assert d["supplier"] == "E100 International Trade sp. z o.o."
    assert d["statement_ref"] == "BE95489/5413791"
    # the buyer's VAT must not leak into the supplier identity
    assert "LV43603043473" not in (d["supplier"] or "")


def test_seller_identity_no_headers_keeps_first_vat():
    # no party headers -> unchanged behaviour: first VAT-id, no name resolved
    name, vat = EX._seller_identity("Some text\nVAT ID: DE123456789\nMore text\n")
    assert (name, vat) == (None, "DE123456789")


def test_generic_detects_non_eur_currency():
    txt = "Faktura nr 778/2026\nData: 12.04.2026\nRazem: 5 000,00 PLN\n"
    d = EX._generic_text_draft([("inv.pdf", txt)])
    assert d is not None
    assert d["currency"] == "PLN"
    assert d["statement_ref"] == "778/2026"
    assert d["statement_date"] == "12.04.2026"


def test_generic_returns_none_on_garbage():
    # essentially-empty / unusable text -> None (behaviour unchanged: manual entry)
    assert EX._generic_text_draft([("x.pdf", "")]) is None
    assert EX._generic_text_draft([("x.pdf", "   \n  ")]) is None
    assert EX._generic_text_draft([("x.pdf", "@@@ ### %%% ^^^ &&& *** !!!")]) is None


def test_generic_never_raises_returns_none(monkeypatch):
    # any internal error is swallowed (logged) and yields None, not a crash.
    monkeypatch.setattr(EX, "_REF_RE", None)       # force an AttributeError inside
    assert EX._generic_text_draft([("x.pdf", _OCR_TEXT)]) is None


# ---------------------------------------------------------------- wiring into _plain_draft
def test_plain_draft_uses_generic_when_unrecognised():
    # no parser match, backend 'parser' (no AI) -> generic prefill, not a blank form.
    files = [("scan.pdf", b"%PDF")]
    d = EX._plain_draft([("scan.pdf", _OCR_TEXT)], files, "parser", "scan.pdf", False)
    assert d["backend"] == "generic"
    assert d["confidence"] == "low"
    assert d["statement_ref"] == "RE-2026-00457"
    assert d["lines"] == []
    assert d["_pdf_bytes"] == files


def test_plain_draft_garbage_still_empty():
    # garbage text -> generic returns None -> the historical empty() fallback is used.
    files = [("blank.pdf", b"%PDF")]
    d = EX._plain_draft([("blank.pdf", "@@@ ### %%%")], files, "parser", "blank.pdf", False)
    assert d["backend"] == "none"
    assert d["statement_ref"] is None
    assert d["lines"] == []
    assert "enter manually" in d["notes"]


def test_plain_draft_manual_mode_stays_blank():
    # backend 'none' is explicit manual entry — the generic heuristic must NOT run.
    files = [("scan.pdf", b"%PDF")]
    d = EX._plain_draft([("scan.pdf", _OCR_TEXT)], files, "none", "scan.pdf", False)
    assert d["backend"] == "none"
    assert d["statement_ref"] is None


def test_real_parser_wins_over_generic():
    # a text an EXISTING parser recognises (Eurowag) must use that parser, not generic.
    text = ("W.A.G. payment solutions\n"
            "Total payment 0261167596\n"
            "Buyer\n SIA Test\n")
    files = [("cover.pdf", b"%PDF")]
    d = EX._plain_draft([("COVER.pdf", text)], files, "auto", "cover.pdf", False)
    assert d["backend"] == "parser"
    assert d["supplier"] == "EUROWAG"


def test_generic_draft_keeps_ocr_flag():
    # ocr_used must still flag the generic draft (note + low confidence).
    files = [("scan.pdf", b"%PDF")]
    d = EX._plain_draft([("scan.pdf", _OCR_TEXT)], files, "parser", "scan.pdf",
                        False, ocr_used=True)
    assert d["backend"] == "generic"
    assert d.get("ocr") is True
    assert "OCR" in d["notes"]
    assert d["confidence"] == "low"

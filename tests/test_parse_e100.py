"""Regression guard for the deterministic E100 International Trade parser, parse_e100.

E100 issues ONE country-specific invoice: page 1 is a per-PRODUCT summary with explicit NET
("Montant hors TVA") and VAT columns; the following pages are a per-transaction annexe whose
station codes carry the supply country (BE167 -> Belgium). The fixture is synthetic but shaped
exactly like the pdftotext output the regexes target (amounts are NET EUR, VAT excluded)."""
import extract as EX
import validate as VAL


# A trimmed-but-faithful E100 invoice: header (buyer THEN seller), the page-1 product summary,
# the "Total / Kopā" line (with space thousands separators), and a few annexe rows for the
# station-prefix country derivation.
E100_TEXT = (
    "BE\n"
    "Client / Pircējs\n"
    'SIA "Iecavnieks Auto"\n'
    "TVA-NR / PVN: LV43603043473\n"
    "Devise du document / Valūta: EUR\n"
    "Facture / Rēķins Nr  BE95489/5413791\n"
    "Vendeur / Pārdevējs\n"
    "E100 International Trade sp. z o.o.\n"
    "2026-03-31 - Date / Datums\n"
    "2026-04-30 - Date de rėglement / Apmaksas termiņš\n"
    "ul. Pory 78/7, 02-757 Warszawa\n"
    "TVA-NR / PVN: BE0676647155\n"
    "1 AdBlue 41 l 1233.99 0.915161 145.46 0.658911 813.09 21.00 170.75 983.84\n"
    "2 Diesel euro 27 l 7781.03 2.105928 1506.78 1.580397 12297.12 21.00 2582.39 14879.51\n"
    "Total / Kopā 1 652.24 13 110.21 2753.14 15863.35\n"
    "7005230001192790089 |  FE373\n"
    "2026-03-17 17:42 BE167 Meer, Amsterdamstraat, 8, black AdBlue 41 l 64.00 0.8932 "
    "0.3300 21.12 0.4655 21.00 6.25 36.04\n"
    "2026-03-17 17:52 BE1042 Meer, Amsterdamstraat, 66, orange Diesel euro 27 l 283.39 "
    "2.0190 0.2100 59.51 1.4950 21.00 88.97 512.65\n"
)


def test_parse_e100_happy_path():
    d = EX.parse_e100([("BE95489.pdf", E100_TEXT)])
    assert d is not None
    assert d["supplier"] == "E100"
    assert d["supplier_vat"] == "BE0676647155"      # the SELLER's VAT, not the buyer's LV id
    assert d["backend"] == "parser" and d["confidence"] == "medium"
    assert d["statement_ref"] == "BE95489/5413791"
    assert d["statement_date"] == "2026-03-31"
    assert d["currency"] == "EUR"
    assert d["customer"] == 'SIA "Iecavnieks Auto"'
    # ONE claim line PER PRODUCT (line-by-line fuel detail), each with its own net/VAT
    assert len(d["lines"]) == 2
    by_prod = {ln["product"]: ln for ln in d["lines"]}
    adblue = by_prod["AdBlue (41)"]
    diesel = by_prod["Diesel euro (27)"]
    assert adblue["net"] == 813.09 and adblue["vat"] == 170.75
    assert diesel["net"] == 12297.12 and diesel["vat"] == 2582.39
    # every line carries the real invoice number as the identifiable prefix, country derived
    assert adblue["invoice_no"] == "BE95489/5413791 #41"
    assert diesel["invoice_no"] == "BE95489/5413791 #27"
    assert all(ln["country"] == "Belgium" for ln in d["lines"])
    # the line sum still ties out to the document total
    assert round(sum(ln["net"] + ln["vat"] for ln in d["lines"]), 2) == 15863.35


def test_parse_e100_ties_out_to_document_total():
    # the parser asserts the stated gross; the line sum must equal it (HARD tie-out gate)
    d = EX.parse_e100([("BE95489.pdf", E100_TEXT)])
    assert d["coversheet_total"] == 15863.35
    vr = VAL.validate_batch(d["lines"], coversheet_total=d["coversheet_total"])
    assert vr["tie"]["ok"] is True and vr["tie"]["diff"] == 0.0
    assert vr["can_commit"] is True and vr["errors"] == 0


def test_parse_e100_not_e100_returns_none():
    # a non-E100 document must be declined so the registry falls through to AI/generic
    assert EX.parse_e100([("x.pdf", "Some other supplier invoice\nTotal 100.00\n")]) is None


def test_parse_e100_registered_via_parser_backend():
    # wired into the registry: backend='parser' selects parse_e100 over the generic heuristic
    d = EX._plain_draft([("BE95489.pdf", E100_TEXT)], [("BE95489.pdf", b"%PDF")],
                        "parser", "BE95489.pdf", False)
    assert d["backend"] == "parser"
    assert d["supplier"] == "E100"
    assert d["lines"][0]["country"] == "Belgium"


def test_e100_parser_runs_before_vision_capture(monkeypatch):
    # DETERMINISTIC-FIRST: a recognised supplier must use its parser even when AI vision
    # capture is ENABLED — the vision model must never be reached for a known layout.
    import vision_capture
    monkeypatch.setattr(vision_capture, "enabled", lambda: True)

    def _boom(*a, **k):
        raise AssertionError("vision capture must NOT run when a parser matches")
    monkeypatch.setattr(vision_capture, "capture", _boom)
    d = EX._plain_draft([("BE95489.pdf", E100_TEXT)], [("BE95489.pdf", b"%PDF")],
                        "auto", "BE95489.pdf", False)
    assert d["backend"] == "parser" and d["supplier"] == "E100"
    assert d["supplier_vat"] == "BE0676647155"


def test_e100_parser_runs_under_ai_backend():
    # Even with an explicit AI backend selected, the deterministic parser runs FIRST (no AI
    # call is made for a recognised supplier) — so the seller VAT is captured, not the buyer.
    d = EX._plain_draft([("BE95489.pdf", E100_TEXT)], [("BE95489.pdf", b"%PDF")],
                        "claude", "BE95489.pdf", False)
    assert d["backend"] == "parser" and d["supplier_vat"] == "BE0676647155"


def test_parse_e100_carries_product_breakdown():
    d = EX.parse_e100([("BE95489.pdf", E100_TEXT)])
    names = {p["name"]: p for p in d["products"]}
    assert names["AdBlue"]["net"] == 813.09 and names["AdBlue"]["code"] == "41"
    assert names["Diesel euro"]["net"] == 12297.12 and names["Diesel euro"]["vat"] == 2582.39


def test_parse_e100_multi_country_does_not_guess():
    # if the annexe shows >1 station country, the parser must NOT guess a single country
    txt = E100_TEXT.replace(
        "2026-03-17 17:52 BE1042 Meer, Amsterdamstraat, 66, orange Diesel euro 27 l 283.39 "
        "2.0190 0.2100 59.51 1.4950 21.00 88.97 512.65\n",
        "2026-03-17 17:52 PL1042 Warszawa, Diesel euro 27 l 283.39 "
        "2.0190 0.2100 59.51 1.4950 21.00 88.97 512.65\n")
    d = EX.parse_e100([("mixed.pdf", txt)])
    assert d["lines"][0]["country"] is None          # don't guess — operator assigns
    assert d["confidence"] == "low"
    assert "MULTIPLE supply countries" in d["notes"]

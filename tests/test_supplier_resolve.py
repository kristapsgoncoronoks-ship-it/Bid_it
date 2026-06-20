"""Supplier recognition: a captured legal name / brand / VAT resolves to an existing code."""
import app


def test_resolves_legal_name_and_brand():
    # demo suppliers.db has EUROWAG (legal "W.A.G. Issuing Services, a.s.", brand "Eurowag")
    assert app._resolve_supplier_code("W.A.G. Issuing Services a.s.") == "EUROWAG"
    assert app._resolve_supplier_code("Eurowag") == "EUROWAG"
    assert app._resolve_supplier_code("EUROWAG") == "EUROWAG"
    assert app._resolve_supplier_code("DKV Mobility") == "DKV"


def test_unknown_name_returns_none():
    assert app._resolve_supplier_code("Totally New Supplier GmbH") is None
    assert app._resolve_supplier_code("") is None
    assert app._resolve_supplier_code(None, None) is None


def test_vat_shaped_detection():
    # real EU VAT ids (prefix + >=6 digits) are recognised, normalised
    assert app._vat_shaped("LV43603043473") == "LV43603043473"
    assert app._vat_shaped("be 0676647155") == "BE0676647155"
    assert app._vat_shaped("CZ29137291") == "CZ29137291"
    # real legal names / brands are NEVER mistaken for a VAT id
    assert app._vat_shaped("Eurowag") is None
    assert app._vat_shaped("Shell Latvia SIA") is None
    assert app._vat_shaped("ATLAS") is None           # AT prefix but no digit body
    assert app._vat_shaped("XX12345678") is None      # unknown country prefix
    assert app._vat_shaped("") is None
    assert app._vat_shaped(None) is None


def test_vat_in_name_field_resolves_by_registration():
    # demo E100 carries VAT registration BE0676647155 — a captured draft that put the VAT
    # id in the NAME field (no separate VAT) must still resolve to the code.
    assert app._resolve_supplier_code("BE0676647155") == "E100"
    assert app._resolve_supplier_code("BE 0676647155") == "E100"


def test_notice_prefills_resolved_code():
    # a captured draft naming the supplier by legal name should be RECOGNISED and the
    # draft's supplier mutated to the matched code (so the form + confirm use it).
    draft = {"supplier": "W.A.G. Issuing Services a.s.", "supplier_vat": "",
             "statement_ref": "INV1", "statement_date": "2026-05-31"}
    with app.app.test_request_context("/extract"):
        html = app._read_first_notice(draft, "2026-05")
    assert "recognised" in html.lower()
    assert draft["supplier"] == "EUROWAG", "the resolved code must prefill the draft"

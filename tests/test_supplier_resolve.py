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


def test_notice_prefills_resolved_code():
    # a captured draft naming the supplier by legal name should be RECOGNISED and the
    # draft's supplier mutated to the matched code (so the form + confirm use it).
    draft = {"supplier": "W.A.G. Issuing Services a.s.", "supplier_vat": "",
             "statement_ref": "INV1", "statement_date": "2026-05-31"}
    with app.app.test_request_context("/extract"):
        html = app._read_first_notice(draft, "2026-05")
    assert "recognised" in html.lower()
    assert draft["supplier"] == "EUROWAG", "the resolved code must prefill the draft"

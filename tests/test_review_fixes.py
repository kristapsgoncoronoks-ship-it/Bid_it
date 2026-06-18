"""Regression tests for the code-review fixes: e-invoice tie-out basis, autofile tie-out
enforcement, and capture-VAT-overwrite protection."""
import supplier_master as SM


def test_autofile_enforces_tieout(monkeypatch):
    """autopilot.autofile must REFUSE (status 'ready') a draft whose line sum != the
    coversheet_total, like the human-confirm and bulk paths."""
    import autopilot
    # a clean, high-confidence-looking draft but with a total that does NOT tie out
    draft = {"supplier": "TEST", "statement_ref": "INV1", "confidence": "high",
             "statement_date": "2026-05-31",
             "lines": [{"invoice_no": "INV1", "date": "2026-05-02", "country": "Germany",
                        "currency": "EUR", "net": 100.0, "vat": 19.0}],
             "coversheet_total": 200.0}  # line sum gross = 119, total says 200 -> mismatch
    st, info = autopilot.autofile(None, {"id": 1}, draft, actor="test")
    assert st == "ready", "a tie-out mismatch must NOT auto-file"
    assert "tie" in (info.get("reason") or "").lower()


def test_autofile_files_when_tie_ok(monkeypatch):
    import autopilot, waiting_room as IQ
    calls = {}

    def _fake_enqueue(payload, user=None):
        calls["reg"] = payload
        return (1, "queued")
    monkeypatch.setattr(IQ, "enqueue_registration", _fake_enqueue)
    import validate as VAL
    monkeypatch.setattr(VAL, "save_baseline", lambda *a, **k: None)
    draft = {"supplier": "TEST", "statement_ref": "INV1", "confidence": "high",
             "statement_date": "2026-05-31",
             "lines": [{"invoice_no": "INV1", "date": "2026-05-02", "country": "Germany",
                        "currency": "EUR", "net": 100.0, "vat": 19.0}],
             "coversheet_total": 119.0}  # ties out
    st, info = autopilot.autofile(None, {"id": 1}, draft, actor="test")
    assert st == "done" and calls.get("reg"), "a tying draft should file"


def test_einvoice_total_ignores_amount_due():
    """parse_einvoice must use the net+VAT total (TaxInclusiveAmount), NOT PayableAmount
    (amount due, net of prepayment) — else a prepaid invoice false-blocks at tie-out."""
    import extract
    # UBL with TaxInclusiveAmount=119 (net+VAT) but PayableAmount=100 (after a 19 prepayment)
    ubl = """<?xml version="1.0"?>
    <Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
             xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
             xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
      <cbc:ID>INV1</cbc:ID><cbc:IssueDate>2026-05-31</cbc:IssueDate>
      <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
      <cac:LegalMonetaryTotal>
        <cbc:TaxExclusiveAmount>100</cbc:TaxExclusiveAmount>
        <cbc:PrepaidAmount>19</cbc:PrepaidAmount>
        <cbc:PayableAmount>100</cbc:PayableAmount>
        <cbc:TaxInclusiveAmount>119</cbc:TaxInclusiveAmount>
      </cac:LegalMonetaryTotal>
    </Invoice>"""
    d = extract.parse_einvoice(ubl.encode("utf-8"))
    # total must be the net+VAT 119, never the amount-due 100
    assert d.get("coversheet_total") in (119.0, None), \
        f"coversheet_total must be net+VAT (119) or absent, got {d.get('coversheet_total')}"
    assert d.get("coversheet_total") != 100.0, "must NOT use the amount-due PayableAmount"


def test_capture_vat_does_not_overwrite_curated(admin_session, monkeypatch):
    """A 'capture' write must not overwrite an existing non-capture VAT number."""
    import importlib, supplier_master as SM2
    importlib.reload(SM2)
    sup = "WAGTEST"
    # seed a curated (document-mining) VAT registration, no entity_name
    SM2.set_vat_registration(sup, "Germany", "DE111111111", source="document mining")
    # a capture write with a DIFFERENT (mis-read) VAT + an entity name
    SM2.set_vat_registration(sup, "Germany", "DE999999999", source="capture",
                             entity_name="W.A.G. Deutschland GmbH")
    name, vat, source = SM2.get_issuer(sup, "Germany")
    assert vat == "DE111111111", "curated VAT number must survive a capture write"
    # the entity name DID seed (the slot was empty), marked source still document-mining-ish
    assert name == "W.A.G. Deutschland GmbH"


def test_manual_vat_update_still_works(admin_session, monkeypatch):
    """A non-capture (admin/doc-mining) write may still update the VAT number."""
    import importlib, supplier_master as SM2
    importlib.reload(SM2)
    sup = "WAGTEST2"
    SM2.set_vat_registration(sup, "Poland", "PL111", source="document mining")
    SM2.set_vat_registration(sup, "Poland", "PL222", source="manual")
    _n, vat, _s = SM2.get_issuer(sup, "Poland")
    assert vat == "PL222", "a manual write should update the VAT number"

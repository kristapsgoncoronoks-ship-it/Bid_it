"""
AI VISION CAPTURE (vision) — the OPT-IN, ADVISORY "AI for capture" path that reads a
scanned / unknown-layout invoice's PAGE IMAGES with a vision model and produces a
comprehensive structured capture document, mapped into the existing review draft. The
vision backend is MONKEYPATCHED throughout; no real key or network is used.

Load-bearing invariants asserted here:
  * OFF (setting off OR no provider) => enabled() False AND capture()/extract() make NO
    backend call; extraction is byte-identical to the existing path.
  * enabled => capture() parses a FULL capture document (header + lines + totals), maps it
    to the draft shape, AND preserves the `capture` superset + the extra per-line keys.
  * a field ABSENT in the model output stays null — NEVER invented.
  * the page cap is honoured.
  * a backend error => None and extract() falls back to the existing parser path.
  * a STRUCTURED e-invoice path is untouched and makes NO vision call even when ON.
  * the review "Capture document" panel renders and ESCAPES a planted XSS.
"""
import re

import pytest

import vision_capture as VC
import ai_verify


# --------------------------------------------------------------- helpers
def _patch_render(monkeypatch, npages):
    """Make ai_verify._render_pages return `npages` fake PNGs, honouring the cap."""
    def _render(pdf_bytes, max_pages):
        cap = max(1, int(max_pages or VC.VISION_CAPTURE_MAX_PAGES))
        return [b"PNG-bytes"] * min(npages, cap)
    monkeypatch.setattr(ai_verify, "_render_pages", _render)


def _full_capture():
    """A complete capture document the mocked model 'returns'."""
    return {
        "header": {
            "supplier": {"name": "DKV Mobility", "vat_number": "DE811907980",
                         "address": "Ratingen", "country": "Germany"},
            "customer": {"name": "Baltic Transport SIA", "vat_number": "LV40003012345",
                         "account_or_card_no": "CARD-77"},
            "invoice": {"number": "INV-2026-0042", "issue_date": "2026-05-31",
                        "due_date": "2026-06-14", "currency": "EUR", "exchange_rate": None},
        },
        "lines": [
            {"date": "2026-05-12", "time": "08:14", "station_name": "Shell A2",
             "city": "Warsaw", "country": "Poland", "product": "Diesel",
             "quantity": 412.5, "unit": "L", "unit_price": 1.42, "discount": 3.10,
             "net": 500.00, "vat_rate": 23, "vat": 115.00, "gross": 615.00,
             "card_no": "CARD-77", "receipt_no": "R-9981"},
            {"date": "2026-05-20", "time": "19:02", "station_name": "Circle K",
             "city": "Vilnius", "country": "Lithuania", "product": "AdBlue",
             "quantity": 20.0, "unit": "L", "unit_price": 0.80, "discount": None,
             "net": 16.00, "vat_rate": 21, "vat": 3.36, "gross": 19.36,
             "card_no": "CARD-77", "receipt_no": "R-9982"},
        ],
        "totals": {"net_total": 516.00, "discount_total": 3.10,
                   "vat_total": 118.36, "gross_total": 634.36},
    }


# --------------------------------------------------------------- enabled() gating
def test_enabled_false_when_setting_off(monkeypatch):
    import auth
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: "claude")
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: "off" if k == VC.SETTING else d)
    assert VC.enabled() is False


def test_enabled_false_when_no_provider(monkeypatch):
    import auth
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: None)
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: "on" if k == VC.SETTING else d)
    assert VC.enabled() is False                     # ON but no provider => OFF


def test_enabled_true_when_on_and_provider(monkeypatch):
    import auth
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: "openai")
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: "on" if k == VC.SETTING else d)
    assert VC.enabled() is True


def test_off_makes_no_backend_call(monkeypatch):
    # no provider -> capture() returns None WITHOUT ever calling a backend
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: None)
    called = []
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", lambda *a, **k: called.append(1))
    monkeypatch.setitem(ai_verify._VISION_CALL, "openai", lambda *a, **k: called.append(1))
    assert VC.capture(b"%PDF-1.4 fake") is None
    assert called == []


# --------------------------------------------------------------- capture() happy path
def test_capture_parses_and_maps(monkeypatch):
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: "claude")
    monkeypatch.setattr(VC, "model_name", lambda *a, **k: "claude-opus-4-8")
    monkeypatch.setattr(VC, "provider_label", lambda *a, **k: "Claude (Anthropic)")
    _patch_render(monkeypatch, 2)
    sent = {}

    def _call(prompt, data_str, images):
        sent["images"] = images
        sent["prompt"] = prompt
        return _full_capture()

    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", _call)
    files = [("scan.pdf", b"%PDF fake")]
    draft = VC.capture(b"%PDF fake", files=files)
    assert draft is not None
    # mapped to the existing draft shape
    assert draft["supplier"] == "DKV Mobility"
    assert draft["supplier_vat"] == "DE811907980"
    assert draft["statement_ref"] == "INV-2026-0042"       # = invoice number
    assert draft["statement_date"] == "2026-05-31"         # = issue_date
    assert draft["currency"] == "EUR"
    assert draft["customer"] == "Baltic Transport SIA"
    assert draft["backend"] == "vision"
    assert draft["confidence"] == "low"
    assert "vision-captured" in draft["notes"]
    # the page image(s) were sent
    assert sent["images"] == [b"PNG-bytes", b"PNG-bytes"]

    # existing line keys are present AND carry the right values
    l0 = draft["lines"][0]
    for k in ("invoice_no", "date", "country", "currency", "net", "vat", "product", "qty"):
        assert k in l0
    assert l0["invoice_no"] == "INV-2026-0042"
    assert l0["country"] == "Poland"
    assert l0["net"] == 500.00 and l0["vat"] == 115.00
    assert l0["product"] == "Diesel" and l0["qty"] == 412.5
    assert l0["_source"] == "vision"

    # the RICHER capture keys are KEPT on the line (superset not lost)
    for k in ("time", "station_name", "city", "unit_price", "discount", "gross",
              "vat_rate", "card_no", "receipt_no"):
        assert k in l0
    assert l0["station_name"] == "Shell A2" and l0["city"] == "Warsaw"
    assert l0["unit_price"] == 1.42 and l0["gross"] == 615.00
    assert l0["card_no"] == "CARD-77" and l0["receipt_no"] == "R-9981"

    # the FULL capture document is preserved under `capture`
    cap = draft["capture"]
    assert cap["header"]["supplier"]["address"] == "Ratingen"
    assert cap["totals"]["gross_total"] == 634.36
    assert len(cap["lines"]) == 2

    # vault plumbing intact
    assert draft["files"] == [{"name": "scan.pdf", "size": len(b"%PDF fake")}]
    assert draft["_pdf_bytes"] == files


def test_absent_field_stays_null_not_invented(monkeypatch):
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: "openai")
    monkeypatch.setattr(VC, "model_name", lambda *a, **k: "gpt-4o")
    monkeypatch.setattr(VC, "provider_label", lambda *a, **k: "OpenAI")
    _patch_render(monkeypatch, 1)

    sparse = {
        "header": {"supplier": {"name": "ACME"},      # no vat/address/country
                   "invoice": {"number": "X1"}},       # no issue_date/currency
        "lines": [{"product": "Diesel", "net": 100.0}],  # no vat/qty/station etc.
        # no totals at all
    }
    monkeypatch.setitem(ai_verify._VISION_CALL, "openai", lambda *a, **k: sparse)
    draft = VC.capture(b"%PDF fake")
    cap = draft["capture"]
    # absent header fields are null, NOT fabricated
    assert cap["header"]["supplier"]["vat_number"] is None
    assert cap["header"]["supplier"]["country"] is None
    assert cap["header"]["invoice"]["issue_date"] is None
    # absent line fields are null (vat absent => null, NOT 0.0 invented in the capture doc)
    cl = cap["lines"][0]
    assert cl["vat"] is None and cl["quantity"] is None and cl["station_name"] is None
    # absent totals block => all null
    assert cap["totals"]["net_total"] is None
    # currency defaults to EUR on the DRAFT (header field is null) — the draft net/vat
    # default to 0.0 for the downstream numeric path, but the capture doc keeps null.
    assert draft["currency"] == "EUR"
    assert draft["lines"][0]["vat"] == 0.0            # draft line numeric fallback
    assert draft["lines"][0]["qty"] is None


def test_page_cap_honoured(monkeypatch):
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: "claude")
    monkeypatch.setattr(VC, "model_name", lambda *a, **k: "m")
    monkeypatch.setattr(VC, "provider_label", lambda *a, **k: "Claude")
    _patch_render(monkeypatch, VC.VISION_CAPTURE_MAX_PAGES + 12)   # a PDF bigger than the cap
    sent = {}
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude",
                        lambda p, d, images: sent.update(n=len(images)) or _full_capture())
    VC.capture(b"%PDF fake")
    assert sent["n"] == VC.VISION_CAPTURE_MAX_PAGES


def test_backend_error_returns_none(monkeypatch):
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: "claude")
    _patch_render(monkeypatch, 1)
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad json")))
    assert VC.capture(b"%PDF fake") is None            # never raises -> None (fallback)


def test_unparseable_response_returns_none(monkeypatch):
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: "claude")
    _patch_render(monkeypatch, 1)
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", lambda *a, **k: "not a dict")
    assert VC.capture(b"%PDF fake") is None


# --------------------------------------------------------------- parse_capture robustness
def test_parse_capture_drops_malformed_lines():
    cap = VC.parse_capture({"header": {}, "lines": ["junk", 5, {"product": "x"}]})
    assert len(cap["lines"]) == 1                      # non-dicts dropped
    assert cap["lines"][0]["product"] == "x"


def test_parse_capture_non_dict_is_none():
    assert VC.parse_capture(["nope"]) is None
    assert VC.parse_capture(None) is None


# --------------------------------------------------------------- extract() wiring
def test_extract_uses_capture_when_enabled(monkeypatch):
    import extract as EX
    monkeypatch.setattr(VC, "enabled", lambda: True)
    captured = {"called": 0}

    def _cap(pdf, files=None, **k):
        captured["called"] += 1
        return {"supplier": "VISION-SUP", "lines": [], "backend": "vision",
                "capture": {"header": {}, "lines": [], "totals": {}},
                "files": [{"name": n, "size": len(b)} for n, b in (files or [])],
                "_pdf_bytes": files, "confidence": "low", "notes": "vision"}

    monkeypatch.setattr(VC, "capture", _cap)
    draft = EX.extract(b"%PDF-1.4 plain fake invoice text here", "scan.pdf", backend="parser")
    assert captured["called"] == 1
    assert draft["backend"] == "vision"
    assert draft["supplier"] == "VISION-SUP"


def test_extract_falls_back_when_capture_none(monkeypatch):
    import extract as EX
    monkeypatch.setattr(VC, "enabled", lambda: True)
    monkeypatch.setattr(VC, "capture", lambda *a, **k: None)   # capture yields nothing
    # with backend=parser and an unrecognised PDF, it falls through to the existing chain
    draft = EX.extract(b"%PDF-1.4 plain fake invoice", "x.pdf", backend="parser")
    assert draft["backend"] != "vision"                # fell back to the deterministic path


def test_extract_off_is_byte_identical(monkeypatch):
    import extract as EX
    monkeypatch.setattr(VC, "enabled", lambda: False)
    called = []
    monkeypatch.setattr(VC, "capture", lambda *a, **k: called.append(1))
    draft = EX.extract(b"%PDF-1.4 plain fake invoice", "x.pdf", backend="parser")
    assert called == []                                # OFF => no capture() call at all
    assert draft["backend"] != "vision"


def test_structured_einvoice_untouched_no_vision_call(monkeypatch):
    """A structured UBL e-invoice must parse deterministically with NO vision call, even
    when vision capture is ON (it is a PLAIN-PDF-only exception)."""
    import extract as EX
    monkeypatch.setattr(VC, "enabled", lambda: True)
    called = []
    monkeypatch.setattr(VC, "capture", lambda *a, **k: called.append(1))
    ubl = (b'<?xml version="1.0"?>'
           b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">'
           b'<ID>E-1</ID><IssueDate>2026-05-31</IssueDate>'
           b'<DocumentCurrencyCode>EUR</DocumentCurrencyCode>'
           b'<InvoiceLine><LineExtensionAmount>100.00</LineExtensionAmount>'
           b'<Country><IdentificationCode>PL</IdentificationCode></Country></InvoiceLine>'
           b'</Invoice>')
    draft = EX.extract(ubl, "einvoice.xml", backend="parser")
    assert called == []                                # the structured path never calls vision
    assert draft["backend"] == "e-invoice"


# --------------------------------------------------------------- review panel + escaping
def _tok(client, path):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def test_capture_panel_renders_and_escapes(client):
    import app as A
    injection = '<img src=x onerror=alert(1)>'
    cap_draft = {
        "supplier": injection, "supplier_vat": "LV1", "statement_ref": "R1",
        "statement_date": "2026-05-31", "currency": "EUR", "customer": "C",
        "lines": [{"invoice_no": "R1", "date": "2026-05-31", "country": injection,
                   "currency": "EUR", "net": 100.0, "vat": 21.0, "product": injection,
                   "qty": 5, "_source": "vision", "station_name": injection,
                   "city": "X", "unit_price": 1.4, "discount": None, "gross": 121.0,
                   "vat_rate": 21, "card_no": "C1", "receipt_no": "R9"}],
        "capture": {
            "header": {"supplier": {"name": injection, "vat_number": "LV1",
                                    "address": injection, "country": "X"},
                       "customer": {"name": "C", "vat_number": None,
                                    "account_or_card_no": None},
                       "invoice": {"number": "R1", "issue_date": "2026-05-31",
                                   "due_date": None, "currency": "EUR",
                                   "exchange_rate": None}},
            "lines": [{"date": "2026-05-31", "station_name": injection, "product": injection,
                       "net": 100.0, "vat": 21.0}],
            "totals": {"net_total": 100.0, "vat_total": 21.0, "gross_total": 121.0},
        },
        "backend": "vision", "confidence": "low", "notes": "vision-captured",
    }
    html = A._capture_document_html(cap_draft, token="ctok", intake_job=1)
    assert "Capture document (AI vision" in html
    # the planted XSS is ESCAPED, never a live tag
    assert injection not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    # download links present
    assert "/extract/capture/ctok.json" in html
    assert "/extract/capture/ctok.txt" in html


def test_capture_panel_empty_when_no_capture():
    import app as A
    assert A._capture_document_html({"supplier": "x", "lines": []}) == ""


def test_capture_download(client):
    import app as A
    cap = {"header": {"supplier": {"name": "ACME"}, "customer": {}, "invoice": {"number": "Z9"}},
           "lines": [{"date": "2026-05-31", "product": "Diesel", "net": 10.0}],
           "totals": {"gross_total": 12.1}}
    A._stash_draft("00000000d1000001", {"supplier": "ACME", "capture": cap, "lines": []})
    rj = client.get("/extract/capture/00000000d1000001.json")
    assert rj.status_code == 200
    assert b"ACME" in rj.data and b"Z9" in rj.data
    rt = client.get("/extract/capture/00000000d1000001.txt")
    assert rt.status_code == 200
    body = rt.get_data(as_text=True)
    assert "AI VISION CAPTURE DOCUMENT" in body and "Diesel" in body


# --------------------------------------------------------------- admin toggle
def test_admin_capture_card_and_toggle(client):
    import auth
    html = client.get("/admin").get_data(as_text=True)
    assert "AI vision capture" in html
    assert "ORIGINAL invoice PDF page images" in html         # the loud warning
    assert 'name="ai_vision_capture_enabled"' in html
    client.post("/admin", data={"_csrf": _tok(client, "/admin"),
                                "__act": "set_ai_vision_capture",
                                "ai_vision_capture_enabled": "on"})
    assert str(auth.get_setting("ai_vision_capture_enabled")).lower() in ("on", "1", "true", "yes")
    client.post("/admin", data={"_csrf": _tok(client, "/admin"),
                                "__act": "set_ai_vision_capture"})       # unchecked => off
    assert str(auth.get_setting("ai_vision_capture_enabled")).lower() in ("off", "0", "false", "no")

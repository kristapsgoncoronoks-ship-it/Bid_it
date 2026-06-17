"""E-INVOICE EXPORT — EN-16931 / UBL 2.1 Invoice XML export of registered invoices.

Covers:
- ubl_invoice_xml produces well-formed XML (parse back with ET) carrying the invoice
  number, seller + buyer VAT ids, >=1 line with quantity + net amount, a VAT breakdown
  with the right rate/amount, and the document totals (BG-22).
- amounts go through money.f2 (a 12.5 -> "12.50" check).
- the batch ZIP contains one XML per registered invoice.
- an invoice with a missing field still produces valid XML (placeholder, no crash).
- read-only: the product DBs are not written.
- the export routes serve with the right content-type and are gated by the `exports`
  perm (a user without it is blocked).
- the hub page renders + escapes XSS.

BASIS: NET EUR, final (rebates applied); gross = net + VAT. Read-only.
"""
import io
import xml.etree.ElementTree as ET
import zipfile

import einvoice_export as E

CBC = "{%s}" % E.NS["cbc"]
CAC = "{%s}" % E.NS["cac"]
INV = "{%s}" % E.NS["inv"]


# ----------------------------------------------------------------- fixture
def _fixture_invoice(missing=False):
    """A registered-invoice dict mirroring what `_lines_to_invoice` returns. With
    `missing=True`, the seller VAT id and a line quantity are absent (to exercise the
    placeholder path)."""
    return E._lines_to_invoice(
        [{"product": "Diesel", "code": "1", "qty": (None if missing else 100.0),
          "net_eur": 125.50, "vat_eur": 28.87},          # ~23% VAT
         {"product": "Road tolls", "code": "4", "qty": 5.0,
          "net_eur": 10.00, "vat_eur": 2.10}],           # 21% VAT
        invoice_no="INV-001",
        issue_date="2026-05-10",
        currency="EUR",
        seller_name="Shell Polska",
        seller_vat=(None if missing else "PL1234567890"),
        buyer_name="Baltic Trans UAB",
        buyer_vat="LT999999999",
    )


# ----------------------------------------------------------------- structure
def test_ubl_invoice_well_formed_and_key_terms():
    name, data = E.ubl_invoice_xml(_fixture_invoice())
    assert isinstance(data, bytes)
    assert data.lstrip().startswith(b"<?xml")
    root = ET.fromstring(data)                       # well-formed
    assert root.tag == INV + "Invoice"

    # BT-1 invoice number, BT-5 currency.
    assert root.find(CBC + "ID").text == "INV-001"
    assert root.find(CBC + "DocumentCurrencyCode").text == "EUR"
    assert root.find(CBC + "CustomizationID").text == E.CUSTOMIZATION_ID

    # Seller (BT-31) + buyer (BT-48) VAT ids.
    sup = root.find(CAC + "AccountingSupplierParty")
    cust = root.find(CAC + "AccountingCustomerParty")
    sup_vat = sup.find(".//" + CBC + "CompanyID").text
    cust_vat = cust.find(".//" + CBC + "CompanyID").text
    assert sup_vat == "PL1234567890"
    assert cust_vat == "LT999999999"


def test_ubl_invoice_lines_have_qty_and_net():
    name, data = E.ubl_invoice_xml(_fixture_invoice())
    root = ET.fromstring(data)
    lines = root.findall(CAC + "InvoiceLine")
    assert len(lines) == 2
    first = lines[0]
    qty = first.find(CBC + "InvoicedQuantity")
    assert qty is not None and float(qty.text) == 100.0
    assert qty.get("unitCode") == E.UNIT_LITRE
    net = first.find(CBC + "LineExtensionAmount")
    assert net is not None and net.text == "125.50"


def test_ubl_vat_breakdown_rates_and_amounts():
    name, data = E.ubl_invoice_xml(_fixture_invoice())
    root = ET.fromstring(data)
    tt = root.find(CAC + "TaxTotal")
    # document VAT total (BT-110) = sum of line VAT (28.87 + 2.10).
    assert tt.find(CBC + "TaxAmount").text == "30.97"
    subs = tt.findall(CAC + "TaxSubtotal")
    assert len(subs) >= 1
    # the 21% road-toll subtotal must carry the right rate + amount.
    by_pct = {}
    for s in subs:
        pct = s.find(".//" + CBC + "Percent").text
        by_pct[pct] = (s.find(CBC + "TaxableAmount").text, s.find(CBC + "TaxAmount").text)
    assert "21" in by_pct
    assert by_pct["21"] == ("10.00", "2.10")
    # every subtotal is standard-rated category 'S'.
    for s in subs:
        assert s.find(".//" + CBC + "ID").text == E.TAX_CATEGORY_STANDARD


def test_ubl_document_totals():
    name, data = E.ubl_invoice_xml(_fixture_invoice())
    root = ET.fromstring(data)
    lmt = root.find(CAC + "LegalMonetaryTotal")
    # line nets 125.50 + 10.00 = 135.50; with VAT 30.97 -> 166.47.
    assert lmt.find(CBC + "LineExtensionAmount").text == "135.50"
    assert lmt.find(CBC + "TaxExclusiveAmount").text == "135.50"
    assert lmt.find(CBC + "TaxInclusiveAmount").text == "166.47"
    assert lmt.find(CBC + "PayableAmount").text == "166.47"


def test_amounts_quantized_money_f2():
    # 12.5 must render as "12.50" (money.f2 / 2-decimal accounting form).
    inv = E._lines_to_invoice(
        [{"product": "Diesel", "code": "1", "qty": 1.0, "net_eur": 12.5, "vat_eur": 0.0}],
        invoice_no="Q-1", issue_date="2026-01-01", currency="EUR",
        seller_name="S", seller_vat="X1", buyer_name="B", buyer_vat="Y1")
    name, data = E.ubl_invoice_xml(inv)
    root = ET.fromstring(data)
    net = root.find(CAC + "InvoiceLine/" + CBC + "LineExtensionAmount").text
    assert net == "12.50"


def test_validate_ubl_accepts_generated_and_rejects_garbage():
    name, data = E.ubl_invoice_xml(_fixture_invoice())
    assert E.validate_ubl(data) is True
    # not well-formed
    try:
        E.validate_ubl(b"<Invoice><unclosed>")
        assert False, "expected ValueError"
    except ValueError:
        pass
    # well-formed but wrong root
    try:
        E.validate_ubl(b"<NotAnInvoice/>")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "not a UBL Invoice" in str(e)


# ----------------------------------------------------------------- placeholders
def test_missing_field_still_valid_with_placeholder():
    name, data = E.ubl_invoice_xml(_fixture_invoice(missing=True))
    assert E.validate_ubl(data) is True                 # still valid
    root = ET.fromstring(data)
    sup = root.find(CAC + "AccountingSupplierParty")
    # the absent seller VAT id is a clean placeholder, NOT a fabricated id.
    assert sup.find(".//" + CBC + "CompanyID").text == E.INPUT_PLACEHOLDER
    # the line with no quantity defaults to 1 / C62 (a placeholder unit), and is
    # flagged by an XML comment — never a fabricated litre figure.
    line = root.find(CAC + "InvoiceLine")
    qty = line.find(CBC + "InvoicedQuantity")
    assert qty.get("unitCode") == E.UNIT_ONE
    # the NET amount is still the registered figure (never placeholdered).
    assert line.find(CBC + "LineExtensionAmount").text == "125.50"


def test_empty_invoice_does_not_crash():
    inv = E._lines_to_invoice([], invoice_no="", issue_date="", currency="EUR",
                              seller_name="", seller_vat="", buyer_name="", buyer_vat="")
    name, data = E.ubl_invoice_xml(inv)
    root = ET.fromstring(data)                          # still well-formed
    assert root.tag == INV + "Invoice"
    assert root.find(CBC + "ID").text == E.INPUT_PLACEHOLDER


# ----------------------------------------------------------------- batch ZIP
def test_batch_zip_one_xml_per_invoice():
    refs = E._enumerate_invoices()
    assert refs, "demo data must carry registered invoices"
    name, data = E.ubl_invoices_zip({"period": "2026"})
    assert name.endswith(".zip")
    zf = zipfile.ZipFile(io.BytesIO(data))
    members = zf.namelist()
    # one XML per registered invoice; each member is a well-formed UBL invoice.
    assert len(members) == len(refs)
    for m in members:
        assert m.endswith(".xml")
        assert E.validate_ubl(zf.read(m)) is True


def test_batch_zip_raises_when_no_match():
    try:
        E.ubl_invoices_zip({"period": "1999"})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "nothing to export" in str(e)


def test_build_invoice_for_unknown_returns_none():
    assert E.build_invoice_for("nobody", "Nowhere", "2026-Q2", "NO-SUCH-REF") is None


# ----------------------------------------------------------------- read-only
def test_export_does_not_write_product_db():
    import os, vat_refund
    fh = vat_refund.ANALYTICS_DB
    before = os.path.getmtime(fh)
    E.ubl_invoices_zip({"period": "2026"})
    after = os.path.getmtime(fh)
    assert after == before, "the product DB must not be written by the export"


# ----------------------------------------------------------------- web
def test_export_einvoice_batch_route(client):
    r = client.get("/export/einvoice/batch?period=2026")
    assert r.status_code == 200
    assert r.mimetype == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.get_data()))
    assert len(zf.namelist()) >= 1


def test_export_einvoice_route_with_client(client):
    refs = E._enumerate_invoices()
    ref = next(r for (_e, _c, _q, r) in refs if "INPUT" not in str(r))
    r = client.get(f"/export/einvoice?ref={ref}")
    assert r.status_code == 200
    assert r.mimetype == "application/xml"
    root = ET.fromstring(r.get_data())
    assert root.tag.endswith("Invoice")


def test_export_einvoice_unknown_ref_friendly_200(client):
    r = client.get("/export/einvoice?ref=NO-SUCH-REF-XYZ")
    assert r.status_code == 200
    assert "Nothing to export" in r.get_data(as_text=True)


def test_exports_hub_renders(client):
    r = client.get("/exports")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Accounting &amp; ERP exports" in html
    assert "/export/saft" in html
    assert "/export/accounting" in html
    assert "/export/einvoice/batch" in html
    assert "NET EUR" in html


def test_exports_hub_escapes_xss(client):
    # the period param is echoed into the page; an injected script must be escaped.
    r = client.get("/exports?period=<script>alert(1)</script>")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ----------------------------------------------------------------- gating
def test_export_routes_are_gated():
    import app
    for ep in ("export_einvoice", "export_einvoice_batch", "exports_hub"):
        assert app.PERM_BY_ENDPOINT.get(ep) == "exports"
        assert ep in app.MODULES["analytics"][1]
        assert ep not in app.ADMIN_ONLY


def test_processor_without_exports_perm_is_blocked(admin_session):
    """A processor lacking the `exports` capability is blocked from the hub + the
    e-invoice exports (the central _guard capability gate)."""
    import app as A
    import auth
    # take away the exports capability from the processor role for this test.
    auth.set_permission("processor", "exports", False)
    try:
        u, pw = "pytest_proc_noexp", "Pytest!Pw123"
        auth.add_user(u, pw, role="processor")   # INSERT OR REPLACE; idempotent
        c = A.app.test_client()
        r = c.post("/login", data={"username": u, "password": pw})
        assert r.status_code == 302
        for path in ("/exports", "/export/einvoice/batch?period=2026",
                     "/export/einvoice?ref=X"):
            resp = c.get(path)
            assert resp.status_code == 403, f"{path} should be forbidden"
    finally:
        auth.set_permission("processor", "exports", True)

"""
INVOICING — PHASE 7 (the final composer polish): logo/branding, discounts (line +
document), and proforma/quote documents. These tests repoint invoicing.DB at a temp file
and exercise:

  - LOGO: stored/sniffed (PNG/JPG), embedded as a data: URI in the HTML, and the PDF/HTML
    degrade gracefully when the logo is absent / corrupt;
  - LINE DISCOUNT (% and amount): reduces the line net + VAT, stored POST-discount;
  - DOCUMENT DISCOUNT: allocated PER RATE proportionally, VAT recomputed on the post-discount
    net, the per-rate breakdown ties out to the totals (cents-exact);
  - a multi-rate invoice with BOTH a line and a document discount computes EXACTLY;
  - the EN-16931 e-invoice emits LINE + DOCUMENT AllowanceCharge, the TaxSubtotal /
    LegalMonetaryTotal tie out, AND round-trips through extract.parse_einvoice;
  - PROFORMA / QUOTE: own non-legal series (no legal-number collision), NO output VAT (absent
    from the VAT report), excluded from AR + revenue; convert_to_invoice copies lines +
    discounts and the converted invoice issues with a real number;
  - i18n: EN default + LV catalog entries for the new strings.
"""
import os
import sys
import struct
import xml.etree.ElementTree as ET
import zlib

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import invoicing  # noqa: E402
import invoicing_reports as IR  # noqa: E402
import money      # noqa: E402


@pytest.fixture()
def inv(tmp_path, monkeypatch):
    monkeypatch.setattr(invoicing, "DB", str(tmp_path / "invoicing.db"), raising=True)
    monkeypatch.setattr(invoicing, "_SCHEMA_READY", set(), raising=True)
    return invoicing


def _set_issuer(inv, **over):
    base = dict(name="Acme Logistics OU", address="Tartu mnt 1, Tallinn, Estonia",
                vat_number="EE100000000", reg_no="12345678", iban="EE001234567890",
                bank="LHV", series="INV", credit_series="KR", proforma_series="PROF",
                quote_series="PIED", number_format="{series}-{year}-{seq:06d}",
                payment_terms_days="14")
    base.update(over)
    inv.set_issuer(base)


def _customer(inv, **over):
    base = dict(name="Bauer GmbH", country="DE", vat_number="DE111111111",
                address="Hauptstr 1, Berlin")
    base.update(over)
    c, _ = inv.add_customer(**base)
    return c


def _png_bytes():
    """A tiny valid 1x1 PNG (real magic + IHDR + IDAT + IEND)."""
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\xff\xff")
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# ====================================================================== A) LOGO / branding
def test_logo_store_sniff_and_data_uri(inv):
    _set_issuer(inv)
    png = _png_bytes()
    mime, err = inv.set_issuer_logo(png, mime="image/png", updated_by="pytest")
    assert err == "" and mime == "image/png"
    gmime, gdata = inv.get_issuer_logo()
    assert gmime == "image/png" and gdata == png
    uri = inv.logo_data_uri()
    assert uri.startswith("data:image/png;base64,")


def test_logo_rejects_non_image_and_oversize(inv):
    _set_issuer(inv)
    mime, err = inv.set_issuer_logo(b"not an image at all", mime="image/png")
    assert mime is None and "PNG or JPG" in err
    big = _png_bytes() + b"\x00" * (invoicing.LOGO_MAX_BYTES + 10)
    mime, err = inv.set_issuer_logo(big, mime="image/png")
    assert mime is None and "too large" in err


def test_logo_sniff_overrides_claimed_mime(inv):
    _set_issuer(inv)
    # claim png but supply jpeg bytes -> stored mime is jpeg (sniffed, not trusted)
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    mime, err = inv.set_issuer_logo(jpeg, mime="image/png")
    assert err == "" and mime == "image/jpeg"


def test_logo_embeds_in_html_and_degrades_when_absent(inv):
    _set_issuer(inv)
    c = _customer(inv)
    draft, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(draft["id"], description="Service", quantity=1, unit_price_net=100,
                 vat_rate=0.21)
    # no logo -> no <img>, but HTML still renders fully
    html0 = inv.invoice_html(draft["id"])
    assert "<img" not in html0
    assert "Acme Logistics OU" in html0
    # with a logo -> a data: URI <img> appears
    inv.set_issuer_logo(_png_bytes(), mime="image/png")
    html1 = inv.invoice_html(draft["id"])
    assert "data:image/png;base64," in html1
    # a CORRUPT logo row must not break rendering (degrade to no image)
    con = inv.connect()
    try:
        con.execute("UPDATE issuer_logo SET data=NULL")
        con.commit()
    finally:
        con.close()
    html2 = inv.invoice_html(draft["id"])
    assert html2 and "Acme Logistics OU" in html2  # still renders


def test_brand_color_only_accepts_hex(inv):
    assert inv._safe_color("#0a3d62") == "#0a3d62"
    assert inv._safe_color("0a3d62") == "#0a3d62"
    assert inv._safe_color("#abc") == "#abc"
    assert inv._safe_color("red; } body{display:none") == ""
    assert inv._safe_color("") == ""
    # a valid brand colour appears in the invoice <style>; an injection attempt never does
    _set_issuer(inv, brand_color="#123456")
    c = _customer(inv)
    d, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(d["id"], description="X", quantity=1, unit_price_net=10, vat_rate=0.21)
    html = inv.invoice_html(d["id"])
    assert "#123456" in html


# ====================================================================== B) LINE discount
def test_line_discount_percent_reduces_net_and_vat(inv):
    net, vat, rate, gross_net, disc = inv.compute_line(
        quantity=2, unit_price_net=50, vat_rate=0.21,
        discount_kind="percent", discount_value=10)
    # gross net 100.00, 10% discount -> 10.00 -> net 90.00, vat 18.90
    assert gross_net == 100.0 and disc == 10.0
    assert net == 90.0
    assert vat == money.f2(90.0 * 0.21) == 18.9


def test_line_discount_amount_reduces_net_and_vat(inv):
    net, vat, rate, gross_net, disc = inv.compute_line(
        quantity=1, unit_price_net=100, vat_rate=0.21,
        discount_kind="amount", discount_value=15)
    assert gross_net == 100.0 and disc == 15.0 and net == 85.0
    assert vat == money.f2(85.0 * 0.21)


def test_line_discount_clamped_to_line(inv):
    # an amount discount bigger than the line clamps to the line (net never < 0)
    net, vat, rate, gross_net, disc = inv.compute_line(
        quantity=1, unit_price_net=20, vat_rate=0.21,
        discount_kind="amount", discount_value=999)
    assert disc == 20.0 and net == 0.0 and vat == 0.0


def test_line_discount_stored_post_discount(inv):
    _set_issuer(inv)
    c = _customer(inv)
    d, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(d["id"], description="Svc", quantity=2, unit_price_net=50, vat_rate=0.21,
                 discount_kind="percent", discount_value=10)
    lines = inv.get_lines(d["id"])
    ln = lines[0]
    assert ln["line_net"] == 90.0           # POST-discount
    assert ln["line_vat"] == 18.9
    assert ln["gross_amount"] == 100.0      # PRE-discount
    assert ln["discount_amount"] == 10.0
    hdr = inv.get_invoice(d["id"])
    assert hdr["net_total"] == 90.0 and hdr["vat_total"] == 18.9


# ====================================================================== B) DOCUMENT discount
def test_document_discount_allocates_per_rate_and_ties_out(inv):
    # two rates: 100 @ 21% and 100 @ 12%; a 10% document discount.
    tot = inv.compute_totals(
        [{"quantity": 1, "unit_price_net": 100, "vat_rate": 0.21},
         {"quantity": 1, "unit_price_net": 100, "vat_rate": 0.12}],
        disc_kind="percent", disc_value=10)
    # subtotal 200, discount 20 (10 per rate), net 180, by-rate net 90 each
    assert tot["doc_discount"] == 20.0
    by = {r["rate"]: r for r in tot["by_rate"]}
    assert by[0.21]["net"] == 90.0 and by[0.21]["vat"] == money.f2(90.0 * 0.21)
    assert by[0.12]["net"] == 90.0 and by[0.12]["vat"] == money.f2(90.0 * 0.12)
    # TIE-OUT: per-rate VAT sums to the total; per-rate net sums to net_total
    assert tot["net_total"] == 180.0
    assert tot["vat_total"] == money.fsum([r["vat"] for r in tot["by_rate"]])
    assert money.f2(tot["net_total"] + tot["vat_total"]) == tot["gross_total"]


def test_document_discount_rounding_remainder_ties_exactly(inv):
    # an uneven split: 100 @ 21% and 50 @ 12%; a 10.00 EUR document discount.
    # proportional: 100/150*10 = 6.6667 -> 6.67 ; 50/150*10 = 3.3333 -> 3.33 ; sum 10.00
    tot = inv.compute_totals(
        [{"quantity": 1, "unit_price_net": 100, "vat_rate": 0.21},
         {"quantity": 1, "unit_price_net": 50, "vat_rate": 0.12}],
        disc_kind="amount", disc_value=10)
    allocs = sorted(r["alloc"] for r in tot["by_rate"])
    assert money.fsum(allocs) == 10.0          # Σalloc == the discount EXACTLY
    assert tot["doc_discount"] == 10.0
    assert tot["net_total"] == 140.0           # 150 − 10
    # VAT recomputed on the post-discount per-rate net
    assert tot["vat_total"] == money.fsum([r["vat"] for r in tot["by_rate"]])


def test_multi_rate_line_and_document_discount_exact_cents(inv):
    _set_issuer(inv)
    c = _customer(inv)
    d, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    # line 1: 2 x 50 @ 21%, 10% line discount -> net 90.00, vat 18.90
    inv.add_line(d["id"], description="A", quantity=2, unit_price_net=50, vat_rate=0.21,
                 discount_kind="percent", discount_value=10)
    # line 2: 1 x 100 @ 12%, 5.00 amount line discount -> net 95.00, vat 11.40
    inv.add_line(d["id"], description="B", quantity=1, unit_price_net=100, vat_rate=0.12,
                 discount_kind="amount", discount_value=5)
    # document discount 10% of (90 + 95 = 185) = 18.50 allocated 90/185 and 95/185
    inv.set_document_discount(d["id"], discount_kind="percent", discount_value=10)
    hdr = inv.get_invoice(d["id"])
    # alloc: 90/185*18.50 = 9.00 ; 95/185*18.50 = 9.50 ; sum 18.50
    # post-doc net: 81.00 @ 21% (vat 17.01) and 85.50 @ 12% (vat 10.26)
    assert hdr["disc_amount"] == 18.5
    assert hdr["net_total"] == money.f2(81.0 + 85.5) == 166.5
    assert hdr["vat_total"] == money.f2(money.f2(81.0 * 0.21) + money.f2(85.5 * 0.12))
    assert hdr["vat_total"] == money.f2(17.01 + 10.26) == 27.27
    assert hdr["gross_total"] == money.f2(166.5 + 27.27) == 193.77


# ====================================================================== EN-16931 allowances
def _ns():
    return {"cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
            "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"}


def _issue_with_discounts(inv):
    _set_issuer(inv)
    c = _customer(inv)
    d, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(d["id"], description="A", quantity=2, unit_price_net=50, vat_rate=0.21,
                 discount_kind="percent", discount_value=10)   # net 90.00
    inv.add_line(d["id"], description="B", quantity=1, unit_price_net=100, vat_rate=0.12,
                 discount_kind="amount", discount_value=5)     # net 95.00
    inv.set_document_discount(d["id"], discount_kind="percent", discount_value=10)
    issued, err = inv.issue(d["id"], issued_by="pytest", issue_date="2026-03-10")
    assert err == "", err
    return issued


def test_einvoice_emits_line_and_document_allowances_and_ties_out(inv):
    issued = _issue_with_discounts(inv)
    xml = inv.einvoice_xml(issued["id"])
    root = ET.fromstring(xml)
    ns = _ns()

    # LINE allowances: each InvoiceLine with a discount carries an AllowanceCharge (false)
    line_allowances = []
    for il in root.findall("cac:InvoiceLine", ns):
        for ac in il.findall("cac:AllowanceCharge", ns):
            ind = ac.find("cbc:ChargeIndicator", ns).text
            amt = float(ac.find("cbc:Amount", ns).text)
            assert ind == "false"
            line_allowances.append(amt)
    assert sorted(line_allowances) == [5.0, 10.0]

    # DOCUMENT allowances: top-level AllowanceCharge per rate (ChargeIndicator false)
    doc_allow = []
    for ac in root.findall("cac:AllowanceCharge", ns):
        assert ac.find("cbc:ChargeIndicator", ns).text == "false"
        doc_allow.append(float(ac.find("cbc:Amount", ns).text))
    assert money.fsum(doc_allow) == 18.5

    # LegalMonetaryTotal: BT-106 line ext = 185.00, BT-107 allowance = 18.50,
    # BT-109 tax-exclusive = 166.50 (post both discounts), BT-112 tax-inclusive ties out.
    lmt = root.find("cac:LegalMonetaryTotal", ns)
    line_ext = float(lmt.find("cbc:LineExtensionAmount", ns).text)
    allow_tot = float(lmt.find("cbc:AllowanceTotalAmount", ns).text)
    tax_excl = float(lmt.find("cbc:TaxExclusiveAmount", ns).text)
    tax_incl = float(lmt.find("cbc:TaxInclusiveAmount", ns).text)
    assert line_ext == 185.0
    assert allow_tot == 18.5
    assert tax_excl == 166.5
    # BR-CO-13: TaxExclusive == LineExtension − AllowanceTotal
    assert money.f2(line_ext - allow_tot) == tax_excl

    # TaxSubtotals: post-discount taxable amounts + VAT, summing to TaxAmount (BT-110)
    tt = root.find("cac:TaxTotal", ns)
    bt110 = float(tt.find("cbc:TaxAmount", ns).text)
    sub_taxable, sub_vat = [], []
    for sub in tt.findall("cac:TaxSubtotal", ns):
        sub_taxable.append(float(sub.find("cbc:TaxableAmount", ns).text))
        sub_vat.append(float(sub.find("cbc:TaxAmount", ns).text))
    assert money.fsum(sub_taxable) == tax_excl       # taxable nets == BT-109
    assert money.fsum(sub_vat) == bt110              # per-rate VAT ties to BT-110
    assert money.f2(tax_excl + bt110) == tax_incl    # gross ties out
    # the exact cents
    assert sorted(sub_taxable) == [81.0, 85.5]
    assert bt110 == money.f2(17.01 + 10.26) == 27.27


def test_einvoice_round_trips_through_parse_einvoice(inv):
    import extract
    issued = _issue_with_discounts(inv)
    xml = inv.einvoice_xml(issued["id"])
    parsed = extract.parse_einvoice(xml)
    assert parsed is not None
    # the per-line net total (BT-131 sum, post LINE discount) is recovered
    net = money.fsum([ln["net"] for ln in parsed["lines"]])
    assert net == 185.0                              # post line, pre document discount
    # the gross tie-out figure (net+VAT, post BOTH discounts) ties to the invoice gross
    hdr = inv.get_invoice(issued["id"])
    assert parsed.get("coversheet_total") == money.f2(hdr["gross_total"])


# ====================================================================== C) PROFORMA / QUOTE
def test_proforma_uses_own_series_no_legal_collision(inv):
    _set_issuer(inv)
    c = _customer(inv)
    # issue a real invoice first -> INV-2026-000001
    di, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(di["id"], description="X", quantity=1, unit_price_net=10, vat_rate=0.21)
    iss_inv, _ = inv.issue(di["id"], issued_by="pytest", issue_date="2026-03-10")
    # a proforma -> PROF-2026-000001 (its OWN series, not the legal invoice series)
    dp, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False, doc_type="proforma")
    inv.add_line(dp["id"], description="X", quantity=1, unit_price_net=10, vat_rate=0.21)
    iss_pf, _ = inv.issue(dp["id"], issued_by="pytest", issue_date="2026-03-10")
    # a quote -> PIED-2026-000001
    dq, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False, doc_type="quote")
    inv.add_line(dq["id"], description="X", quantity=1, unit_price_net=10, vat_rate=0.21)
    iss_qt, _ = inv.issue(dq["id"], issued_by="pytest", issue_date="2026-03-10")

    assert iss_inv["number"].startswith("INV-")
    assert iss_pf["number"].startswith("PROF-")
    assert iss_qt["number"].startswith("PIED-")
    # the proforma/quote NEVER took a number from the legal invoice series
    assert iss_pf["number"] != iss_inv["number"]
    assert "INV-" not in iss_pf["number"] and "INV-" not in iss_qt["number"]
    # the legal series counter is untouched by the proforma/quote: the NEXT real invoice is 2
    d2, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(d2["id"], description="X", quantity=1, unit_price_net=10, vat_rate=0.21)
    iss2, _ = inv.issue(d2["id"], issued_by="pytest", issue_date="2026-03-11")
    assert iss2["number"] == "INV-2026-000002"


def test_proforma_excluded_from_vat_revenue_and_ar(inv):
    _set_issuer(inv)
    c = _customer(inv)
    # a REAL invoice in March (100 @ 21%)
    di, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(di["id"], description="Real", quantity=1, unit_price_net=100, vat_rate=0.21)
    inv.issue(di["id"], issued_by="pytest", issue_date="2026-03-10")
    # a PROFORMA in March (9999 @ 21%) — must NOT count anywhere
    dp, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False, doc_type="proforma")
    inv.add_line(dp["id"], description="Proforma", quantity=1, unit_price_net=9999,
                 vat_rate=0.21)
    inv.issue(dp["id"], issued_by="pytest", issue_date="2026-03-10")

    vat = IR.vat_output_report("2026-03-01", "2026-03-31", "2026-03")
    assert vat["net_total"] == 100.0           # the proforma's 9999 is absent
    assert vat["vat_total"] == money.f2(100.0 * 0.21)

    rev = IR.revenue_report("2026-03-01", "2026-03-31", "2026-03")
    assert rev["net_total"] == 100.0
    assert rev["invoice_count"] == 1           # only the real invoice counts

    ar = inv.accounts_receivable(today="2026-03-15")
    # only the real invoice's outstanding (100 + 21 = 121) appears
    assert ar["total_outstanding"] == 121.0


def test_proforma_has_no_output_vat_and_no_einvoice(inv):
    _set_issuer(inv)
    c = _customer(inv)
    dp, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False, doc_type="proforma")
    inv.add_line(dp["id"], description="P", quantity=1, unit_price_net=100, vat_rate=0.21)
    iss, _ = inv.issue(dp["id"], issued_by="pytest", issue_date="2026-03-10")
    # a proforma carries no e-invoice (not a tax invoice)
    with pytest.raises(ValueError):
        inv.einvoice_xml(iss["id"])
    # the PDF/HTML loudly labels it as not a VAT invoice
    html = inv.invoice_html(iss["id"])
    assert "PROFORMA" in html.upper()
    txt = inv.invoice_text(iss["id"])
    assert "PROFORMA INVOICE" in txt and "not a VAT invoice" in txt


def test_convert_to_invoice_copies_lines_and_discounts_and_issues(inv):
    _set_issuer(inv)
    c = _customer(inv)
    dp, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False, doc_type="proforma")
    inv.add_line(dp["id"], description="A", quantity=2, unit_price_net=50, vat_rate=0.21,
                 discount_kind="percent", discount_value=10)
    inv.add_line(dp["id"], description="B", quantity=1, unit_price_net=100, vat_rate=0.12)
    inv.set_document_discount(dp["id"], discount_kind="amount", discount_value=10)
    inv.issue(dp["id"], issued_by="pytest", issue_date="2026-03-10")

    real, err = inv.convert_to_invoice(dp["id"], created_by="pytest")
    assert err == "" and real is not None
    assert real["doc_type"] == invoicing.DOC_INVOICE
    assert real["status"] == invoicing.STATUS_DRAFT
    assert real["converted_from_id"] == dp["id"]
    # lines + discounts copied
    rlines = inv.get_lines(real["id"])
    assert len(rlines) == 2
    assert rlines[0]["discount_kind"] == "percent" and rlines[0]["discount_amount"] == 10.0
    assert real["disc_kind"] == "amount" and real["disc_value"] == 10.0
    # it issues normally and gets a REAL legal number
    iss, ierr = inv.issue(real["id"], issued_by="pytest", issue_date="2026-03-11")
    assert ierr == "" and iss["number"].startswith("INV-")


def test_cannot_convert_an_ordinary_invoice(inv):
    _set_issuer(inv)
    c = _customer(inv)
    di, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(di["id"], description="X", quantity=1, unit_price_net=10, vat_rate=0.21)
    inv.issue(di["id"], issued_by="pytest", issue_date="2026-03-10")
    out, err = inv.convert_to_invoice(di["id"])
    assert out is None and "proforma" in err


def test_proforma_refuses_payment(inv):
    _set_issuer(inv)
    c = _customer(inv)
    dp, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False, doc_type="proforma")
    inv.add_line(dp["id"], description="P", quantity=1, unit_price_net=100, vat_rate=0.21)
    iss, _ = inv.issue(dp["id"], issued_by="pytest", issue_date="2026-03-10")
    out, err = inv.record_payment(iss["id"], 50)
    assert out is None and "proforma" in err.lower()


# ====================================================================== i18n
def test_i18n_en_default_lv_catalog():
    import i18n
    # EN default: the string returns unchanged
    assert i18n.t("Document discount") == "Document discount"
    assert i18n.t("Convert to invoice") == "Convert to invoice"
    # LV catalog entries exist
    assert i18n.t("Document discount", "lv") == "Dokumenta atlaide"
    assert i18n.t("Convert to invoice", "lv") == "Pārvērst par rēķinu"
    assert i18n.t("PROFORMA INVOICE", "lv") == "Priekšapmaksas rēķins"
    assert i18n.t("QUOTE", "lv") == "Piedāvājums"

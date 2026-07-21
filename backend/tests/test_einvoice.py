"""Structured e-invoice XML recognition: UBL 2.1, UN-CEFACT CII, and the
Factur-X/ZUGFeRD hybrid-PDF embedded-XML path."""

import io
from decimal import Decimal

import pytest

from app.services import einvoice, parser

UBL = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>INV-UBL-7788</cbc:ID>
  <cbc:IssueDate>2026-04-01</cbc:IssueDate>
  <cbc:DueDate>2026-05-01</cbc:DueDate>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PartyLegalEntity><cbc:RegistrationName>Northwind Traders GmbH</cbc:RegistrationName></cac:PartyLegalEntity>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:LegalMonetaryTotal><cbc:PayableAmount currencyID="EUR">363.00</cbc:PayableAmount></cac:LegalMonetaryTotal>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID>
    <cbc:InvoicedQuantity unitCode="EA">2</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="EUR">200.00</cbc:LineExtensionAmount>
    <cac:Item><cbc:Name>Managed database</cbc:Name>
      <cac:ClassifiedTaxCategory><cbc:Percent>21</cbc:Percent></cac:ClassifiedTaxCategory></cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="EUR">100.00</cbc:PriceAmount></cac:Price>
  </cac:InvoiceLine>
  <cac:InvoiceLine>
    <cbc:ID>2</cbc:ID>
    <cbc:InvoicedQuantity unitCode="EA">4</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="EUR">100.00</cbc:LineExtensionAmount>
    <cac:Item><cbc:Name>CDN bandwidth</cbc:Name>
      <cac:ClassifiedTaxCategory><cbc:Percent>21</cbc:Percent></cac:ClassifiedTaxCategory></cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="EUR">25.00</cbc:PriceAmount></cac:Price>
  </cac:InvoiceLine>
</Invoice>"""

CII = """<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
  xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
  xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
  xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocument><ram:ID>INV-CII-4242</ram:ID>
    <ram:IssueDateTime><udt:DateTimeString format="102">20260415</udt:DateTimeString></ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:IncludedSupplyChainTradeLineItem>
      <ram:SpecifiedTradeProduct><ram:Name>Consulting day</ram:Name></ram:SpecifiedTradeProduct>
      <ram:SpecifiedLineTradeAgreement>
        <ram:NetPriceProductTradePrice><ram:ChargeAmount>800.00</ram:ChargeAmount></ram:NetPriceProductTradePrice>
      </ram:SpecifiedLineTradeAgreement>
      <ram:SpecifiedLineTradeDelivery><ram:BilledQuantity unitCode="DAY">3</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>
      <ram:SpecifiedLineTradeSettlement>
        <ram:ApplicableTradeTax><ram:RateApplicablePercent>20</ram:RateApplicablePercent></ram:ApplicableTradeTax>
        <ram:SpecifiedTradeSettlementLineMonetarySummation><ram:LineTotalAmount>2400.00</ram:LineTotalAmount></ram:SpecifiedTradeSettlementLineMonetarySummation>
      </ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty><ram:Name>Globex Consulting SA</ram:Name></ram:SellerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:TaxTotalAmount currencyID="EUR">480.00</ram:TaxTotalAmount>
        <ram:GrandTotalAmount>2880.00</ram:GrandTotalAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>"""


def test_parse_ubl():
    r = parser.parse_invoice_file("inv.xml", UBL.encode())
    d = r.draft
    assert d.vendor_name == "Northwind Traders GmbH"
    assert d.invoice_number == "INV-UBL-7788"
    assert str(d.issue_date) == "2026-04-01"
    assert d.currency == "EUR"
    assert len(d.line_items) == 2
    # Regression: leaf Elements are falsy in ElementTree; quantities must survive.
    assert [li.quantity for li in d.line_items] == [Decimal("2"), Decimal("4")]
    assert d.line_items[0].amount == Decimal("200.00")
    assert d.line_items[0].tax_rate == Decimal("21")


def test_parse_cii():
    r = parser.parse_invoice_file("inv.xml", CII.encode())
    d = r.draft
    assert d.vendor_name == "Globex Consulting SA"
    assert d.invoice_number == "INV-CII-4242"
    assert str(d.issue_date) == "2026-04-15"  # 20260415 (format 102)
    assert len(d.line_items) == 1
    assert d.line_items[0].quantity == Decimal("3")
    assert d.line_items[0].amount == Decimal("2400.00")
    assert d.line_items[0].tax_rate == Decimal("20")


def test_content_sniff_without_xml_extension():
    # A generic filename still recognised as an e-invoice by content.
    r = parser.parse_invoice_file("attachment.dat", UBL.encode())
    assert r.draft.invoice_number == "INV-UBL-7788"


def test_reject_non_einvoice_xml():
    with pytest.raises(ValueError):
        einvoice.parse_xml_bytes(b"<foo><bar>1</bar></foo>", "x.xml")


@pytest.mark.asyncio
async def test_ubl_upload_and_save(auth_client, parse_upload):
    files = {"file": ("invoice.xml", io.BytesIO(UBL.encode()), "application/xml")}
    up = await parse_upload(auth_client, files)
    draft = up["draft"]
    saved = await auth_client.post("/api/v1/invoices", json=draft)
    assert saved.status_code == 201, saved.text
    body = saved.json()
    # 2*100 + 4*25 = 300 subtotal; 21% ⇒ 63.00 tax ⇒ 363.00 total (matches header).
    assert body["subtotal"] == "300.00"
    assert body["tax_amount"] == "63.00"
    assert body["total"] == "363.00"


def _facturx_pdf(cii_xml: str) -> bytes:
    reportlab = pytest.importorskip("reportlab")  # noqa: F841
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(72, 800, "Hybrid Factur-X invoice")
    c.showPage()
    c.save()
    buf.seek(0)
    writer = PdfWriter()
    writer.append_pages_from_reader(PdfReader(buf))
    writer.add_attachment("factur-x.xml", cii_xml.encode("utf-8"))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_facturx_hybrid_pdf_uses_embedded_xml_not_ocr():
    pytest.importorskip("pypdf")
    pdf = _facturx_pdf(CII)
    assert einvoice.extract_embedded_xml(pdf) is not None
    r = parser.parse_invoice_file("hybrid.pdf", pdf)
    # Read from the embedded structured XML — exact, no OCR.
    assert any("embedded in this PDF" in w for w in r.warnings)
    assert r.draft.invoice_number == "INV-CII-4242"
    assert r.draft.line_items[0].quantity == Decimal("3")

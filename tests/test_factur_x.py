"""Hybrid PDF intake (Factur-X / ZUGFeRD): the EN 16931 invoice XML embedded inside the
PDF is extracted and routed through the SAME deterministic `parse_einvoice` path — high
confidence, zero AI — and the ORIGINAL PDF (not the XML) is what gets vaulted."""
import io

import pytest

# Minimal UN/CEFACT CII invoice — this is exactly Factur-X/ZUGFeRD's structured format,
# and `parse_einvoice` already handles `CrossIndustryInvoice` (see test_einvoice for UBL).
CII = b"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
 xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
 xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
 <rsm:ExchangedDocument><ram:ID>FX-2026-77</ram:ID>
  <ram:IssueDateTime><ram:DateTimeString format="102">20260531</ram:DateTimeString></ram:IssueDateTime>
 </rsm:ExchangedDocument>
 <rsm:SupplyChainTradeTransaction>
  <ram:IncludedSupplyChainTradeLineItem>
   <ram:SpecifiedLineTradeSettlement>
    <ram:SpecifiedTradeSettlementLineMonetarySummation>
     <ram:LineTotalAmount>800.00</ram:LineTotalAmount>
    </ram:SpecifiedTradeSettlementLineMonetarySummation>
   </ram:SpecifiedLineTradeSettlement>
  </ram:IncludedSupplyChainTradeLineItem>
  <ram:ApplicableHeaderTradeAgreement>
   <ram:SellerTradeParty><ram:Name>Toll Collect GmbH</ram:Name>
    <ram:SpecifiedTaxRegistration><ram:ID>DE811569869</ram:ID></ram:SpecifiedTaxRegistration>
   </ram:SellerTradeParty>
  </ram:ApplicableHeaderTradeAgreement>
  <ram:ApplicableHeaderTradeSettlement>
   <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
  </ram:ApplicableHeaderTradeSettlement>
 </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>"""


def _hybrid_pdf(attachment_name="factur-x.xml", xml=CII):
    """Build a real hybrid PDF: one blank page + the invoice XML as an embedded file."""
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.add_attachment(attachment_name, xml)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _plain_pdf():
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_hybrid_pdf_parsed_no_ai():
    import extract as EX
    pdf = _hybrid_pdf()
    # backend="none" => no AI is even reachable, so any extracted line MUST come from the
    # embedded XML (a manual/empty draft would have zero lines).
    d = EX.extract(pdf, "inv.pdf", backend="none")
    assert d["confidence"] == "high"
    assert d["backend"] == "e-invoice"
    assert "Factur-X" in d["notes"]
    assert d["supplier"] == "Toll Collect GmbH"
    assert d["statement_ref"] == "FX-2026-77"
    assert d["statement_date"] == "2026-05-31"
    assert len(d["lines"]) == 1 and d["lines"][0]["net"] == 800.0
    # the ORIGINAL hybrid PDF is vaulted, NOT the bare XML
    assert d["_pdf_bytes"][0][1] == pdf
    assert d["_pdf_bytes"][0][1] != CII
    assert d["files"][0]["name"] == "inv.pdf"


def test_embedded_xml_extraction():
    import extract as EX
    assert EX._pdf_embedded_xml(_hybrid_pdf()) == CII
    # plain PDF with no attachment -> None (no exception)
    assert EX._pdf_embedded_xml(_plain_pdf()) is None
    # corrupt / garbage bytes -> None (no exception)
    assert EX._pdf_embedded_xml(b"not a pdf at all") is None
    assert EX._pdf_embedded_xml(b"") is None


def test_embedded_xml_by_unconventional_name():
    """A non-standard attachment filename is still accepted when its root is an invoice."""
    import extract as EX
    pdf = _hybrid_pdf(attachment_name="my-invoice-data.xml")
    assert EX._pdf_embedded_xml(pdf) == CII


def test_embedded_non_invoice_attachment_ignored():
    """A .xml attachment that is NOT an invoice document is ignored (falls through)."""
    import extract as EX
    pdf = _hybrid_pdf(attachment_name="metadata.xml", xml=b"<note>hello</note>")
    assert EX._pdf_embedded_xml(pdf) is None


def test_oversize_attachment_rejected(monkeypatch):
    """An attachment past the zip-bomb cap is rejected (None), even if conventionally named."""
    import extract as EX
    monkeypatch.setattr(EX, "ZIP_MAX_MEMBER_BYTES", 16)
    assert EX._pdf_embedded_xml(_hybrid_pdf()) is None


def test_plain_pdf_falls_through():
    import extract as EX
    pdf = _plain_pdf()
    # A non-hybrid PDF must take the normal path: backend="parser" with no recognised
    # supplier yields the empty manual draft, unchanged by this feature.
    d = EX.extract(pdf, "plain.pdf", backend="parser")
    assert d["backend"] == "none"
    assert d["lines"] == []
    assert "Factur-X" not in (d.get("notes") or "")

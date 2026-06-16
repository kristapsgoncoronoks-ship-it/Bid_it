"""Factur-X / ZUGFeRD / EN-16931 PROFILE gate (A1).

Only the EN 16931 ("comfort") and EXTENDED profiles are guaranteed to carry per-LINE
detail. MINIMUM and BASIC-WL legally omit invoice lines (header/VAT totals only), so a
draft built from them must NOT be trusted as a complete high-confidence capture — it is
downgraded to 'medium' and the reviewer is told to confirm lines against the PDF. A
totals-only document (no structured lines) downgrades the same way regardless of profile.
"""
import io


def _cii(profile_urn=None, doc_id="FX-1", with_line=True):
    """A UN/CEFACT CII invoice; optionally declares a profile and/or a real line item."""
    ctx = ""
    if profile_urn is not None:
        ctx = (f"<rsm:ExchangedDocumentContext>"
               f"<ram:GuidelineSpecifiedDocumentContextParameter>"
               f"<ram:ID>{profile_urn}</ram:ID>"
               f"</ram:GuidelineSpecifiedDocumentContextParameter>"
               f"</rsm:ExchangedDocumentContext>")
    line = ""
    if with_line:
        line = ("<ram:IncludedSupplyChainTradeLineItem>"
                "<ram:SpecifiedLineTradeSettlement>"
                "<ram:SpecifiedTradeSettlementLineMonetarySummation>"
                "<ram:LineTotalAmount>100.00</ram:LineTotalAmount>"
                "</ram:SpecifiedTradeSettlementLineMonetarySummation>"
                "</ram:SpecifiedLineTradeSettlement>"
                "</ram:IncludedSupplyChainTradeLineItem>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rsm:CrossIndustryInvoice'
        ' xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"'
        ' xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">'
        + ctx +
        f"<rsm:ExchangedDocument><ram:ID>{doc_id}</ram:ID>"
        '<ram:IssueDateTime><ram:DateTimeString format="102">20260531</ram:DateTimeString></ram:IssueDateTime>'
        "</rsm:ExchangedDocument>"
        "<rsm:SupplyChainTradeTransaction>"
        + line +
        "<ram:ApplicableHeaderTradeAgreement>"
        "<ram:SellerTradeParty><ram:Name>DKV Euro Service GmbH</ram:Name>"
        "<ram:SpecifiedTaxRegistration><ram:ID>DE811569869</ram:ID></ram:SpecifiedTaxRegistration>"
        "</ram:SellerTradeParty></ram:ApplicableHeaderTradeAgreement>"
        "<ram:ApplicableHeaderTradeSettlement>"
        "<ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>"
        "</ram:ApplicableHeaderTradeSettlement>"
        "</rsm:SupplyChainTradeTransaction>"
        "</rsm:CrossIndustryInvoice>"
    ).encode()


# header-totals-only UBL (no InvoiceLine), names the EN-16931 totals fields
UBL_TOTALS_ONLY = (
    '<?xml version="1.0"?>'
    '<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"'
    ' xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
    "<cbc:ID>INV-TOT</cbc:ID><cbc:IssueDate>2026-05-31</cbc:IssueDate>"
    "<cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>"
    "<cbc:TaxExclusiveAmount currencyID=\"EUR\">500.00</cbc:TaxExclusiveAmount>"
    "<cbc:TaxAmount currencyID=\"EUR\">105.00</cbc:TaxAmount>"
    "</Invoice>"
).encode()


def _hybrid_pdf(xml, name="factur-x.xml"):
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.add_attachment(name, xml)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


# --------------------------------------------------------------- profile detection unit
def test_profile_detection_and_ordering():
    import xml.etree.ElementTree as ET
    import extract as EX
    cases = {
        "urn:factur-x.eu:1p0:minimum": "minimum",
        "urn:factur-x.eu:1p0:basicwl": "basicwl",
        "urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:basic": "basic",
        "urn:cen.eu:en16931:2017": "en16931",
        "urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended": "extended",
        "urn:cen.eu:en16931:2017#compliant#urn:xeinvoicing.de:cius:xrechnung_3.0": "xrechnung",
    }
    for urn, want in cases.items():
        root = ET.fromstring(_cii(profile_urn=urn))
        assert EX._einvoice_profile(root) == want, urn
    # no context declared -> None
    assert EX._einvoice_profile(ET.fromstring(_cii(profile_urn=None))) is None


# --------------------------------------------------------------- line-bearing profiles -> high
def test_en16931_profile_with_lines_stays_high():
    import extract as EX
    d = EX.extract(_cii("urn:cen.eu:en16931:2017", with_line=True), "invoice.xml")
    assert d["confidence"] == "high"
    assert d["profile"] == "en16931"
    assert len(d["lines"]) == 1 and d["lines"][0]["net"] == 100.0


def test_extended_profile_with_lines_stays_high():
    import extract as EX
    d = EX.extract(_cii("urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended"),
                   "invoice.xml")
    assert d["confidence"] == "high" and d["profile"] == "extended"


# --------------------------------------------------------------- line-less profiles -> medium
def test_minimum_profile_downgraded_even_with_a_line():
    """MINIMUM never carries reliable line detail — downgrade on the profile alone."""
    import extract as EX
    d = EX.extract(_cii("urn:factur-x.eu:1p0:minimum", with_line=True), "invoice.xml")
    assert d["confidence"] == "medium"
    assert d["profile"] == "minimum"
    assert "line-item detail" in d["notes"]


def test_basicwl_profile_downgraded():
    import extract as EX
    d = EX.extract(_cii("urn:factur-x.eu:1p0:basicwl", with_line=False), "invoice.xml")
    assert d["confidence"] == "medium" and d["profile"] == "basicwl"


def test_totals_only_downgraded_without_profile():
    """A document with no structured lines falls back to header totals -> medium."""
    import extract as EX
    d = EX.extract(UBL_TOTALS_ONLY, "invoice.xml")
    assert d["confidence"] == "medium"
    assert len(d["lines"]) == 1 and d["lines"][0]["net"] == 500.0
    assert "header" in d["notes"] or "line-item detail" in d["notes"]


# --------------------------------------------------------------- hybrid PDF keeps the gate
def test_hybrid_minimum_profile_is_medium_and_vaults_pdf():
    import extract as EX
    pdf = _hybrid_pdf(_cii("urn:factur-x.eu:1p0:minimum", with_line=True))
    d = EX.extract(pdf, "invoice.pdf", backend="none")
    assert d["confidence"] == "medium" and d["profile"] == "minimum"
    # the ORIGINAL hybrid PDF (not the bare XML) is what gets vaulted
    assert d["_pdf_bytes"] and d["_pdf_bytes"][0][1] == pdf


def test_hybrid_en16931_profile_is_high():
    import extract as EX
    pdf = _hybrid_pdf(_cii("urn:cen.eu:en16931:2017", with_line=True))
    d = EX.extract(pdf, "invoice.pdf", backend="none")
    assert d["confidence"] == "high" and d["profile"] == "en16931"

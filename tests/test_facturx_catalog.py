"""Factur-X / ZUGFeRD embedded-XML extraction: the catalog /Names /EmbeddedFiles
name-tree fallback (extract._embedded_files_via_catalog) and the end-to-end probe
(extract._pdf_embedded_xml). The fallback path runs when the modern
`reader.attachments` API is unavailable or raises on a document, so it must still
recover the embedded invoice XML from the PDF catalog name tree."""
import io
import pypdf
import pytest
import extract


# A minimal but valid CII (Factur-X/ZUGFeRD) invoice root — recognised by
# extract._xml_invoice_root, so it is found even without a conventional filename.
CII_XML = (b'<?xml version="1.0" encoding="UTF-8"?>'
           b'<rsm:CrossIndustryInvoice '
           b'xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100" '
           b'xmlns:ram="urn:un:unece:uncefact:data:standard:'
           b'ReusableAggregateBusinessInformationEntity:100">'
           b'<rsm:ExchangedDocument><ram:ID>INV-42</ram:ID></rsm:ExchangedDocument>'
           b'</rsm:CrossIndustryInvoice>')


def _hybrid_pdf(attachment_name, xml=CII_XML):
    """A 1-page PDF carrying `xml` as an embedded file under `attachment_name`
    (pypdf writes the /Root /Names /EmbeddedFiles name tree)."""
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.add_attachment(attachment_name, xml)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_catalog_fallback_reads_embedded_file_directly():
    # The fallback walks /Root /Names /EmbeddedFiles directly off a real reader.
    pdf = _hybrid_pdf("factur-x.xml")
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    out = extract._embedded_files_via_catalog(reader)
    assert "factur-x.xml" in out
    assert out["factur-x.xml"] == CII_XML


def test_catalog_fallback_empty_on_plain_pdf():
    # No embedded files -> the fallback returns an empty dict, never raises.
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    reader = pypdf.PdfReader(io.BytesIO(buf.getvalue()))
    assert extract._embedded_files_via_catalog(reader) == {}


def test_pdf_embedded_xml_primary_path():
    # End-to-end: a conventionally-named Factur-X attachment is returned.
    assert extract._pdf_embedded_xml(_hybrid_pdf("factur-x.xml")) == CII_XML


def test_pdf_embedded_xml_falls_back_to_catalog(monkeypatch):
    # When reader.attachments raises (older pypdf / a document it chokes on), the
    # probe must fall through to the catalog name-tree walk and still recover the XML.
    def _boom(self):
        raise RuntimeError("attachments API unavailable on this document")
    monkeypatch.setattr(pypdf.PdfReader, "attachments", property(_boom))
    assert extract._pdf_embedded_xml(_hybrid_pdf("factur-x.xml")) == CII_XML


def test_pdf_embedded_xml_finds_unconventional_name_via_invoice_root(monkeypatch):
    # An attachment NOT in the known-name list is still found via its invoice root,
    # exercised through the catalog fallback.
    def _boom(self):
        raise RuntimeError("attachments API unavailable")
    monkeypatch.setattr(pypdf.PdfReader, "attachments", property(_boom))
    assert extract._pdf_embedded_xml(_hybrid_pdf("invoice_data.xml")) == CII_XML


def test_pdf_embedded_xml_plain_pdf_returns_none():
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    assert extract._pdf_embedded_xml(buf.getvalue()) is None

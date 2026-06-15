"""Per-file handling of MIXED hybrid/plain PDF batches in extract.extract().

Today's two paths (ALL-hybrid → deterministic _facturx_draft; ALL-plain → parser/AI)
must stay byte-identical; only the previously-degenerate MIXED case changes: the hybrid
PDFs are parsed deterministically from their embedded XML, the plain PDFs via the
parser/AI path, and the results are merged — without losing a line or dropping a PDF
from the vault. Offline, in-memory ZIP batches only; deterministic."""
import io
import zipfile
import pypdf
import pytest
import extract


# A minimal but valid CII (Factur-X/ZUGFeRD) invoice root with one line — recognised by
# extract._xml_invoice_root and parsed by parse_einvoice.
def _cii(inv_no="INV-42", supplier="ACME Fuel"):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rsm:CrossIndustryInvoice '
        'xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100" '
        'xmlns:ram="urn:un:unece:uncefact:data:standard:'
        'ReusableAggregateBusinessInformationEntity:100">'
        '<rsm:ExchangedDocument><ram:ID>' + inv_no + '</ram:ID></rsm:ExchangedDocument>'
        '<rsm:SupplyChainTradeTransaction>'
        '<ram:ApplicableHeaderTradeAgreement>'
        '<ram:SellerTradeParty><ram:Name>' + supplier + '</ram:Name></ram:SellerTradeParty>'
        '</ram:ApplicableHeaderTradeAgreement>'
        '<ram:ApplicableHeaderTradeSettlement>'
        '<ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>'
        '</ram:ApplicableHeaderTradeSettlement>'
        '<ram:IncludedSupplyChainTradeLineItem>'
        '<ram:SpecifiedLineTradeSettlement>'
        '<ram:SpecifiedTradeSettlementLineMonetarySummation>'
        '<ram:LineTotalAmount>100.00</ram:LineTotalAmount>'
        '</ram:SpecifiedTradeSettlementLineMonetarySummation>'
        '</ram:SpecifiedLineTradeSettlement>'
        '</ram:IncludedSupplyChainTradeLineItem>'
        '</rsm:SupplyChainTradeTransaction>'
        '</rsm:CrossIndustryInvoice>'
    ).encode("utf-8")


def _hybrid_pdf(attachment_name="factur-x.xml", xml=None):
    """A 1-page PDF carrying `xml` as an embedded Factur-X file."""
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.add_attachment(attachment_name, xml if xml is not None else _cii())
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _plain_pdf():
    """A 1-page PDF with NO embedded invoice XML."""
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _zip(members):
    """members: list of (name, bytes) -> in-memory ZIP bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members:
            z.writestr(name, data)
    return buf.getvalue()


# --------------------------------------------------------------- regression: ALL-hybrid
def test_all_hybrid_batch_unchanged():
    h1 = _hybrid_pdf(xml=_cii("INV-1", "Alpha"))
    h2 = _hybrid_pdf(xml=_cii("INV-2", "Alpha"))
    batch = _zip([("a.pdf", h1), ("b.pdf", h2)])
    d = extract.extract(batch, "batch.zip", backend="parser")
    assert d["backend"] == "e-invoice"
    assert d["confidence"] == "high"
    # both hybrids vaulted (original PDFs, not the XML)
    assert len(d["_pdf_bytes"]) == 2
    assert {b for _, b in d["_pdf_bytes"]} == {h1, h2}
    assert len(d["files"]) == 2


# --------------------------------------------------------------- regression: ALL-plain
def test_all_plain_batch_unchanged():
    p1 = _plain_pdf()
    p2 = _plain_pdf()
    batch = _zip([("a.pdf", p1), ("b.pdf", p2)])
    d = extract.extract(batch, "batch.zip", backend="parser")
    # No parser matches a blank page, no AI configured -> empty "enter manually" draft.
    assert d["backend"] == "none"
    assert "enter manually" in d["notes"]
    assert d["lines"] == []
    assert len(d["_pdf_bytes"]) == 2

    # Identical to driving the plain path directly with the same inputs.
    files = extract.unpack(batch, "batch.zip")
    texts = [(n, extract.pdf_text(b)) for n, b in files]
    direct = extract._plain_draft(texts, files, "parser", "batch.zip", False)
    assert direct["backend"] == d["backend"]
    assert direct["notes"] == d["notes"]
    assert direct["lines"] == d["lines"]


# --------------------------------------------------------------- the new MIXED path
def test_mixed_batch_merges_and_vaults_all():
    h = _hybrid_pdf(xml=_cii("INV-9", "Structured Co"))
    p = _plain_pdf()
    batch = _zip([("hybrid.pdf", h), ("plain.pdf", p)])
    d = extract.extract(batch, "batch.zip", backend="parser")  # no AI
    assert d["backend"] == "mixed"
    assert d["confidence"] == "medium"
    # structured header wins
    assert d["supplier"] == "Structured Co"
    # the hybrid's structured line(s) are present
    assert any(ln.get("invoice_no") == "INV-9" or ln.get("invoice_ref") == "INV-9"
               or "INV-9" in str(ln) for ln in d["lines"])
    # BOTH PDFs vaulted
    assert len(d["_pdf_bytes"]) == 2
    assert {b for _, b in d["_pdf_bytes"]} == {h, p}
    assert len(d["files"]) == 2
    # clear reviewer note
    assert "Mixed batch" in d["notes"]
    assert "verify before submitting" in d["notes"]


# --------------------------------------------------------------- _merge_mixed unit
def test_merge_mixed_never_drops_line_prefers_structured_vaults_all():
    h_draft = {
        "supplier": "Structured Co", "statement_ref": "S-1", "statement_date": "2026-01-01",
        "currency": "EUR", "customer": "Cust A",
        "lines": [{"invoice_no": "H1"}, {"invoice_no": "H2"}],
        "files": [{"name": "h.pdf", "size": 3}], "_pdf_bytes": [("h.pdf", b"hhh")],
        "backend": "e-invoice", "confidence": "high", "notes": "hybrid note",
    }
    p_draft = {
        "supplier": None, "statement_ref": "P-1", "statement_date": None,
        "currency": "EUR", "customer": None,
        "lines": [{"invoice_no": "P1"}],
        "files": [{"name": "p.pdf", "size": 4}], "_pdf_bytes": [("p.pdf", b"pppp")],
        "backend": "none", "confidence": "low", "notes": "plain note",
    }
    all_files = [("h.pdf", b"hhh"), ("p.pdf", b"pppp")]
    m = extract._merge_mixed(h_draft, p_draft, all_files)
    # never drops a line
    assert len(m["lines"]) == 3
    # prefers structured header when truthy, falls back when missing
    assert m["supplier"] == "Structured Co"      # h truthy
    assert m["statement_ref"] == "S-1"           # h truthy (not p's P-1)
    # vaults ALL files
    assert len(m["_pdf_bytes"]) == 2
    assert {b for _, b in m["_pdf_bytes"]} == {b"hhh", b"pppp"}
    # confidence lowered, backend reflects merge
    assert m["confidence"] == "medium"
    assert m["backend"] == "mixed"
    # both prior notes preserved alongside the reviewer note
    assert "hybrid note" in m["notes"] and "plain note" in m["notes"]
    assert "Mixed batch" in m["notes"]


def test_merge_mixed_falls_back_to_plain_header_when_hybrid_blank():
    h_draft = {"supplier": None, "currency": None, "lines": [{"x": 1}],
               "_pdf_bytes": [("h.pdf", b"h")], "notes": ""}
    p_draft = {"supplier": "Plain Co", "currency": "USD", "lines": [],
               "_pdf_bytes": [("p.pdf", b"p")], "notes": ""}
    m = extract._merge_mixed(h_draft, p_draft, [("h.pdf", b"h"), ("p.pdf", b"p")])
    assert m["supplier"] == "Plain Co"           # h blank -> fall back to p
    assert m["currency"] == "USD"


# --------------------------------------------------------------- strict transient retry
def test_mixed_propagates_transient_error_under_strict(monkeypatch):
    # The plain subset goes through the AI path; a transient AI failure under strict=True
    # must propagate so the intake queue can retry the WHOLE batch later.
    def _boom(be, texts):
        raise RuntimeError("rate limit exceeded")
    monkeypatch.setattr(extract, "_ai_extract", _boom)
    monkeypatch.setattr(extract, "is_transient_error", lambda e: True)
    monkeypatch.setitem(extract._AI, "claude", lambda texts: None)

    h = _hybrid_pdf(xml=_cii("INV-3", "Struct"))
    p = _plain_pdf()
    batch = _zip([("hybrid.pdf", h), ("plain.pdf", p)])
    with pytest.raises(extract.TransientExtractionError):
        extract.extract(batch, "batch.zip", backend="claude", strict=True)

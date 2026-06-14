"""WO2 — .docx placeholder prefill (split-run fix) + soffice .docx->PDF pipeline.

Covers the run-merge `_fill_docx` (split + whole placeholders), the leftover probe that
re-reads the produced docx, `doc_render.docx_to_pdf` (real conversion skip-if-absent +
the unconditional None-fallback path), and `generate_document` docx as_pdf delivery."""
import importlib
import io
import shutil
import zipfile

import pytest

# A minimal but VALID OOXML container: the two structural parts Word needs to open a file
# (`[Content_Types].xml`, `_rels/.rels`) plus the caller-supplied `word/document.xml` body.
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>")
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>')
_DOC_OPEN = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>')
_DOC_CLOSE = "</w:body></w:document>"


def _docx(body_inner):
    """Build a valid .docx whose document body is `body_inner` (a string of <w:p>…)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", _DOC_OPEN + body_inner + _DOC_CLOSE)
    return buf.getvalue()


def _doc_xml(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read("word/document.xml").decode("utf-8")


def test_fill_docx_split_run_placeholder():
    """A {{company_name}} split as <w:t>{{</w:t><w:t>company_name</w:t><w:t>}}</w:t>
    (the way Word fragments placeholders across formatting runs) IS filled."""
    import customer_master as cm
    body = ("<w:p><w:r><w:t>{{</w:t></w:r>"
            "<w:r><w:t>company_name</w:t></w:r>"
            "<w:r><w:t>}}</w:t></w:r></w:p>")
    out = cm._fill_docx(_docx(body), {"company_name": "Acme SIA"})
    xml = _doc_xml(out)
    assert "Acme SIA" in xml
    assert "{{" not in xml and "}}" not in xml
    # output must still be a valid, re-openable OOXML zip
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        assert z.testzip() is None
        assert "[Content_Types].xml" in z.namelist()


def test_fill_docx_whole_placeholder_preserves_adjacent_run():
    """A whole {{refund_country}} in one run is filled WITHOUT disturbing a differently
    styled neighbouring run (formatting preserved for the common case)."""
    import customer_master as cm
    body = ('<w:p>'
            '<w:r><w:rPr><w:b/></w:rPr><w:t>BOLD_KEEP</w:t></w:r>'
            '<w:r><w:t xml:space="preserve">for {{refund_country}}</w:t></w:r>'
            '</w:p>')
    out = cm._fill_docx(_docx(body), {"refund_country": "Belgium"})
    xml = _doc_xml(out)
    assert "Belgium" in xml
    # the styled run survives untouched (run props + its literal text)
    assert "<w:b/>" in xml and "BOLD_KEEP" in xml
    assert 'xml:space="preserve"' in xml


def test_fill_docx_value_is_xml_escaped():
    """A value containing markup characters stays valid XML after substitution."""
    import customer_master as cm
    body = "<w:p><w:r><w:t>{{company_name}}</w:t></w:r></w:p>"
    out = cm._fill_docx(_docx(body), {"company_name": "A & B <Ltd>"})
    xml = _doc_xml(out)
    assert "&amp;" in xml and "&lt;Ltd&gt;" in xml
    # the produced part must parse as XML
    import xml.dom.minidom as md
    md.parseString(xml)  # raises if malformed


def test_docx_leftover_probe_detects_split_and_whole():
    """fill_template re-reads the PRODUCED docx and surfaces unfilled placeholders,
    whether they were split across runs or whole in a single run."""
    import customer_master as cm
    body = ("<w:p><w:r><w:t>{{company_name}}</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>{{mis</w:t></w:r><w:r><w:t>sing}}</w:t></w:r></w:p>")
    filled, ext, leftover = cm.fill_template(_docx(body), "docx",
                                             {"company_name": "Acme SIA"})
    assert ext == "docx"
    # company_name resolved; the split {{missing}} is detected despite the run boundary
    assert "missing" in leftover
    assert "company_name" not in leftover


def _soffice_works():
    """True only if a soffice binary is present AND actually converts in this env.
    (A binary can be installed but non-functional under a headless/sandbox — e.g. it
    exits 0 yet emits no PDF; we must not assert a real conversion in that case.)"""
    import doc_render
    if not (shutil.which("soffice") or shutil.which("libreoffice")):
        return False
    probe = _docx("<w:p><w:r><w:t>probe</w:t></w:r>"
                  "<w:r><w:t> body</w:t></w:r></w:p>")
    out = doc_render.docx_to_pdf(probe)
    return bool(out and out.startswith(b"%PDF"))


def test_docx_to_pdf_none_fallback_when_binary_missing(monkeypatch):
    """The unconditional fallback path: with no soffice binary resolvable, docx_to_pdf
    returns None and never raises."""
    import doc_render
    importlib.reload(doc_render)
    monkeypatch.setattr(doc_render, "_soffice_bin", lambda: None)
    assert doc_render.docx_to_pdf(_docx("<w:p><w:r><w:t>hi</w:t></w:r></w:p>")) is None


def test_docx_to_pdf_failure_returns_none_not_raise():
    """Even with a present-but-non-functional soffice (or a malformed input it rejects),
    docx_to_pdf returns None instead of raising — feeding garbage exercises the
    non-zero-exit / no-output branches without depending on a working LibreOffice."""
    import doc_render
    out = doc_render.docx_to_pdf(b"not a real docx at all")
    assert out is None or out.startswith(b"%PDF")


@pytest.mark.skipif(not _soffice_works(),
                    reason="LibreOffice (soffice) absent or non-functional in this env")
def test_docx_to_pdf_real_conversion():
    """With a working soffice present, a valid .docx converts to PDF bytes."""
    import doc_render
    out = doc_render.docx_to_pdf(_docx("<w:p><w:r><w:t>Hello PDF</w:t></w:r></w:p>"))
    assert out is not None and out.startswith(b"%PDF")


def test_generate_document_docx_as_pdf(tmp_path, monkeypatch):
    """generate_document(as_pdf=True) on a .docx template returns ext 'pdf' (bytes %PDF)
    when soffice works, else falls back to ext 'docx' (prefilled) — never raising.
    Either way the split-run placeholder was merged and no leftovers remain."""
    import customer_master as cm
    importlib.reload(cm)
    monkeypatch.setattr(cm, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(cm, "_SCHEMA_READY", set())
    monkeypatch.setattr(cm, "DOCDIR", str(tmp_path / "cdocs"))
    cm.add_customer("ACME", "Acme SIA", "LV", reg_number="LV123")
    con = cm.connect()
    body = ("<w:p><w:r><w:t>{{</w:t></w:r><w:r><w:t>company_name</w:t></w:r>"
            "<w:r><w:t>}}</w:t></w:r></w:p>")
    tid = cm.add_template(con, "POA", "power_of_attorney", "poa.docx", _docx(body))
    data, name, ext, leftover = cm.generate_document(con, tid, "ACME", as_pdf=True)
    con.close()
    assert leftover == []
    if ext == "pdf":
        assert name.endswith(".pdf") and data.startswith(b"%PDF")
    else:
        # graceful fallback: the prefilled (split-run merged) .docx is delivered
        assert ext == "docx" and name.endswith(".docx")
        assert "Acme SIA" in _doc_xml(data)

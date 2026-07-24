"""File-security gate: type validation, disguised-content + malware rejection."""

import pytest

from app.services import filesec

GOOD_CSV = b"vendor,invoice_number,amount\nAcme,INV-1,10.00\n"
GOOD_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
GOOD_JSON = b'{"invoice_number": "INV-1", "line_items": []}'
GOOD_XML = b'<?xml version="1.0"?><Invoice xmlns="urn:oasis"><ID>1</ID></Invoice>'


def test_accepts_valid_kinds():
    assert filesec.check("a.csv", GOOD_CSV) == "csv"
    assert filesec.check("a.pdf", GOOD_PDF) == "pdf"
    assert filesec.check("a.json", GOOD_JSON) == "json"
    assert filesec.check("a.xml", GOOD_XML) == "xml"


def test_rejects_executable_disguised_as_pdf():
    exe = b"MZ\x90\x00" + b"\x00" * 64  # Windows PE header
    with pytest.raises(filesec.FileRejected):
        filesec.check("invoice.pdf", exe)

    elf = b"\x7fELF" + b"\x00" * 64  # Linux ELF
    with pytest.raises(filesec.FileRejected):
        filesec.check("invoice.pdf", elf)


def test_rejects_html_script_disguised_as_pdf():
    html = b"<!DOCTYPE html><html><script>alert('x')</script></html>"
    with pytest.raises(filesec.FileRejected, match="signature|HTML|script|type"):
        filesec.check("invoice.pdf", html)


def test_rejects_zip_and_office_macro_carriers():
    with pytest.raises(filesec.FileRejected):
        filesec.check("invoice.pdf", b"PK\x03\x04" + b"\x00" * 32)  # zip/xlsx/docx
    with pytest.raises(filesec.FileRejected):
        filesec.check("book.pdf", b"\xd0\xcf\x11\xe0" + b"\x00" * 32)  # legacy OLE (macros)


def test_rejects_wrong_extension_and_shebang():
    with pytest.raises(filesec.FileRejected):
        filesec.check("run.exe", b"anything")  # not an allowed kind
    with pytest.raises(filesec.FileRejected):
        filesec.check("x.csv", b"#!/bin/sh\nrm -rf /\n")  # shebang script


def test_rejects_eicar_malware_signature():
    payload = b"%PDF-1.4 " + filesec.EICAR + b" trailer"
    with pytest.raises(filesec.FileRejected, match="Malware|EICAR"):
        filesec.scan_malware(payload)


def test_rejects_oversize(monkeypatch):
    monkeypatch.setattr(filesec.settings, "max_upload_mb", 1)
    big = b"%PDF-1.4" + b"0" * (2 * 1024 * 1024)
    with pytest.raises(filesec.FileRejected, match="too large"):
        filesec.check("big.pdf", big)


def test_receipt_kinds_allow_images_reject_csv():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    assert filesec.check("r.png", png, allowed=filesec.RECEIPT_KINDS) == "png"
    with pytest.raises(filesec.FileRejected):
        filesec.check("r.csv", GOOD_CSV, allowed=filesec.RECEIPT_KINDS)

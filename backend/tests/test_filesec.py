"""File-security gate: type validation, disguised-content + malware rejection."""

import sys
import types
from collections.abc import Callable
from typing import Any

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


# --- ClamAV configured-scanner branches (R7) -------------------------------
#
# `clamd` is not an installed/declared runtime dependency (see requirements.txt:46,
# commented out) and `clamav_enabled` defaults to False, so these branches never run
# in the shipped default configuration. That is exactly why they need direct coverage:
# nothing else in the suite ever imports `clamd` or sets `clamav_enabled=True`. We
# inject a fake `clamd` module into `sys.modules` so `scan_malware`'s
# `import clamd` / `cd.instream(...)` path can be exercised with no real daemon and
# no real package — proving the FOUND / non-OK-status / unreachable-scanner branches
# each fail as documented (fail-CLOSED on FOUND/error/unreachable, pass on OK).


def _install_fake_clamd(
    monkeypatch: pytest.MonkeyPatch,
    instream: Callable[[], Any],
    *,
    record: dict[str, str] | None = None,
) -> None:
    """Replace `sys.modules["clamd"]` with a fake module whose
    `ClamdUnixSocket`/`ClamdNetworkSocket` classes call `instream()` for their
    `instream()` result (return a `{"stream": (status, signature)}` dict, or raise
    to simulate an unreachable daemon). `record["class"]` (if given) captures which
    socket class was instantiated, so the unix-vs-network branch is provable too.
    """
    fake_module = types.ModuleType("clamd")

    class _FakeSocket:
        def __init__(self, *args: object, **kwargs: object) -> None:
            if record is not None:
                record["class"] = type(self).__name__

        def instream(self, _stream: object) -> Any:
            return instream()

    class ClamdUnixSocket(_FakeSocket):
        pass

    class ClamdNetworkSocket(_FakeSocket):
        pass

    fake_module.ClamdUnixSocket = ClamdUnixSocket  # type: ignore[attr-defined]
    fake_module.ClamdNetworkSocket = ClamdNetworkSocket  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "clamd", fake_module)


def test_clamav_found_rejects_with_signature(monkeypatch):
    monkeypatch.setattr(filesec.settings, "clamav_enabled", True)
    _install_fake_clamd(monkeypatch, lambda: {"stream": ("FOUND", "Win.Test.EICAR_HDB-1")})
    with pytest.raises(filesec.FileRejected, match="Malware detected.*Win.Test.EICAR_HDB-1"):
        filesec.scan_malware(GOOD_PDF)


def test_clamav_non_ok_status_rejects(monkeypatch):
    monkeypatch.setattr(filesec.settings, "clamav_enabled", True)
    _install_fake_clamd(monkeypatch, lambda: {"stream": ("ERROR", None)})
    with pytest.raises(filesec.FileRejected, match="error"):
        filesec.scan_malware(GOOD_PDF)


def test_clamav_unreachable_fails_closed(monkeypatch):
    """A configured-but-unreachable daemon must fail CLOSED, never silently pass."""
    monkeypatch.setattr(filesec.settings, "clamav_enabled", True)

    def _raise() -> Any:
        raise ConnectionRefusedError("clamd daemon unreachable")

    _install_fake_clamd(monkeypatch, _raise)
    with pytest.raises(filesec.FileRejected, match="unavailable"):
        filesec.scan_malware(GOOD_PDF)


def test_clamav_ok_status_passes(monkeypatch):
    monkeypatch.setattr(filesec.settings, "clamav_enabled", True)
    _install_fake_clamd(monkeypatch, lambda: {"stream": ("OK", None)})
    filesec.scan_malware(GOOD_PDF)  # does not raise


def test_clamav_uses_unix_socket_when_configured(monkeypatch):
    monkeypatch.setattr(filesec.settings, "clamav_enabled", True)
    monkeypatch.setattr(filesec.settings, "clamav_unix_socket", "/tmp/clamd.sock")
    record: dict[str, str] = {}
    _install_fake_clamd(monkeypatch, lambda: {"stream": ("OK", None)}, record=record)
    filesec.scan_malware(GOOD_PDF)
    assert record["class"] == "ClamdUnixSocket"


def test_clamav_uses_network_socket_by_default(monkeypatch):
    monkeypatch.setattr(filesec.settings, "clamav_enabled", True)
    monkeypatch.setattr(filesec.settings, "clamav_unix_socket", None)
    record: dict[str, str] = {}
    _install_fake_clamd(monkeypatch, lambda: {"stream": ("OK", None)}, record=record)
    filesec.scan_malware(GOOD_PDF)
    assert record["class"] == "ClamdNetworkSocket"


def test_clamav_eicar_short_circuits_before_scanner(monkeypatch):
    """The EICAR check runs BEFORE the `clamd` import/instream call — even with a
    scanner configured, EICAR is caught deterministically without hitting the fake
    (or a real) daemon at all."""
    monkeypatch.setattr(filesec.settings, "clamav_enabled", True)

    def _boom() -> Any:
        raise AssertionError("scanner should never be reached for EICAR content")

    _install_fake_clamd(monkeypatch, _boom)
    payload = b"%PDF-1.4 " + filesec.EICAR + b" trailer"
    with pytest.raises(filesec.FileRejected, match="Malware|EICAR"):
        filesec.scan_malware(payload)

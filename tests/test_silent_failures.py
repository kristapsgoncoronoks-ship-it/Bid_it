"""Failures that used to be swallowed by bare `except: pass` must now leave a
trace in the application log (applog) while still degrading gracefully."""
import importlib
import logging
import sqlite3


def test_legacy_claim_import_failure_logged_not_fatal(tmp_path, monkeypatch, caplog):
    # A corrupt legacy fuel_history.db used to make the migration fail silently —
    # indistinguishable from "no legacy data". It must log and still serve a
    # working (empty) claims DB.
    import vat_refund
    importlib.reload(vat_refund)
    corrupt = tmp_path / "fuel_history.db"
    corrupt.write_bytes(b"this is definitely not a sqlite database\x00" * 64)
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "vat_claims.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(corrupt))
    vat_refund._SCHEMA_READY.clear()

    with caplog.at_level(logging.ERROR, logger="ffs.vat_refund"):
        con = vat_refund.connect()              # must not raise
    # graceful degradation: schema exists, just no migrated rows
    assert con.execute("SELECT COUNT(*) FROM vat_applications").fetchone()[0] == 0
    con.close()
    assert "legacy claim-DB import failed" in caplog.text


def test_ftp_delete_failure_logged_not_fatal(caplog):
    # A failed FTP delete is treated as "already gone" — but no longer silently.
    import document_vault

    class _FailingFtp:
        def delete(self, remote):
            raise OSError("550 no such file")
        def quit(self):
            pass

    fb = document_vault.FtpBackend(connect=lambda: _FailingFtp())
    with caplog.at_level(logging.WARNING, logger="ffs.document_vault"):
        fb.delete("ftp://fuelvault/invoices/x.pdf")   # must not raise
    assert "ftp delete" in caplog.text and "x.pdf" in caplog.text

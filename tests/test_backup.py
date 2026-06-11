"""Tests for auto-backup scheduling, the backup.due() logic, settings, and
document integrity verification."""
import hashlib
import importlib
import os
import sqlite3

import pytest


# ---------------------------------------------------------------- backup.due
@pytest.fixture()
def bk(tmp_path, monkeypatch):
    import backup
    importlib.reload(backup)
    monkeypatch.setattr(backup, "BACKUPDIR", str(tmp_path / "backups"))
    os.makedirs(backup.BACKUPDIR, exist_ok=True)
    return backup


def test_due_no_snapshot(bk):
    assert bk.due(24) is True          # nothing yet -> due
    assert bk.due(0) is False          # disabled
    assert bk.due(-1) is False


def test_due_respects_age(bk):
    import time
    p = os.path.join(bk.BACKUPDIR, "ffs_20260101_000000.zip")
    open(p, "wb").write(b"x")
    assert bk.due(24) is False         # fresh
    os.utime(p, (time.time() - 25 * 3600, time.time() - 25 * 3600))
    assert bk.due(24) is True          # older than 24h


# ---------------------------------------------------------------- settings
def test_settings_roundtrip(tmp_path, monkeypatch):
    import auth
    importlib.reload(auth)
    monkeypatch.setattr(auth, "DB", str(tmp_path / "sec.db"))
    assert auth.get_setting("backup_interval_hours", "0") == "0"
    auth.set_setting("backup_interval_hours", "24")
    assert auth.get_setting("backup_interval_hours") == "24"


# ---------------------------------------------------------------- doc integrity
def _doc_con(tmp_path, fname, sha):
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE invoice_documents (id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity, supplier, invoice_ref, filename, stored_path, sha256, size, kind, backend)""")
    con.execute("""INSERT INTO invoice_documents
        (entity, supplier, invoice_ref, filename, stored_path, sha256, size, backend)
        VALUES ('E','S','REF',?,?,?,?, 'local')""",
        (fname, str(tmp_path / fname), sha, 5))
    con.commit()
    return con


def test_verify_documents_ok_corrupt_missing(tmp_path, monkeypatch):
    import vat_refund
    monkeypatch.setattr(vat_refund, "DOCDIR", str(tmp_path))
    (tmp_path / "a.pdf").write_bytes(b"hello")
    sha = hashlib.sha256(b"hello").hexdigest()
    con = _doc_con(tmp_path, "a.pdf", sha)

    _, summ = vat_refund.verify_documents(con)
    assert (summ["ok"], summ["corrupt"], summ["missing"]) == (1, 0, 0)

    (tmp_path / "a.pdf").write_bytes(b"tampered-content")   # corrupt in place
    _, summ = vat_refund.verify_documents(con)
    assert summ["corrupt"] == 1

    os.remove(tmp_path / "a.pdf")                           # gone
    _, summ = vat_refund.verify_documents(con)
    assert summ["missing"] == 1


# ---------------------------------------------------------------- admin actions
def test_admin_set_schedule_and_backup(client, tmp_path, monkeypatch):
    import re
    import app as A
    import auth
    import backup
    monkeypatch.setattr(backup, "BACKUPDIR", str(tmp_path / "bk"))

    def tok():
        return re.search(r'name="_csrf" value="([^"]+)"',
                         client.get("/admin").get_data(as_text=True)).group(1)

    # set a daily schedule
    r = client.post("/admin", data={"_csrf": tok(), "__act": "set_backup_schedule", "interval": "24"})
    assert "every 24 hour" in r.get_data(as_text=True)
    assert auth.get_setting("backup_interval_hours") == "24"

    # manual backup writes a snapshot (under the monkeypatched BACKUPDIR)
    r = client.post("/admin", data={"_csrf": tok(), "__act": "run_backup"})
    assert "Backup created" in r.get_data(as_text=True)
    import glob
    assert glob.glob(os.path.join(str(tmp_path / "bk"), "ffs_*.zip"))

    # scheduler tick is idempotent once a fresh snapshot exists
    assert A._backup_tick() is None


def test_verify_docs_logs_failures(client):
    import re
    import auth
    auth.clear_errors()
    # the repo ships document rows but not the documents/ files -> all MISSING
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/admin").get_data(as_text=True)).group(1)
    r = client.post("/admin", data={"_csrf": tok, "__act": "verify_docs"})
    body = r.get_data(as_text=True)
    assert "integrity FAILED" in body or "missing" in body
    assert any(e["context"] == "document integrity" for e in auth.recent_errors())
    auth.clear_errors()

"""Pluggable document-storage backends: local, and the new FTP/FTPS archive.
The FTP transport is stubbed (no network/server needed), mirroring the SharePoint
test pattern."""
import os


class FakeFtp:
    """A minimal in-memory stand-in for ftplib.FTP / FTP_TLS."""
    def __init__(self, store):
        self.store = store
        self.dirs = []
    def mkd(self, path):
        self.dirs.append(path)
    def storbinary(self, cmd, fh):
        self.store[cmd[len("STOR "):]] = fh.read()
    def retrbinary(self, cmd, cb):
        cb(self.store[cmd[len("RETR "):]])
    def quit(self):
        pass


# ---------------------------------------------------------------- logical vault path
def test_invoice_vault_path_structure():
    import doc_storage as DS
    # Customer (+reg) / Year / Country / Claim-period / file
    assert (DS.invoice_vault_path("Jupiter Plus AS", "EE100127540", "Germany",
                                  "2026-05", "BE98759.pdf")
            == "Jupiter Plus AS EE100127540/2026/Germany/Q2/BE98759.pdf")
    # quarterly period passes straight through
    assert DS.invoice_vault_path("X", "R", "Spain", "2026-Q3", "f.pdf").endswith("/Spain/Q3/f.pdf")
    # annual claim
    assert "/Annual/" in DS.invoice_vault_path("X", "R", "Spain", "2026", "f.pdf")
    # missing metadata never drops a file at the root
    p = DS.invoice_vault_path("X", None, None, None, "f.pdf")
    assert p == "X/unsorted/unknown-country/unsorted/f.pdf"


def test_customer_vault_path_structure():
    import doc_storage as DS
    assert (DS.customer_vault_path("SIA OMUSS", "LV123", "Poland", "power_of_attorney", "poa.pdf")
            == "SIA OMUSS LV123/customer-documents/Poland/power_of_attorney/poa.pdf")
    assert "/general/" in DS.customer_vault_path("SIA OMUSS", "LV123", None, "signed_contract", "c.pdf")


def test_segment_sanitisation():
    import doc_storage as DS
    # slashes and illegal chars in a country/name can't break out of their segment
    p = DS.invoice_vault_path("Bad/Co:\\x", "R", "../etc", "2026-05", "a*b.pdf")
    assert ".." not in p.split("/")[2] and "\\" not in p and ":" not in p
    assert p.count("/") == 4                       # exactly 5 segments


def test_local_writes_nested_folders(tmp_path):
    import doc_storage as DS
    lb = DS.LocalBackend(str(tmp_path / "documents"))
    rel = DS.invoice_vault_path("Jupiter Plus AS", "EE1", "Germany", "2026-05", "x.pdf")
    loc, _ = lb.put(rel, b"%PDF nested")
    assert os.path.isfile(loc) and "Germany" in loc and "Q2" in loc
    assert lb.get(loc) == b"%PDF nested"


def test_local_put_blocks_traversal(tmp_path):
    import doc_storage as DS
    import pytest
    lb = DS.LocalBackend(str(tmp_path / "documents"))
    with pytest.raises(ValueError):
        lb.put("../escape.pdf", b"x")


def test_local_roundtrip(tmp_path):
    import doc_storage as DS
    lb = DS.LocalBackend(str(tmp_path / "documents"))
    loc, url = lb.put("inv.pdf", b"%PDF-1.4 local")
    assert url is None
    assert lb.get(loc) == b"%PDF-1.4 local"


def test_ftp_roundtrip_creates_nested_dirs(monkeypatch):
    import doc_storage as DS
    monkeypatch.setenv("FTP_DIR", "vault")
    store = {}
    fakes = []
    def open_fake():
        f = FakeFtp(store); fakes.append(f); return f
    fb = DS.FtpBackend(connect=open_fake)
    rel = DS.invoice_vault_path("Cust", "R1", "Germany", "2026-05", "inv.pdf")
    loc, url = fb.put(rel, b"%PDF-1.4 ftp bytes")
    assert loc == f"ftp://vault/{rel}" and url is None
    assert store[f"vault/{rel}"] == b"%PDF-1.4 ftp bytes"
    # every folder level was created on the server
    assert "vault" in fakes[0].dirs and f"vault/Cust R1" in fakes[0].dirs
    assert f"vault/Cust R1/2026/Germany/Q2" in fakes[0].dirs
    assert fb.get(loc) == b"%PDF-1.4 ftp bytes"


def test_get_bytes_routes_by_locator_prefix(tmp_path, monkeypatch):
    import doc_storage as DS
    # ftp:// -> FtpBackend (stubbed)
    store = {"vault/x.pdf": b"hello-ftp"}
    monkeypatch.setattr(DS.FtpBackend, "_default_open", lambda self: FakeFtp(store))
    assert DS.get_bytes("ftp://vault/x.pdf", str(tmp_path)) == b"hello-ftp"
    # a plain path -> LocalBackend
    docdir = tmp_path / "documents"; docdir.mkdir()
    p = docdir / "y.pdf"; p.write_bytes(b"hello-local")
    assert DS.get_bytes(str(p), str(docdir)) == b"hello-local"


def test_attach_document_files_under_logical_path(tmp_path, monkeypatch):
    """End-to-end: attach_document derives the customer reg-number + the invoice's
    country/period and files the PDF under the logical vault tree."""
    import importlib
    import vat_refund, supplier_db, customer_db, doc_storage
    for m in (supplier_db, customer_db, vat_refund):
        importlib.reload(m)
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "fh.db"))
    monkeypatch.setattr(vat_refund, "DOCDIR", str(tmp_path / "docs"))
    monkeypatch.setattr(supplier_db, "DB", str(tmp_path / "sup.db"))
    monkeypatch.setattr(customer_db, "DB", str(tmp_path / "cust.db"))

    customer_db.add_customer("JUP", "Jupiter Plus AS", "EE", reg_number="EE100127540")
    scon = supplier_db.connect()
    scon.execute("""INSERT INTO supplier_invoices
        (supplier, country, invoice_no, invoice_date, period, currency, gross_total)
        VALUES ('DKV','Germany','INV1','2026-05-10','2026-05','EUR',100)""")
    scon.commit(); scon.close()

    con = vat_refund.connect()
    ok, _ = vat_refund.attach_document(con, "JUP", "DKV", "INV1",
                                       file_bytes=b"%PDF inv", filename="INV1.pdf")
    sp = con.execute("SELECT stored_path FROM invoice_documents").fetchone()["stored_path"]
    con.close()
    assert ok
    norm = sp.replace("\\", "/")
    assert "Jupiter Plus AS EE100127540/2026/Germany/Q2/" in norm
    assert doc_storage.get_bytes(sp, str(tmp_path / "docs")) == b"%PDF inv"


def test_backend_selection(monkeypatch, tmp_path):
    import doc_storage as DS
    monkeypatch.setattr(DS, "BACKEND", "ftp")
    assert DS.backend(str(tmp_path)).name == "ftp"
    monkeypatch.setattr(DS, "BACKEND", "local")
    assert DS.backend(str(tmp_path)).name == "local"


def test_ftp_uses_tls_by_default(monkeypatch):
    # FTPS (encrypted) must be the default; plain FTP only when explicitly disabled.
    import doc_storage as DS
    import ftplib
    created = {}
    class Probe(ftplib.FTP_TLS):
        def connect(self, *a, **k): created["tls"] = True
        def login(self, *a, **k): pass
        def prot_p(self): created["prot_p"] = True
        def set_pasv(self, *a, **k): pass
    monkeypatch.setattr(ftplib, "FTP_TLS", Probe)
    monkeypatch.setenv("FTP_HOST", "h"); monkeypatch.setenv("FTP_USER", "u")
    monkeypatch.setenv("FTP_PASSWORD", "p")
    DS.FtpBackend()._default_open()
    assert created.get("tls") and created.get("prot_p")   # encrypted control + data

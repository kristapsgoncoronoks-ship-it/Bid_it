"""Pluggable document-storage backends: local, and the new FTP/FTPS archive.
The FTP transport is stubbed (no network/server needed), mirroring the SharePoint
test pattern."""
import os


class FakeFtp:
    """A minimal in-memory stand-in for ftplib.FTP / FTP_TLS."""
    def __init__(self, store):
        self.store = store
    def mkd(self, path):
        pass
    def storbinary(self, cmd, fh):
        self.store[cmd[len("STOR "):]] = fh.read()
    def retrbinary(self, cmd, cb):
        cb(self.store[cmd[len("RETR "):]])
    def quit(self):
        pass


def test_local_roundtrip(tmp_path):
    import doc_storage as DS
    lb = DS.LocalBackend(str(tmp_path / "documents"))
    loc, url = lb.put("inv.pdf", b"%PDF-1.4 local")
    assert url is None
    assert lb.get(loc) == b"%PDF-1.4 local"


def test_ftp_roundtrip(monkeypatch):
    import doc_storage as DS
    monkeypatch.setenv("FTP_DIR", "vault/invoices")
    store = {}
    fb = DS.FtpBackend(connect=lambda: FakeFtp(store))
    loc, url = fb.put("E1_Q8_REF_inv.pdf", b"%PDF-1.4 ftp bytes")
    assert loc == "ftp://vault/invoices/E1_Q8_REF_inv.pdf"
    assert url is None                              # FTP has no browser web_url
    assert store["vault/invoices/E1_Q8_REF_inv.pdf"] == b"%PDF-1.4 ftp bytes"
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

"""
DOCUMENT STORAGE BACKENDS - local folder (default), SharePoint (Microsoft Graph),
or an FTP/FTPS file archive.

The vault logic (hashing, dedup, submission guard) is unchanged; only WHERE the
bytes live is pluggable, chosen by DOC_BACKEND. Locators stored in
invoice_documents.stored_path / customer_documents.stored_path:
    local:      <WORKDIR>/documents/<file>                    (plain path, as before)
    sharepoint: sp://{drive_id}/{item_id}                    (+ web_url column)
    ftp:        ftp://{remote_path}                           (path on the FTP server)

get_bytes() routes a stored locator to the right backend by its prefix, so history
keeps resolving even after you switch the default backend.

ENABLE FTP / FTPS (a file-archive server):
  Set on the machine running the app, then restart:
     DOC_BACKEND=ftp
     FTP_HOST=archive.example.com        FTP_PORT=21        (port optional)
     FTP_USER=...                        FTP_PASSWORD=...
     FTP_DIR=fuelvault/invoices          (base folder; created if missing)
     FTP_TLS=1   -> FTPS, explicit TLS (DEFAULT, encrypted) | 0 -> plain FTP
     FTP_PASSIVE=1  (default; set 0 for active mode)
  Uses Python's stdlib ftplib only (no extra dependency, works on a stock Windows
  box). PLAIN FTP IS CLEAR-TEXT - keep FTP_TLS=1 unless on a trusted private LAN.
  Move existing local history with migrate_local_to_ftp() once.
  (For SFTP/SSH instead of FTPS we'd add paramiko - say the word.)

ENABLE SHAREPOINT (one-time setup by your M365 admin):
  1. Entra ID (Azure AD) > App registrations > New registration ("Fleet Fuel Vault").
  2. API permissions > Microsoft Graph > Application permissions >
     Sites.Selected  (recommended - grant access ONLY to the target site)
     then admin consent; grant the app write access to the site via
     `POST /sites/{site-id}/permissions` (or use Sites.ReadWrite.All if accepted).
  3. Certificates & secrets > New client secret.
  4. Find the site & drive ids once:
     GET https://graph.microsoft.com/v1.0/sites/{hostname}:/sites/{SiteName}
     GET https://graph.microsoft.com/v1.0/sites/{site-id}/drives
  5. Set environment variables on the machine running the app:
     SP_TENANT_ID, SP_CLIENT_ID, SP_CLIENT_SECRET, SP_DRIVE_ID,
     SP_FOLDER (e.g. "FuelVAT/Invoices"), DOC_BACKEND=sharepoint
  6. Restart app. New uploads go to SharePoint; existing local files keep
     working; run migrate_local_to_sharepoint() once to move history.

Network note: Graph calls need outbound internet - run on your machine/server
(this sandboxed environment has egress disabled, so the SharePoint path here is
verified with a stubbed transport; the local backend is fully live).
"""
import os

BACKEND = os.environ.get("DOC_BACKEND", "local")
GRAPH = "https://graph.microsoft.com/v1.0"


class LocalBackend:
    name = "local"
    def __init__(self, docdir):
        self.docdir = docdir
    def put(self, safe_name, data):
        os.makedirs(self.docdir, exist_ok=True)
        path = f"{self.docdir}/{safe_name}"
        with open(path, "wb") as f:
            f.write(data)
        return path, None                      # locator, web_url
    def _safe_path(self, locator):
        """Resolve a stored locator and ensure it stays under docdir.
        Guards against tampered/traversal locators (e.g. '../../etc/passwd')."""
        base = os.path.realpath(self.docdir)
        real = os.path.realpath(locator)
        if not (real == base or real.startswith(base + os.sep)):
            raise ValueError(f"locator escapes document store: {locator!r}")
        return real
    def get(self, locator):
        return open(self._safe_path(locator), "rb").read()


class SharePointBackend:
    name = "sharepoint"
    def __init__(self, session=None):
        import requests
        self.rq = session or requests.Session()
        self.tenant = os.environ["SP_TENANT_ID"]
        self.client = os.environ["SP_CLIENT_ID"]
        self.secret = os.environ["SP_CLIENT_SECRET"]
        self.drive = os.environ["SP_DRIVE_ID"]
        self.folder = os.environ.get("SP_FOLDER", "FuelVAT").strip("/")
        self._token = None

    def token(self):
        if not self._token:
            r = self.rq.post(
                f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token",
                data={"grant_type": "client_credentials", "client_id": self.client,
                      "client_secret": self.secret,
                      "scope": "https://graph.microsoft.com/.default"}, timeout=30)
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return self._token

    def _h(self):
        return {"Authorization": f"Bearer {self.token()}"}

    def put(self, safe_name, data):
        # simple upload (<4 MB per Graph docs; invoices are well under). For larger
        # scans switch to an uploadSession - same endpoint family.
        url = (f"{GRAPH}/drives/{self.drive}/root:/{self.folder}/{safe_name}:/content")
        r = self.rq.put(url, headers={**self._h(),
                        "Content-Type": "application/octet-stream"}, data=data, timeout=60)
        r.raise_for_status()
        j = r.json()
        return f"sp://{self.drive}/{j['id']}", j.get("webUrl")

    def get(self, locator):
        drive, item = locator[len("sp://"):].split("/", 1)
        r = self.rq.get(f"{GRAPH}/drives/{drive}/items/{item}/content",
                        headers=self._h(), timeout=60, allow_redirects=True)
        r.raise_for_status()
        return r.content


class FtpBackend:
    """An FTP/FTPS file-archive backend (stdlib ftplib only). Bytes are stored
    under FTP_DIR on the server; the locator is ftp://<remote_path>. A fresh
    connection is opened per operation (robust against idle drops); tests inject a
    fake connector. FTPS (explicit TLS) is the default — keep it on off-LAN."""
    name = "ftp"

    def __init__(self, connect=None):
        self._open = connect or self._default_open
        self.base = os.environ.get("FTP_DIR", "fuelvault").strip("/")

    def _default_open(self):
        import ftplib
        use_tls = os.environ.get("FTP_TLS", "1") != "0"
        ftp = (ftplib.FTP_TLS() if use_tls else ftplib.FTP())
        ftp.connect(os.environ["FTP_HOST"], int(os.environ.get("FTP_PORT", "21")), timeout=60)
        ftp.login(os.environ.get("FTP_USER", ""), os.environ.get("FTP_PASSWORD", ""))
        if use_tls:
            ftp.prot_p()                       # encrypt the data channel too
        ftp.set_pasv(os.environ.get("FTP_PASSIVE", "1") != "0")
        return ftp

    def _ensure_base(self, ftp):
        path = ""
        for part in self.base.split("/"):
            if not part:
                continue
            path = f"{path}/{part}" if path else part
            try: ftp.mkd(path)
            except Exception: pass             # already exists (or no-permission to mkd)

    def put(self, safe_name, data):
        import io
        ftp = self._open()
        try:
            self._ensure_base(ftp)
            remote = f"{self.base}/{safe_name}" if self.base else safe_name
            ftp.storbinary(f"STOR {remote}", io.BytesIO(data))
            return f"ftp://{remote}", None      # locator, web_url
        finally:
            try: ftp.quit()
            except Exception: pass

    def get(self, locator):
        import io
        remote = locator[len("ftp://"):]
        ftp = self._open(); buf = io.BytesIO()
        try:
            ftp.retrbinary(f"RETR {remote}", buf.write)
            return buf.getvalue()
        finally:
            try: ftp.quit()
            except Exception: pass


def backend(docdir):
    if BACKEND == "sharepoint":
        return SharePointBackend()
    if BACKEND == "ftp":
        return FtpBackend()
    return LocalBackend(docdir)


def get_bytes(locator, docdir):
    """Route a stored locator to the right backend regardless of current default,
    so old history keeps working after switching backends."""
    loc = str(locator)
    if loc.startswith("sp://"):
        return SharePointBackend().get(locator)
    if loc.startswith("ftp://"):
        return FtpBackend().get(locator)
    return LocalBackend(docdir).get(locator)


def migrate_local_to_sharepoint(con, docdir):
    """One-time migration of existing local documents into SharePoint.
    Run on a machine with network + SP_* env vars set."""
    sp = SharePointBackend()
    moved = 0
    for r in con.execute("SELECT id, stored_path, filename FROM invoice_documents").fetchall():
        if str(r["stored_path"]).startswith("sp://"):
            continue
        data = open(r["stored_path"], "rb").read()
        loc, url = sp.put(os.path.basename(r["stored_path"]), data)
        con.execute("UPDATE invoice_documents SET stored_path=?, backend='sharepoint', web_url=? WHERE id=?",
                    (loc, url, r["id"]))
        moved += 1
    con.commit()
    return moved


def migrate_local_to_ftp(con, docdir, table="invoice_documents"):
    """One-time migration of existing local documents into the FTP archive.
    Run on a machine with the FTP_* env vars set."""
    ftp = FtpBackend()
    moved = 0
    for r in con.execute(f"SELECT id, stored_path FROM {table}").fetchall():
        loc = str(r["stored_path"])
        if loc.startswith("ftp://") or loc.startswith("sp://"):
            continue
        data = open(loc, "rb").read()
        new_loc, _ = ftp.put(os.path.basename(loc), data)
        con.execute(f"UPDATE {table} SET stored_path=?, backend='ftp', web_url=NULL WHERE id=?",
                    (new_loc, r["id"]))
        moved += 1
    con.commit()
    return moved


# ---------------- self-test with stubbed transport (no network needed) ----------------
if __name__ == "__main__":
    print("=== LocalBackend live test ===")
    lb = LocalBackend("/tmp/docstore_test")
    loc, url = lb.put("test.pdf", b"%PDF-1.4 demo")
    assert lb.get(loc) == b"%PDF-1.4 demo"
    print("  put/get OK ->", loc)

    print("=== SharePointBackend path test (stubbed Graph transport) ===")
    class _Resp:
        def __init__(self, j=None, content=b""): self._j, self.content = j or {}, content
        def raise_for_status(self): pass
        def json(self): return self._j
    class _Stub:
        def post(self, url, **k):
            assert "oauth2/v2.0/token" in url
            return _Resp({"access_token": "stub-token"})
        def put(self, url, headers=None, data=None, **k):
            assert url.startswith(f"{GRAPH}/drives/DRV/root:/FuelVAT/Invoices/")
            assert headers["Authorization"] == "Bearer stub-token"
            return _Resp({"id": "ITEM123",
                          "webUrl": "https://contoso.sharepoint.com/sites/Fleet/FuelVAT/x.pdf"})
        def get(self, url, **k):
            assert url == f"{GRAPH}/drives/DRV/items/ITEM123/content"
            return _Resp(content=b"%PDF-1.4 from sharepoint")
    os.environ.update(SP_TENANT_ID="T", SP_CLIENT_ID="C", SP_CLIENT_SECRET="S",
                      SP_DRIVE_ID="DRV", SP_FOLDER="FuelVAT/Invoices")
    sp = SharePointBackend(session=_Stub())
    loc, url = sp.put("invoice.pdf", b"%PDF-1.4 demo")
    print("  upload ->", loc, "|", url)
    assert loc == "sp://DRV/ITEM123"
    data = sp.get(loc)
    assert data == b"%PDF-1.4 from sharepoint"
    print("  download OK (", data[:20], ")")

    print("=== FtpBackend path test (stubbed ftplib transport) ===")
    class _FakeFtp:
        store = {}                              # shared 'server' filesystem
        def mkd(self, path): pass
        def storbinary(self, cmd, fh): _FakeFtp.store[cmd[len("STOR "):]] = fh.read()
        def retrbinary(self, cmd, cb): cb(_FakeFtp.store[cmd[len("RETR "):]])
        def quit(self): pass
    os.environ.update(FTP_DIR="fuelvault/invoices")
    fb = FtpBackend(connect=lambda: _FakeFtp())
    loc, url = fb.put("invoice.pdf", b"%PDF-1.4 ftp demo")
    print("  upload ->", loc, "|", url)
    assert loc == "ftp://fuelvault/invoices/invoice.pdf" and url is None
    got = fb.get(loc)
    assert got == b"%PDF-1.4 ftp demo"
    print("  download OK (", got[:20], ")")
    print("All storage backend tests passed.")

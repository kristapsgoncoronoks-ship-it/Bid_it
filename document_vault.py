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
import os, re

import applog

log = applog.get("document_vault")

BACKEND = os.environ.get("DOC_BACKEND", "local")
GRAPH = "https://graph.microsoft.com/v1.0"


# ---------------------------------------------------------------- logical vault path
# Documents are filed under a HUMAN-NAVIGABLE folder tree so they can be located by
# hand on any backend. The SAME path is built regardless of where the bytes finally
# live (local folder / SharePoint / FTP) — only the storage transport differs.
#
#   <Customer> <RegNo> / <Year> / <Country> / <Claim period> / <file>
#   e.g.  Jupiter Plus AS EE100127540 / 2026 / Germany / Q2 / Jupiter_DKV_INV123.pdf
#
# Customer onboarding/country documents (not tied to an invoice) file under:
#   <Customer> <RegNo> / customer-documents / <country|general> / <kind> / <file>
_SAFE_EXTRA = " ._-()&+"

def _seg(s, default="unsorted"):
    """Sanitise ONE path segment so it is safe on every backend (no slashes, no
    Windows-illegal chars, no leading/trailing dots/spaces)."""
    s = ("" if s is None else str(s)).strip()
    out = "".join(c if (c.isalnum() or c in _SAFE_EXTRA) else "_" for c in s)
    out = out.strip(" .")
    return out or default

def vault_path(folders, filename):
    """Join sanitised folder segments + a filename into the logical vault path."""
    parts = [_seg(f) for f in folders]
    parts.append(_seg(filename, default="document"))
    return "/".join(parts)

def _year_of(period):
    m = re.match(r"^(\d{4})", str(period or ""))
    return m.group(1) if m else "unsorted"

def period_label(period):
    """Map a period to its claim/declaration folder: monthly '2026-05' -> 'Q2',
    quarterly '2026-Q3' -> 'Q3', annual '2026-YEAR'/'2026' -> 'Annual', else
    'unsorted'."""
    p = str(period or "").strip()
    m = re.match(r"^\d{4}-Q([1-4])$", p)
    if m:
        return f"Q{m.group(1)}"
    if re.match(r"^\d{4}-YEAR$", p) or re.match(r"^\d{4}$", p):
        return "Annual"
    m = re.match(r"^\d{4}-(\d{2})", p)
    if m:
        return f"Q{(int(m.group(1)) - 1) // 3 + 1}"
    return "unsorted"

def _customer_folder(customer, reg_number):
    reg = (reg_number or "").strip()
    if reg and reg.upper() != "INPUT":
        return f"{customer} {reg}".strip()
    return str(customer or "customer")

def invoice_vault_path(customer, reg_number, country, period, filename):
    """Logical path for a supplier-invoice document (the main archive tree)."""
    return vault_path([_customer_folder(customer, reg_number), _year_of(period),
                       country or "unknown-country", period_label(period)], filename)

def customer_vault_path(customer, reg_number, scope, kind, filename):
    """Logical path for an onboarding / country-activation customer document."""
    return vault_path([_customer_folder(customer, reg_number), "customer-documents",
                       scope or "general", kind or "other"], filename)


class LocalBackend:
    name = "local"
    def __init__(self, docdir):
        self.docdir = docdir
    def put(self, name, data):
        os.makedirs(self.docdir, exist_ok=True)
        # `name` may be a multi-segment vault path; create the subfolders and keep
        # the final file strictly inside docdir (guard tampered/'..' names).
        real = os.path.realpath(os.path.join(self.docdir, name))
        base = os.path.realpath(self.docdir)
        if not real.startswith(base + os.sep):
            raise ValueError(f"path escapes document store: {name!r}")
        os.makedirs(os.path.dirname(real), exist_ok=True)
        with open(real, "wb") as f:
            f.write(data)
        return real, None                      # locator, web_url
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
    def delete(self, locator):
        real = self._safe_path(locator)
        base = os.path.realpath(self.docdir)
        try:
            os.remove(real)
        except FileNotFoundError:
            return
        # prune folders left empty by the move (e.g. an emptied .../Germany/Q2)
        d = os.path.dirname(real)
        while d.startswith(base + os.sep):
            try: os.rmdir(d)
            except OSError: break
            d = os.path.dirname(d)



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

    def put(self, name, data):
        # simple upload (<4 MB per Graph docs; invoices are well under). For larger
        # scans switch to an uploadSession - same endpoint family. `name` may carry
        # subfolders (the logical vault path); Graph creates intermediate folders.
        from urllib.parse import quote
        path = quote(f"{self.folder}/{name}".strip("/"), safe="/")
        url = (f"{GRAPH}/drives/{self.drive}/root:/{path}:/content")
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

    def delete(self, locator):
        drive, item = locator[len("sp://"):].split("/", 1)
        r = self.rq.delete(f"{GRAPH}/drives/{drive}/items/{item}",
                           headers=self._h(), timeout=60)
        if getattr(r, "status_code", 204) not in (200, 202, 204, 404):
            r.raise_for_status()


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

    def _ensure_dirs(self, ftp, dirpath):
        """Create every folder in `dirpath` (the base + the document's subfolders)."""
        path = ""
        for part in dirpath.split("/"):
            if not part:
                continue
            path = f"{path}/{part}" if path else part
            try: ftp.mkd(path)
            except Exception as e:             # already exists (or no-permission to mkd)
                log.debug("ftp mkd '%s' skipped (usually exists already): %s", path, e)

    def put(self, name, data):
        import io
        ftp = self._open()
        try:
            remote = f"{self.base}/{name}" if self.base else name
            self._ensure_dirs(ftp, "/".join(remote.split("/")[:-1]))
            ftp.storbinary(f"STOR {remote}", io.BytesIO(data))
            return f"ftp://{remote}", None      # locator, web_url
        finally:
            try: ftp.quit()
            except Exception as e:              # upload already done; connection drop only
                log.warning("ftp quit failed after put '%s': %s", name, e)

    def get(self, locator):
        import io
        remote = locator[len("ftp://"):]
        ftp = self._open(); buf = io.BytesIO()
        try:
            ftp.retrbinary(f"RETR {remote}", buf.write)
            return buf.getvalue()
        finally:
            try: ftp.quit()
            except Exception as e:              # download already done; connection drop only
                log.warning("ftp quit failed after get '%s': %s", remote, e)

    def delete(self, locator):
        remote = locator[len("ftp://"):]
        ftp = self._open()
        try:
            try: ftp.delete(remote)
            except Exception as e:             # treat as already gone, but record it
                log.warning("ftp delete '%s' failed (treated as already gone): %s",
                            remote, e)
        finally:
            try: ftp.quit()
            except Exception as e:
                log.warning("ftp quit failed after delete '%s': %s", remote, e)


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


def delete(locator, docdir):
    """Delete a stored document, routed by its locator prefix."""
    loc = str(locator)
    if loc.startswith("sp://"):
        return SharePointBackend().delete(locator)
    if loc.startswith("ftp://"):
        return FtpBackend().delete(locator)
    return LocalBackend(docdir).delete(locator)


def copy_to(new_name, data, docdir):
    """Write `data` to a new logical path on the current backend and return
    (new_locator, web_url). Pairs with delete() for a DB-safe move: copy_to ->
    update the stored_path row -> delete the old locator, so the database always
    references a file that exists."""
    return backend(docdir).put(new_name, data)


def migrate_local_to_sharepoint(con, docdir):
    """One-time migration of existing local documents into SharePoint.
    Run on a machine with network + SP_* env vars set, via the CLI:
        python document_vault.py --migrate sharepoint"""
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
    Run on a machine with the FTP_* env vars set, via the CLI:
        python document_vault.py --migrate ftp"""
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


# ---------------- migration CLI -------------------------------------------------------
def _migrate_cli(target):
    """Dispatch `--migrate sharepoint|ftp` to the matching migrate function over the live
    vat_refund DB/docstore, printing the moved-document count. Returns an exit code (0 ok,
    2 bad arg / unconfigured backend). Thin wrapper — no migration logic here."""
    import vat_refund
    fns = {"sharepoint": migrate_local_to_sharepoint, "ftp": migrate_local_to_ftp}
    fn = fns.get((target or "").strip().lower())
    if fn is None:
        print("usage: python document_vault.py --migrate sharepoint|ftp")
        return 2
    con = vat_refund.connect()
    try:
        moved = fn(con, vat_refund.DOCDIR)
    except Exception as e:
        print(f"migration to {target} failed (backend configured? env vars set?): {e}")
        return 2
    finally:
        con.close()
    print(f"migrated {moved} document(s) to {target}.")
    return 0


# ---------------- self-test with stubbed transport (no network needed) ----------------
if __name__ == "__main__":
    import sys
    if "--migrate" in sys.argv:
        i = sys.argv.index("--migrate")
        target = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        sys.exit(_migrate_cli(target))

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

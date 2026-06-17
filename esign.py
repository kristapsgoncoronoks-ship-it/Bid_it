"""
E-SIGNATURE (②) — a SIMPLE ELECTRONIC SIGNATURE (SES) with a full audit trail over the
secure-sharing module (B1/B2) and the generated CRM contracts (item ①).

WHAT THIS IS — AND IS NOT. This is a *Simple* Electronic Signature under eIDAS Art. 3(10):
a typed (and optionally drawn) signature + an explicit consent statement + a logged audit
trail (signer identity, UTC time, IP, user-agent). It is NOT a Qualified Electronic
Signature (QES): there is NO certificate, NO Trust Service Provider, NO time-stamping
authority. Every signing surface labels this "SES — not a qualified electronic signature".

THE INTEGRITY ANCHOR. A signature BINDS the SHA-256 of the EXACT bytes that were presented
for signing (signed_doc_sha256). The produced, stamped+certified PDF is stored in the vault
and verify() re-hashes the live bytes against the recorded digests — so a later byte change
of either the original-as-signed or the produced signed PDF makes verify() FAIL. The hash is
the tamper-evidence; we invent no crypto.

HOW THE SIGNED PDF IS BUILT (best-effort; never raises). Take the original vault bytes,
(1) STAMP a signature block on the last page via the pypdf overlay technique reused from
share_watermark, then (2) APPEND a "Signature Certificate" page rendered with
customer_master.text_to_pdf and merged via pypdf. On ANY PDF failure we fall back to
recording the signature EVENT against the ORIGINAL bytes (the SES audit record still stands)
and log via applog — a stamping hiccup must never lose a signature.

OWN DB. Like every other app-owned module this owns its SQLite file (esign.db) via connect()
+ db_migrate, audit-installed, WAL-tuned. Every row carries a tenant_id (tenancy seam, inert
today), stamped on INSERT via tenancy.write_tenant().

DATA-PRODUCT BOUNDARY. esign.db is APP-OWNED. The signed PDF is written to the document vault
strictly through document_vault.copy_to / read through document_vault.get_bytes — never a
caller-controlled path, never a writable product-DB handle.

SAFETY. Every API is best-effort and returns values, never raising to the caller (mirrors
sharing.py / notify.py): an (obj, "") / (None, err) pair, a bool, or None. Failures are
logged via applog.
"""
import os
import io
import sqlite3
import secrets
import hashlib
import datetime

import applog
import audit
import db_tuning
import db_migrate
import tenancy

log = applog.get("esign")

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/esign.db"

# The on-screen / certificate label — SES, explicitly NOT qualified (eIDAS).
SES_NOTICE = ("SES — not a qualified electronic signature (eIDAS Art. 3(10)): a typed/drawn "
              "signature with logged consent and an audit trail; no certificate or trust "
              "service provider is involved.")

STATUSES = ("pending", "signed", "declined", "void")

# A drawn-signature data URL can be large; cap what we persist so esign.db stays small.
MAX_SIGNATURE_IMAGE = 200_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS signature_requests (
    id           INTEGER PRIMARY KEY,
    subject_ref  TEXT NOT NULL,
    title        TEXT,
    requested_by TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    tenant_id    TEXT NOT NULL DEFAULT 'default',
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_signature_requests_status
    ON signature_requests(status, created_at);
CREATE TABLE IF NOT EXISTS signatures (
    id               INTEGER PRIMARY KEY,
    request_id       INTEGER NOT NULL,
    signer_name      TEXT NOT NULL,
    signer_email     TEXT,
    consent_text     TEXT,
    signed_doc_sha256 TEXT,
    signature_image  TEXT,
    signed_locator   TEXT,
    signed_sha256    TEXT,
    ip               TEXT,
    user_agent       TEXT,
    tenant_id        TEXT NOT NULL DEFAULT 'default',
    signed_at        TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_signatures_request ON signatures(request_id, signed_at);
"""

# APPEND-ONLY migrations (positions stable). Nothing yet — the base SCHEMA carries the
# signed_locator / signed_sha256 columns so a fresh install needs no ALTER.
_MIGRATIONS = []

_SCHEMA_READY = set()   # DB files whose schema is set up this process

DEFAULT_CONSENT = ("I agree that my typed/drawn name is my electronic signature on this "
                   "document, and I consent to signing electronically.")


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)
    audit.bind(con)
    if DB == ":memory:" or DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        db_migrate.apply(con, "esign", _MIGRATIONS)
        audit.install_audit(con, ["signature_requests", "signatures"])
        con.commit()
        _SCHEMA_READY.add(DB)
    return con


# ---------------------------------------------------------------- requests
def create_request(subject_ref, title, requested_by):
    """Open a NEW signature request (status 'pending') over the vault locator / `doc:<id>`
    reference `subject_ref`. Returns (request_dict, "") or (None, error). Never raises."""
    subject_ref = (subject_ref or "").strip()
    if not subject_ref:
        return None, "a document reference is required"
    try:
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO signature_requests
                   (subject_ref, title, requested_by, status, tenant_id)
                   VALUES (?,?,?, 'pending', ?)""",
                (subject_ref, (title or "").strip() or None, (requested_by or "") or None,
                 tenancy.write_tenant()))
            con.commit()
            row = con.execute("SELECT * FROM signature_requests WHERE id=?",
                              (cur.lastrowid,)).fetchone()
        finally:
            con.close()
        return (dict(row) if row else None), ""
    except Exception as e:
        log.exception("create_request failed for subject_ref=%r", subject_ref)
        return None, f"could not create request ({str(e)[:80]})"


def get_request(request_id):
    """Return the request row dict by id, or None. Never raises."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            row = con.execute("SELECT * FROM signature_requests WHERE id=?" + frag,
                              [request_id, *tp]).fetchone()
        finally:
            con.close()
        return dict(row) if row else None
    except Exception as e:
        log.warning("get_request failed for %s: %s", request_id, e)
        return None


def list_requests(requested_by=None):
    """Signature requests, newest first; optionally filtered to a creator. Each row carries
    a `signatures` count. Returns a list of dicts. Never raises -> []."""
    try:
        con = connect()
        try:
            q = ("""SELECT r.*, (SELECT COUNT(*) FROM signatures s WHERE s.request_id=r.id)
                           AS signatures
                    FROM signature_requests r WHERE 1=1""")
            params = []
            if requested_by is not None:
                q += " AND r.requested_by=?"
                params.append(requested_by or "")
            frag, tp = tenancy.scope_clause("r.tenant_id")
            q += frag
            params.extend(tp)
            q += " ORDER BY r.created_at DESC, r.id DESC"
            rows = con.execute(q, params).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("list_requests failed: %s", e)
        return []


def void_request(request_id, actor=None):
    """Void a request (it can no longer be signed). Idempotent, best-effort. Returns
    (True, "") or (False, err). `actor` is informational (the audit actor is set by the
    caller's request hook). Never raises."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            cur = con.execute(
                "UPDATE signature_requests SET status='void' "
                "WHERE id=? AND status!='void'" + frag,
                [request_id, *tp])
            con.commit()
            if cur.rowcount == 0:
                # already void, or no such request — distinguish for the caller
                exists = con.execute("SELECT 1 FROM signature_requests WHERE id=?" + frag,
                                     [request_id, *tp]).fetchone()
                if not exists:
                    return False, "no such request"
                return True, ""   # already void: idempotent success
            return True, ""
        finally:
            con.close()
    except Exception as e:
        log.exception("void_request failed for %s", request_id)
        return False, f"could not void ({str(e)[:80]})"


def _mark_signed(con, request_id):
    """Flip a pending request to 'signed' (no-op if it isn't pending). Caller commits.
    Tenant-scoped (inert when multitenant OFF)."""
    frag, tp = tenancy.scope_clause("tenant_id")
    con.execute("UPDATE signature_requests SET status='signed' "
                "WHERE id=? AND status='pending'" + frag, [request_id, *tp])


# ---------------------------------------------------------------- signing
def record_signature(request_id, signer_name, original_bytes, *, signer_email=None,
                     consent_text=None, signature_image=None, ip=None, user_agent=None,
                     docdir=None, filename=None):
    """Record an SES signature against request `request_id` over `original_bytes` (the EXACT
    bytes that were presented for signing). Returns (signature_dict, "") or (None, error).
    Never raises.

    The bytes' SHA-256 is the integrity anchor (signed_doc_sha256). A signed PDF is produced
    (stamp on the last page + an appended Signature Certificate page) and stored in the vault
    via document_vault.copy_to under `docdir`; its locator + SHA-256 are recorded. On any PDF
    failure we still record the signature EVENT (the audit record stands) with no signed
    locator — logged via applog. Flips a pending request to 'signed'.

    `consent_text` defaults to DEFAULT_CONSENT (required, non-empty after defaulting).
    `signature_image` (a drawn-signature data URL) is stored compactly (truncated)."""
    signer_name = (signer_name or "").strip()
    if not signer_name:
        return None, "a signer name is required"
    consent_text = (consent_text or "").strip() or DEFAULT_CONSENT
    if original_bytes is None:
        return None, "the document bytes to sign are required"

    r = get_request(request_id)
    if not r:
        return None, "no such request"
    if r.get("status") == "void":
        return None, "this request has been voided"

    sha = hashlib.sha256(original_bytes).hexdigest()
    signed_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # produce the signed PDF (best-effort) and vault it.
    signed_locator = None
    signed_sha = None
    try:
        signed_bytes = build_signed_pdf(
            original_bytes, signer_name=signer_name, signer_email=signer_email,
            consent_text=consent_text, sha256=sha, signed_at=signed_at, ip=ip,
            title=r.get("title"))
    except Exception as e:
        # build_signed_pdf is itself defensive, but never let a producer bug lose the event.
        log.warning("build_signed_pdf raised for request %s: %s", request_id, e)
        signed_bytes = None
    if signed_bytes:
        try:
            import document_vault
            name = _vault_name(r, filename)
            signed_locator, _url = document_vault.copy_to(name, signed_bytes,
                                                          docdir or _docdir())
            signed_sha = hashlib.sha256(signed_bytes).hexdigest()
        except Exception as e:
            log.exception("vault write of signed PDF failed for request %s", request_id)
            signed_locator = None
            signed_sha = None

    img = (signature_image or "")
    if img and len(img) > MAX_SIGNATURE_IMAGE:
        img = img[:MAX_SIGNATURE_IMAGE]

    try:
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO signatures
                   (request_id, signer_name, signer_email, consent_text,
                    signed_doc_sha256, signature_image, signed_locator, signed_sha256,
                    ip, user_agent, tenant_id, signed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (request_id, signer_name, (signer_email or "").strip()[:200] or None,
                 consent_text[:4000], sha, (img or None), signed_locator, signed_sha,
                 (ip or "")[:64], (user_agent or "")[:400], tenancy.write_tenant(),
                 signed_at))
            _mark_signed(con, request_id)
            con.commit()
            row = con.execute("SELECT * FROM signatures WHERE id=?",
                              (cur.lastrowid,)).fetchone()
        finally:
            con.close()
        return (dict(row) if row else None), ""
    except Exception as e:
        log.exception("record_signature insert failed for request %s", request_id)
        return None, f"could not record signature ({str(e)[:80]})"


def signatures_for(request_id):
    """All signatures recorded against a request, newest first, as dicts. Never raises -> []."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            rows = con.execute(
                "SELECT * FROM signatures WHERE request_id=?" + frag
                + " ORDER BY signed_at DESC, id DESC",
                [request_id, *tp]).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("signatures_for failed for %s: %s", request_id, e)
        return []


def get_signature(signature_id):
    """Return a signature row dict by id, or None. Never raises."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            row = con.execute("SELECT * FROM signatures WHERE id=?" + frag,
                              [signature_id, *tp]).fetchone()
        finally:
            con.close()
        return dict(row) if row else None
    except Exception as e:
        log.warning("get_signature failed for %s: %s", signature_id, e)
        return None


def verify(signature_id, original_bytes=None, docdir=None):
    """Re-hash the signed/original bytes and confirm the recorded SHA-256 binding still holds.
    Returns a dict:
        {"ok": bool, "signature_id": id, "doc_ref": <request subject_ref or None>,
         "expected_doc_sha256": <as recorded>, "signed_locator": <or None>,
         "signed_ok": <bool|None>, "doc_ok": <bool|None>, "detail": <human note>}
    - signed_ok: re-hash of the produced signed PDF (from the vault) == recorded signed_sha256.
    - doc_ok: if `original_bytes` is supplied, its SHA-256 == recorded signed_doc_sha256.
    `ok` is True iff every check that COULD run passed and at least one ran. Never raises."""
    out = {"ok": False, "signature_id": signature_id, "doc_ref": None,
           "expected_doc_sha256": None, "signed_locator": None,
           "signed_ok": None, "doc_ok": None, "detail": ""}
    sig = get_signature(signature_id)
    if not sig:
        out["detail"] = "no such signature"
        return out
    out["expected_doc_sha256"] = sig.get("signed_doc_sha256")
    out["signed_locator"] = sig.get("signed_locator")
    req = get_request(sig.get("request_id"))
    if req:
        out["doc_ref"] = req.get("subject_ref")

    checks = []
    # 1) the produced signed PDF in the vault still hashes to the recorded digest.
    if sig.get("signed_locator") and sig.get("signed_sha256"):
        try:
            import document_vault
            data = document_vault.get_bytes(sig["signed_locator"], docdir or _docdir())
            got = hashlib.sha256(data).hexdigest()
            out["signed_ok"] = secrets_eq(got, sig["signed_sha256"])
            checks.append(out["signed_ok"])
        except Exception as e:
            log.warning("verify: signed-PDF re-hash failed for sig %s: %s", signature_id, e)
            out["signed_ok"] = False
            checks.append(False)

    # 2) the supplied original-as-signed bytes still match the bound document digest.
    if original_bytes is not None and sig.get("signed_doc_sha256"):
        got = hashlib.sha256(original_bytes).hexdigest()
        out["doc_ok"] = secrets_eq(got, sig["signed_doc_sha256"])
        checks.append(out["doc_ok"])

    if not checks:
        out["detail"] = "nothing to verify (no stored signed PDF and no original bytes given)"
        out["ok"] = False
        return out
    out["ok"] = all(checks)
    out["detail"] = "verified" if out["ok"] else "INTEGRITY CHECK FAILED — bytes altered"
    return out


def secrets_eq(a, b):
    """Constant-time hex-digest comparison (never raises)."""
    try:
        return secrets.compare_digest(str(a), str(b))
    except Exception:
        return False


# ---------------------------------------------------------------- signed-PDF production
def build_signed_pdf(original_bytes, *, signer_name, signer_email=None, consent_text=None,
                     sha256=None, signed_at=None, ip=None, title=None):
    """Return the SIGNED PDF bytes: the original with a signature block stamped on the LAST
    page + an appended "Signature Certificate" page. On ANY failure returns None (caller
    records the signature event against the original bytes regardless). Never raises.

    Reuses the share_watermark pypdf-overlay technique for the stamp and
    customer_master.text_to_pdf (then a pypdf merge) for the certificate page."""
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception as e:
        log.warning("build_signed_pdf: pypdf unavailable: %s", e)
        return None
    try:
        reader = PdfReader(io.BytesIO(original_bytes))
        if reader.is_encrypted:
            log.info("build_signed_pdf: source PDF is encrypted — skipping stamp/cert")
            return None
        writer = PdfWriter(clone_from=reader)
        # 1) stamp a small signature block onto the LAST page.
        if len(writer.pages):
            last = writer.pages[-1]
            box = last.mediabox
            stamp = _signature_stamp_page(float(box.width), float(box.height),
                                          signer_name, signed_at)
            if stamp is not None:
                try:
                    last.merge_page(stamp)
                except Exception as e:
                    log.warning("build_signed_pdf: stamp merge failed: %s", e)
        # 2) append a rendered "Signature Certificate" page.
        cert_pdf = _certificate_pdf(signer_name=signer_name, signer_email=signer_email,
                                    consent_text=consent_text, sha256=sha256,
                                    signed_at=signed_at, ip=ip, title=title)
        if cert_pdf:
            try:
                cert_reader = PdfReader(io.BytesIO(cert_pdf))
                for p in cert_reader.pages:
                    writer.add_page(p)
            except Exception as e:
                log.warning("build_signed_pdf: certificate append failed: %s", e)
        out = io.BytesIO()
        writer.write(out)
        data = out.getvalue()
        if not data:
            return None
        return data
    except Exception as e:
        log.warning("build_signed_pdf failed (%d bytes in): %s",
                    len(original_bytes or b""), e)
        return None


def _pdf_escape(s):
    """Escape a string for a PDF literal-string operand (reused from share_watermark)."""
    out = []
    for ch in str(s):
        if ch in ("\\", "(", ")"):
            out.append("\\" + ch)
        elif 32 <= ord(ch) < 127 or 160 <= ord(ch) <= 255:
            out.append(ch)
        else:
            out.append("?")
    return "".join(out).encode("latin-1", "replace")


def _signature_stamp_page(width, height, signer_name, signed_at):
    """Build a one-page pypdf overlay carrying a signature block in the lower-left, to be
    merged onto the document's last page (the share_watermark overlay technique). Returns a
    PageObject or None on failure."""
    try:
        from pypdf import PageObject
        from pypdf.generic import (DecodedStreamObject, NameObject, DictionaryObject)
    except Exception as e:
        log.warning("_signature_stamp_page: pypdf unavailable: %s", e)
        return None
    try:
        page = PageObject.create_blank_page(width=width, height=height)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        fonts = DictionaryObject()
        fonts[NameObject("/F1")] = font
        res = DictionaryObject()
        res[NameObject("/Font")] = fonts
        page[NameObject("/Resources")] = res

        x, y = 40, 44
        w = max(180, min(width - 80, 320))
        lines = [
            (f"Electronically signed by: {signer_name}", 11),
            (f"Date (UTC): {signed_at or ''}", 9),
            ("SES — not a qualified electronic signature", 7),
        ]
        ops = [b"q",
               # a faint box around the signature block
               b"0.45 0.45 0.45 RG", b"0.75 w",
               f"{x-8} {y-12} {w} 56 re S".encode("latin-1"),
               b"0.10 0.10 0.10 rg"]
        ty = y + 32
        for text, size in lines:
            ops.append(b"BT")
            ops.append(f"/F1 {size} Tf".encode("latin-1"))
            ops.append(f"1 0 0 1 {x} {ty} Tm".encode("latin-1"))
            ops.append(b"(" + _pdf_escape(text) + b") Tj")
            ops.append(b"ET")
            ty -= size + 5
        ops.append(b"Q")

        stream = DecodedStreamObject()
        stream.set_data(b"\n".join(ops))
        page[NameObject("/Contents")] = stream
        return page
    except Exception as e:
        log.warning("_signature_stamp_page failed: %s", e)
        return None


def _certificate_pdf(*, signer_name, signer_email=None, consent_text=None, sha256=None,
                     signed_at=None, ip=None, title=None):
    """Render the standalone "Signature Certificate" page bytes via
    customer_master.text_to_pdf. Returns PDF bytes or None. Never raises."""
    try:
        import customer_master
    except Exception as e:
        log.warning("_certificate_pdf: customer_master unavailable: %s", e)
        return None
    try:
        lines = [
            "SIGNATURE CERTIFICATE",
            "",
            SES_NOTICE,
            "",
            f"Document: {title or '(untitled)'}",
            f"Signer name: {signer_name}",
            f"Signer email: {signer_email or '(not provided)'}",
            f"Signed at (UTC): {signed_at or ''}",
            f"Signer IP: {ip or '(not recorded)'}",
            "",
            "Document SHA-256 (the exact bytes presented for signing):",
            f"  {sha256 or '(unavailable)'}",
            "",
            "Consent statement accepted by the signer:",
            f"  {consent_text or DEFAULT_CONSENT}",
            "",
            "This certificate evidences a Simple Electronic Signature (SES). It is NOT a",
            "qualified electronic signature: no certificate, trust service provider or",
            "qualified time-stamp is involved. The SHA-256 above is the integrity anchor —",
            "any later change to the signed bytes invalidates this signature on verification.",
        ]
        return customer_master.text_to_pdf("\n".join(lines), title="Signature Certificate")
    except Exception as e:
        log.warning("_certificate_pdf failed: %s", e)
        return None


# ---------------------------------------------------------------- vault helpers
def _docdir():
    """The document vault directory (shared with the rest of the platform). Resolved via
    vat_refund.DOCDIR; falls back to <WORKDIR>/documents."""
    try:
        import vat_refund as VR
        return VR.DOCDIR
    except Exception as e:
        log.warning("_docdir: vat_refund.DOCDIR unavailable, using default: %s", e)
        return os.path.join(WORKDIR, "documents")


def _vault_name(request, filename):
    """Build a stable, human-navigable vault filename for a produced signed PDF. Filed under
    an 'e-signatures' folder so signed copies are easy to locate by hand."""
    import document_vault
    base = (filename or request.get("title") or f"request_{request.get('id')}")
    base = str(base).rsplit("/", 1)[-1]
    if base.lower().endswith(".pdf"):
        base = base[:-4]
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    name = f"{base}_signed_{stamp}_{secrets.token_hex(3)}.pdf"
    return document_vault.vault_path(["e-signatures", str(request.get("id") or "req")], name)


if __name__ == "__main__":
    # offline smoke (uses the live esign.db): create -> sign -> verify -> tamper -> void.
    def _pdf():
        from pypdf import PdfWriter, PageObject
        w = PdfWriter()
        w.add_page(PageObject.create_blank_page(width=300, height=300))
        b = io.BytesIO(); w.write(b); return b.getvalue()

    body = _pdf()
    req, err = create_request("doc:smoke", "Smoke contract", "system")
    if err:
        print("create error:", err)
    else:
        sig, e2 = record_signature(req["id"], "Alice Tester", body,
                                   signer_email="alice@example.com", ip="127.0.0.1",
                                   user_agent="smoke")
        print("signed:", bool(sig), "locator:", (sig or {}).get("signed_locator"))
        v = verify(sig["id"], original_bytes=body)
        print("verify ok:", v["ok"], v["detail"])
        bad = verify(sig["id"], original_bytes=body + b"tampered")
        print("verify tampered ok:", bad["ok"], bad["detail"])
        ok, msg = void_request(req["id"], "system")
        print("void:", ok, "status now:", get_request(req["id"])["status"])

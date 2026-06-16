"""
SECURE SHARE LINKS (B1) — a Papermark/DocSend-style trackable public link over a
vaulted document.

WHAT THIS IS. An authenticated user mints an unguessable public LINK to ONE vaulted
PDF (referenced by its vault locator = the invoice_documents.stored_path), optionally
gated by an expiry, a password and/or an email-capture, and can later see WHO viewed
it. The public viewer (app.py /s/<token>) embeds the PDF in a same-origin <iframe>
served from /s/<token>/file and records ONE view server-side. NO pdf.js / page-by-page
analytics / watermark / data-rooms here — those are later phases.

VAULT BYTES. A link stores the vault LOCATOR (doc_ref) only — never bytes, never a
filesystem path the caller controls. The bytes are fetched strictly through
document_vault.get_bytes(doc_ref, DOCDIR), which routes by locator prefix and (local
backend) guards against path traversal. So there is exactly one resolution path and it
is the same traversal-safe one the rest of the app uses.

OWN DB. Like every other module this owns its SQLite file (sharing.db) via connect() +
db_migrate, audit-installed, WAL-tuned. Tables carry a tenant_id (tenancy seam, inert
today): stamped with tenancy.write_tenant() on INSERT, never filtered yet.

PASSWORDS. We REUSE auth's scrypt KDF (auth._hash) — no invented crypto. A per-link
random salt is generated; password_hash stores "<salt_hex>:<hash_hex>". Verification is
constant-time (secrets.compare_digest). A link with no password stores NULL.

SAFETY. The whole public surface is enumeration-safe (missing/revoked/expired all look
the same to a caller) and every API call is best-effort: failures are logged via applog
and returned AS VALUES (an (ok, error) pair or None) — this module never raises to the
caller, mirroring notify/tenancy read paths.
"""
import os
import sqlite3
import secrets
import datetime

import applog
import audit
import db_tuning
import db_migrate
import tenancy

log = applog.get("sharing")

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/sharing.db"

# A view from the SAME (link, email) within this window is treated as a refresh, not a
# fresh visit: we still serve the document but do NOT double-count or re-notify the owner.
VIEW_DEDUP_SECONDS = 600

SCHEMA = """
CREATE TABLE IF NOT EXISTS share_links (
    id            INTEGER PRIMARY KEY,
    token         TEXT UNIQUE NOT NULL,
    doc_ref       TEXT NOT NULL,
    title         TEXT,
    created_by    TEXT,
    tenant_id     TEXT NOT NULL DEFAULT 'default',
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at    TEXT,
    password_hash TEXT,
    require_email INTEGER NOT NULL DEFAULT 0,
    revoked       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_share_links_token ON share_links(token);
CREATE TABLE IF NOT EXISTS share_views (
    id          INTEGER PRIMARY KEY,
    link_id     INTEGER NOT NULL,
    viewer_email TEXT,
    ip          TEXT,
    user_agent  TEXT,
    viewed_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    tenant_id   TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS ix_share_views_link ON share_views(link_id, viewed_at);
CREATE TABLE IF NOT EXISTS share_agreements (
    id           INTEGER PRIMARY KEY,
    link_id      INTEGER NOT NULL,
    viewer_email TEXT,
    accepted_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    ip           TEXT,
    user_agent   TEXT,
    tenant_id    TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS ix_share_agreements_link ON share_agreements(link_id, accepted_at);
"""

# B2 migrations (NDA gate + dynamic watermark): APPEND only — positions are stable. The
# CREATE TABLE for share_agreements lives in SCHEMA (idempotent CREATE IF NOT EXISTS); the
# new COLUMNS on the existing share_links table go through db_migrate so each ALTER runs
# ONCE per database.
_MIGRATIONS = [
    "ALTER TABLE share_links ADD COLUMN nda_required INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE share_links ADD COLUMN agreement_text TEXT",
    "ALTER TABLE share_links ADD COLUMN watermark INTEGER NOT NULL DEFAULT 0",
]

_SCHEMA_READY = set()   # DB files whose schema is set up this process


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)  # WAL + busy_timeout for safe multi-process access
    audit.bind(con)      # audit triggers call ffs_actor(); register it every connect
    if DB == ":memory:" or DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        # versioned migrations: each runs ONCE per database (db_migrate). Append only.
        db_migrate.apply(con, "sharing", _MIGRATIONS)
        audit.install_audit(con, ["share_links", "share_views", "share_agreements"])
        con.commit()
        _SCHEMA_READY.add(DB)
    return con


# ---------------------------------------------------------------- passwords (reuse auth)
def _hash_password(password):
    """Hash a share-link password with auth's scrypt KDF (no invented crypto). Returns
    "<salt_hex>:<hash_hex>" for storage, or None for an empty password."""
    if not password:
        return None
    import auth
    salt = secrets.token_bytes(16)
    digest = auth._hash(password, salt, auth.NEW_N, auth.SCRYPT_MAXMEM)
    return f"{salt.hex()}:{digest.hex()}"


def check_password(stored, password):
    """Constant-time verify of `password` against a stored "<salt_hex>:<hash_hex>".
    A link with no password (stored falsy) needs none -> True. Never raises."""
    if not stored:
        return True
    try:
        import auth
        salt_hex, want_hex = str(stored).split(":", 1)
        salt = bytes.fromhex(salt_hex)
        got = auth._hash(password or "", salt, auth.NEW_N, auth.SCRYPT_MAXMEM)
        return secrets.compare_digest(got.hex(), want_hex)
    except Exception as e:
        log.warning("check_password failed (treating as wrong): %s", e)
        return False


# ---------------------------------------------------------------- create / read
def create_link(doc_ref, title, actor, expires_at=None, password=None, require_email=False,
                nda_required=False, agreement_text=None, watermark=False):
    """Mint a share link for the vault locator `doc_ref`. Returns (link_dict, "") on
    success or (None, error_message). Never raises to the caller — failures are logged
    and returned as a value.

    B2 opt-ins: `nda_required` (+ `agreement_text` rendered on the agreement page) gates
    the document behind a logged "I agree"; `watermark` overlays a per-viewer diagonal
    watermark on every page of the streamed PDF."""
    doc_ref = (doc_ref or "").strip()
    if not doc_ref:
        return None, "a document reference is required"
    token = secrets.token_urlsafe(32)
    try:
        con = connect()
        try:
            con.execute(
                """INSERT INTO share_links
                   (token, doc_ref, title, created_by, tenant_id, expires_at,
                    password_hash, require_email, revoked, nda_required,
                    agreement_text, watermark)
                   VALUES (?,?,?,?,?,?,?,?,0,?,?,?)""",
                (token, doc_ref, (title or "").strip() or None, actor or "",
                 tenancy.write_tenant(), (expires_at or None),
                 _hash_password(password), 1 if require_email else 0,
                 1 if nda_required else 0,
                 ((agreement_text or "").strip() or None) if nda_required else None,
                 1 if watermark else 0))
            con.commit()
            row = con.execute("SELECT * FROM share_links WHERE token=?", (token,)).fetchone()
        finally:
            con.close()
        return (dict(row) if row else None), ""
    except Exception as e:
        log.exception("create_link failed for doc_ref=%r", doc_ref)
        return None, f"could not create link ({str(e)[:80]})"


def get_by_token(token):
    """Return the link row dict for `token`, or None (unknown/error). Never raises."""
    if not token:
        return None
    try:
        con = connect()
        try:
            row = con.execute("SELECT * FROM share_links WHERE token=?", (token,)).fetchone()
        finally:
            con.close()
        return dict(row) if row else None
    except Exception as e:
        log.warning("get_by_token failed: %s", e)
        return None


def get_by_id(link_id):
    """Return the link row dict by id, or None. Never raises."""
    try:
        con = connect()
        try:
            row = con.execute("SELECT * FROM share_links WHERE id=?", (link_id,)).fetchone()
        finally:
            con.close()
        return dict(row) if row else None
    except Exception as e:
        log.warning("get_by_id failed: %s", e)
        return None


# ---------------------------------------------------------------- gates
def is_expired(link):
    """True iff the link has an expires_at in the past. Unparseable/blank = not expired
    (fail open on the timestamp only — revoked/missing are handled separately)."""
    if not link:
        return True
    exp = link.get("expires_at")
    if not exp:
        return False
    try:
        dt = _parse_ts(exp)
        if dt is None:
            return False
        return dt < datetime.datetime.utcnow()
    except Exception as e:
        log.warning("is_expired parse failed for %r: %s", exp, e)
        return False


def is_active(link):
    """A link is usable iff it exists, is NOT revoked, and is NOT expired. Never raises."""
    if not link:
        return False
    return not link.get("revoked") and not is_expired(link)


def _parse_ts(s):
    """Parse an ISO-ish timestamp (date or datetime) to a naive UTC datetime, or None."""
    s = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- views
def record_view(link, email, ip, ua):
    """Record ONE view of `link`. Returns True iff a NEW view row was written (a refresh
    by the same (link, email) within VIEW_DEDUP_SECONDS returns False so callers don't
    re-notify the owner). Never raises — failures are logged and return False."""
    if not link:
        return False
    try:
        con = connect()
        try:
            link_id = link["id"]
            email = (email or None)
            recent = con.execute(
                """SELECT 1 FROM share_views
                   WHERE link_id=? AND COALESCE(viewer_email,'')=COALESCE(?,'')
                     AND viewed_at >= datetime('now', ?) LIMIT 1""",
                (link_id, email, f"-{int(VIEW_DEDUP_SECONDS)} seconds")).fetchone()
            if recent:
                return False
            con.execute(
                """INSERT INTO share_views (link_id, viewer_email, ip, user_agent, tenant_id)
                   VALUES (?,?,?,?,?)""",
                (link_id, email, (ip or "")[:64], (ua or "")[:400], tenancy.write_tenant()))
            con.commit()
            return True
        finally:
            con.close()
    except Exception as e:
        log.warning("record_view failed for link %s: %s", (link or {}).get("id"), e)
        return False


def views_for(link_id):
    """All recorded views of a link, newest first, as dicts. Never raises -> []."""
    try:
        con = connect()
        try:
            rows = con.execute(
                """SELECT id, link_id, viewer_email, ip, user_agent, viewed_at
                   FROM share_views WHERE link_id=? ORDER BY viewed_at DESC, id DESC""",
                (link_id,)).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("views_for failed for link %s: %s", link_id, e)
        return []


def view_count(link_id):
    """Number of recorded views for a link. Never raises -> 0."""
    try:
        con = connect()
        try:
            return con.execute("SELECT COUNT(*) FROM share_views WHERE link_id=?",
                               (link_id,)).fetchone()[0]
        finally:
            con.close()
    except Exception as e:
        log.warning("view_count failed for link %s: %s", link_id, e)
        return 0


# ---------------------------------------------------------------- NDA / agreement gate
def record_agreement(link, email, ip, ua):
    """Log ONE NDA/agreement acceptance for `link` into share_agreements. Returns True iff
    a row was written. Best-effort: failures are logged and return False — this never
    raises to the caller (mirrors record_view)."""
    if not link:
        return False
    try:
        con = connect()
        try:
            con.execute(
                """INSERT INTO share_agreements
                   (link_id, viewer_email, ip, user_agent, tenant_id)
                   VALUES (?,?,?,?,?)""",
                (link["id"], (email or None), (ip or "")[:64], (ua or "")[:400],
                 tenancy.write_tenant()))
            con.commit()
            return True
        finally:
            con.close()
    except Exception as e:
        log.warning("record_agreement failed for link %s: %s",
                    (link or {}).get("id"), e)
        return False


def has_accepted(link, marker):
    """True iff the agreement gate is satisfied for this link given the caller's session
    `marker` (the per-token truthy flag the web layer keeps, exactly like the password /
    email markers). A link that does NOT require an NDA is always satisfied. Never
    raises (a falsy/missing link is treated as not accepted)."""
    if not link:
        return False
    if not link.get("nda_required"):
        return True
    return bool(marker)


def agreements_for(link_id):
    """All logged acceptances of a link, newest first, as dicts. Never raises -> []."""
    try:
        con = connect()
        try:
            rows = con.execute(
                """SELECT id, link_id, viewer_email, ip, user_agent, accepted_at
                   FROM share_agreements WHERE link_id=? ORDER BY accepted_at DESC, id DESC""",
                (link_id,)).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("agreements_for failed for link %s: %s", link_id, e)
        return []


# ---------------------------------------------------------------- list / revoke
def list_links(actor):
    """Links CREATED BY `actor`, newest first, each with a `views` count. Returns a list
    of dicts. Never raises -> []. (An admin-only surface in the app; this is the per-user
    view — pass the acting username.)"""
    try:
        con = connect()
        try:
            rows = con.execute(
                """SELECT l.*, (SELECT COUNT(*) FROM share_views v WHERE v.link_id=l.id)
                          AS views
                   FROM share_links l WHERE l.created_by=?
                   ORDER BY l.created_at DESC, l.id DESC""", (actor or "",)).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("list_links failed for %r: %s", actor, e)
        return []


def revoke(link_id, actor):
    """Revoke a link (it stops serving immediately). Returns (True, "") or (False, err).
    Never raises. `actor` is informational for the audit actor (set by the caller's
    request hook); this is best-effort and idempotent."""
    try:
        con = connect()
        try:
            cur = con.execute("UPDATE share_links SET revoked=1 WHERE id=?", (link_id,))
            con.commit()
            if cur.rowcount == 0:
                return False, "no such link"
            return True, ""
        finally:
            con.close()
    except Exception as e:
        log.exception("revoke failed for link %s", link_id)
        return False, f"could not revoke ({str(e)[:80]})"


if __name__ == "__main__":
    # offline smoke (uses the live sharing.db): create -> read -> gate -> view -> revoke.
    lk, err = create_link("/tmp/none.pdf", "smoke", "system")
    if err:
        print("create error:", err)
    else:
        print("created token:", lk["token"][:12], "active:", is_active(lk))
        record_view(lk, "a@b.c", "127.0.0.1", "smoke-agent")
        print("views:", view_count(lk["id"]))
        ok, e = revoke(lk["id"], "system")
        print("revoked:", ok, "active now:", is_active(get_by_id(lk["id"])))

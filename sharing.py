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
    # B3 — per-page ("page-by-page") view analytics for the pdf.js viewer. One row per
    # flushed (link, view-session, page) engagement beacon; dwell accumulates across
    # beacons. tenant_id stamped on INSERT (tenancy seam, inert today), audit-installed.
    """CREATE TABLE IF NOT EXISTS share_page_views (
        id           INTEGER PRIMARY KEY,
        link_id      INTEGER NOT NULL,
        view_session TEXT,
        page_number  INTEGER NOT NULL,
        dwell_ms     INTEGER NOT NULL DEFAULT 0,
        viewed_at    TEXT DEFAULT CURRENT_TIMESTAMP,
        tenant_id    TEXT NOT NULL DEFAULT 'default'
    )""",
    "CREATE INDEX IF NOT EXISTS ix_share_page_views_link "
    "ON share_page_views(link_id, page_number)",
    # B4 — DATA ROOMS: group several vaulted documents into one branded, access-
    # controlled room with a single shareable link, folders, per-document page
    # analytics (reusing share_page_views) and a Q&A module. APPEND only — positions
    # stable. tenant_id on every table (tenancy seam, inert today), audit-installed.
    """CREATE TABLE IF NOT EXISTS datarooms (
        id          INTEGER PRIMARY KEY,
        name        TEXT NOT NULL,
        title       TEXT,
        created_by  TEXT,
        tenant_id   TEXT NOT NULL DEFAULT 'default',
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
        archived    INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS dataroom_documents (
        id          INTEGER PRIMARY KEY,
        room_id     INTEGER NOT NULL,
        doc_ref     TEXT NOT NULL,
        title       TEXT,
        folder      TEXT,
        sort_order  INTEGER NOT NULL DEFAULT 0,
        tenant_id   TEXT NOT NULL DEFAULT 'default',
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS ix_dataroom_documents_room "
    "ON dataroom_documents(room_id, sort_order)",
    # A ROOM-level access link that MIRRORS the share_links gate columns, so the SAME
    # gate helpers (_share_gate_or_form / is_active / record_room_agreement) run over
    # it unchanged. Its id namespace is separate from share_links.
    """CREATE TABLE IF NOT EXISTS dataroom_links (
        id            INTEGER PRIMARY KEY,
        token         TEXT UNIQUE NOT NULL,
        room_id       INTEGER NOT NULL,
        created_by    TEXT,
        tenant_id     TEXT NOT NULL DEFAULT 'default',
        created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
        expires_at    TEXT,
        password_hash TEXT,
        require_email INTEGER NOT NULL DEFAULT 0,
        nda_required  INTEGER NOT NULL DEFAULT 0,
        agreement_text TEXT,
        watermark     INTEGER NOT NULL DEFAULT 0,
        revoked       INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS ix_dataroom_links_token ON dataroom_links(token)",
    # NDA/agreement acceptances against a ROOM link (separate id namespace from
    # share_agreements, so we never cross-count a document link with a room link).
    """CREATE TABLE IF NOT EXISTS dataroom_agreements (
        id           INTEGER PRIMARY KEY,
        room_link_id INTEGER NOT NULL,
        viewer_email TEXT,
        accepted_at  TEXT DEFAULT CURRENT_TIMESTAMP,
        ip           TEXT,
        user_agent   TEXT,
        tenant_id    TEXT NOT NULL DEFAULT 'default'
    )""",
    "CREATE INDEX IF NOT EXISTS ix_dataroom_agreements_link "
    "ON dataroom_agreements(room_link_id, accepted_at)",
    """CREATE TABLE IF NOT EXISTS dataroom_questions (
        id           INTEGER PRIMARY KEY,
        room_id      INTEGER NOT NULL,
        viewer_email TEXT,
        question     TEXT NOT NULL,
        answer       TEXT,
        status       TEXT NOT NULL DEFAULT 'open',
        created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
        answered_at  TEXT,
        tenant_id    TEXT NOT NULL DEFAULT 'default'
    )""",
    "CREATE INDEX IF NOT EXISTS ix_dataroom_questions_room "
    "ON dataroom_questions(room_id, created_at)",
    # Per-(room link, document) page-engagement beacons — the room analogue of
    # share_page_views. Kept SEPARATE from share_page_views so the document-link id
    # namespace and the room-link id namespace can never cross-count; the validation/
    # clamping logic is shared via _record_page_view_row.
    """CREATE TABLE IF NOT EXISTS dataroom_page_views (
        id           INTEGER PRIMARY KEY,
        room_link_id INTEGER NOT NULL,
        doc_id       INTEGER NOT NULL,
        view_session TEXT,
        page_number  INTEGER NOT NULL,
        dwell_ms     INTEGER NOT NULL DEFAULT 0,
        viewed_at    TEXT DEFAULT CURRENT_TIMESTAMP,
        tenant_id    TEXT NOT NULL DEFAULT 'default'
    )""",
    "CREATE INDEX IF NOT EXISTS ix_dataroom_page_views_doc "
    "ON dataroom_page_views(room_link_id, doc_id, page_number)",
]

# Beacon sanity bounds (defence-in-depth; the client also clamps). A single flushed
# span over this is capped, and absurd page numbers are dropped.
MAX_DWELL_MS = 30 * 60 * 1000     # 30 minutes per flushed span
MAX_PAGE_NUMBER = 10000           # no real shared invoice has more pages than this

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
        audit.install_audit(con, ["share_links", "share_views", "share_agreements",
                                  "share_page_views", "datarooms", "dataroom_documents",
                                  "dataroom_links", "dataroom_agreements",
                                  "dataroom_questions", "dataroom_page_views"])
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


# ---------------------------------------------------------------- per-page (B3) analytics
def _clamp_page_dwell(page, dwell_ms):
    """Validate/clamp a (page, dwell_ms) beacon point (defence-in-depth — the client
    clamps too). Returns (page, dwell_ms) or None if the page is invalid: a non-positive
    or non-integer page, or a page beyond MAX_PAGE_NUMBER, is rejected; dwell is coerced
    to int, floored at 0 and capped at MAX_DWELL_MS. Shared by the B1/B2/B3 share-link
    beacon (record_page_view) and the B4 data-room beacon (record_room_page_view)."""
    try:
        page = int(page)
    except (TypeError, ValueError):
        return None
    if page < 1 or page > MAX_PAGE_NUMBER:
        return None
    try:
        dwell_ms = int(dwell_ms)
    except (TypeError, ValueError):
        dwell_ms = 0
    if dwell_ms < 0:
        dwell_ms = 0
    if dwell_ms > MAX_DWELL_MS:
        dwell_ms = MAX_DWELL_MS
    return page, dwell_ms


def record_page_view(link, view_session, page, dwell_ms):
    """Record ONE page-engagement beacon for `link`: the viewer dwelled `dwell_ms` on
    `page` during the visit identified by `view_session`. Returns True iff a row was
    written. Best-effort and never raises — the caller has ALREADY re-run the per-token
    gate; this just persists a validated, clamped data point."""
    if not link:
        return False
    clamped = _clamp_page_dwell(page, dwell_ms)
    if clamped is None:
        return False
    page, dwell_ms = clamped
    try:
        con = connect()
        try:
            con.execute(
                """INSERT INTO share_page_views
                   (link_id, view_session, page_number, dwell_ms, tenant_id)
                   VALUES (?,?,?,?,?)""",
                (link["id"], (view_session or "")[:120], page, dwell_ms,
                 tenancy.write_tenant()))
            con.commit()
            return True
        finally:
            con.close()
    except Exception as e:
        log.warning("record_page_view failed for link %s: %s",
                    (link or {}).get("id"), e)
        return False


def page_engagement(link_id, total_pages=None):
    """Aggregate per-page engagement for a link. Returns a dict:
        {"pages": [{"page": n, "dwell_ms": ms, "sessions": k}, ...],   # by page asc
         "pages_viewed": <distinct pages with dwell>,
         "total_ms": <sum of dwell across all pages>,
         "visitors": <distinct view_session count>,
         "total_pages": <total_pages or None>,
         "completion_pct": <pages_viewed/total_pages*100 or None>}
    `total_pages` (the document's real page count, if known) lets us compute a
    completion %. Never raises -> a zeroed dict."""
    empty = {"pages": [], "pages_viewed": 0, "total_ms": 0, "visitors": 0,
             "total_pages": total_pages, "completion_pct": None}
    try:
        con = connect()
        try:
            rows = con.execute(
                """SELECT page_number AS page, SUM(dwell_ms) AS dwell_ms,
                          COUNT(DISTINCT COALESCE(view_session,'')) AS sessions
                   FROM share_page_views WHERE link_id=?
                   GROUP BY page_number ORDER BY page_number ASC""",
                (link_id,)).fetchall()
            visitors = con.execute(
                """SELECT COUNT(DISTINCT COALESCE(view_session,''))
                   FROM share_page_views WHERE link_id=?""", (link_id,)).fetchone()[0]
        finally:
            con.close()
        pages = [{"page": r["page"], "dwell_ms": int(r["dwell_ms"] or 0),
                  "sessions": int(r["sessions"] or 0)} for r in rows]
        pages_viewed = sum(1 for p in pages if p["dwell_ms"] > 0)
        total_ms = sum(p["dwell_ms"] for p in pages)
        completion = None
        if total_pages and total_pages > 0:
            completion = round(min(pages_viewed, total_pages) / total_pages * 100, 1)
        return {"pages": pages, "pages_viewed": pages_viewed, "total_ms": total_ms,
                "visitors": int(visitors or 0), "total_pages": total_pages,
                "completion_pct": completion}
    except Exception as e:
        log.warning("page_engagement failed for link %s: %s", link_id, e)
        return empty


def visitor_timeline(link_id):
    """Per-visitor (view_session) page breakdown for a link, newest visit first. Returns
    a list of dicts:
        {"view_session": s, "pages_viewed": k, "total_ms": ms,
         "last_at": <max viewed_at>, "pages": [{"page": n, "dwell_ms": ms}, ...]}
    Never raises -> []."""
    try:
        con = connect()
        try:
            rows = con.execute(
                """SELECT COALESCE(view_session,'') AS view_session, page_number AS page,
                          SUM(dwell_ms) AS dwell_ms, MAX(viewed_at) AS last_at
                   FROM share_page_views WHERE link_id=?
                   GROUP BY COALESCE(view_session,''), page_number""",
                (link_id,)).fetchall()
        finally:
            con.close()
        sessions = {}
        for r in rows:
            s = r["view_session"]
            d = sessions.setdefault(
                s, {"view_session": s, "pages": [], "total_ms": 0, "last_at": ""})
            ms = int(r["dwell_ms"] or 0)
            d["pages"].append({"page": r["page"], "dwell_ms": ms})
            d["total_ms"] += ms
            if (r["last_at"] or "") > d["last_at"]:
                d["last_at"] = r["last_at"] or ""
        out = []
        for d in sessions.values():
            d["pages"].sort(key=lambda p: p["page"])
            d["pages_viewed"] = sum(1 for p in d["pages"] if p["dwell_ms"] > 0)
            out.append(d)
        out.sort(key=lambda d: d["last_at"], reverse=True)
        return out
    except Exception as e:
        log.warning("visitor_timeline failed for link %s: %s", link_id, e)
        return []


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


# ================================================================ B4 — DATA ROOMS
# A data room groups several vaulted documents into one branded, access-controlled
# space behind a SINGLE shareable, gated link. Every helper here is best-effort and
# NEVER raises to the caller (mirrors the B1-B3 read paths): failures are logged and
# returned as a value ((obj, "") / (None, err) / None / []). tenant_id is stamped on
# INSERT via tenancy.write_tenant() (inert today). The ROOM LINK mirrors the share_links
# gate columns so the web layer's existing _share_gate_or_form runs over it unchanged.

# ---------------------------------------------------------------- rooms + documents
def create_room(name, title, actor):
    """Create a data room. Returns (room_dict, "") or (None, error). Never raises."""
    name = (name or "").strip()
    if not name:
        return None, "a room name is required"
    try:
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO datarooms (name, title, created_by, tenant_id)
                   VALUES (?,?,?,?)""",
                (name, (title or "").strip() or None, actor or "",
                 tenancy.write_tenant()))
            con.commit()
            row = con.execute("SELECT * FROM datarooms WHERE id=?",
                              (cur.lastrowid,)).fetchone()
        finally:
            con.close()
        return (dict(row) if row else None), ""
    except Exception as e:
        log.exception("create_room failed for name=%r", name)
        return None, f"could not create room ({str(e)[:80]})"


def get_room(room_id):
    """Return the room row dict by id, or None. Never raises."""
    try:
        con = connect()
        try:
            row = con.execute("SELECT * FROM datarooms WHERE id=?",
                              (room_id,)).fetchone()
        finally:
            con.close()
        return dict(row) if row else None
    except Exception as e:
        log.warning("get_room failed for %s: %s", room_id, e)
        return None


def list_rooms(actor):
    """Rooms CREATED BY `actor`, newest first, each with a `documents` count. Returns a
    list of dicts. Never raises -> []."""
    try:
        con = connect()
        try:
            rows = con.execute(
                """SELECT r.*, (SELECT COUNT(*) FROM dataroom_documents d
                                WHERE d.room_id=r.id) AS documents
                   FROM datarooms r WHERE r.created_by=?
                   ORDER BY r.created_at DESC, r.id DESC""", (actor or "",)).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("list_rooms failed for %r: %s", actor, e)
        return []


def add_document(room_id, doc_ref, title=None, folder=None, sort_order=None):
    """Add a vaulted document (by its vault locator `doc_ref`) to a room. `folder` is a
    free-text grouping (nullable); `sort_order` controls position within its folder
    (defaults to append). Returns (doc_dict, "") or (None, error). Never raises."""
    doc_ref = (doc_ref or "").strip()
    if not doc_ref:
        return None, "a document reference is required"
    try:
        con = connect()
        try:
            if sort_order is None:
                nxt = con.execute(
                    "SELECT COALESCE(MAX(sort_order), -1)+1 FROM dataroom_documents "
                    "WHERE room_id=?", (room_id,)).fetchone()[0]
                sort_order = int(nxt or 0)
            cur = con.execute(
                """INSERT INTO dataroom_documents
                   (room_id, doc_ref, title, folder, sort_order, tenant_id)
                   VALUES (?,?,?,?,?,?)""",
                (room_id, doc_ref, (title or "").strip() or None,
                 (folder or "").strip() or None, int(sort_order),
                 tenancy.write_tenant()))
            con.commit()
            row = con.execute("SELECT * FROM dataroom_documents WHERE id=?",
                              (cur.lastrowid,)).fetchone()
        finally:
            con.close()
        return (dict(row) if row else None), ""
    except Exception as e:
        log.exception("add_document failed for room=%s doc_ref=%r", room_id, doc_ref)
        return None, f"could not add document ({str(e)[:80]})"


def list_documents(room_id):
    """Documents in a room, ordered by folder then sort_order then id. Returns a list of
    dicts. Never raises -> []."""
    try:
        con = connect()
        try:
            rows = con.execute(
                """SELECT * FROM dataroom_documents WHERE room_id=?
                   ORDER BY COALESCE(folder,''), sort_order, id""",
                (room_id,)).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("list_documents failed for room %s: %s", room_id, e)
        return []


def get_document(room_id, doc_id):
    """Return a room document by (room_id, doc_id) — enforcing membership: a doc_id that
    does NOT belong to room_id returns None. Never raises."""
    try:
        con = connect()
        try:
            row = con.execute(
                "SELECT * FROM dataroom_documents WHERE id=? AND room_id=?",
                (doc_id, room_id)).fetchone()
        finally:
            con.close()
        return dict(row) if row else None
    except Exception as e:
        log.warning("get_document failed for room %s doc %s: %s", room_id, doc_id, e)
        return None


# ---------------------------------------------------------------- room link (gated)
def create_room_link(room_id, actor, expires_at=None, password=None, require_email=False,
                     nda_required=False, agreement_text=None, watermark=False):
    """Mint a ROOM-level access link mirroring the share_links gate columns. Returns
    (link_dict, "") or (None, error). Never raises. The same gate options as a B2 share
    link (expiry/password/NDA/email/watermark) apply to the whole room."""
    try:
        room_id = int(room_id)
    except (TypeError, ValueError):
        return None, "a valid room is required"
    token = secrets.token_urlsafe(32)
    try:
        con = connect()
        try:
            con.execute(
                """INSERT INTO dataroom_links
                   (token, room_id, created_by, tenant_id, expires_at, password_hash,
                    require_email, nda_required, agreement_text, watermark, revoked)
                   VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
                (token, room_id, actor or "", tenancy.write_tenant(),
                 (expires_at or None), _hash_password(password),
                 1 if require_email else 0, 1 if nda_required else 0,
                 ((agreement_text or "").strip() or None) if nda_required else None,
                 1 if watermark else 0))
            con.commit()
            row = con.execute("SELECT * FROM dataroom_links WHERE token=?",
                              (token,)).fetchone()
        finally:
            con.close()
        return (dict(row) if row else None), ""
    except Exception as e:
        log.exception("create_room_link failed for room=%s", room_id)
        return None, f"could not create room link ({str(e)[:80]})"


def get_room_link_by_token(token):
    """Return the room-link row dict for `token`, or None. Never raises."""
    if not token:
        return None
    try:
        con = connect()
        try:
            row = con.execute("SELECT * FROM dataroom_links WHERE token=?",
                              (token,)).fetchone()
        finally:
            con.close()
        return dict(row) if row else None
    except Exception as e:
        log.warning("get_room_link_by_token failed: %s", e)
        return None


def list_room_links(room_id):
    """All access links for a room, newest first, as dicts. Never raises -> []."""
    try:
        con = connect()
        try:
            rows = con.execute(
                """SELECT * FROM dataroom_links WHERE room_id=?
                   ORDER BY created_at DESC, id DESC""", (room_id,)).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("list_room_links failed for room %s: %s", room_id, e)
        return []


def revoke_room_link(room_link_id):
    """Revoke a room link (it stops serving immediately). Returns (True, "") or
    (False, err). Idempotent, best-effort, never raises."""
    try:
        con = connect()
        try:
            cur = con.execute("UPDATE dataroom_links SET revoked=1 WHERE id=?",
                              (room_link_id,))
            con.commit()
            if cur.rowcount == 0:
                return False, "no such link"
            return True, ""
        finally:
            con.close()
    except Exception as e:
        log.exception("revoke_room_link failed for %s", room_link_id)
        return False, f"could not revoke ({str(e)[:80]})"


def record_room_agreement(room_link, email, ip, ua):
    """Log ONE NDA/agreement acceptance for a ROOM link into dataroom_agreements. The
    web layer's _share_gate_or_form calls this (injected) exactly like record_agreement
    for a document link. Returns True iff a row was written. Never raises."""
    if not room_link:
        return False
    try:
        con = connect()
        try:
            con.execute(
                """INSERT INTO dataroom_agreements
                   (room_link_id, viewer_email, ip, user_agent, tenant_id)
                   VALUES (?,?,?,?,?)""",
                (room_link["id"], (email or None), (ip or "")[:64], (ua or "")[:400],
                 tenancy.write_tenant()))
            con.commit()
            return True
        finally:
            con.close()
    except Exception as e:
        log.warning("record_room_agreement failed for link %s: %s",
                    (room_link or {}).get("id"), e)
        return False


# ---------------------------------------------------------------- room page analytics
def record_room_page_view(room_link, doc_id, view_session, page, dwell_ms):
    """Record ONE page-engagement beacon for a room document, keyed to (room link, doc).
    Reuses the SAME validation/clamping as the B3 share-link beacon. Returns True iff a
    row was written. The caller has ALREADY re-run the per-token gate. Never raises."""
    if not room_link:
        return False
    try:
        doc_id = int(doc_id)
    except (TypeError, ValueError):
        return False
    clamped = _clamp_page_dwell(page, dwell_ms)
    if clamped is None:
        return False
    page, dwell_ms = clamped
    try:
        con = connect()
        try:
            con.execute(
                """INSERT INTO dataroom_page_views
                   (room_link_id, doc_id, view_session, page_number, dwell_ms, tenant_id)
                   VALUES (?,?,?,?,?,?)""",
                (room_link["id"], doc_id, (view_session or "")[:120], page, dwell_ms,
                 tenancy.write_tenant()))
            con.commit()
            return True
        finally:
            con.close()
    except Exception as e:
        log.warning("record_room_page_view failed for link %s doc %s: %s",
                    (room_link or {}).get("id"), doc_id, e)
        return False


def room_engagement(room_id, total_pages_by_doc=None):
    """Aggregate per-document page engagement for a room across ALL its access links.
    Returns a list (one entry per document that has a row OR is in the room), ordered like
    list_documents:
        [{"doc_id": id, "doc_ref": ref, "title": t, "folder": f,
          "pages": [{"page": n, "dwell_ms": ms, "sessions": k}, ...],
          "pages_viewed": k, "total_ms": ms, "visitors": v,
          "total_pages": p_or_None, "completion_pct": pct_or_None}, ...]
    `total_pages_by_doc` (a {doc_id: real_page_count} map, if known) lets us compute a
    completion %. Never raises -> []."""
    total_pages_by_doc = total_pages_by_doc or {}
    docs = list_documents(room_id)
    out = []
    try:
        con = connect()
        try:
            for d in docs:
                rows = con.execute(
                    """SELECT pv.page_number AS page, SUM(pv.dwell_ms) AS dwell_ms,
                              COUNT(DISTINCT COALESCE(pv.view_session,'')) AS sessions
                       FROM dataroom_page_views pv
                       JOIN dataroom_links l ON l.id = pv.room_link_id
                       WHERE l.room_id=? AND pv.doc_id=?
                       GROUP BY pv.page_number ORDER BY pv.page_number ASC""",
                    (room_id, d["id"])).fetchall()
                visitors = con.execute(
                    """SELECT COUNT(DISTINCT COALESCE(pv.view_session,''))
                       FROM dataroom_page_views pv
                       JOIN dataroom_links l ON l.id = pv.room_link_id
                       WHERE l.room_id=? AND pv.doc_id=?""",
                    (room_id, d["id"])).fetchone()[0]
                pages = [{"page": r["page"], "dwell_ms": int(r["dwell_ms"] or 0),
                          "sessions": int(r["sessions"] or 0)} for r in rows]
                pages_viewed = sum(1 for p in pages if p["dwell_ms"] > 0)
                total_ms = sum(p["dwell_ms"] for p in pages)
                tp = total_pages_by_doc.get(d["id"])
                completion = None
                if tp and tp > 0:
                    completion = round(min(pages_viewed, tp) / tp * 100, 1)
                out.append({
                    "doc_id": d["id"], "doc_ref": d["doc_ref"],
                    "title": d.get("title"), "folder": d.get("folder"),
                    "pages": pages, "pages_viewed": pages_viewed,
                    "total_ms": total_ms, "visitors": int(visitors or 0),
                    "total_pages": tp, "completion_pct": completion})
        finally:
            con.close()
        return out
    except Exception as e:
        log.warning("room_engagement failed for room %s: %s", room_id, e)
        return out


# ---------------------------------------------------------------- Q&A
def ask_question(room_id, viewer_email, question):
    """Store a viewer question against a room (status 'open'). Returns (question_dict,
    "") or (None, error). Never raises. The web layer notifies the owner separately."""
    question = (question or "").strip()
    if not question:
        return None, "a question is required"
    try:
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO dataroom_questions
                   (room_id, viewer_email, question, status, tenant_id)
                   VALUES (?,?,?, 'open', ?)""",
                (room_id, (viewer_email or "").strip()[:200] or None,
                 question[:4000], tenancy.write_tenant()))
            con.commit()
            row = con.execute("SELECT * FROM dataroom_questions WHERE id=?",
                              (cur.lastrowid,)).fetchone()
        finally:
            con.close()
        return (dict(row) if row else None), ""
    except Exception as e:
        log.exception("ask_question failed for room=%s", room_id)
        return None, f"could not store question ({str(e)[:80]})"


def answer_question(question_id, answer):
    """Answer a question — sets the answer text, flips status to 'answered' and stamps
    answered_at. Returns (True, "") or (False, error). Never raises."""
    answer = (answer or "").strip()
    if not answer:
        return False, "an answer is required"
    try:
        con = connect()
        try:
            cur = con.execute(
                """UPDATE dataroom_questions
                   SET answer=?, status='answered', answered_at=CURRENT_TIMESTAMP
                   WHERE id=?""", (answer[:8000], question_id))
            con.commit()
            if cur.rowcount == 0:
                return False, "no such question"
            return True, ""
        finally:
            con.close()
    except Exception as e:
        log.exception("answer_question failed for %s", question_id)
        return False, f"could not answer ({str(e)[:80]})"


def get_question(question_id):
    """Return a question row dict by id, or None. Never raises."""
    try:
        con = connect()
        try:
            row = con.execute("SELECT * FROM dataroom_questions WHERE id=?",
                              (question_id,)).fetchone()
        finally:
            con.close()
        return dict(row) if row else None
    except Exception as e:
        log.warning("get_question failed for %s: %s", question_id, e)
        return None


def questions_for(room_id):
    """All questions for a room, newest first, as dicts. Never raises -> []."""
    try:
        con = connect()
        try:
            rows = con.execute(
                """SELECT * FROM dataroom_questions WHERE room_id=?
                   ORDER BY created_at DESC, id DESC""", (room_id,)).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("questions_for failed for room %s: %s", room_id, e)
        return []


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

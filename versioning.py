"""
DOCUMENT VERSIONING (A4) — a logical document has an ORDERED CHAIN of versions
(the Mayan EDMS / Paperless model: one logical document, many byte-versions over
time, history append-only). Version 1 is the original; each new upload becomes the
new CURRENT version and supersedes the prior one; reverting to an old version is
itself recorded as a NEW version pointing at the old version's bytes — history is
NEVER destroyed.

DATA-PRODUCT BOUNDARY. The data-processing ENGINE owns and WRITES the product DBs
(fuel_history.db = transactions/master); the app reads them strictly READ-ONLY via
dataproduct.connect(). The invoice_documents INDEX (vat_claims.db) is compliance-
owned and is the canonical "current original" record — this module does NOT touch
it. Versioning is an APP-OWNED OVERLAY in its own DB (versions.db, gitignored),
keyed by the same stable `doc:<id>` REFERENCE the A2 search index and A3 metadata
already mint for a vaulted document (`subject_ref`). This module NEVER opens a
product DB and NEVER adds a column to one. It records only POINTERS (a vault
locator + sha + size) at the document's version chain.

WHERE THE BYTES LIVE. The vault (`document_vault`) is a pluggable STORAGE backend
(local / SharePoint / FTP), DISTINCT from the product DBs, and it exposes an
app-callable store API (`copy_to(name, data, docdir)` -> `backend.put`, which is
exactly what vat_refund.attach_document uses). So a NEW version's bytes are vaulted
THROUGH THAT API (SHA-256 + the vault's own dedup), and we record the returned
locator — no engine state is written in-request, no product DB is opened writable.
A version can also be created by LINKING an already-vaulted locator (no new bytes),
for re-pointing the current at an existing file. get_version_bytes() reads back
through document_vault.get_bytes(), so any backend resolves.

OWN DB. Like every other app module this owns its SQLite file via connect() +
db_migrate, audit-installed, db_tuning-tuned. Rows carry a tenant_id (the tenancy
seam, inert today): stamped with tenancy.write_tenant() on INSERT, never filtered yet.

BEST-EFFORT / NEVER-RAISE. Every public API is best-effort and returns AS A VALUE
((obj, "") / (None, err) / [] / None / bool) — it never raises into the caller,
mirroring metadata.py / sharing.py / tenancy.py read paths. Failures are logged via
applog.
"""
import os
import hashlib
import sqlite3

import applog
import audit
import db_tuning
import db_migrate
import tenancy

log = applog.get("versioning")

WORKDIR = os.path.dirname(os.path.abspath(__file__))
# App-owned versioning DB (gitignored). A module-level attr so tests can repoint it
# the same way they repoint metadata.DB / search.DB / history.DB.
DB = f"{WORKDIR}/versions.db"

# Where new-version bytes are vaulted (the SAME docstore the invoice vault uses, so a
# version sits beside the original). A module-level attr so a test can point it at a
# temp vault. Versioned files are filed under a "<subject>/versions" subtree.
DOCDIR = f"{WORKDIR}/documents"

SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_versions (
    id            INTEGER PRIMARY KEY,
    subject_ref   TEXT NOT NULL,              -- the logical document's `doc:<id>`
    version_no    INTEGER NOT NULL,           -- 1 = original; increments per version
    vault_locator TEXT NOT NULL,              -- stored_path/locator of THIS version's bytes
    sha256        TEXT,
    size          INTEGER,
    note          TEXT,
    created_by    TEXT,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    superseded    INTEGER NOT NULL DEFAULT 0, -- 1 once a newer version replaces it
    reverted_from INTEGER,                    -- the version id this one re-points at (revert)
    tenant_id     TEXT NOT NULL DEFAULT 'default'
);
-- One row per (subject, version_no).
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_versions_sv
    ON doc_versions(subject_ref, version_no);
CREATE INDEX IF NOT EXISTS ix_doc_versions_subject ON doc_versions(subject_ref);
"""

# Versioned migrations: APPEND new statements at the END (positions are stable).
_MIGRATIONS = [
    # ── TENANT-QUALIFIED UNIQUE re-key (multi-tenant correctness) ───────────────
    # The natural key here is (subject_ref, version_no): "one row per version of a
    # logical document". The SCHEMA ships it as a UNIQUE INDEX, NOT tenant-qualified,
    # so under the `multitenant` switch ON two tenants holding the SAME logical
    # document ref could not BOTH have a version_no 1 — they would COLLIDE on the
    # UNIQUE, losing/blocking a tenant's version chain. Re-key the UNIQUE to
    # (tenant_id, subject_ref, version_no) so uniqueness is per-tenant.
    #
    # A UNIQUE *index* (not a table-level constraint / PK) re-keys with a simple DROP
    # INDEX + CREATE — NO table rebuild, so the surrogate `id` PK, the rows, and the
    # audit triggers (which live on the TABLE, not the index) are all untouched.
    # Existing rows already carry tenant_id='default' (the P1 column DEFAULT), so the
    # new index builds cleanly over them. OFF byte-identical: with one tenant
    # ('default') a (tenant_id, subject_ref, version_no) UNIQUE rejects a duplicate
    # exactly as (subject_ref, version_no) did. APPEND-ONLY — keep at END; idempotent
    # (each statement runs once per DB via db_migrate; DROP … IF EXISTS is re-runnable).
    "DROP INDEX IF EXISTS ux_doc_versions_sv",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_versions_tsv "
    "ON doc_versions(tenant_id, subject_ref, version_no)",
]

_SCHEMA_READY = set()   # DB files whose schema is set up this process


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)  # WAL + busy_timeout for safe multi-process access
    audit.bind(con)      # audit triggers call ffs_actor(); register it every connect
    if DB == ":memory:" or DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        db_migrate.apply(con, "versioning", _MIGRATIONS)
        audit.install_audit(con, ["doc_versions"])
        con.commit()
        _SCHEMA_READY.add(DB)
    return con


# ============================================================ helpers
def _norm_ref(subject_ref):
    return (subject_ref or "").strip()


def _row(r):
    return dict(r) if r else None


def _chain(con, subject_ref):
    """All version rows for a subject, OLDEST first (internal helper). Tenant-scoped
    (inert when multitenant OFF)."""
    frag, tp = tenancy.scope_clause("tenant_id")
    return con.execute(
        "SELECT * FROM doc_versions WHERE subject_ref=?" + frag
        + " ORDER BY version_no", [subject_ref, *tp]).fetchall()


def _vault_bytes(data, subject_ref, version_no, filename=None):
    """Store `data` in the vault under a per-subject 'versions' subtree and return
    (locator, web_url). Uses document_vault.copy_to (-> backend.put), the SAME
    app-callable store API vat_refund.attach_document uses; SHA-256 dedup is the
    vault's own. NEVER touches a product DB."""
    import document_vault
    safe_subject = subject_ref.replace(":", "_")
    name = filename or f"v{version_no}"
    vault_name = document_vault.vault_path([safe_subject, "versions"],
                                           f"v{version_no}_{name}")
    return document_vault.copy_to(vault_name, data, DOCDIR)


# ============================================================ recording versions
def record_initial(subject_ref, vault_locator, sha256=None, size=None, actor=None,
                   note="original"):
    """Seed a document's version chain with version 1 = the ORIGINAL (the bytes
    already vaulted by the invoice attach). IDEMPOTENT: a no-op (returns the existing
    current) if a chain already exists for this subject — so calling it on every view
    is safe and never duplicates. Returns (version_dict, "") or (None, error). Never
    raises."""
    subject_ref = _norm_ref(subject_ref)
    if not subject_ref:
        return None, "a document reference is required"
    if not (vault_locator or "").strip():
        return None, "a vault locator is required"
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            existing = con.execute(
                "SELECT * FROM doc_versions WHERE subject_ref=?" + frag
                + " ORDER BY version_no DESC LIMIT 1", [subject_ref, *tp]).fetchone()
            if existing is not None:
                # already seeded — idempotent no-op, return the current version
                return current(subject_ref), ""
            con.execute(
                """INSERT INTO doc_versions (subject_ref, version_no, vault_locator,
                       sha256, size, note, created_by, superseded, tenant_id)
                   VALUES (?,?,?,?,?,?,?,0,?)""",
                (subject_ref, 1, str(vault_locator), sha256, size, note,
                 actor or "system", tenancy.write_tenant()))
            con.commit()
            row = con.execute(
                "SELECT * FROM doc_versions WHERE subject_ref=? AND version_no=1" + frag,
                [subject_ref, *tp]).fetchone()
        finally:
            con.close()
        return _row(row), ""
    except Exception as e:
        log.exception("record_initial failed for %r", subject_ref)
        return None, f"could not record initial version ({str(e)[:80]})"


def add_version(subject_ref, new_bytes=None, *, vault_locator=None, sha256=None,
               size=None, filename=None, note="", actor=None, _reverted_from=None):
    """Add a NEW current version to `subject_ref`'s chain and mark the prior current
    superseded — history is kept. Two byte-ingestion paths (pick ONE):

      * new_bytes   — raw bytes to VAULT now (the upload path). Stored via the vault's
                      app-callable store API (document_vault.copy_to -> backend.put,
                      SHA-256 + the vault's own dedup); the returned locator is recorded.
      * vault_locator — an ALREADY-vaulted locator to LINK as a new version (no new
                      bytes; the re-point/link-existing path). sha256/size optional.

    If the subject has NO chain yet, version 1 is seeded from THIS call's bytes/locator
    (so add_version always lands a valid current). Returns (version_dict, "") or
    (None, error). Never raises. NEVER opens a product DB."""
    subject_ref = _norm_ref(subject_ref)
    if not subject_ref:
        return None, "a document reference is required"
    if new_bytes is None and not (vault_locator or "").strip():
        return None, "either new bytes or an existing vault locator is required"
    try:
        con = connect()
        try:
            chain = _chain(con, subject_ref)
            next_no = (chain[-1]["version_no"] + 1) if chain else 1
            # Resolve the bytes -> a vault locator (+ sha/size). The link-existing path
            # records the locator as-is; the upload path vaults the bytes first.
            if new_bytes is not None:
                data = bytes(new_bytes)
                sha = sha256 or hashlib.sha256(data).hexdigest()
                sz = size if size is not None else len(data)
                loc, _web = _vault_bytes(data, subject_ref, next_no, filename)
            else:
                loc = str(vault_locator)
                sha = sha256
                sz = size
            con.execute(
                """INSERT INTO doc_versions (subject_ref, version_no, vault_locator,
                       sha256, size, note, created_by, superseded, reverted_from,
                       tenant_id)
                   VALUES (?,?,?,?,?,?,?,0,?,?)""",
                (subject_ref, next_no, loc, sha, sz, (note or "").strip() or None,
                 actor or "system", _reverted_from, tenancy.write_tenant()))
            # supersede everything BELOW the new current (so a re-seed of a damaged
            # chain still leaves exactly one current = the newest).
            frag, tp = tenancy.scope_clause("tenant_id")
            con.execute(
                "UPDATE doc_versions SET superseded=1 "
                "WHERE subject_ref=? AND version_no<?" + frag,
                [subject_ref, next_no, *tp])
            con.commit()
            row = con.execute(
                "SELECT * FROM doc_versions WHERE subject_ref=? AND version_no=?" + frag,
                [subject_ref, next_no, *tp]).fetchone()
        finally:
            con.close()
        return _row(row), ""
    except Exception as e:
        log.exception("add_version failed for %r", subject_ref)
        return None, f"could not add version ({str(e)[:80]})"


def revert_to(version_id, actor=None, note=None):
    """Make an OLD version the current one — by recording it as a NEW version that
    points at the old version's bytes. History is NEVER destroyed (the intervening
    versions stay in the chain, just superseded). Returns (new_version_dict, "") or
    (None, error). Never raises."""
    src = get_version(version_id)
    if src is None:
        return None, "no such version"
    msg = note if note is not None else f"reverted to v{src['version_no']}"
    return add_version(
        src["subject_ref"], vault_locator=src["vault_locator"],
        sha256=src.get("sha256"), size=src.get("size"),
        note=msg, actor=actor, _reverted_from=src["id"])


# ============================================================ reads (never raise)
def versions_for(subject_ref):
    """The full version chain for `subject_ref`, NEWEST first (the chain view). Each
    dict carries an `is_current` flag (the single non-superseded newest). Never
    raises -> []."""
    subject_ref = _norm_ref(subject_ref)
    if not subject_ref:
        return []
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            rows = con.execute(
                "SELECT * FROM doc_versions WHERE subject_ref=?" + frag
                + " ORDER BY version_no DESC", [subject_ref, *tp]).fetchall()
        finally:
            con.close()
        out = [dict(r) for r in rows]
        cur_no = out[0]["version_no"] if out else None
        for d in out:
            d["is_current"] = (d["version_no"] == cur_no)
        return out
    except Exception as e:
        log.warning("versions_for failed for %r: %s", subject_ref, e)
        return []


def current(subject_ref):
    """The CURRENT (newest, non-superseded) version of `subject_ref`, or None. Never
    raises."""
    subject_ref = _norm_ref(subject_ref)
    if not subject_ref:
        return None
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            row = con.execute(
                "SELECT * FROM doc_versions WHERE subject_ref=?" + frag
                + " ORDER BY version_no DESC LIMIT 1", [subject_ref, *tp]).fetchone()
        finally:
            con.close()
        return _row(row)
    except Exception as e:
        log.warning("current failed for %r: %s", subject_ref, e)
        return None


def get_version(version_id):
    """Return one version row dict by id, or None. Never raises."""
    try:
        vid = int(version_id)
    except (TypeError, ValueError):
        return None
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            row = con.execute("SELECT * FROM doc_versions WHERE id=?" + frag,
                              [vid, *tp]).fetchone()
        finally:
            con.close()
        return _row(row)
    except Exception as e:
        log.warning("get_version failed for %r: %s", version_id, e)
        return None


def get_version_bytes(version_id):
    """Read back the vaulted bytes of a specific version through
    document_vault.get_bytes (so any backend — local/SharePoint/FTP — resolves).
    Returns (data, "") or (None, error). Never raises."""
    v = get_version(version_id)
    if v is None:
        return None, "no such version"
    try:
        import document_vault
        data = document_vault.get_bytes(v["vault_locator"], DOCDIR)
        return data, ""
    except Exception as e:
        log.warning("get_version_bytes failed for %r: %s", version_id, e)
        return None, f"could not read version bytes ({str(e)[:80]})"


def has_chain(subject_ref):
    """True iff `subject_ref` already has a version chain. Never raises -> False."""
    return current(subject_ref) is not None


if __name__ == "__main__":
    # offline smoke (uses the live versions.db + a temp vault): seed -> add -> revert.
    import tempfile
    DOCDIR = tempfile.mkdtemp(prefix="ver_vault_")
    ref = "doc:smoke"
    # seed version 1 from an already-vaulted locator (the original attach's path)
    import document_vault
    loc0, _ = document_vault.copy_to("doc_smoke/original.pdf", b"%PDF original", DOCDIR)
    v1, err = record_initial(ref, loc0, note="original")
    print("v1:", (v1 or {}).get("version_no"), err or "ok")
    v2, err = add_version(ref, b"%PDF revised", note="fixed totals", actor="smoke")
    print("v2:", (v2 or {}).get("version_no"), err or "ok")
    rv, err = revert_to(v1["id"], actor="smoke")
    print("revert ->", (rv or {}).get("version_no"), (rv or {}).get("note"), err or "ok")
    print("chain:", [(d["version_no"], d["is_current"], d["note"]) for d in versions_for(ref)])
    data, _ = get_version_bytes(current(ref)["id"])
    print("current bytes:", data)

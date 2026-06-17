"""Multi-tenant UNIQUE re-key — tenant-qualified natural-key UNIQUE for versioning.db's
doc_versions table.

doc_versions already carries a tenant_id column (P1) and tenant-scoped reads / stamped
writes (P2), but its UNIQUE INDEX was on the NATURAL key (subject_ref, version_no) only —
so under the `multitenant` switch ON two tenants holding the SAME logical document ref
could not BOTH have a version_no 1; they would COLLIDE on the UNIQUE, losing/blocking a
tenant's version chain. This slice re-keys the UNIQUE to (tenant_id, subject_ref,
version_no).

Because it is a UNIQUE *index* (not a table-level constraint / PK), the re-key is a plain
DROP INDEX + CREATE — no table rebuild — so the surrogate `id` PK, the rows and the audit
triggers (on the table, not the index) are untouched.

What this file proves:
  * NO CROSS-TENANT CLOBBER: tenant A and tenant B can each hold the SAME (subject_ref,
    version_no) — both persist, each reads back its own chain.
  * EXISTING-DB PRESERVATION: an OLD-schema (natural UNIQUE + P1 tenant_id) populated
    versions.db survives the migration with row counts preserved, rows stamped 'default',
    and the index tenant-qualified.
  * OFF byte-identical: with the switch OFF a duplicate (subject_ref, version_no) is still
    rejected exactly as before.
  * The RE-KEYED INDEX leads with tenant_id and the old index is gone.
"""
import importlib
import sqlite3

import pytest


def _fresh_env(tmp_path, monkeypatch, switch):
    """Wire a fresh versions.db / security.db and set the multitenant switch.
    Returns (versioning, tenancy)."""
    import auth
    import tenancy
    import versioning
    importlib.reload(versioning)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(versioning, "DB", str(tmp_path / "versions.db"))
    monkeypatch.setattr(versioning, "DOCDIR", str(tmp_path / "documents"))
    versioning._SCHEMA_READY.clear()

    auth.set_setting("multitenant", "1" if switch else "0")
    assert tenancy.multitenant_enabled() is switch
    return versioning, tenancy


@pytest.fixture()
def on(tmp_path, monkeypatch):
    vr, tn = _fresh_env(tmp_path, monkeypatch, switch=True)
    try:
        yield vr, tn
    finally:
        tn.reset_tenant()


# ── The core fix: no cross-tenant clobber on the SAME (subject_ref, version_no) ────
def test_no_cross_tenant_clobber(on):
    """Both tenants build a chain on the SAME subject_ref. Pre-rekey the (subject_ref,
    version_no) UNIQUE would have blocked B's version_no 1; now both persist."""
    vr, tn = on
    ref = "doc:shared"
    tn.set_tenant("A")
    v_a1, e = vr.add_version(ref, b"%PDF A v1", note="orig A")
    assert v_a1 and not e, e
    v_a2, e = vr.add_version(ref, b"%PDF A v2", note="rev A")
    assert v_a2 and not e, e

    tn.set_tenant("B")
    v_b1, e = vr.add_version(ref, b"%PDF B v1", note="orig B")
    assert v_b1 and not e, e

    tn.reset_tenant()
    tn.set_owner_scope()
    con = vr.connect()
    try:
        rows = sorted((r["tenant_id"], r["version_no"], r["note"]) for r in con.execute(
            "SELECT tenant_id, version_no, note FROM doc_versions WHERE subject_ref=?",
            (ref,)))
    finally:
        con.close()
    tn.reset_tenant()
    # BOTH tenants hold version_no 1 for the same subject_ref — no UNIQUE collision.
    assert rows == [("A", 1, "orig A"), ("A", 2, "rev A"), ("B", 1, "orig B")]

    # each tenant reads only its OWN chain
    tn.set_tenant("A")
    assert {v["note"] for v in vr.versions_for(ref)} == {"orig A", "rev A"}
    tn.set_tenant("B")
    assert {v["note"] for v in vr.versions_for(ref)} == {"orig B"}
    tn.reset_tenant()


# ── The re-keyed index: tenant_id leads; the old index is gone ────────────────────
def test_rekeyed_index_is_tenant_qualified(on):
    vr, tn = on
    con = vr.connect()
    try:
        idx = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='doc_versions'")}
        sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE name='ux_doc_versions_tsv'").fetchone()
    finally:
        con.close()
    assert "ux_doc_versions_sv" not in idx          # old natural-key index dropped
    assert "ux_doc_versions_tsv" in idx             # tenant-qualified replacement
    assert sql and "tenant_id, subject_ref, version_no" in sql[0]


# ── OFF byte-identical: a duplicate (subject_ref, version_no) is still rejected ────
def test_switch_off_rejects_duplicate_natural_key(tmp_path, monkeypatch):
    vr, tn = _fresh_env(tmp_path, monkeypatch, switch=False)
    tn.set_tenant("ZZZ")   # inert while OFF
    con = vr.connect()
    try:
        con.execute("INSERT INTO doc_versions (subject_ref, version_no, vault_locator) "
                    "VALUES ('doc:1', 1, 'loc')")
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO doc_versions (subject_ref, version_no, vault_locator) "
                        "VALUES ('doc:1', 1, 'loc2')")
            con.commit()
    finally:
        con.close()
    tn.reset_tenant()


# ── Existing-DB preservation: OLD natural-UNIQUE populated versions.db, migrated ──
def test_rebuild_preserves_rows_on_existing_db(tmp_path, monkeypatch):
    """Hand-build an OLD-schema (natural UNIQUE index + P1 tenant_id) versions.db with
    rows, then let connect()/db_migrate run the re-key. Row counts preserved, rows stamped
    'default', the old index gone and the tenant-qualified one present."""
    vr, tn = _fresh_env(tmp_path, monkeypatch, switch=False)

    con = sqlite3.connect(vr.DB)
    con.executescript("""
        CREATE TABLE doc_versions (
            id INTEGER PRIMARY KEY, subject_ref TEXT NOT NULL, version_no INTEGER NOT NULL,
            vault_locator TEXT NOT NULL, sha256 TEXT, size INTEGER, note TEXT,
            created_by TEXT, created_at TEXT, superseded INTEGER NOT NULL DEFAULT 0,
            reverted_from INTEGER, tenant_id TEXT NOT NULL DEFAULT 'default');
        CREATE UNIQUE INDEX ux_doc_versions_sv ON doc_versions(subject_ref, version_no);
        CREATE INDEX ix_doc_versions_subject ON doc_versions(subject_ref);
    """)
    con.execute("INSERT INTO doc_versions (subject_ref, version_no, vault_locator, note) "
                "VALUES ('doc:1', 1, 'loc1', 'orig')")
    con.execute("INSERT INTO doc_versions (subject_ref, version_no, vault_locator, note) "
                "VALUES ('doc:1', 2, 'loc2', 'rev')")
    con.commit()
    before = con.execute("SELECT COUNT(*) FROM doc_versions").fetchone()[0]
    con.close()

    vr._SCHEMA_READY.clear()
    con = vr.connect()
    try:
        after = con.execute("SELECT COUNT(*) FROM doc_versions").fetchone()[0]
        tenants = {r["tenant_id"] for r in con.execute("SELECT tenant_id FROM doc_versions")}
        idx = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='doc_versions'")}
    finally:
        con.close()
    assert before == after == 2
    assert tenants == {"default"}
    assert "ux_doc_versions_sv" not in idx and "ux_doc_versions_tsv" in idx


# ── Idempotent: re-running connect() does not error ───────────────────────────────
def test_migration_idempotent(on):
    vr, tn = on
    vr._SCHEMA_READY.clear()
    con = vr.connect()       # runs db_migrate again over an already-migrated DB
    try:
        idx = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='doc_versions'")}
    finally:
        con.close()
    assert "ux_doc_versions_tsv" in idx

"""Multi-tenant UNIQUE re-key — tenant-qualified natural-key UNIQUEs for metadata.db's
field_values and tag_links tables.

Both tables already carry a tenant_id column (P1) and tenant-scoped reads / stamped
writes (P2), but their UNIQUE INDEXes were on the NATURAL key only:
  • field_values(field_id, subject_ref)  — "one value per (field, subject)";
  • tag_links(tag_id, subject_ref)        — "one link per (tag, subject)".
Under the `multitenant` switch ON two tenants assigning the SAME field/subject (or
tag/subject) would COLLIDE on the UNIQUE — and set_value's ON CONFLICT would even
OVERWRITE the other tenant's value. This slice re-keys each UNIQUE (and the set_value
upsert conflict target) to lead with tenant_id.

Each is a UNIQUE *index* (not a table-level constraint / PK), so the re-key is a plain
DROP INDEX + CREATE — no table rebuild — leaving the surrogate `id` PK, the rows and the
audit triggers untouched.

What this file proves:
  * NO CROSS-TENANT CLOBBER on both tables: A and B hold the SAME natural key, each its
    own value/link.
  * set_value's ON CONFLICT still upserts IN PLACE within a tenant (after the re-key).
  * EXISTING-DB PRESERVATION: an OLD-schema (natural UNIQUE + P1 tenant_id) populated
    metadata.db migrates with no row loss, rows stamped 'default', indexes re-keyed.
  * OFF byte-identical: a duplicate natural key is still rejected exactly as before.
"""
import importlib
import sqlite3

import pytest


def _fresh_env(tmp_path, monkeypatch, switch):
    import auth
    import tenancy
    import metadata
    importlib.reload(metadata)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(metadata, "DB", str(tmp_path / "metadata.db"))
    metadata._SCHEMA_READY.clear()

    auth.set_setting("multitenant", "1" if switch else "0")
    assert tenancy.multitenant_enabled() is switch
    return metadata, tenancy


@pytest.fixture()
def on(tmp_path, monkeypatch):
    md, tn = _fresh_env(tmp_path, monkeypatch, switch=True)
    try:
        yield md, tn
    finally:
        tn.reset_tenant()


# ── field_values: no cross-tenant clobber on (field_id, subject_ref) ──────────────
def test_field_values_no_cross_tenant_clobber(on):
    md, tn = on
    # Each tenant defines its own field (surrogate ids differ); to force the SAME natural
    # key we set values against the SAME field_id and subject_ref via a shared field id.
    tn.set_tenant("A")
    fa, _ = md.define_field("Amount", "text")
    tn.set_tenant("B")
    fb, _ = md.define_field("Amount", "text")
    tn.reset_tenant()

    # Use a shared field_id value (say 1) and shared subject to exercise the natural key.
    # define_field gives each tenant a surrogate id; the natural-key collision we care
    # about is (field_id, subject_ref). Drive it directly with field id 1 + same subject.
    tn.set_tenant("A")
    ok, e = md.set_value(fa["id"], "doc:x", "A-value")
    assert ok, e
    tn.set_tenant("B")
    # B reuses A's field id to force the SAME (field_id, subject_ref) natural key. B's
    # get_field won't see A's field (scoped), so insert the value row directly to prove the
    # UNIQUE permits it across tenants.
    con = md.connect()
    try:
        con.execute("INSERT INTO field_values (field_id, subject_ref, value, tenant_id) "
                    "VALUES (?,?,?,?)", (fa["id"], "doc:x", "B-value", tn.write_tenant()))
        con.commit()
    finally:
        con.close()
    tn.reset_tenant()

    tn.set_owner_scope()
    con = md.connect()
    try:
        rows = sorted((r["tenant_id"], r["value"]) for r in con.execute(
            "SELECT tenant_id, value FROM field_values WHERE field_id=? AND subject_ref='doc:x'",
            (fa["id"],)))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", "A-value"), ("B", "B-value")]


def test_field_values_upsert_in_place_within_tenant(on):
    """set_value's ON CONFLICT(tenant_id, field_id, subject_ref) still corrects in place
    within a tenant (one row, value replaced)."""
    md, tn = on
    tn.set_tenant("A")
    f, _ = md.define_field("Note", "text")
    md.set_value(f["id"], "doc:1", "first")
    md.set_value(f["id"], "doc:1", "second")
    vals = md.get_values("doc:1")
    tn.reset_tenant()
    assert len(vals) == 1 and vals[0]["value"] == "second"


# ── tag_links: no cross-tenant clobber on (tag_id, subject_ref) ───────────────────
def test_tag_links_no_cross_tenant_clobber(on):
    md, tn = on
    tn.set_tenant("A")
    ta, _ = md.create_tag("Tax")
    md.assign_tag(ta["id"], "doc:y")
    tn.reset_tenant()
    # Force the SAME (tag_id, subject_ref) for tenant B via a direct insert.
    tn.set_tenant("B")
    con = md.connect()
    try:
        con.execute("INSERT INTO tag_links (tag_id, subject_ref, tenant_id) VALUES (?,?,?)",
                    (ta["id"], "doc:y", tn.write_tenant()))
        con.commit()
    finally:
        con.close()
    tn.reset_tenant()

    tn.set_owner_scope()
    con = md.connect()
    try:
        rows = sorted((r["tenant_id"]) for r in con.execute(
            "SELECT tenant_id FROM tag_links WHERE tag_id=? AND subject_ref='doc:y'",
            (ta["id"],)))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == ["A", "B"]


# ── Re-keyed indexes lead with tenant_id; old indexes gone ────────────────────────
def test_rekeyed_indexes_are_tenant_qualified(on):
    md, tn = on
    con = md.connect()
    try:
        idx = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        fv = con.execute(
            "SELECT sql FROM sqlite_master WHERE name='ux_field_values_tfs'").fetchone()
        tl = con.execute(
            "SELECT sql FROM sqlite_master WHERE name='ux_tag_links_tts'").fetchone()
    finally:
        con.close()
    assert "ux_field_values_fs" not in idx and "ux_field_values_tfs" in idx
    assert "ux_tag_links_ts" not in idx and "ux_tag_links_tts" in idx
    assert fv and "tenant_id, field_id, subject_ref" in fv[0]
    assert tl and "tenant_id, tag_id, subject_ref" in tl[0]


# ── OFF byte-identical: duplicate natural keys still rejected ─────────────────────
def test_switch_off_rejects_duplicates(tmp_path, monkeypatch):
    md, tn = _fresh_env(tmp_path, monkeypatch, switch=False)
    tn.set_tenant("ZZZ")   # inert while OFF
    con = md.connect()
    try:
        con.execute("INSERT INTO field_values (field_id, subject_ref, value, tenant_id) "
                    "VALUES (1, 'doc:1', 'a', 'default')")
        con.execute("INSERT INTO tag_links (tag_id, subject_ref, tenant_id) "
                    "VALUES (1, 'doc:1', 'default')")
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO field_values (field_id, subject_ref, value, tenant_id) "
                        "VALUES (1, 'doc:1', 'b', 'default')")
            con.commit()
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO tag_links (tag_id, subject_ref, tenant_id) "
                        "VALUES (1, 'doc:1', 'default')")
            con.commit()
    finally:
        con.close()
    tn.reset_tenant()


# ── Existing-DB preservation: OLD natural-UNIQUE populated metadata.db, migrated ──
def test_migration_preserves_rows_on_existing_db(tmp_path, monkeypatch):
    md, tn = _fresh_env(tmp_path, monkeypatch, switch=False)

    con = sqlite3.connect(md.DB)
    con.executescript("""
        CREATE TABLE custom_fields (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'text', options TEXT, created_at TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default');
        CREATE TABLE field_values (id INTEGER PRIMARY KEY, field_id INTEGER NOT NULL,
            subject_ref TEXT NOT NULL, value TEXT, tenant_id TEXT NOT NULL DEFAULT 'default');
        CREATE UNIQUE INDEX ux_field_values_fs ON field_values(field_id, subject_ref);
        CREATE INDEX ix_field_values_subject ON field_values(subject_ref);
        CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL, parent_id INTEGER,
            color TEXT, created_at TEXT, tenant_id TEXT NOT NULL DEFAULT 'default');
        CREATE TABLE tag_links (id INTEGER PRIMARY KEY, tag_id INTEGER NOT NULL,
            subject_ref TEXT NOT NULL, tenant_id TEXT NOT NULL DEFAULT 'default');
        CREATE UNIQUE INDEX ux_tag_links_ts ON tag_links(tag_id, subject_ref);
        CREATE INDEX ix_tag_links_subject ON tag_links(subject_ref);
        CREATE INDEX ix_tag_links_tag ON tag_links(tag_id);
    """)
    con.execute("INSERT INTO field_values (field_id, subject_ref, value) VALUES (1,'doc:1','v')")
    con.execute("INSERT INTO tags (id, name) VALUES (1,'Tax')")
    con.execute("INSERT INTO tag_links (tag_id, subject_ref) VALUES (1,'doc:1')")
    con.commit()
    fv_before = con.execute("SELECT COUNT(*) FROM field_values").fetchone()[0]
    tl_before = con.execute("SELECT COUNT(*) FROM tag_links").fetchone()[0]
    con.close()

    md._SCHEMA_READY.clear()
    con = md.connect()
    try:
        fv_after = con.execute("SELECT COUNT(*) FROM field_values").fetchone()[0]
        tl_after = con.execute("SELECT COUNT(*) FROM tag_links").fetchone()[0]
        fv_t = {r["tenant_id"] for r in con.execute("SELECT tenant_id FROM field_values")}
        idx = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        con.close()
    assert fv_before == fv_after == 1
    assert tl_before == tl_after == 1
    assert fv_t == {"default"}
    assert "ux_field_values_fs" not in idx and "ux_field_values_tfs" in idx
    assert "ux_tag_links_ts" not in idx and "ux_tag_links_tts" in idx

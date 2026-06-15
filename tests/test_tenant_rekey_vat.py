"""Multi-tenant PK-REKEY — tenant-qualified PRIMARY KEYs / UNIQUE constraints for the
LEGAL-CRITICAL vat_claims.db (5 natural-key tables, ALL audited).

Follows the already-approved templates: benchmark.db (un-audited) and portal.db /
CRM customers.db / suppliers.db (AUDITED). The vat_claims.db claim tables already carry
a tenant_id column (P1) and tenant-scoped reads / stamped writes (P2), but their PKs /
UNIQUE constraints did NOT include tenant_id, so the upserts (ON CONFLICT) and the
lock/dedup UNIQUEs resolved on the NATURAL key only. Under the `multitenant` switch ON,
two tenants writing the same natural key would COLLIDE and overwrite / block each other's
legally binding claim rows — cross-tenant data loss. This slice rebuilds each of the 5
tables with tenant_id FIRST in the PK / UNIQUE clause (LAST in the column list).

The 5 rebuilt tables and their old->new key (and the preserved natural audit rowkey
== audit._cols_pk pks[0]):
    vat_applications       PK (entity, refund_country, ref_period)
                            -> (tenant_id, entity, refund_country, ref_period)   rowkey entity
    vat_invoice_waivers    PK (entity, refund_country, ref_period, supplier)
                            -> (tenant_id, ...)                                  rowkey entity
    note_invoice_overrides PK (supplier, country, note)
                            -> (tenant_id, supplier, country, note)              rowkey supplier
    vat_claimed_invoices   UNIQUE (entity, refund_country, supplier, invoice_ref)
                            -> UNIQUE (tenant_id, ...)                           rowkey rowid (rowid table)
    invoice_documents      id PK + UNIQUE (entity, supplier, invoice_ref, sha256)
                            -> id PK + UNIQUE (tenant_id, ...)                   rowkey id (preserved)

What this file proves:
  * NO CROSS-TENANT CLOBBER on all 5 tables (real writers where one exists):
    vat_applications (the set_status upsert), vat_invoice_waivers (add_waiver),
    note_invoice_overrides (set_note_override), vat_claimed_invoices (the lock insert),
    invoice_documents (attach_document) — A and B can hold the SAME natural key and each
    reads its OWN.
  * invoice_documents.id PRESERVED across the rebuild on an existing DB (ids referenced
    by /doc/<id>) — not reassigned.
  * AUDIT SURVIVES the rebuild on all 5 tables: aud_<t>_i/u/d exist and a write logs an
    audit_log row keyed by the PRESERVED natural rowkey (entity / supplier / id / rowid),
    NOT tenant_id/'default'.
  * EXISTING-DB PRESERVATION: an OLD-schema (natural PK/UNIQUE + P1 tenant_id) populated
    vat_claims.db rebuilt via connect() keeps per-table row counts, tenant-qualifies every
    key, and preserves invoice_documents ids.
  * CLAIM MATH UNCHANGED: this rebuild touches ONLY the PK/UNIQUE shape + ON CONFLICT
    targets + audit ordering — no figure, status, lock, fee or gate. The full existing vat
    suite passing UNCHANGED (OFF byte-identical) is the standing math proof; this file adds
    one explicit OFF in-place-correction assertion.
"""
import importlib
import sqlite3

import pytest


COUNTRY = "France"
PERIOD = "2099-Q3"


def _fresh_env(tmp_path, monkeypatch, switch):
    """Wire a fresh vat_claims.db / suppliers.db / customers.db / security.db and set the
    multitenant switch. CRITICALLY clears vat_refund._SCHEMA_READY AND
    audit._AUDIT_INSTALLED so connect() runs schema + migrations + install_audit exactly
    like a fresh process — the audit triggers are then (re)created against the rekeyed
    tables. Returns (vat_refund, supplier_master, tenancy, audit)."""
    import auth
    import tenancy
    import audit
    import supplier_master
    import customer_master
    import vat_refund

    importlib.reload(supplier_master)
    importlib.reload(customer_master)
    importlib.reload(vat_refund)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "suppliers.db"))
    supplier_master._SCHEMA_READY.clear()
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "customers.db"))
    customer_master._SCHEMA_READY.clear()
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "vat_claims.db"))
    # keep the legacy migration source pointed at a non-existent DB so a fresh
    # vat_claims.db does NOT seed from the real demo product DB.
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "fh_legacy.db"))
    monkeypatch.setattr(vat_refund, "DOCDIR", str(tmp_path / "documents"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    audit._AUDIT_INSTALLED.clear()   # mirror a fresh process: install_audit must RUN

    # the claim reads transactions through the product accessor; build an empty
    # tenant-stamped fuel_history.db and point the accessor at it (this rekey slice is
    # about the claim-record SCHEMA, not the analytics math — an empty store is fine).
    import dataproduct
    fh = str(tmp_path / "fuel_history.db")
    fcon = sqlite3.connect(fh)
    fcon.execute("""CREATE TABLE transactions (
        period TEXT, entity TEXT, supplier TEXT, country TEXT, vehicle TEXT,
        date TEXT, time TEXT, station TEXT, product TEXT, product_group TEXT,
        qty REAL, currency TEXT, net_local REAL, vat_local REAL, gross_local REAL,
        net_eur REAL, vat_eur REAL, net_eur_eff REAL, note TEXT,
        tenant_id TEXT NOT NULL DEFAULT 'default')""")
    fcon.commit(); fcon.close()
    monkeypatch.setattr(vat_refund, "analytics_connect",
                        lambda: dataproduct.connect("fuel_history", path=fh))
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)

    auth.set_setting("multitenant", "1" if switch else "0")
    assert tenancy.multitenant_enabled() is switch
    return vat_refund, supplier_master, tenancy, audit


@pytest.fixture()
def on(tmp_path, monkeypatch):
    """multitenant switch ON."""
    vr, sm, tn, ad = _fresh_env(tmp_path, monkeypatch, switch=True)
    try:
        yield vr, sm, tn, ad
    finally:
        tn.reset_tenant()


# ── helpers ──────────────────────────────────────────────────────────────────────

def _lock(vr, tn, tenant, ent, ctry, sup, ref, period):
    """Drive the SAME insert the lock-acquisition branch of set_status uses (stamping
    the bound tenant). Proves the tenant-qualified UNIQUE lets A and B both lock the
    same (entity, refund_country, supplier, invoice_ref). Does NOT touch the claim
    math/figures — it is exactly the lock SQL."""
    tn.set_tenant(tenant)
    con = vr.connect()
    try:
        con.execute("""INSERT INTO vat_claimed_invoices
                       (entity, refund_country, supplier, invoice_ref, ref_period, tenant_id)
                       VALUES (?,?,?,?,?,?)""",
                    (ent, ctry, sup, ref, period, tn.queue_tenant()))
        con.commit()
    finally:
        con.close()


def _attach(vr, tn, tenant, ent, sup, ref, body):
    tn.set_tenant(tenant)
    con = vr.connect()
    try:
        ok, msg = vr.attach_document(con, ent, sup, ref, file_bytes=body,
                                     filename=f"{ref}.pdf", country=COUNTRY, period=PERIOD)
        assert ok, msg
    finally:
        con.close()


# ── NO cross-tenant clobber (driving the REAL writers) ───────────────────────────

def test_vat_applications_no_cross_tenant_clobber(on):
    """The set_status upsert (INSERT … ON CONFLICT(tenant_id, entity, refund_country,
    ref_period)) on the SAME (entity, refund_country, ref_period) as A and B — both
    rows persist with own status; the upsert still corrects in place WITHIN a tenant."""
    vr, sm, tn, _ = on
    tn.set_tenant("A")
    con = vr.connect()
    try:
        ok, _ = vr.set_status(con, "ENT", COUNTRY, PERIOD, "draft", gate_activation=False)
        assert ok
    finally:
        con.close()
    tn.set_tenant("B")
    con = vr.connect()
    try:
        ok, _ = vr.set_status(con, "ENT", COUNTRY, PERIOD, "draft", gate_activation=False)
        assert ok
    finally:
        con.close()
    tn.reset_tenant()

    tn.set_owner_scope()
    con = vr.connect()
    try:
        rows = sorted((r["tenant_id"], r["status"]) for r in con.execute(
            "SELECT tenant_id, status FROM vat_applications "
            "WHERE entity='ENT' AND refund_country=? AND ref_period=?", (COUNTRY, PERIOD)))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", "draft"), ("B", "draft")]
    # both rows exist (no clobber) — distinct tenants, same natural key
    assert len(rows) == 2

    # the upsert corrects in place WITHIN a tenant (not a new row)
    tn.set_tenant("A")
    con = vr.connect()
    try:
        vr.set_status(con, "ENT", COUNTRY, PERIOD, "submitted", gate_activation=False)
    finally:
        con.close()
    tn.set_owner_scope()
    con = vr.connect()
    try:
        a_rows = con.execute(
            "SELECT status FROM vat_applications WHERE entity='ENT' AND tenant_id='A'").fetchall()
        b_rows = con.execute(
            "SELECT status FROM vat_applications WHERE entity='ENT' AND tenant_id='B'").fetchall()
    finally:
        con.close()
    tn.reset_tenant()
    assert [r["status"] for r in a_rows] == ["submitted"]   # in place
    assert [r["status"] for r in b_rows] == ["draft"]       # untouched


def test_vat_invoice_waivers_no_cross_tenant_clobber(on):
    """add_waiver (INSERT … ON CONFLICT(tenant_id, entity, refund_country, ref_period,
    supplier)) on the SAME natural key as A and B — both persist with own reason."""
    vr, sm, tn, _ = on
    # add_waiver only allows a genuinely-uninvoiced supplier (no registered invoices);
    # SUP has none in either tenant, so it is waivable.
    tn.set_tenant("A")
    con = vr.connect()
    try:
        ok, _ = vr.add_waiver(con, "ENT", COUNTRY, PERIOD, "SUP", reason="A reason")
        assert ok
    finally:
        con.close()
    tn.set_tenant("B")
    con = vr.connect()
    try:
        ok, _ = vr.add_waiver(con, "ENT", COUNTRY, PERIOD, "SUP", reason="B reason")
        assert ok
    finally:
        con.close()
    tn.reset_tenant()

    tn.set_owner_scope()
    con = vr.connect()
    try:
        rows = sorted((r["tenant_id"], r["reason"]) for r in con.execute(
            "SELECT tenant_id, reason FROM vat_invoice_waivers "
            "WHERE entity='ENT' AND supplier='SUP'"))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", "A reason"), ("B", "B reason")]


def test_note_invoice_overrides_no_cross_tenant_clobber(on, monkeypatch):
    """set_note_override (INSERT … ON CONFLICT(tenant_id, supplier, country, note)) on
    the SAME (supplier, country, note) as A and B — both persist with own invoice_ref."""
    vr, sm, tn, _ = on
    # set_note_override validates the target ref is a registered, non-synthetic invoice
    # for (supplier, country); stub the registry per tenant so each maps its own ref.
    refs = {"A": "INV-A", "B": "INV-B"}
    monkeypatch.setattr(vr, "_registered_refs",
                        lambda supplier, country, scon=None: {refs[tn.queue_tenant()]})

    tn.set_tenant("A")
    vr.set_note_override("SUP", COUNTRY, "fuel-card-7", "INV-A", actor="amy")
    tn.set_tenant("B")
    vr.set_note_override("SUP", COUNTRY, "fuel-card-7", "INV-B", actor="bob")
    tn.reset_tenant()

    tn.set_owner_scope()
    con = vr.connect()
    try:
        rows = sorted((r["tenant_id"], r["invoice_ref"]) for r in con.execute(
            "SELECT tenant_id, invoice_ref FROM note_invoice_overrides "
            "WHERE supplier='SUP' AND country=? AND note='fuel-card-7'", (COUNTRY,)))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", "INV-A"), ("B", "INV-B")]


def test_vat_claimed_invoices_no_cross_tenant_clobber(on):
    """The lock insert on the SAME (entity, refund_country, supplier, invoice_ref) as A
    and B — both rows persist (tenant-qualified UNIQUE), each reads its OWN lock_state."""
    vr, sm, tn, _ = on
    _lock(vr, tn, "A", "ENT", COUNTRY, "SUP", "INV", PERIOD)
    _lock(vr, tn, "B", "ENT", COUNTRY, "SUP", "INV", "2099-Q4")

    tn.set_owner_scope()
    con = vr.connect()
    try:
        rows = sorted((r["tenant_id"], r["ref_period"]) for r in con.execute(
            "SELECT tenant_id, ref_period FROM vat_claimed_invoices "
            "WHERE entity='ENT' AND refund_country=? AND supplier='SUP' AND invoice_ref='INV'",
            (COUNTRY,)))
    finally:
        con.close()
    tn.reset_tenant()
    assert rows == [("A", PERIOD), ("B", "2099-Q4")]

    # each tenant reads its OWN lock via the scoped lock_state
    tn.set_tenant("A")
    con = vr.connect()
    try:
        assert vr.lock_state(con, "ENT", COUNTRY, "SUP", "INV") == PERIOD
    finally:
        con.close()
    tn.set_tenant("B")
    con = vr.connect()
    try:
        assert vr.lock_state(con, "ENT", COUNTRY, "SUP", "INV") == "2099-Q4"
    finally:
        con.close()
    tn.reset_tenant()


def test_invoice_documents_no_cross_tenant_clobber(on):
    """attach_document registers the SAME (entity, supplier, invoice_ref, sha256) doc
    for A and B — both stored (tenant-qualified UNIQUE), with DISTINCT preserved ids."""
    vr, sm, tn, _ = on
    body = b"identical-pdf-bytes"
    _attach(vr, tn, "A", "ENT", "SUP", "INV", body)
    _attach(vr, tn, "B", "ENT", "SUP", "INV", body)

    tn.set_owner_scope()
    con = vr.connect()
    try:
        rows = sorted((r["tenant_id"], r["id"]) for r in con.execute(
            "SELECT tenant_id, id FROM invoice_documents "
            "WHERE entity='ENT' AND supplier='SUP' AND invoice_ref='INV'"))
    finally:
        con.close()
    tn.reset_tenant()
    tenants = [t for t, _id in rows]
    ids = [_id for _t, _id in rows]
    assert tenants == ["A", "B"]
    assert len(set(ids)) == 2            # distinct ids (surrogate PK)


# ── AUDIT survives the rebuild on all 5 tables ───────────────────────────────────

AUDITED = [
    "vat_applications", "vat_claimed_invoices", "invoice_documents",
    "vat_invoice_waivers", "note_invoice_overrides",
]
# natural audit rowkey (pks[0]) preserved per table (rowid tables have no declared pk)
ROWKEY = {
    "vat_applications": "entity",
    "vat_invoice_waivers": "entity",
    "note_invoice_overrides": "supplier",
    "invoice_documents": "id",
    "vat_claimed_invoices": "rowid",
}


def test_audit_triggers_exist_on_all_five_tables(on):
    """After a fresh connect (caches cleared in the fixture), aud_<t>_i/u/d must exist on
    every RENAMED table — DROP TABLE dropped them, install_audit (now AFTER db_migrate)
    recreated them on the final post-migration schema."""
    vr, sm, tn, _ = on
    tn.set_tenant("A")
    con = vr.connect()
    try:
        trigs = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
    finally:
        con.close()
    tn.reset_tenant()
    for t in AUDITED:
        for sfx in ("i", "u", "d"):
            assert f"aud_{t}_{sfx}" in trigs, (t, sfx)


def test_audit_rowkey_is_preserved_natural_key_not_tenant(on, monkeypatch):
    """A write to each rekeyed table logs an audit_log row keyed by the PRESERVED natural
    rowkey (vat_applications/vat_invoice_waivers->entity, note_invoice_overrides->supplier,
    invoice_documents->id, vat_claimed_invoices->rowid) — NOT tenant_id/'default'."""
    vr, sm, tn, ad = on
    monkeypatch.setattr(vr, "_registered_refs",
                        lambda supplier, country, scon=None: {"INV-A"})
    tn.set_tenant("A")
    # vat_applications (set_status upsert)
    con = vr.connect()
    try:
        vr.set_status(con, "AUDENT", COUNTRY, PERIOD, "draft", gate_activation=False)
        # vat_invoice_waivers (add_waiver) — SUP has no registered invoices
        vr.add_waiver(con, "AUDENT", COUNTRY, PERIOD, "AUDSUP", reason="r")
    finally:
        con.close()
    # note_invoice_overrides (set_note_override)
    vr.set_note_override("AUDSUP", COUNTRY, "note-1", "INV-A", actor="amy")
    # vat_claimed_invoices (lock insert) + invoice_documents (attach)
    _lock(vr, tn, "A", "AUDENT", COUNTRY, "AUDSUP", "INV-A", PERIOD)
    _attach(vr, tn, "A", "AUDENT", "AUDSUP", "INV-A", b"audit-pdf")
    tn.reset_tenant()

    con = vr.connect()
    try:
        for t in AUDITED:
            keys = {r["rowkey"] for r in con.execute(
                "SELECT rowkey FROM audit_log WHERE tbl=? AND action='INSERT'", (t,))}
            assert keys, t
            assert "A" not in keys and "default" not in keys, (t, keys)
            _cols, pk0 = ad._cols_pk(con, t)
            assert pk0 == ROWKEY[t], (t, pk0)
        # spot-check exact preserved key values
        ent_keys = {r["rowkey"] for r in con.execute(
            "SELECT rowkey FROM audit_log WHERE tbl='vat_applications' AND action='INSERT'")}
        sup_keys = {r["rowkey"] for r in con.execute(
            "SELECT rowkey FROM audit_log WHERE tbl='note_invoice_overrides' AND action='INSERT'")}
    finally:
        con.close()
    assert "AUDENT" in ent_keys     # vat_applications rowkey == entity
    assert "AUDSUP" in sup_keys     # note_invoice_overrides rowkey == supplier


# ── The rebuilt schema: tenant_id FIRST in the key, LAST in column order ──────────

def test_rebuilt_schema_has_tenant_qualified_keys(on):
    vr, sm, tn, ad = on
    tn.set_tenant("A")
    con = vr.connect()
    try:
        for t in AUDITED:
            sql = con.execute(
                "SELECT sql FROM sqlite_master WHERE name=? AND type='table'", (t,)
            ).fetchone()[0]
            info = con.execute(f"PRAGMA table_info({t})").fetchall()
            # tenant_id is the LAST column in DEFINITION order -> pks[0] stays natural.
            assert info[-1]["name"] == "tenant_id", t
            if t in ("vat_claimed_invoices", "invoice_documents"):
                assert "UNIQUE (tenant_id" in sql, (t, sql)
            else:
                assert "PRIMARY KEY (tenant_id" in sql, (t, sql)
        # invoice_documents keeps its surrogate id PRIMARY KEY
        idoc = con.execute(
            "SELECT sql FROM sqlite_master WHERE name='invoice_documents' AND type='table'"
        ).fetchone()[0]
        assert "id INTEGER PRIMARY KEY" in idoc
    finally:
        con.close()
    tn.reset_tenant()


# ── Existing-DB preservation: OLD-schema populated vat_claims.db, rebuilt ─────────

def _premark_pre_rekey(con, n):
    """Pre-mark the pre-rekey vat_refund migration statements (`n` of them: the fee/
    settlement/status ALTERs + the P1 tenant_column_ddls, i.e. every statement BEFORE the
    first `__rekey` rebuild) as already applied, so db_migrate.apply() runs ONLY the rekey
    rebuild statements against an already-P1 schema — mirroring an existing vat_claims.db
    in the field. (Pre-running the ALTERs would fail on our hand-built P1 schema with a
    duplicate-column error.)"""
    con.execute("""CREATE TABLE IF NOT EXISTS _ffs_migrations (
        module TEXT, idx INTEGER, statement TEXT,
        applied_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (module, idx))""")
    for i in range(n):
        con.execute("INSERT OR IGNORE INTO _ffs_migrations(module,idx,statement)"
                    " VALUES ('vat_refund',?,'pre')", (i,))


def _count_pre_rekey_statements(tenancy):
    """Derive (not hardcode) the number of migration statements BEFORE the first
    `__rekey` rebuild, by reconstructing the SAME prefix connect() passes to
    db_migrate.apply (the fee/settlement/status ALTERs + the P1 tenant_column_ddls)."""
    pre = [
        "ALTER TABLE invoice_documents ADD COLUMN backend TEXT DEFAULT 'local'",
        "ALTER TABLE invoice_documents ADD COLUMN web_url TEXT",
        "ALTER TABLE vat_applications ADD COLUMN fee_eur REAL",
        "ALTER TABLE vat_applications ADD COLUMN fee_pct REAL",
        "ALTER TABLE vat_applications ADD COLUMN fee_min REAL",
        "ALTER TABLE vat_applications ADD COLUMN fee_billed_date TEXT",
        "ALTER TABLE vat_applications ADD COLUMN payout_to TEXT",
        "ALTER TABLE vat_applications ADD COLUMN fee_invoice_no TEXT",
        "ALTER TABLE vat_applications ADD COLUMN fee_invoice_date TEXT",
        "ALTER TABLE vat_applications ADD COLUMN status_code TEXT",
        "ALTER TABLE vat_applications ADD COLUMN decision_date TEXT",
        "ALTER TABLE vat_applications ADD COLUMN status_note TEXT",
        "ALTER TABLE vat_applications ADD COLUMN action_deadline TEXT",
    ]
    pre += list(tenancy.tenant_column_ddls([
        "vat_applications", "vat_claimed_invoices", "invoice_documents",
        "vat_invoice_waivers", "note_invoice_overrides",
    ]))
    return len(pre)


def test_rebuild_preserves_rows_and_doc_ids_on_existing_db(tmp_path, monkeypatch):
    """Build an OLD-schema (natural PK/UNIQUE + P1 tenant_id columns) populated
    vat_claims.db across the 5 tables, run the rebuild via connect(), assert per-table
    row counts before==after, every key tenant-qualified, and invoice_documents ids
    UNCHANGED."""
    vr, sm, tn, _ = _fresh_env(tmp_path, monkeypatch, switch=False)

    con = sqlite3.connect(vr.DB)
    # OLD natural-key schema + the P1 tenant_id column on each (no rekey yet).
    con.executescript("""
        CREATE TABLE vat_applications (
            entity TEXT, refund_country TEXT, ref_period TEXT,
            vat_eur REAL, vat_local REAL, currency TEXT,
            status TEXT DEFAULT 'draft', updated TEXT DEFAULT CURRENT_TIMESTAMP,
            submitted_date TEXT, approved_date TEXT, paid_date TEXT, paid_amount REAL,
            fee_eur REAL, fee_pct REAL, fee_min REAL, fee_billed_date TEXT,
            payout_to TEXT, fee_invoice_no TEXT, fee_invoice_date TEXT,
            status_code TEXT, decision_date TEXT, status_note TEXT, action_deadline TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            PRIMARY KEY (entity, refund_country, ref_period));
        CREATE TABLE vat_claimed_invoices (
            entity TEXT, refund_country TEXT, supplier TEXT, invoice_ref TEXT,
            ref_period TEXT, locked_at TEXT DEFAULT CURRENT_TIMESTAMP,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            UNIQUE (entity, refund_country, supplier, invoice_ref));
        CREATE TABLE invoice_documents (
            id INTEGER PRIMARY KEY,
            entity TEXT, supplier TEXT, invoice_ref TEXT,
            filename TEXT, stored_path TEXT, sha256 TEXT, size INTEGER,
            kind TEXT DEFAULT 'original_pdf', uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            backend TEXT DEFAULT 'local', web_url TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            UNIQUE (entity, supplier, invoice_ref, sha256));
        CREATE TABLE vat_invoice_waivers (
            entity TEXT, refund_country TEXT, ref_period TEXT, supplier TEXT,
            reason TEXT, waived_by TEXT, waived_at TEXT DEFAULT CURRENT_TIMESTAMP,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            PRIMARY KEY (entity, refund_country, ref_period, supplier));
        CREATE TABLE note_invoice_overrides (
            supplier TEXT, country TEXT, note TEXT, invoice_ref TEXT,
            changed_by TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            PRIMARY KEY (supplier, country, note));
    """)
    con.execute("INSERT INTO vat_applications (entity, refund_country, ref_period, vat_eur)"
                " VALUES ('E1','France','2099-Q3',123.45)")
    con.execute("INSERT INTO vat_applications (entity, refund_country, ref_period, vat_eur)"
                " VALUES ('E2','Sweden','2099-Q3',9.0)")
    con.execute("INSERT INTO vat_claimed_invoices (entity, refund_country, supplier, invoice_ref)"
                " VALUES ('E1','France','SUP','INV1')")
    # explicit ids 7 and 42 — must be preserved verbatim across the rebuild (/doc/<id>)
    con.execute("INSERT INTO invoice_documents (id, entity, supplier, invoice_ref, sha256)"
                " VALUES (7,'E1','SUP','INV1','abc')")
    con.execute("INSERT INTO invoice_documents (id, entity, supplier, invoice_ref, sha256)"
                " VALUES (42,'E2','SUP','INV2','def')")
    con.execute("INSERT INTO vat_invoice_waivers (entity, refund_country, ref_period, supplier)"
                " VALUES ('E1','France','2099-Q3','SUP')")
    con.execute("INSERT INTO note_invoice_overrides (supplier, country, note, invoice_ref)"
                " VALUES ('SUP','France','n','INV1')")
    _premark_pre_rekey(con, _count_pre_rekey_statements(tn))
    con.commit()
    before = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in AUDITED}
    ids_before = sorted(r[0] for r in con.execute("SELECT id FROM invoice_documents"))
    con.close()

    # connect() runs db_migrate.apply -> ONLY the rekey rebuild migrations.
    vr._SCHEMA_READY.clear()
    con = vr.connect()
    try:
        after = {}
        for t in AUDITED:
            after[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            sql = con.execute(
                "SELECT sql FROM sqlite_master WHERE name=? AND type='table'", (t,)
            ).fetchone()[0]
            if t in ("vat_claimed_invoices", "invoice_documents"):
                assert "UNIQUE (tenant_id" in sql, (t, sql)
            else:
                assert "PRIMARY KEY (tenant_id" in sql, (t, sql)
        ids_after = sorted(r[0] for r in con.execute("SELECT id FROM invoice_documents"))
        amt = con.execute(
            "SELECT vat_eur FROM vat_applications WHERE entity='E1'").fetchone()[0]
    finally:
        con.close()
    assert before == after
    assert before == {"vat_applications": 2, "vat_claimed_invoices": 1,
                      "invoice_documents": 2, "vat_invoice_waivers": 1,
                      "note_invoice_overrides": 1}
    assert ids_before == ids_after == [7, 42]   # ids preserved verbatim (not reassigned)
    assert amt == 123.45                          # figure carried over untouched


# ── OFF byte-identical: single 'default' tenant; same-key upsert corrects in place ─

def test_switch_off_same_key_corrects_in_place(tmp_path, monkeypatch):
    """With the switch OFF, every write is the single 'default' tenant, so re-running
    set_status on the same (entity, refund_country, ref_period) corrects in place (one
    row) exactly as today — even with a stray thread tenant set."""
    vr, sm, tn, _ = _fresh_env(tmp_path, monkeypatch, switch=False)
    tn.set_tenant("ZZZ")   # inert while OFF
    con = vr.connect()
    try:
        vr.set_status(con, "ENT", COUNTRY, PERIOD, "draft", gate_activation=False)
        vr.set_status(con, "ENT", COUNTRY, PERIOD, "submitted", gate_activation=False)
        rows = con.execute(
            "SELECT tenant_id, status FROM vat_applications "
            "WHERE entity='ENT' AND refund_country=? AND ref_period=?",
            (COUNTRY, PERIOD)).fetchall()
    finally:
        con.close()
    tn.reset_tenant()
    assert [(r["tenant_id"], r["status"]) for r in rows] == [("default", "submitted")]

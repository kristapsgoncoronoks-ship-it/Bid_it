"""Multi-tenancy P2 — CROSS-TENANT ISOLATION harness for suppliers.db.

Completes the suppliers.db enforcement slice across BOTH modules that touch it,
behind the `multitenant` switch:

  * supplier_master.py — the supplier master, VAT registrations, discount rules
    and the supplier-invoice registry (the claim-build / engine read surface
    `get_issuer` / `get_invoices`).
  * invoice_control.py — receipt control (`control_summary`) and the statement
    reconcile (`reconcile_statements`), which read suppliers.db via the
    supplier-master connection, plus the `register_statement` worker write path.

The two halves of enforcement asserted here:

  * WRITES stamp the bound tenant (`tenancy.queue_tenant()` — the soft primitive
    these engine/worker/seed writes use), so a row created "as tenant A" carries
    tenant_id='A'. register_statement runs on the worker under the bound tenant.
  * READS filter by `tenancy.scope_clause()`, so as tenant A `get_issuer` /
    `get_invoices` / `vat_registrations` / `discount_rules` and the receipt-control
    / reconcile reads see ONLY A's suppliers/invoices — B's data is ABSENT (the
    GDPR/no-bleed proof). The platform OWNER sees BOTH (the audited cross-tenant
    analytics exception).

A COMPLETE-isolation guard: scoping only one of the two modules' suppliers.db
reads would leak via the unscoped module, so both are exercised here.

DISTINCT supplier CODES per tenant (BPA for A, BPB for B): the supplier PK is
`code` and does NOT include tenant_id, so an identical code across tenants would
collide on the shared row (a future re-keying slice, NOT a read-leak — reads are
scoped). The CRM/pricing harnesses use distinct keys for the same reason.

CARDINAL invariant: with the switch OFF (default) writes stamp 'default' and reads
are unscoped — byte-identical to today. The existing supplier / register /
invoice_control / statement-resync suites are the standing OFF proof; this file
adds one explicit OFF assertion alongside the ON isolation proofs.
"""
import importlib
import sqlite3

import pytest


TEST_PERIOD = "2099-09"


@pytest.fixture()
def sup(tmp_path, monkeypatch):
    """Fresh suppliers.db + vat_claims.db (the vault) + a tenant-stamped
    fuel_history.db (transactions) + security.db, with the `multitenant` switch ON.

    Mirrors the invoice_control `tinydb` harness (self-contained period in throwaway
    DBs) plus the CRM/pricing switch wiring. Yields
    (supplier_master, invoice_control, vat_refund, tenancy, fh_path).
    """
    import auth
    import dataproduct
    import tenancy
    import supplier_master
    import vat_refund
    import invoice_control as IC

    importlib.reload(supplier_master)
    importlib.reload(vat_refund)
    importlib.reload(IC)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())

    # engine-owned product DB (transactions), read READ-ONLY via the product boundary.
    fh = str(tmp_path / "fuel_history.db")
    con = sqlite3.connect(fh)
    con.execute("""CREATE TABLE transactions (
        period TEXT, supplier TEXT, country TEXT, date TEXT, qty REAL)""")
    con.commit(); con.close()

    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "suppliers.db"))
    supplier_master._SCHEMA_READY.clear()
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "vat_claims.db"))
    # keep the legacy migration source pointed at a non-existent DB so a fresh
    # vat_claims.db does NOT seed invoice_documents from the real demo product DB.
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "fh_legacy.db"))
    vat_refund._SCHEMA_READY.clear()
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)
    monkeypatch.setattr(IC, "FUEL_HISTORY_DB", fh)
    # build the vault schema once so invoice_documents exists for reconcile/control.
    vat_refund.connect().close()

    # Arm the master switch (stored in app_settings/security.db like every module
    # switch). This is what makes scope_clause()/queue_tenant() engage.
    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True

    try:
        yield supplier_master, IC, vat_refund, tenancy, fh
    finally:
        tenancy.reset_tenant()


def _seed_supplier(sm, tenancy, tenant, code, cadence="monthly"):
    """Insert a `suppliers` master row stamped with the bound tenant. No single-
    supplier public write path exists (seed() is bulk); the worker-context tests
    likewise insert directly. queue_tenant() resolves to the bound tenant ON."""
    tenancy.set_tenant(tenant)
    con = sm.connect()
    try:
        con.execute(
            "INSERT INTO suppliers (code, legal_name, invoice_cadence, tenant_id) "
            "VALUES (?,?,?,?)",
            (code, f"{code} Legal Name", cadence, tenancy.queue_tenant()))
        con.commit()
    finally:
        con.close()


def _seed_txn(fh, supplier, country="France", date="2099-09-10", qty=100.0):
    con = sqlite3.connect(fh)
    con.execute("INSERT INTO transactions (period, supplier, country, date, qty) "
                "VALUES (?,?,?,?,?)", (TEST_PERIOD, supplier, country, date, qty))
    con.commit(); con.close()


def _seed_tenant(sm, IC, tenancy, tenant, code):
    """Build A FULL suppliers.db footprint for one tenant via the REAL write paths,
    with `tenant` bound (bind BEFORE connect — connect may re-seed/migrate under ON).
    Distinct `code` per tenant avoids the supplier-PK collision."""
    _seed_supplier(sm, tenancy, tenant, code, cadence="monthly")
    tenancy.set_tenant(tenant)
    # VAT registration (set_vat_registration -> supplier_vat_registrations).
    sm.set_vat_registration(code, "France", f"FR-{code}", source="test")
    # discount rule (set_discount_rule -> supplier_discounts).
    sm.set_discount_rule(code, country="France", expected_discount_eur_l=0.10,
                         note=f"{code} rule")
    # statement + statement_invoices + supplier_invoices, via register_statement
    # (the worker write path). VAT > 0 so it auto-syncs into supplier_invoices.
    IC.register_statement(
        code, f"ST-{code}", TEST_PERIOD, "2099-09-30",
        lines=[(f"INV-{code}", "2099-09-16", "France", "EUR", 500.0, 100.0)],
        customer="Nonexistent Co")
    tenancy.reset_tenant()


def _seed_two_tenants(sm, IC, tenancy):
    _seed_tenant(sm, IC, tenancy, "A", "BPA")
    _seed_tenant(sm, IC, tenancy, "B", "BPB")


# ── supplier_master WRITE stamping ───────────────────────────────────────────────

def test_writes_stamp_the_bound_tenant(sup):
    sm, IC, vr, tenancy, fh = sup
    _seed_two_tenants(sm, IC, tenancy)
    # Inspect the raw rows with NO scope (owner) to read the stamped tenant_id.
    tenancy.set_owner_scope()
    con = sm.connect()
    try:
        suppliers = {r["code"]: r["tenant_id"]
                     for r in con.execute("SELECT code, tenant_id FROM suppliers")}
        vatregs = {r["supplier"]: r["tenant_id"] for r in
                   con.execute("SELECT supplier, tenant_id FROM supplier_vat_registrations")}
        discounts = {r["supplier"]: r["tenant_id"] for r in
                     con.execute("SELECT supplier, tenant_id FROM supplier_discounts")}
        invoices = {r["supplier"]: r["tenant_id"] for r in
                    con.execute("SELECT supplier, tenant_id FROM supplier_invoices")}
        statements = {r["supplier"]: r["tenant_id"] for r in
                      con.execute("SELECT supplier, tenant_id FROM supplier_statements")}
        stlines = {r["supplier"]: r["tenant_id"] for r in
                   con.execute("SELECT supplier, tenant_id FROM statement_invoices")}
    finally:
        con.close()
    assert suppliers == {"BPA": "A", "BPB": "B"}
    assert vatregs == {"BPA": "A", "BPB": "B"}
    assert discounts == {"BPA": "A", "BPB": "B"}
    assert invoices == {"BPA": "A", "BPB": "B"}
    assert statements == {"BPA": "A", "BPB": "B"}
    assert stlines == {"BPA": "A", "BPB": "B"}


# ── supplier_master READ isolation (the core GDPR proof) ──────────────────────────

def test_tenant_a_sees_only_its_suppliers(sup):
    sm, IC, vr, tenancy, fh = sup
    _seed_two_tenants(sm, IC, tenancy)
    tenancy.set_tenant("A")
    # get_issuer: A's own supplier resolves to its name; B's is invisible -> the
    # miss path returns the code as the name (supplier row not visible).
    assert sm.get_issuer("BPA", "France")[0] == "BPA Legal Name"
    assert sm.get_issuer("BPB", "France")[0] == "BPB"   # B invisible -> code stub
    # get_invoices: A sees its registered invoice; B's is absent -> the INPUT stub.
    assert sm.get_invoices("BPA", "France") == [("INV-BPA", "2099-09-16")]
    assert sm.get_invoices("BPB", "France") == [("INPUT: France invoice", "")]
    # vat_registrations / discount_rules: only A's rows.
    assert {r["supplier"] for r in sm.vat_registrations()} == {"BPA"}
    assert {r["supplier"] for r in sm.discount_rules()} == {"BPA"}


def test_tenant_b_sees_only_its_suppliers(sup):
    sm, IC, vr, tenancy, fh = sup
    _seed_two_tenants(sm, IC, tenancy)
    tenancy.set_tenant("B")
    assert sm.get_issuer("BPB", "France")[0] == "BPB Legal Name"
    assert sm.get_issuer("BPA", "France")[0] == "BPA"
    assert sm.get_invoices("BPB", "France") == [("INV-BPB", "2099-09-16")]
    assert sm.get_invoices("BPA", "France") == [("INPUT: France invoice", "")]
    assert {r["supplier"] for r in sm.vat_registrations()} == {"BPB"}
    assert {r["supplier"] for r in sm.discount_rules()} == {"BPB"}


# ── invoice_control READ isolation (the second module over suppliers.db) ──────────

def test_control_summary_is_tenant_scoped(sup):
    """control_summary reads suppliers (cadence) + supplier_invoices via the
    supplier-master handle. As tenant A it must see only A's supplier rows."""
    sm, IC, vr, tenancy, fh = sup
    _seed_two_tenants(sm, IC, tenancy)
    # activity for BOTH suppliers (transactions are not suppliers.db; scoped later).
    _seed_txn(fh, "BPA", "France")
    _seed_txn(fh, "BPB", "France")

    tenancy.set_tenant("A")
    rows, _ = IC.control_summary(TEST_PERIOD)
    suppliers = {r["supplier"] for r in rows}
    assert suppliers == {"BPA"}                  # B's supplier row is invisible to A
    tenancy.set_tenant("B")
    rows_b, _ = IC.control_summary(TEST_PERIOD)
    assert {r["supplier"] for r in rows_b} == {"BPB"}


def test_reconcile_statements_is_tenant_scoped(sup):
    """reconcile_statements reads supplier_statements / statement_invoices /
    supplier_invoices via the supplier-master handle. As tenant A only A's
    statements are reconciled."""
    sm, IC, vr, tenancy, fh = sup
    _seed_two_tenants(sm, IC, tenancy)

    tenancy.set_tenant("A")
    out = IC.reconcile_statements(TEST_PERIOD)
    assert {L["supplier"] for L in out} == {"BPA"}
    assert {L["statement"] for L in out} == {"ST-BPA"}
    # A's registered+VAT line reconciles as a PROCESS verdict (not NOT REGISTERED).
    assert all(L["verdict"].startswith("PROCESS") for L in out)
    assert all("NOT REGISTERED" not in L["verdict"] for L in out)

    tenancy.set_tenant("B")
    out_b = IC.reconcile_statements(TEST_PERIOD)
    assert {L["supplier"] for L in out_b} == {"BPB"}
    assert {L["statement"] for L in out_b} == {"ST-BPB"}


# ── OWNER cross-tenant scope (the audited analytics exception) ────────────────────

def test_owner_scope_sees_both_tenants(sup):
    sm, IC, vr, tenancy, fh = sup
    _seed_two_tenants(sm, IC, tenancy)
    tenancy.set_owner_scope()
    assert {r["supplier"] for r in sm.vat_registrations()} == {"BPA", "BPB"}
    assert {r["supplier"] for r in sm.discount_rules()} == {"BPA", "BPB"}
    out = IC.reconcile_statements(TEST_PERIOD)
    assert {L["supplier"] for L in out} == {"BPA", "BPB"}


# ── WRITE under the WORKER: register_statement via _do_register stamps the tenant ─

def test_register_via_worker_stamps_tenant(sup, tmp_path, monkeypatch):
    """register_statement enqueued by tenant A and run by the worker (_do_register
    binds the job's tenant) lands tenant_id='A' on the suppliers.db rows it writes.
    Reuses the worker-context proof pattern: enqueue under A, process_one() rebinds."""
    sm, IC, vr, tenancy, fh = sup
    import auth
    import waiting_room
    importlib.reload(waiting_room)
    monkeypatch.setattr(waiting_room, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(waiting_room, "INBOX", str(tmp_path / "inbox"))
    waiting_room._SCHEMA_READY.clear()
    import import_log
    importlib.reload(import_log)
    monkeypatch.setattr(import_log, "DB", str(tmp_path / "import_log.db"))
    import_log._READY.clear()

    # _do_register does `import invoice_control as IC`, which resolves to the SAME
    # cached module object our fixture pointed at the tmp DBs (FUEL_HISTORY_DB +
    # supplier_master/vat_refund DBs are patched on the module instances), so the
    # worker writes land in the throwaway suppliers.db.
    tenancy.set_tenant("A")
    jid, _ = waiting_room.enqueue_registration(
        {"supplier": "WRK", "statement_ref": "ST-WRK", "period": TEST_PERIOD,
         "statement_date": "2099-09-30",
         "lines": [["INV-WRK", "2099-09-16", "France", "EUR", 500.0, 100.0]],
         "customer": "Nonexistent Co"},
        user="amy")
    tenancy.reset_tenant()                       # worker must re-bind from the row

    assert waiting_room.process_one() == (jid, "done")
    assert tenancy.current_tenant() is None       # context reset after the job

    tenancy.set_owner_scope()
    con = sm.connect()
    try:
        st = con.execute("SELECT tenant_id FROM supplier_statements WHERE supplier='WRK'").fetchone()
        inv = con.execute("SELECT tenant_id FROM supplier_invoices WHERE supplier='WRK'").fetchone()
        line = con.execute("SELECT tenant_id FROM statement_invoices WHERE supplier='WRK'").fetchone()
    finally:
        con.close()
    assert st["tenant_id"] == "A"
    assert inv["tenant_id"] == "A"
    assert line["tenant_id"] == "A"


# ── OFF regression: byte-identical to today ───────────────────────────────────────

def test_switch_off_stamps_default_and_reads_unscoped(tmp_path, monkeypatch):
    """With the switch OFF (the default), writes stamp 'default' (== the column
    DEFAULT) and reads are unscoped — identical to today, even with a tenant bound."""
    import auth
    import dataproduct
    import tenancy
    import supplier_master
    import vat_refund
    import invoice_control as IC
    importlib.reload(supplier_master)
    importlib.reload(vat_refund)
    importlib.reload(IC)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    fh = str(tmp_path / "fuel_history.db")
    con = sqlite3.connect(fh)
    con.execute("""CREATE TABLE transactions (
        period TEXT, supplier TEXT, country TEXT, date TEXT, qty REAL)""")
    con.commit(); con.close()
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "suppliers.db"))
    supplier_master._SCHEMA_READY.clear()
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "vat_claims.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "fh_legacy.db"))
    vat_refund._SCHEMA_READY.clear()
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)
    monkeypatch.setattr(IC, "FUEL_HISTORY_DB", fh)
    vat_refund.connect().close()
    assert tenancy.multitenant_enabled() is False

    # Even with a tenant set on the thread, OFF keeps scope_clause/queue_tenant inert.
    tenancy.set_tenant("A")
    con = supplier_master.connect()
    try:
        con.execute("INSERT INTO suppliers (code, legal_name, invoice_cadence, tenant_id) "
                    "VALUES (?,?,?,?)", ("BP", "BP Legal", "monthly", tenancy.queue_tenant()))
        con.commit()
    finally:
        con.close()
    supplier_master.set_vat_registration("BP", "France", "FR-BP", source="test")
    IC.register_statement("BP", "ST-BP", TEST_PERIOD, "2099-09-30",
                          lines=[("INV-BP", "2099-09-16", "France", "EUR", 500.0, 100.0)],
                          customer="Nonexistent Co")
    tenancy.reset_tenant()

    con = supplier_master.connect()
    try:
        # writes stamped the column DEFAULT 'default' (queue_tenant() OFF -> default).
        assert con.execute("SELECT tenant_id FROM suppliers WHERE code='BP'").fetchone()["tenant_id"] == "default"
        assert con.execute("SELECT tenant_id FROM supplier_invoices WHERE supplier='BP'").fetchone()["tenant_id"] == "default"
        assert con.execute("SELECT tenant_id FROM supplier_statements WHERE supplier='BP'").fetchone()["tenant_id"] == "default"
    finally:
        con.close()

    # Reads are unscoped: visible regardless of any thread tenant.
    tenancy.set_tenant("ZZZ")
    assert supplier_master.get_issuer("BP", "France")[0] == "BP Legal"
    assert supplier_master.get_invoices("BP", "France") == [("INV-BP", "2099-09-16")]
    assert {r["supplier"] for r in supplier_master.vat_registrations()} == {"BP"}
    out = IC.reconcile_statements(TEST_PERIOD)
    assert {L["supplier"] for L in out} == {"BP"}
    tenancy.reset_tenant()

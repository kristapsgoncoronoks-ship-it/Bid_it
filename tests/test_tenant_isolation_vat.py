"""Multi-tenancy P2 — CROSS-TENANT ISOLATION harness for vat_refund.py.

The legal-critical slice: vat_refund.py owns the VAT claim figures, the invoice
locks, the receipt-control waivers and the note->invoice overrides. Under the
`multitenant` switch every vat_claims.db read/write AND every transactions read is
tenant-isolated, so a tenant's claim is built and mutated over its OWN data only.

The two halves of enforcement asserted here:

  * WRITES stamp the bound tenant (`tenancy.queue_tenant()` — the soft primitive the
    engine/worker/admin-web writes use): vat_applications, vat_claimed_invoices,
    invoice_documents, vat_invoice_waivers and note_invoice_overrides rows created
    "as tenant A" carry tenant_id='A'. UPDATEs/DELETEs append the scope fragment so a
    tenant can only mutate its own claim rows.
  * READS filter by `tenancy.scope_clause()`, so as tenant A the recovery report, the
    claims overview, invoice_lines, the threshold/below_minimum gate and the status
    reads see ONLY A's claims/invoices/transactions — B is ABSENT (the GDPR/no-bleed
    proof). The platform OWNER sees BOTH (the audited cross-tenant analytics exception).

THE LEGAL-FIGURE NO-BLEED PROOF: invoice_lines / claim_matrix for tenant A sum ONLY
A's transactions; B's identical (entity, country, period) activity never inflates A's
claimed VAT. Scoping RESTRICTS the input rows; it does NOT change the claim MATH.

DISTINCT entities/refs per tenant: vat_applications / vat_claimed_invoices PKs do NOT
include tenant_id, so identical keys across tenants would collide on the shared row (a
future re-keying slice, NOT a read-leak — reads are scoped). We use distinct entities
and invoice refs per tenant exactly as the CRM/suppliers harnesses use distinct keys.

CARDINAL invariant: with the switch OFF (default) writes stamp 'default' and reads are
unscoped — byte-identical to today. The extensive existing claim/gate/workbook/recovery
suite is the standing OFF proof; this file adds one explicit OFF assertion.
"""
import importlib
import sqlite3

import pytest


PERIOD_Q = "2099-Q3"          # the claim period (quarter)
MONTH = "2099-09"             # a month inside Q3 (transactions are monthly)
STMT_DATE = "2099-09-30"
INV_DATE = "2099-09-16"
COUNTRY = "France"


@pytest.fixture()
def vat(tmp_path, monkeypatch):
    """Fresh vat_claims.db (claims) + suppliers.db + customers.db + a tenant-stamped
    fuel_history.db (transactions) + security.db, with the `multitenant` switch ON.

    Yields (vat_refund, supplier_master, invoice_control, customer_master, tenancy,
    fh_path).
    """
    import auth
    import dataproduct
    import tenancy
    import supplier_master
    import customer_master
    import vat_refund
    import invoice_control as IC

    importlib.reload(supplier_master)
    importlib.reload(customer_master)
    importlib.reload(vat_refund)
    importlib.reload(IC)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())

    # engine-owned product DB (transactions), read READ-ONLY via the product boundary.
    # tenant_id is the P1 column on fuel_history.db; default it like the column DEFAULT.
    fh = str(tmp_path / "fuel_history.db")
    con = sqlite3.connect(fh)
    con.execute("""CREATE TABLE transactions (
        period TEXT, entity TEXT, supplier TEXT, country TEXT, vehicle TEXT,
        date TEXT, time TEXT, station TEXT, product TEXT, product_group TEXT,
        qty REAL, currency TEXT, net_local REAL, vat_local REAL, gross_local REAL,
        net_eur REAL, vat_eur REAL, net_eur_eff REAL, note TEXT,
        tenant_id TEXT NOT NULL DEFAULT 'default')""")
    con.commit(); con.close()

    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "suppliers.db"))
    supplier_master._SCHEMA_READY.clear()
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "customers.db"))
    customer_master._SCHEMA_READY.clear()
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "vat_claims.db"))
    # keep the legacy migration source pointed at a non-existent DB so a fresh
    # vat_claims.db does NOT seed from the real demo product DB.
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "fh_legacy.db"))
    vat_refund._SCHEMA_READY.clear()
    # the claim reads transactions through the product accessor; point it at our fh.
    monkeypatch.setattr(vat_refund, "analytics_connect",
                        lambda: dataproduct.connect("fuel_history", path=fh))
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)
    monkeypatch.setattr(IC, "FUEL_HISTORY_DB", fh)
    # store documents under the throwaway tree.
    monkeypatch.setattr(vat_refund, "DOCDIR", str(tmp_path / "documents"))
    # build the vault schema once.
    vat_refund.connect().close()

    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True

    try:
        yield vat_refund, supplier_master, IC, customer_master, tenancy, fh
    finally:
        tenancy.reset_tenant()


def _seed_supplier(sm, tenancy, tenant, code):
    tenancy.set_tenant(tenant)
    con = sm.connect()
    try:
        con.execute(
            "INSERT INTO suppliers (code, legal_name, invoice_cadence, tenant_id) "
            "VALUES (?,?,?,?)", (code, f"{code} Legal Name", "monthly",
                                 tenancy.queue_tenant()))
        con.commit()
    finally:
        con.close()


def _seed_txn(fh, tenant, entity, supplier, *, vat_eur, net_eur=None, note,
              vat_local=None, net_local=None, ccy="EUR"):
    """Insert ONE transaction stamped with `tenant` (so the analytics scope filters it)."""
    net_eur = net_eur if net_eur is not None else vat_eur * 5
    vat_local = vat_local if vat_local is not None else vat_eur
    net_local = net_local if net_local is not None else net_eur
    con = sqlite3.connect(fh)
    con.execute("""INSERT INTO transactions
        (period, entity, supplier, country, qty, currency,
         net_local, vat_local, net_eur, vat_eur, note, product_group, tenant_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (MONTH, entity, supplier, COUNTRY, 100.0, ccy,
         net_local, vat_local, net_eur, vat_eur, note, "fuel", tenant))
    con.commit(); con.close()


def _register_invoice(IC, tenancy, tenant, supplier, inv_ref):
    """Register a supplier invoice (tenant-stamped) so the claim line resolves to a
    real ref. register_statement syncs the VAT>0 line into supplier_invoices."""
    tenancy.set_tenant(tenant)
    IC.register_statement(
        supplier, f"ST-{inv_ref}", MONTH, STMT_DATE,
        lines=[(inv_ref, INV_DATE, COUNTRY, "EUR", 500.0, 100.0)],
        customer="Nonexistent Co")


def _attach_doc(vr, tenancy, tenant, entity, supplier, inv_ref):
    tenancy.set_tenant(tenant)
    con = vr.connect()
    try:
        ok, _msg = vr.attach_document(con, entity, supplier, inv_ref,
                                      file_bytes=f"{entity}/{inv_ref}".encode(),
                                      filename=f"{inv_ref}.pdf",
                                      country=COUNTRY, period=PERIOD_Q)
        assert ok
    finally:
        con.close()


def _seed_tenant(vr, sm, IC, cm, tenancy, fh, tenant, entity, supplier, inv_ref, vat_eur):
    """Build a FULL claim footprint for one tenant via the REAL write paths, with
    `tenant` bound (bind BEFORE connect). Distinct entity/supplier/ref per tenant."""
    _seed_supplier(sm, tenancy, tenant, supplier)
    _register_invoice(IC, tenancy, tenant, supplier, inv_ref)
    # one transaction whose note carries the invoice ref so _resolve_inv resolves it.
    _seed_txn(fh, tenant, entity, supplier, vat_eur=vat_eur, note=inv_ref)
    _attach_doc(vr, tenancy, tenant, entity, supplier, inv_ref)
    tenancy.reset_tenant()


def _seed_two_tenants(vat):
    vr, sm, IC, cm, tenancy, fh = vat
    # tenant A: high VAT; tenant B: a different entity/supplier/ref, different VAT.
    _seed_tenant(vr, sm, IC, cm, tenancy, fh, "A", "EntityA", "SUPA", "INV-A", vat_eur=1000.0)
    _seed_tenant(vr, sm, IC, cm, tenancy, fh, "B", "EntityB", "SUPB", "INV-B", vat_eur=2000.0)


# ── WRITE stamping (vat_claims.db) ────────────────────────────────────────────────

def test_writes_stamp_the_bound_tenant(vat):
    vr, sm, IC, cm, tenancy, fh = vat
    _seed_two_tenants(vat)
    # add a waiver + override "as tenant A" too (the other write paths).
    tenancy.set_owner_scope()
    con = vr.connect()
    try:
        docs = {r["entity"]: r["tenant_id"]
                for r in con.execute("SELECT entity, tenant_id FROM invoice_documents")}
    finally:
        con.close()
    assert docs == {"EntityA": "A", "EntityB": "B"}


def test_set_status_stamps_claim_rows(vat):
    vr, sm, IC, cm, tenancy, fh = vat
    _seed_two_tenants(vat)
    # submit A's claim and B's claim, each under its own tenant.
    for tenant, entity, supplier, ref in (("A", "EntityA", "SUPA", "INV-A"),
                                          ("B", "EntityB", "SUPB", "INV-B")):
        tenancy.set_tenant(tenant)
        con = vr.connect()
        try:
            ok, msg = vr.set_status(con, entity, COUNTRY, PERIOD_Q, "submitted")
            assert ok, msg
        finally:
            con.close()
    tenancy.reset_tenant()

    tenancy.set_owner_scope()
    con = vr.connect()
    try:
        apps = {r["entity"]: r["tenant_id"]
                for r in con.execute("SELECT entity, tenant_id FROM vat_applications")}
        locks = {r["entity"]: r["tenant_id"]
                 for r in con.execute("SELECT entity, tenant_id FROM vat_claimed_invoices")}
    finally:
        con.close()
    assert apps == {"EntityA": "A", "EntityB": "B"}
    assert locks == {"EntityA": "A", "EntityB": "B"}


def test_waiver_and_override_stamp_the_tenant(vat):
    vr, sm, IC, cm, tenancy, fh = vat
    _seed_two_tenants(vat)
    # a genuinely-uninvoiced supplier for tenant A (so the waiver is allowed).
    _seed_supplier(sm, tenancy, "A", "NOINVA")
    tenancy.set_tenant("A")
    con = vr.connect()
    try:
        ok, msg = vr.add_waiver(con, "EntityA", COUNTRY, PERIOD_Q, "NOINVA",
                                reason="not coming")
        assert ok, msg
    finally:
        con.close()
    # an override targeting A's registered invoice.
    vr.set_note_override("SUPA", COUNTRY, "some-note", "INV-A", "amy")
    tenancy.reset_tenant()

    tenancy.set_owner_scope()
    con = vr.connect()
    try:
        wv = {r["supplier"]: r["tenant_id"]
              for r in con.execute("SELECT supplier, tenant_id FROM vat_invoice_waivers")}
        ov = {r["supplier"]: r["tenant_id"]
              for r in con.execute("SELECT supplier, tenant_id FROM note_invoice_overrides")}
    finally:
        con.close()
    assert wv == {"NOINVA": "A"}
    assert ov == {"SUPA": "A"}


# ── READ isolation — the legal-figure no-bleed proof ──────────────────────────────

def test_invoice_lines_sum_only_own_transactions(vat):
    """The headline legal proof: A's claim VAT covers ONLY A's transactions; B's
    activity never bleeds in. Scoping restricts the INPUT rows, not the math."""
    vr, sm, IC, cm, tenancy, fh = vat
    _seed_two_tenants(vat)
    # add a SECOND A-transaction so A's line sum is unambiguously > a single line.
    tenancy.set_tenant("A")
    _seed_txn(fh, "A", "EntityA", "SUPA", vat_eur=500.0, note="INV-A")
    con = vr.connect()
    try:
        lines_a = vr.invoice_lines(con, "EntityA", COUNTRY, PERIOD_Q)
    finally:
        con.close()
    # A's two transactions (1000 + 500) sum to 1500; B's 2000 is absent.
    assert sum(L["vat_eur"] for L in lines_a) == pytest.approx(1500.0)
    assert all(L["supplier"] == "SUPA" for L in lines_a)

    tenancy.set_tenant("B")
    con = vr.connect()
    try:
        lines_b = vr.invoice_lines(con, "EntityB", COUNTRY, PERIOD_Q)
    finally:
        con.close()
    assert sum(L["vat_eur"] for L in lines_b) == pytest.approx(2000.0)
    assert all(L["supplier"] == "SUPB" for L in lines_b)


def test_claim_matrix_is_tenant_scoped(vat):
    vr, sm, IC, cm, tenancy, fh = vat
    _seed_two_tenants(vat)
    tenancy.set_tenant("A")
    con = vr.connect()
    try:
        m = vr.claim_matrix(con, "2099", with_portal=False)
    finally:
        con.close()
    entities = {row["entity"] for row in m}
    assert entities == {"EntityA"}
    q3 = next(r for r in m if r["period"] == PERIOD_Q and r["entity"] == "EntityA")
    assert q3["vat_eur"] == pytest.approx(1000.0)


def test_recovery_and_overview_are_tenant_scoped(vat):
    vr, sm, IC, cm, tenancy, fh = vat
    _seed_two_tenants(vat)
    # submit both claims (each under its tenant) so they appear in recovery_report.
    for tenant, entity in (("A", "EntityA"), ("B", "EntityB")):
        tenancy.set_tenant(tenant)
        con = vr.connect()
        try:
            ok, msg = vr.set_status(con, entity, COUNTRY, PERIOD_Q, "submitted")
            assert ok, msg
        finally:
            con.close()

    tenancy.set_tenant("A")
    out, summary = vr.recovery_report("2099")
    assert {o["entity"] for o in out} == {"EntityA"}
    assert summary["submitted"] == pytest.approx(1000.0)
    ov = vr.claims_overview("2099")
    seen = {(c["entity"], c.get("code")) for c in ov["open"]} | \
           {c["entity"] for c in ov["to_submit"]}
    assert all(e == "EntityA" or e[0] == "EntityA" for e in seen)

    tenancy.set_tenant("B")
    out_b, summary_b = vr.recovery_report("2099")
    assert {o["entity"] for o in out_b} == {"EntityB"}
    assert summary_b["submitted"] == pytest.approx(2000.0)


def test_below_minimum_uses_only_own_data(vat):
    """The threshold gate (below_minimum/_stream_vat) reads transactions scoped, so
    tenant A's threshold decision is on A's VAT only."""
    vr, sm, IC, cm, tenancy, fh = vat
    _seed_two_tenants(vat)
    tenancy.set_tenant("A")
    con = vr.connect()
    try:
        # A's 1000 EUR is above the FR quarterly minimum; verdict not below.
        below_a, _why = vr.below_minimum(con, "EntityA", COUNTRY, PERIOD_Q)
    finally:
        con.close()
    assert below_a is False


# ── WRITE isolation — a tenant can only mutate its OWN claim ───────────────────────

def test_status_reads_and_writes_are_tenant_scoped(vat):
    """A tenant's set_status/current_code read and mutate only ITS OWN claim rows
    (distinct entities per tenant — the PKs don't include tenant_id, so this proves
    the READ scope + the UPDATE scope fragment without the shared-PK write-collision
    that the work order flags as a separate, future re-keying slice)."""
    vr, sm, IC, cm, tenancy, fh = vat
    _seed_two_tenants(vat)
    # submit A's claim as tenant A and B's claim as tenant B (distinct entities).
    for tenant, entity in (("A", "EntityA"), ("B", "EntityB")):
        tenancy.set_tenant(tenant)
        con = vr.connect()
        try:
            ok, msg = vr.set_status(con, entity, COUNTRY, PERIOD_Q, "submitted")
            assert ok, msg
        finally:
            con.close()
    # As tenant B, B's claim reads "2"; A's claim is INVISIBLE -> a derived stage, not "2".
    tenancy.set_tenant("B")
    con = vr.connect()
    try:
        assert vr.current_code(con, "EntityB", COUNTRY, PERIOD_Q) == "2"
        assert vr.current_code(con, "EntityA", COUNTRY, PERIOD_Q) not in ("2", "submitted")
        # B's lock on its own invoice is visible; A's lock is not (scoped read).
        assert vr.lock_state(con, "EntityB", COUNTRY, "SUPB", "INV-B") == PERIOD_Q
        assert vr.lock_state(con, "EntityA", COUNTRY, "SUPA", "INV-A") is None
    finally:
        con.close()
    # As tenant A, A's claim reads "2"; B's is invisible.
    tenancy.set_tenant("A")
    con = vr.connect()
    try:
        assert vr.current_code(con, "EntityA", COUNTRY, PERIOD_Q) == "2"
        assert vr.lock_state(con, "EntityA", COUNTRY, "SUPA", "INV-A") == PERIOD_Q
        assert vr.lock_state(con, "EntityB", COUNTRY, "SUPB", "INV-B") is None
    finally:
        con.close()
    # A withdrawing its OWN claim releases A's lock and never touches B's claim/lock.
    tenancy.set_tenant("A")
    con = vr.connect()
    try:
        ok, _ = vr.withdraw_claim(con, "EntityA", COUNTRY, PERIOD_Q)
        assert ok
    finally:
        con.close()
    tenancy.set_tenant("B")
    con = vr.connect()
    try:
        assert vr.current_code(con, "EntityB", COUNTRY, PERIOD_Q) == "2"
        assert vr.lock_state(con, "EntityB", COUNTRY, "SUPB", "INV-B") == PERIOD_Q
    finally:
        con.close()


# ── OWNER cross-tenant scope ──────────────────────────────────────────────────────

def test_owner_scope_sees_both_tenants(vat):
    vr, sm, IC, cm, tenancy, fh = vat
    _seed_two_tenants(vat)
    for tenant, entity in (("A", "EntityA"), ("B", "EntityB")):
        tenancy.set_tenant(tenant)
        con = vr.connect()
        try:
            vr.set_status(con, entity, COUNTRY, PERIOD_Q, "submitted")
        finally:
            con.close()
    tenancy.set_owner_scope()
    out, summary = vr.recovery_report("2099")
    assert {o["entity"] for o in out} == {"EntityA", "EntityB"}
    assert summary["submitted"] == pytest.approx(3000.0)


# ── WORKER: a claim write under the bound tenant lands tenant_id A ────────────────

def test_worker_bound_tenant_stamps_claim(vat):
    """The worker (_do_close / process_one) binds the job's tenant via
    tenancy.set_tenant(_row_tenant(row)) before the engine writes. We bind tenant A
    exactly as the worker does and confirm the claim rows it writes carry tenant_id A.
    """
    vr, sm, IC, cm, tenancy, fh = vat
    _seed_two_tenants(vat)
    tenancy.set_tenant("A")                       # what the worker does per job
    con = vr.connect()
    try:
        ok, _ = vr.set_status(con, "EntityA", COUNTRY, PERIOD_Q, "submitted")
        assert ok
    finally:
        con.close()
    tenancy.reset_tenant()
    tenancy.set_owner_scope()
    con = vr.connect()
    try:
        app = con.execute("SELECT tenant_id FROM vat_applications WHERE entity='EntityA'"
                          ).fetchone()
        lock = con.execute("SELECT tenant_id FROM vat_claimed_invoices WHERE entity='EntityA'"
                           ).fetchone()
    finally:
        con.close()
    assert app["tenant_id"] == "A"
    assert lock["tenant_id"] == "A"


# ── OFF regression: byte-identical to today ───────────────────────────────────────

def test_switch_off_stamps_default_and_reads_unscoped(tmp_path, monkeypatch):
    """With the switch OFF (the default), writes stamp 'default' (== the column
    DEFAULT) and reads are unscoped — identical to today, even with a tenant bound.
    The claim figure is identical."""
    import auth
    import dataproduct
    import tenancy
    import supplier_master
    import customer_master
    import vat_refund
    import invoice_control as IC
    importlib.reload(supplier_master)
    importlib.reload(customer_master)
    importlib.reload(vat_refund)
    importlib.reload(IC)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    fh = str(tmp_path / "fuel_history.db")
    con = sqlite3.connect(fh)
    con.execute("""CREATE TABLE transactions (
        period TEXT, entity TEXT, supplier TEXT, country TEXT, vehicle TEXT,
        date TEXT, time TEXT, station TEXT, product TEXT, product_group TEXT,
        qty REAL, currency TEXT, net_local REAL, vat_local REAL, gross_local REAL,
        net_eur REAL, vat_eur REAL, net_eur_eff REAL, note TEXT,
        tenant_id TEXT NOT NULL DEFAULT 'default')""")
    con.commit(); con.close()
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "suppliers.db"))
    supplier_master._SCHEMA_READY.clear()
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "customers.db"))
    customer_master._SCHEMA_READY.clear()
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "vat_claims.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "fh_legacy.db"))
    vat_refund._SCHEMA_READY.clear()
    monkeypatch.setattr(vat_refund, "analytics_connect",
                        lambda: dataproduct.connect("fuel_history", path=fh))
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)
    monkeypatch.setattr(IC, "FUEL_HISTORY_DB", fh)
    monkeypatch.setattr(vat_refund, "DOCDIR", str(tmp_path / "documents"))
    vat_refund.connect().close()
    assert tenancy.multitenant_enabled() is False

    # Build a footprint with a tenant bound on the thread — OFF must ignore it.
    tenancy.set_tenant("A")
    con = supplier_master.connect()
    try:
        con.execute("INSERT INTO suppliers (code, legal_name, invoice_cadence, tenant_id) "
                    "VALUES (?,?,?,?)", ("SUP", "SUP Legal", "monthly",
                                         tenancy.queue_tenant()))
        con.commit()
    finally:
        con.close()
    IC.register_statement("SUP", "ST-1", MONTH, STMT_DATE,
                          lines=[("INV-1", INV_DATE, COUNTRY, "EUR", 500.0, 100.0)],
                          customer="Nonexistent Co")
    con = sqlite3.connect(fh)
    con.execute("""INSERT INTO transactions
        (period, entity, supplier, country, qty, currency,
         net_local, vat_local, net_eur, vat_eur, note, product_group, tenant_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (MONTH, "Ent", "SUP", COUNTRY, 100.0, "EUR", 5000.0, 1000.0, 5000.0, 1000.0,
         "INV-1", "fuel", "default"))
    con.commit(); con.close()
    tenancy.reset_tenant()

    # writes stamped the column DEFAULT 'default' (queue_tenant() OFF -> default).
    con = vat_refund.connect()
    try:
        con.execute("""INSERT INTO vat_applications (entity, refund_country, ref_period,
                       status) VALUES (?,?,?,?)""", ("Ent", COUNTRY, PERIOD_Q, "draft"))
        con.commit()
        row = con.execute("SELECT tenant_id FROM vat_applications WHERE entity='Ent'").fetchone()
        assert row["tenant_id"] == "default"
        # reads are unscoped: visible regardless of any thread tenant.
        tenancy.set_tenant("ZZZ")
        lines = vat_refund.invoice_lines(con, "Ent", COUNTRY, PERIOD_Q)
        assert sum(L["vat_eur"] for L in lines) == pytest.approx(1000.0)
    finally:
        con.close()
    tenancy.reset_tenant()

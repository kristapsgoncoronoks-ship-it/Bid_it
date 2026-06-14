"""Controllable VAT-claim status workflow (1A..5) on an ADJUSTABLE, system-controlled
checklist: derivation of the pre-submission stage, the period-end hard gate, the
submission lock behaviour (3B/3C/3D keep locks; withdraw releases), and the
configurable checklist rules."""
import importlib

import pytest


def _modules(tmp_path, monkeypatch):
    import customer_master, supplier_master, vat_refund
    importlib.reload(customer_master); importlib.reload(supplier_master)
    importlib.reload(vat_refund)
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "cdocs"))
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "s.db"))
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "a.db"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    # one invoice in the claim stream, with its document attached (claim-level checks)
    monkeypatch.setattr(vat_refund, "stream_invoices", lambda *a, **k: [("BP", "INV1")])
    monkeypatch.setattr(vat_refund, "docs_index", lambda con: {("Acme SIA", "BP", "INV1")})
    return customer_master, vat_refund


def _complete_checklist(cm):
    con = cm.connect()
    if not con.execute("SELECT 1 FROM customers WHERE code='ACME'").fetchone():
        con.close(); cm.add_customer("ACME", "Acme SIA", "LV"); con = cm.connect()
    con.execute("""UPDATE customers SET reg_number='LV123', vat_number='LV456',
                   legal_address='Riga 1', nace_code='49.41' WHERE code='ACME'"""); con.commit()
    cm.add_document(con, "ACME", "signed_contract", "c.pdf", b"C")
    cm.add_document(con, "ACME", "trade_registry", "t.pdf", b"T")
    con.execute("INSERT INTO customer_bank_accounts (customer, iban) VALUES ('ACME','LV99TESTIBAN')")
    con.commit()
    cm.add_country_document(con, "ACME", "Belgium", "power_of_attorney", "poa.pdf", b"P")
    con.close()


def _invoice_doc(vr, extra_vat=0.0):
    vc = vr.connect()
    vc.execute("""INSERT INTO invoice_documents (entity, supplier, invoice_ref, filename, sha256)
                  VALUES ('Acme SIA','BP','INV1','i.pdf','abc123')""")
    vc.commit(); vc.close()
    # The fee-freeze base now sums the claim_set via invoice_lines (F-C), which needs a
    # realistic transactions schema + a supplier registration so the BP/INV1 invoice
    # resolves. Register BP with a real VAT number (so the line is not INPUT) and one
    # registered invoice INV1; the txn note 'INV1' resolves the line to that invoice.
    import supplier_master  # already reloaded + DB-monkeypatched by _modules()
    sc = supplier_master.connect()
    sc.execute("INSERT INTO suppliers (code, legal_name) VALUES ('BP','B2Mobility GmbH')")
    sc.execute("""INSERT INTO supplier_vat_registrations (supplier, country, vat_number, source)
                  VALUES ('BP','Belgium','BE0123456789','registry')""")
    sc.execute("""INSERT INTO supplier_invoices (supplier, country, invoice_no, invoice_date)
                  VALUES ('BP','Belgium','INV1','2026-01-10')""")
    if extra_vat:
        # a SECOND registered invoice so the off-claim txn (note 'INV2') resolves to
        # its own (supplier, invoice) key — never folded onto INV1.
        sc.execute("""INSERT INTO supplier_invoices (supplier, country, invoice_no, invoice_date)
                      VALUES ('BP','Belgium','INV2','2026-02-10')""")
    sc.commit(); sc.close()
    # transactions is engine-owned; analytics_connect() is now a READ-ONLY handle, so
    # seed the fee-freeze data through a direct writable connection to the tmp-path
    # ANALYTICS_DB. The submit path still reads them via analytics_connect().
    import sqlite3
    ac = sqlite3.connect(vr.ANALYTICS_DB)
    ac.execute("""CREATE TABLE IF NOT EXISTS transactions (
        entity TEXT, supplier TEXT, country TEXT, period TEXT, product_group TEXT,
        note TEXT, qty REAL, currency TEXT, net_local REAL, vat_local REAL,
        net_eur REAL, vat_eur REAL)""")
    ac.execute("""INSERT INTO transactions
        (entity, supplier, country, period, product_group, note, qty, currency,
         net_local, vat_local, net_eur, vat_eur)
        VALUES ('Acme SIA','BP','Belgium','2026-01','Diesel','INV1',500,'EUR',
                4762,1000,4762,1000)""")
    if extra_vat:
        # A SECOND BP invoice in the SAME quarter that is NOT in the claim_set
        # (stream_invoices is monkeypatched to return only INV1). A raw period-wide
        # SUM(vat_eur) would wrongly fold this into the frozen base; the claim_set
        # basis must not.
        ac.execute("""INSERT INTO transactions
            (entity, supplier, country, period, product_group, note, qty, currency,
             net_local, vat_local, net_eur, vat_eur)
            VALUES ('Acme SIA','BP','Belgium','2026-02','Diesel','INV2',500,'EUR',
                    ?,?,?,?)""", (extra_vat * 4.762, extra_vat, extra_vat * 4.762, extra_vat))
    ac.commit(); ac.close()


# ------------------------------------------------------------ adjustable checklist
def test_default_checklist_seeded_and_evaluates(tmp_path, monkeypatch):
    cm, _vr = _modules(tmp_path, monkeypatch)
    con = cm.connect()
    keys = [r["key"] for r in cm.list_checklist_rules(con)]
    assert keys == ["contract", "customer_data", "bank_account", "nace",
                    "trade_register", "power_of_attorney"]
    cm.add_customer("ACME", "Acme SIA", "LV")          # nothing satisfied yet
    items = cm.evaluate_checklist(con, "ACME", None)    # customer-scoped only
    assert items and all(ok is False for _k, _l, _s, ok in items)
    con.close()


def test_checklist_rules_are_adjustable(tmp_path, monkeypatch):
    cm, _vr = _modules(tmp_path, monkeypatch)
    con = cm.connect()
    # add a new rule, toggle it off, delete it
    ok, _ = cm.set_checklist_rule(con, "vat_cert", "VAT certificate", "country", "document",
                                  "vat_certificate")
    assert ok and any(r["key"] == "vat_cert" for r in cm.list_checklist_rules(con))
    cm.toggle_checklist_rule(con, "vat_cert", False)
    assert all(r["key"] != "vat_cert" for r in cm.list_checklist_rules(con, active_only=True))
    cm.delete_checklist_rule(con, "vat_cert")
    assert all(r["key"] != "vat_cert" for r in cm.list_checklist_rules(con))
    # a data rule must name a known verifier
    ok, msg = cm.set_checklist_rule(con, "bad", "Bad", "customer", "data", "nope")
    assert not ok and "verifier" in msg
    con.close()


# ------------------------------------------------------------ stage derivation
def test_stage_1A_then_period_gate_then_ready(tmp_path, monkeypatch):
    cm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")           # checklist incomplete
    con = vr.connect()
    stage, items = vr.derive_stage(con, "Acme SIA", "Belgium", "2026-Q1")
    assert stage == "1A" and any(not ok for _l, ok in items)
    con.close()
    # complete the checklist -> a CLOSED period is ready (1E); an OPEN one is 1B
    _complete_checklist(cm)
    con = vr.connect()
    assert vr.derive_stage(con, "Acme SIA", "Belgium", "2026-Q1")[0] == "1E"   # Q1 ended
    assert vr.derive_stage(con, "Acme SIA", "Belgium", "2026-Q4")[0] == "1B"   # Q4 open
    con.close()


def test_period_end_dates():
    import vat_refund as vr, datetime
    assert vr.period_end_date("2026-Q2") == datetime.date(2026, 6, 30)
    assert vr.period_end_date("2026-YEAR") == datetime.date(2026, 12, 31)
    assert vr.period_ended("2026-Q1", today=datetime.date(2026, 6, 12)) is True
    assert vr.period_ended("2026-Q4", today=datetime.date(2026, 6, 12)) is False


# ------------------------------------------------------------ submission + locks
def test_submit_gate_and_lock_lifecycle(tmp_path, monkeypatch):
    cm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    con = vr.connect()
    # auto codes can't be set by hand
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "1E")
    assert not ok and "system-controlled" in msg
    # checklist incomplete -> submit blocked, listing what's missing
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")
    assert not ok and "checklist incomplete" in msg
    con.close()

    _complete_checklist(cm); _invoice_doc(vr)
    con = vr.connect()
    # period still open -> hard block
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q4", "2")
    assert not ok and "period has not ended" in msg
    # closed period, checklist complete -> submit OK and invoices LOCK
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")
    assert ok, msg
    assert con.execute("SELECT COUNT(*) FROM vat_claimed_invoices").fetchone()[0] == 1
    assert con.execute("""SELECT status_code FROM vat_applications WHERE entity='Acme SIA'
                          AND refund_country='Belgium' AND ref_period='2026-Q1'""").fetchone()[0] == "2"
    # advance to money received -> engine 'paid'
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "3A")
    assert ok and con.execute("""SELECT status FROM vat_applications WHERE ref_period='2026-Q1'"""
                              ).fetchone()[0] == "paid"
    # rejection KEEPS the locks (we appeal / invoice the fee)
    ok, _ = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "3B")
    assert ok and con.execute("SELECT COUNT(*) FROM vat_claimed_invoices").fetchone()[0] == 1
    # appeal also keeps locks
    ok, _ = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "3D")
    assert ok and con.execute("SELECT COUNT(*) FROM vat_claimed_invoices").fetchone()[0] == 1
    # only an explicit withdraw releases the invoices
    ok, _ = vr.withdraw_claim(con, "Acme SIA", "Belgium", "2026-Q1")
    assert ok and con.execute("SELECT COUNT(*) FROM vat_claimed_invoices").fetchone()[0] == 0
    con.close()


def test_quarterly_freeze_base_is_claim_set_not_all_period_vat(tmp_path, monkeypatch):
    """F-C: on quarterly submission the FROZEN fee VAT base must sum ONLY the invoices
    actually in this claim (claim_set), mirroring the annual branch — NOT a raw
    SUM(vat_eur) over ALL period transactions. Here BP has two Q1 invoices (INV1 €1000
    in Jan, INV2 €350 in Feb) but stream_invoices puts only INV1 in the claim; the
    frozen vat_eur must be 1000, and the fee 8% of 1000 = 80, NOT 8% of 1350."""
    cm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    # a real fee so the freeze is observable (% beats the minimum)
    cc = cm.connect()
    cc.execute("UPDATE customers SET fee_pct=8, fee_min=10 WHERE code='ACME'")
    cc.commit(); cc.close()
    _complete_checklist(cm)
    _invoice_doc(vr, extra_vat=350.0)   # INV1 (€1000, in claim) + INV2 (€350, NOT in claim)

    con = vr.connect()
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")
    assert ok, msg
    # only INV1 is locked into this claim
    assert con.execute("SELECT COUNT(*) FROM vat_claimed_invoices").fetchone()[0] == 1
    row = con.execute("""SELECT vat_eur, fee_eur, fee_pct FROM vat_applications
                         WHERE entity='Acme SIA' AND refund_country='Belgium'
                         AND ref_period='2026-Q1'""").fetchone()
    con.close()
    # frozen base = claim_set sum (1000), NOT all-period VAT (1350)
    assert float(row["vat_eur"]) == 1000.0
    assert float(row["fee_pct"]) == 8.0
    assert float(row["fee_eur"]) == 80.0     # 8% of 1000, not 108 (= 8% of 1350)


def test_build_workbook_does_not_clobber_submitted_frozen_base(tmp_path, monkeypatch):
    """DATA_ARCHITECTURE #1: build_workbook upserts claim_matrix (an ALL-period
    SUM(vat_eur)) into vat_applications. It must NEVER refresh a SUBMITTED stream's
    FROZEN vat_eur/vat_local/fee. Here Q1 has €1000 in the claim_set (INV1) + €350
    off-claim (INV2) -> claim_matrix recomputes 1350, but the frozen base stays 1000."""
    cm, vr = _modules(tmp_path, monkeypatch)
    monkeypatch.setattr(vr, "WORKDIR", str(tmp_path))   # keep generated Excel out of the repo
    cm.add_customer("ACME", "Acme SIA", "LV")
    cc = cm.connect()
    cc.execute("UPDATE customers SET fee_pct=8, fee_min=10 WHERE code='ACME'")
    cc.commit(); cc.close()
    _complete_checklist(cm)
    _invoice_doc(vr, extra_vat=350.0)   # INV1 (€1000, in claim) + INV2 (€350, off-claim)

    con = vr.connect()
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")
    assert ok, msg
    pre = con.execute("""SELECT vat_eur, vat_local, fee_eur, status FROM vat_applications
                         WHERE ref_period='2026-Q1'""").fetchone()
    assert pre["status"] == "submitted"
    assert float(pre["vat_eur"]) == 1000.0     # frozen claim_set base
    assert float(pre["fee_eur"]) == 80.0       # 8% of 1000
    # The freeze writes only vat_eur (the canonical claim base); vat_local is left as-is.
    frozen_vat_local = pre["vat_local"]

    # Sanity: claim_matrix (the source of the upsert) does see the all-period 1350.
    mrow = [m for m in vr.claim_matrix(con, 2026, with_portal=False)
            if m["period"] == "2026-Q1"][0]
    assert float(mrow["vat_eur"]) == 1350.0    # all-period recompute differs from the freeze

    # Generating the workbook AFTER submission must NOT clobber the frozen base.
    vr.build_workbook(con, 2026)
    post = con.execute("""SELECT vat_eur, vat_local, fee_eur FROM vat_applications
                          WHERE ref_period='2026-Q1'""").fetchone()
    con.close()
    assert float(post["vat_eur"]) == 1000.0          # NOT clobbered to 1350
    assert post["vat_local"] == frozen_vat_local     # not overwritten with the 1350-basis local
    assert float(post["fee_eur"]) == 80.0            # fee base preserved


def test_build_workbook_refreshes_draft_base(tmp_path, monkeypatch):
    """Contrast: a DRAFT (un-submitted) stream IS refreshed by build_workbook from the
    all-period claim_matrix — the guard only protects LOCKING (submitted/approved/paid)
    streams, so the existing draft-refresh / draft-row-creation behaviour is preserved."""
    cm, vr = _modules(tmp_path, monkeypatch)
    monkeypatch.setattr(vr, "WORKDIR", str(tmp_path))
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_checklist(cm)
    _invoice_doc(vr, extra_vat=350.0)   # €1000 + €350 in Q1, nothing submitted

    con = vr.connect()
    # no application row exists yet -> the INSERT half must create a DRAFT row
    assert con.execute("SELECT COUNT(*) FROM vat_applications").fetchone()[0] == 0
    vr.build_workbook(con, 2026)
    row = con.execute("""SELECT vat_eur, status FROM vat_applications
                         WHERE ref_period='2026-Q1'""").fetchone()
    assert row is not None and row["status"] == "draft"
    assert float(row["vat_eur"]) == 1350.0     # draft refreshed to the all-period recompute
    con.close()


def _submit_claim_pct8(tmp_path, monkeypatch):
    """Set up + submit a claim whose fee FREEZES at pct=8, min=10 on vat_eur=1000.
    Returns (cm, vr, con)."""
    cm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    cc = cm.connect()
    cc.execute("UPDATE customers SET fee_pct=8, fee_min=10 WHERE code='ACME'")
    cc.commit(); cc.close()
    _complete_checklist(cm); _invoice_doc(vr)
    con = vr.connect()
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")
    assert ok, msg
    return cm, vr, con


def test_record_payment_bills_fee_on_paid_amount(tmp_path, monkeypatch):
    """M5a: the fee re-bills on the ACTUALLY-refunded amount, not the full claimed VAT.
    Partial refund of €600 (8% = €48, above min €10) -> fee 48, NOT 80 (= 8% of 1000).
    Only the BASE changes; the frozen pct/min are untouched and compute_fee is reused."""
    import customer_master as CM
    cm, vr, con = _submit_claim_pct8(tmp_path, monkeypatch)
    # frozen at submission: pct=8, min=10, base=1000 -> fee 80
    pre = con.execute("""SELECT vat_eur, fee_eur, fee_pct, fee_min FROM vat_applications
                         WHERE ref_period='2026-Q1'""").fetchone()
    assert float(pre["vat_eur"]) == 1000.0 and float(pre["fee_eur"]) == 80.0

    ok, msg = vr.record_payment(con, "Acme SIA", "Belgium", "2026-Q1", 600, "2026-07-15")
    assert ok, msg
    row = con.execute("""SELECT paid_amount, paid_date, status, status_code, fee_eur,
                         fee_pct, fee_min FROM vat_applications WHERE ref_period='2026-Q1'"""
                      ).fetchone()
    assert float(row["paid_amount"]) == 600.0
    assert row["paid_date"] == "2026-07-15"
    assert row["status"] == "paid" and row["status_code"] == "3A"
    # fee re-billed on the PAID amount via compute_fee, at the FROZEN rate
    expect, _ = CM.compute_fee(600, row["fee_pct"], row["fee_min"])
    assert float(row["fee_eur"]) == float(expect) == 48.0    # NOT 80 (8% of 1000)
    # the frozen pct/min did NOT change
    assert float(row["fee_pct"]) == 8.0 and float(row["fee_min"]) == 10.0
    con.close()


def test_record_payment_full_refund_fee_unchanged(tmp_path, monkeypatch):
    """A full refund (paid == claimed) leaves the fee on the claimed basis (8% of 1000)."""
    import customer_master as CM
    cm, vr, con = _submit_claim_pct8(tmp_path, monkeypatch)
    ok, msg = vr.record_payment(con, "Acme SIA", "Belgium", "2026-Q1", 1000, "2026-07-15")
    assert ok, msg
    row = con.execute("""SELECT paid_amount, fee_eur, fee_pct, fee_min FROM vat_applications
                         WHERE ref_period='2026-Q1'""").fetchone()
    assert float(row["paid_amount"]) == 1000.0
    expect, _ = CM.compute_fee(1000, row["fee_pct"], row["fee_min"])
    assert float(row["fee_eur"]) == float(expect) == 80.0    # unchanged from claimed basis
    con.close()


def test_record_payment_validates_and_gates(tmp_path, monkeypatch):
    """Negative amounts and non-submitted claims are rejected."""
    cm, vr, con = _submit_claim_pct8(tmp_path, monkeypatch)
    ok, msg = vr.record_payment(con, "Acme SIA", "Belgium", "2026-Q1", -5, "2026-07-15")
    assert not ok and ">= 0" in msg
    # an unknown / unsubmitted claim can't take a payment
    ok, msg = vr.record_payment(con, "Acme SIA", "Belgium", "2026-Q2", 100, "2026-07-15")
    assert not ok
    con.close()


def test_record_payment_atomic_rolls_back_paid_amount_on_failure(tmp_path, monkeypatch):
    """M5a: if the paid-transition FAILS after the paid_amount stamp, the stamp must be
    ROLLED BACK — never half-written. We monkeypatch set_status_code to raise after the
    record_payment UPDATE has run (but is still uncommitted, pending in the same txn);
    set_status's `except: con.rollback(); raise` would normally clear it, but here we
    bypass set_status entirely, so record_payment must surface the error with NO
    committed paid_amount."""
    cm, vr, con = _submit_claim_pct8(tmp_path, monkeypatch)
    pre = con.execute("""SELECT paid_amount, status, fee_eur FROM vat_applications
                         WHERE ref_period='2026-Q1'""").fetchone()
    assert pre["paid_amount"] is None and pre["status"] == "submitted"

    def boom(*a, **k):
        raise RuntimeError("transition blew up after the paid_amount stamp")
    monkeypatch.setattr(vr, "set_status_code", boom)

    with pytest.raises(RuntimeError):
        vr.record_payment(con, "Acme SIA", "Belgium", "2026-Q1", 600, "2026-07-15")

    # The pending UPDATE was discarded by the exception unwinding (rollback): read it on
    # a FRESH connection so nothing is masked by the dirty in-transaction view.
    con.rollback()
    con.close()
    con2 = vr.connect()
    post = con2.execute("""SELECT paid_amount, status, fee_eur FROM vat_applications
                          WHERE ref_period='2026-Q1'""").fetchone()
    assert post["paid_amount"] is None          # NOT half-written
    assert post["status"] == "submitted"        # still pre-payment
    assert float(post["fee_eur"]) == 80.0       # fee untouched (claimed basis)
    con2.close()


def test_record_payment_happy_path_atomic_state(tmp_path, monkeypatch):
    """M5a happy path unchanged: a recorded payment commits paid_amount + status='paid'
    + status_code='3A' + the paid-base fee + fee_billed_date TOGETHER. Verified on a
    FRESH connection so we observe only COMMITTED state (atomic, not a dirty read)."""
    import customer_master as CM
    cm, vr, con = _submit_claim_pct8(tmp_path, monkeypatch)
    ok, msg = vr.record_payment(con, "Acme SIA", "Belgium", "2026-Q1", 600, "2026-07-15")
    assert ok, msg
    con.close()
    con2 = vr.connect()
    row = con2.execute("""SELECT paid_amount, paid_date, status, status_code, fee_eur,
                          fee_billed_date, fee_pct, fee_min FROM vat_applications
                          WHERE ref_period='2026-Q1'""").fetchone()
    con2.close()
    assert float(row["paid_amount"]) == 600.0
    assert row["status"] == "paid" and row["status_code"] == "3A"
    assert row["paid_date"] == "2026-07-15" and row["fee_billed_date"] is not None
    expect, _ = CM.compute_fee(600, row["fee_pct"], row["fee_min"])
    assert float(row["fee_eur"]) == float(expect) == 48.0     # paid-base fee, frozen rate


def test_raw_rejected_keeps_locks_only_withdraw_releases(tmp_path, monkeypatch):
    """R4: the RAW engine status 'rejected' (set_status, not the 3B code path) must
    KEEP the invoice locks — exactly like 3B. Freeing them on rejection would let the
    same invoices be re-claimed elsewhere (duplicate-submission exposure). Only
    withdraw_claim releases the locks."""
    cm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_checklist(cm); _invoice_doc(vr)
    con = vr.connect()

    # submit -> invoices lock
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")
    assert ok, msg
    assert con.execute("SELECT COUNT(*) FROM vat_claimed_invoices").fetchone()[0] == 1

    # drive the raw engine status to 'rejected' -> locks MUST remain
    ok, msg = vr.set_status(con, "Acme SIA", "Belgium", "2026-Q1", "rejected",
                            gate_activation=False)
    assert ok, msg
    assert "stay locked" in msg
    assert con.execute("SELECT COUNT(*) FROM vat_claimed_invoices").fetchone()[0] == 1

    # only an explicit withdraw frees them
    ok, _ = vr.withdraw_claim(con, "Acme SIA", "Belgium", "2026-Q1")
    assert ok and con.execute("SELECT COUNT(*) FROM vat_claimed_invoices").fetchone()[0] == 0
    con.close()


def test_disabling_a_rule_changes_the_gate(tmp_path, monkeypatch):
    cm, vr = _modules(tmp_path, monkeypatch)
    # everything EXCEPT NACE
    cm.add_customer("ACME", "Acme SIA", "LV", reg_number="LV1", vat_number="LV2")
    con = cm.connect()
    con.execute("UPDATE customers SET legal_address='Riga' WHERE code='ACME'"); con.commit()
    cm.add_document(con, "ACME", "signed_contract", "c.pdf", b"C")
    cm.add_document(con, "ACME", "trade_registry", "t.pdf", b"T")
    con.execute("INSERT INTO customer_bank_accounts (customer, iban) VALUES ('ACME','LV99X')")
    con.commit()
    cm.add_country_document(con, "ACME", "Belgium", "power_of_attorney", "p.pdf", b"P")
    con.close()
    vcon = vr.connect()
    assert vr.derive_stage(vcon, "Acme SIA", "Belgium", "2026-Q1")[0] == "1A"   # NACE missing
    vcon.close()
    # admin disables the NACE rule -> claim is now ready
    con = cm.connect(); cm.toggle_checklist_rule(con, "nace", False); con.close()
    vcon = vr.connect()
    assert vr.derive_stage(vcon, "Acme SIA", "Belgium", "2026-Q1")[0] == "1E"
    vcon.close()


# ------------------------------------------------------------ mini-CRM doc generation
def test_template_merge_and_generate(tmp_path, monkeypatch):
    import customer_master as cm
    import importlib
    importlib.reload(cm)
    monkeypatch.setattr(cm, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(cm, "_SCHEMA_READY", set())
    monkeypatch.setattr(cm, "DOCDIR", str(tmp_path / "cdocs"))
    cm.add_customer("ACME", "Acme SIA", "LV", reg_number="LV123", vat_number="LV456")
    con = cm.connect()
    # text template fills fully and reports leftover placeholders
    out, ext, left = cm.fill_template(b"{{company_name}} / {{reg_number}} / {{nope}}", "txt",
                                      cm.merge_fields(con, "ACME"))
    assert b"Acme SIA / LV123 /" in out and left == ["nope"]
    # docx template: placeholder inside the Word XML is replaced
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:t>POA for {{company_name}} in {{refund_country}}</w:t>")
    tid = cm.add_template(con, "POA", "power_of_attorney", "poa.docx", buf.getvalue())
    data, name, dext, _ = cm.generate_document(con, tid, "ACME", country="Belgium")
    assert dext == "docx" and name == "POA_ACME_Belgium.docx"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert "Acme SIA" in z.read("word/document.xml").decode() and "Belgium" in z.read("word/document.xml").decode()
    # list + delete
    assert any(t["name"] == "POA" for t in cm.list_templates(con))
    cm.delete_template(con, tid)
    assert cm.get_template(con, tid) is None
    con.close()


# ------------------------------------------------------------ per-status data + 4/4A/5
def test_status_note_deadline_and_decision_date(tmp_path, monkeypatch):
    cm, vr = _modules(tmp_path, monkeypatch)
    _complete_checklist(cm); _invoice_doc(vr)
    con = vr.connect()
    assert vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")[0]
    # document request with a response deadline + note
    ok, _ = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2B",
                               note="authority wants CMRs", deadline="2026-07-15")
    assert ok
    r = con.execute("SELECT status_note, action_deadline, decision_date FROM vat_applications"
                    ).fetchone()
    assert r["status_note"] == "authority wants CMRs" and r["action_deadline"] == "2026-07-15"
    assert r["decision_date"] is None                   # no decision yet
    # decision arrives -> decision_date stamped, open-action deadline cleared
    assert vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "3A")[0]
    r = con.execute("SELECT decision_date, action_deadline FROM vat_applications").fetchone()
    assert r["decision_date"] and r["action_deadline"] is None
    con.close()


def test_suggested_next_and_close_lifecycle(tmp_path, monkeypatch):
    cm, vr = _modules(tmp_path, monkeypatch)
    # the suggestion respects the payout route after money received
    assert vr.suggested_next("3A", "customer") == "4"
    assert vr.suggested_next("3A", "us") == "4A"
    assert vr.suggested_next("4") == "5" and vr.suggested_next("5") is None
    assert vr.suggested_next("3B") == "3D"              # rejection -> appeal
    # full closing lifecycle keeps the engine at 'paid'
    _complete_checklist(cm); _invoice_doc(vr)
    con = vr.connect()
    for code in ("2", "3A", "4", "5"):
        assert vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", code)[0], code
    r = con.execute("SELECT status, status_code FROM vat_applications").fetchone()
    assert (r["status"], r["status_code"]) == ("paid", "5")
    con.close()


def test_expired_document_fails_checklist(tmp_path, monkeypatch):
    """An expired power of attorney stops satisfying the checklist (claim back to 1A)."""
    cm, vr = _modules(tmp_path, monkeypatch)
    _complete_checklist(cm)
    con = cm.connect()
    # expire the POA
    con.execute("UPDATE customer_documents SET valid_until='2020-01-01' "
                "WHERE kind='power_of_attorney'")
    con.commit(); con.close()
    vcon = vr.connect()
    stage, items = vr.derive_stage(vcon, "Acme SIA", "Belgium", "2026-Q1")
    assert stage == "1A"
    assert any(l == "Power of attorney" and not ok for l, ok in items)
    vcon.close()
    # renew it -> ready again
    con = cm.connect()
    cm.add_document(con, "ACME", "power_of_attorney", "poa2.pdf", b"P2",
                    country="Belgium", valid_until="2030-01-01")
    con.close()
    vcon = vr.connect()
    assert vr.derive_stage(vcon, "Acme SIA", "Belgium", "2026-Q1")[0] == "1E"
    vcon.close()


def test_generate_pdf_output(tmp_path, monkeypatch):
    import customer_master as cm
    import importlib, io
    importlib.reload(cm)
    monkeypatch.setattr(cm, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(cm, "_SCHEMA_READY", set())
    monkeypatch.setattr(cm, "DOCDIR", str(tmp_path / "cdocs"))
    cm.add_customer("ACME", "Acme SIA", "LV", reg_number="LV123")
    con = cm.connect()
    tid = cm.add_template(con, "Contract", "signed_contract", "c.txt",
                          b"Contract between {{company_name}} (reg {{reg_number}}) and Agency.")
    data, name, ext, left = cm.generate_document(con, tid, "ACME", as_pdf=True)
    con.close()
    assert ext == "pdf" and name.endswith(".pdf") and data.startswith(b"%PDF") and left == []
    from pypdf import PdfReader
    text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
    assert "Acme SIA" in text and "LV123" in text

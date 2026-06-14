"""Receipt-control SUBMISSION GATE + admin WAIVE (R5).

A supplier with claim activity but NO registered invoice for a refund country gets a
SYNTHETIC 'INPUT: <ctry> invoice' ref (case (a) — the invoice simply isn't coming). The
submission gate NAMES it as a missing required invoice and BLOCKS the claim; an admin may
WAIVE it ('invoice not coming'), which both clears the gate and EXCLUDES that supplier's
transactions from the filed claim. A supplier that HAS >=2 registered invoices but no
note-match resolves to 'UNMATCHED' (case (b) — a matching fix, NOT a missing invoice): it
is NEVER waivable and ALWAYS blocks. These tests pin both halves of that conflict.

Unlike test_claim_status, stream_invoices is NOT monkeypatched: the real
invoice_lines -> supplier_master.get_invoices path drives the synthetic refs so the
case-(a) / case-(b) distinction is exercised end-to-end.
"""
import importlib
import sqlite3

import pytest


def _modules(tmp_path, monkeypatch):
    import audit, customer_master, supplier_master, vat_refund
    importlib.reload(customer_master); importlib.reload(supplier_master)
    importlib.reload(vat_refund)
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "cdocs"))
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "s.db"))
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "a.db"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    audit.reset_actor()
    return customer_master, supplier_master, vat_refund


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


def _txn(vr, supplier, note, vat_eur):
    """Seed one Belgium/2026-Q1 transaction for ACME, through a writable handle to the
    engine-owned analytics DB (analytics_connect() is read-only)."""
    ac = sqlite3.connect(vr.ANALYTICS_DB)
    ac.execute("""CREATE TABLE IF NOT EXISTS transactions (
        entity TEXT, supplier TEXT, country TEXT, period TEXT, product_group TEXT,
        note TEXT, qty REAL, currency TEXT, net_local REAL, vat_local REAL,
        net_eur REAL, vat_eur REAL)""")
    ac.execute("""INSERT INTO transactions
        (entity, supplier, country, period, product_group, note, qty, currency,
         net_local, vat_local, net_eur, vat_eur)
        VALUES ('Acme SIA',?,'Belgium','2026-01','Diesel',?,500,'EUR',?,?,?,?)""",
        (supplier, note, vat_eur * 4.762, vat_eur, vat_eur * 4.762, vat_eur))
    ac.commit(); ac.close()


def _register_supplier(sm, code, name, invoices=()):
    """A supplier with a VAT registration (so its vat_id is not INPUT) and `invoices`
    (supplier, country, no) registered. With zero invoices it is the case-(a) waivable
    supplier (get_invoices returns only the 'INPUT: …' stub)."""
    sc = sm.connect()
    sc.execute("INSERT INTO suppliers (code, legal_name) VALUES (?,?)", (code, name))
    sc.execute("""INSERT INTO supplier_vat_registrations (supplier, country, vat_number, source)
                  VALUES (?,'Belgium','BE0123456789','registry')""", (code,))
    for no, date in invoices:
        sc.execute("""INSERT INTO supplier_invoices (supplier, country, invoice_no, invoice_date)
                      VALUES (?,'Belgium',?,?)""", (code, no, date))
    sc.commit(); sc.close()


def _doc(vr, supplier, ref):
    vc = vr.connect()
    vc.execute("""INSERT INTO invoice_documents (entity, supplier, invoice_ref, filename, sha256)
                  VALUES ('Acme SIA',?,?,'i.pdf',?)""", (supplier, ref, supplier + ref))
    vc.commit(); vc.close()


def _checklist_item(items, prefix):
    for label, ok in items:
        if label.startswith(prefix):
            return label, ok
    return None, None


# --------------------------------------------------------------------------- case (a)
def test_uninvoiced_supplier_named_and_blocks(tmp_path, monkeypatch):
    """1+2: a supplier with activity but ZERO registered invoices is NAMED in the
    receipt-control item and BLOCKS submission; nothing is written while unwaived."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_checklist(cm)
    _register_supplier(sm, "NOINV", "No-Invoice GmbH", invoices=())   # case (a)
    _txn(vr, "NOINV", "whatever", 500.0)

    con = vr.connect()
    items = vr.submission_checklist(con, "Acme SIA", "Belgium", "2026-Q1")
    label, ok = _checklist_item(items, "Receipt control: required invoices received")
    assert ok is False
    assert "missing: NOINV" in label

    # submit (code 2) is BLOCKED and names the supplier; no application row written
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")
    assert not ok and "checklist incomplete" in msg and "NOINV" in msg
    assert con.execute("SELECT COUNT(*) FROM vat_applications").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM vat_claimed_invoices").fetchone()[0] == 0
    con.close()


def test_waive_excludes_supplier_and_files_only_matched(tmp_path, monkeypatch):
    """3: admin waives the uninvoiced supplier AND a second supplier has a matched,
    documented, above-threshold invoice -> submission SUCCEEDS, files ONLY the matched
    invoice, frozen VAT == matched only, and the status_note records the waive use."""
    import audit
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_checklist(cm)
    _register_supplier(sm, "NOINV", "No-Invoice GmbH", invoices=())                 # case (a)
    _register_supplier(sm, "BP", "B2Mobility", invoices=[("INV1", "2026-01-10")])   # matched
    _txn(vr, "NOINV", "no invoice coming", 250.0)
    _txn(vr, "BP", "INV1", 1000.0)
    _doc(vr, "BP", "INV1")

    con = vr.connect()
    audit.set_actor(con, "admin_alice")
    ok, msg = vr.add_waiver(con, "Acme SIA", "Belgium", "2026-Q1", "NOINV", reason="bankrupt")
    assert ok, msg

    # gate now clear -> submit succeeds
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")
    assert ok, msg

    # ONLY the matched invoice is locked into the claim
    locked = con.execute("""SELECT supplier, invoice_ref FROM vat_claimed_invoices""").fetchall()
    assert [(r["supplier"], r["invoice_ref"]) for r in locked] == [("BP", "INV1")]

    row = con.execute("""SELECT vat_eur, vat_local, status, status_note FROM vat_applications
                         WHERE ref_period='2026-Q1'""").fetchone()
    assert row["status"] == "submitted"
    assert float(row["vat_eur"]) == 1000.0          # matched only; the waived 250 excluded
    assert float(row["vat_local"]) == 1000.0
    assert "filed excluding waived suppliers: NOINV" in (row["status_note"] or "")
    audit.reset_actor()
    con.close()


def test_two_uninvoiced_waive_one_still_blocks_on_other(tmp_path, monkeypatch):
    """4: two uninvoiced suppliers, waive one -> still blocked, naming the OTHER only."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_checklist(cm)
    _register_supplier(sm, "NOINV1", "NoInv One", invoices=())
    _register_supplier(sm, "NOINV2", "NoInv Two", invoices=())
    _txn(vr, "NOINV1", "x", 100.0)
    _txn(vr, "NOINV2", "y", 100.0)

    con = vr.connect()
    ok, _ = vr.add_waiver(con, "Acme SIA", "Belgium", "2026-Q1", "NOINV1")
    assert ok
    items = vr.submission_checklist(con, "Acme SIA", "Belgium", "2026-Q1")
    label, ok = _checklist_item(items, "Receipt control: required invoices received")
    assert ok is False
    assert "NOINV2" in label and "NOINV1" not in label
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")
    assert not ok and "NOINV2" in msg
    con.close()


# --------------------------------------------------------------------------- case (b)
def test_unmatched_supplier_with_invoices_is_not_waivable(tmp_path, monkeypatch):
    """5 (the crux): a supplier WITH >=2 registered invoices but no note-match resolves to
    'UNMATCHED' (case (b), a matching fix). add_waiver REFUSES it, and submission stays
    blocked on the 'All invoice refs resolved' item — never on the receipt-control item."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_checklist(cm)
    # 2 registered invoices but the transaction note matches NEITHER -> UNMATCHED
    _register_supplier(sm, "AMB", "Ambiguous SA",
                       invoices=[("R1", "2026-01-05"), ("R2", "2026-01-20")])
    _txn(vr, "AMB", "no-such-note", 800.0)

    con = vr.connect()
    # confirm the stream really produced UNMATCHED (not the INPUT stub)
    refs = {r for _s, r in vr.stream_invoices(con, "Acme SIA", "Belgium", "2026-Q1")}
    assert "UNMATCHED" in refs

    # add_waiver REFUSES (supplier HAS registered invoices)
    ok, msg = vr.add_waiver(con, "Acme SIA", "Belgium", "2026-Q1", "AMB")
    assert not ok and "HAS registered invoice" in msg
    assert con.execute("SELECT COUNT(*) FROM vat_invoice_waivers").fetchone()[0] == 0

    items = vr.submission_checklist(con, "Acme SIA", "Belgium", "2026-Q1")
    # the receipt-control item is NOT what blocks (no waivable-missing supplier)...
    rc_label, rc_ok = _checklist_item(items, "Receipt control: required invoices received")
    assert rc_ok is True and "missing:" not in rc_label
    # ...the 'refs resolved' item is what blocks (UNMATCHED is non-waivable synthetic)
    _resolved_label, resolved_ok = _checklist_item(items, "All invoice refs resolved")
    assert resolved_ok is False
    # and submission is blocked
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")
    assert not ok and "checklist incomplete" in msg
    con.close()


def test_no_synthetic_slips_through_two_item_split(tmp_path, monkeypatch):
    """Coverage proof: with BOTH a case-(a) (NOINV) and a case-(b) (AMB) synthetic in the
    same stream, the two items together block EXACTLY the same set the old single item did
    -- every synthetic is caught -- and waiving the case-(a) one leaves case-(b) blocking."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_checklist(cm)
    _register_supplier(sm, "NOINV", "No-Invoice GmbH", invoices=())
    _register_supplier(sm, "AMB", "Ambiguous SA",
                       invoices=[("R1", "2026-01-05"), ("R2", "2026-01-20")])
    _txn(vr, "NOINV", "x", 100.0)
    _txn(vr, "AMB", "no-such-note", 100.0)

    con = vr.connect()
    invs = vr.stream_invoices(con, "Acme SIA", "Belgium", "2026-Q1")
    all_synth = {(s, r) for s, r in invs if vr._synthetic(r)}
    assert len(all_synth) == 2          # NOINV(INPUT stub) + AMB(UNMATCHED)

    # before any waiver: union of the two items' blocked sets == every synthetic
    items = vr.submission_checklist(con, "Acme SIA", "Belgium", "2026-Q1")
    assert _checklist_item(items, "Receipt control: required invoices received")[1] is False
    assert _checklist_item(items, "All invoice refs resolved")[1] is False

    # waive the case-(a) -> receipt-control clears, but case-(b) still blocks 'refs resolved'
    vr.add_waiver(con, "Acme SIA", "Belgium", "2026-Q1", "NOINV")
    items = vr.submission_checklist(con, "Acme SIA", "Belgium", "2026-Q1")
    assert _checklist_item(items, "Receipt control: required invoices received")[1] is True
    assert _checklist_item(items, "All invoice refs resolved")[1] is False   # AMB never silenced
    con.close()


def test_matched_invoice_is_never_dropped_or_waivable(tmp_path, monkeypatch):
    """6: a supplier with a matched (real-ref) invoice can't be waived and its ref survives
    the lock-time waiver filter."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_checklist(cm)
    _register_supplier(sm, "BP", "B2Mobility", invoices=[("INV1", "2026-01-10")])
    _txn(vr, "BP", "INV1", 1000.0)
    _doc(vr, "BP", "INV1")

    con = vr.connect()
    # the matched supplier is not waivable (it HAS a registered invoice)
    ok, msg = vr.add_waiver(con, "Acme SIA", "Belgium", "2026-Q1", "BP")
    assert not ok and "HAS registered invoice" in msg
    # even if a stray waiver row existed, set_status must NOT drop the matched ref:
    con.execute("""INSERT INTO vat_invoice_waivers
                   (entity, refund_country, ref_period, supplier) VALUES
                   ('Acme SIA','Belgium','2026-Q1','BP')""")
    con.commit()
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")
    assert ok, msg
    assert [(r["supplier"], r["invoice_ref"]) for r in
            con.execute("SELECT supplier, invoice_ref FROM vat_claimed_invoices")] == [("BP", "INV1")]
    con.close()


def test_waiver_idempotent_and_resubmit_noop(tmp_path, monkeypatch):
    """8: add_waiver is idempotent (one row); a second submit of an already-submitted
    stream is a no-op (still one claimed invoice)."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_checklist(cm)
    _register_supplier(sm, "NOINV", "No-Invoice GmbH", invoices=())
    _register_supplier(sm, "BP", "B2Mobility", invoices=[("INV1", "2026-01-10")])
    _txn(vr, "NOINV", "x", 100.0)
    _txn(vr, "BP", "INV1", 1000.0)
    _doc(vr, "BP", "INV1")

    con = vr.connect()
    vr.add_waiver(con, "Acme SIA", "Belgium", "2026-Q1", "NOINV", reason="r1")
    vr.add_waiver(con, "Acme SIA", "Belgium", "2026-Q1", "NOINV", reason="r2")
    assert con.execute("SELECT COUNT(*) FROM vat_invoice_waivers").fetchone()[0] == 1
    assert con.execute("SELECT reason FROM vat_invoice_waivers").fetchone()[0] == "r2"

    assert vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")[0]
    n1 = con.execute("SELECT COUNT(*) FROM vat_claimed_invoices").fetchone()[0]
    # re-submitting the same code is a no-op (already in that locked state)
    vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")
    n2 = con.execute("SELECT COUNT(*) FROM vat_claimed_invoices").fetchone()[0]
    assert n1 == n2 == 1
    con.close()


def test_waiver_is_audited_to_acting_admin(tmp_path, monkeypatch):
    """9: the waiver INSERT is audit-logged with the acting admin as changed_by."""
    import audit
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_checklist(cm)
    _register_supplier(sm, "NOINV", "No-Invoice GmbH", invoices=())
    _txn(vr, "NOINV", "x", 100.0)

    con = vr.connect()
    audit.set_actor(con, "admin_bob")
    ok, _ = vr.add_waiver(con, "Acme SIA", "Belgium", "2026-Q1", "NOINV")
    assert ok
    rows = audit.history(con, table="vat_invoice_waivers", action="INSERT")
    assert rows, "expected an audited INSERT for the waiver"
    assert any(r["changed_by"] == "admin_bob" for r in rows)
    audit.reset_actor()
    con.close()


def test_add_waiver_guard_keys_on_registered_invoices(tmp_path, monkeypatch):
    """R5 precision: the waivable predicate keys on _no_registered_invoices (zero rows in
    supplier_invoices) -- NOT on the ref literal. _waivable_missing is True only for the
    INPUT-stub case-(a), False for case-(b) UNMATCHED (registered invoices exist)."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _register_supplier(sm, "NOINV", "No-Invoice GmbH", invoices=())
    _register_supplier(sm, "AMB", "Ambiguous SA",
                       invoices=[("R1", "2026-01-05"), ("R2", "2026-01-20")])
    scon = sm.connect()
    # case (a): no registered invoice -> the INPUT stub IS waivable
    assert vr._no_registered_invoices("NOINV", "Belgium", scon) is True
    assert vr._waivable_missing("INPUT: Belgium invoice", "NOINV", "Belgium", scon) is True
    # case (b): registered invoices exist -> UNMATCHED is NOT waivable
    assert vr._no_registered_invoices("AMB", "Belgium", scon) is False
    assert vr._waivable_missing("UNMATCHED", "AMB", "Belgium", scon) is False
    # an ALL: aggregate is NEVER waivable even if no invoices are on file (precision guard)
    assert vr._waivable_missing("ALL: Belgium", "NOINV", "Belgium", scon) is False
    scon.close()

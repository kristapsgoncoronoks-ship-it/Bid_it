"""Controllable VAT-claim status workflow (1A..5) on an ADJUSTABLE, system-controlled
checklist: derivation of the pre-submission stage, the period-end hard gate, the
submission lock behaviour (3B/3C/3D keep locks; withdraw releases), and the
configurable checklist rules."""
import importlib

import pytest


def _modules(tmp_path, monkeypatch):
    import customer_master, vat_refund
    importlib.reload(customer_master); importlib.reload(vat_refund)
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "cdocs"))
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


def _invoice_doc(vr):
    vc = vr.connect()
    vc.execute("""INSERT INTO invoice_documents (entity, supplier, invoice_ref, filename, sha256)
                  VALUES ('Acme SIA','BP','INV1','i.pdf','abc123')""")
    vc.commit(); vc.close()
    # transactions live in the analytics DB; the submit path reads them to freeze the fee
    ac = vr.analytics_connect()
    ac.execute("CREATE TABLE IF NOT EXISTS transactions (entity TEXT, country TEXT, period TEXT, vat_eur REAL)")
    ac.execute("INSERT INTO transactions VALUES ('Acme SIA','Belgium','2026-01',1000)")
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

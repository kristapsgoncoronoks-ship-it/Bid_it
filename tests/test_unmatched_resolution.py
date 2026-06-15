"""UNMATCHED-resolution: an admin-curated note->invoice override (vat_refund).

A claim line tags 'UNMATCHED' when a transaction's note matches no registered invoice
AND there isn't exactly one registered invoice for that supplier/country (case (b) of
test_invoice_waiver) -- a HARD block on filing. An admin may map that note to an
EXISTING registered invoice so the line resolves to it INSTEAD of UNMATCHED. This is an
ASSOCIATION (bucketing) change ONLY: it never moves a net/VAT amount, and the target is
re-validated as still-registered & non-synthetic at READ time so a stale override can
never inject a non-existent/synthetic ref.

These tests reuse the test_invoice_waiver fixture shape (real
invoice_lines -> supplier_master.get_invoices path; stream_invoices NOT monkeypatched).
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


def _txn(vr, supplier, note, vat_eur):
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
    sc = sm.connect()
    sc.execute("INSERT INTO suppliers (code, legal_name) VALUES (?,?)", (code, name))
    sc.execute("""INSERT INTO supplier_vat_registrations (supplier, country, vat_number, source)
                  VALUES (?,'Belgium','BE0123456789','registry')""", (code,))
    for no, date in invoices:
        sc.execute("""INSERT INTO supplier_invoices (supplier, country, invoice_no, invoice_date)
                      VALUES (?,'Belgium',?,?)""", (code, no, date))
    sc.commit(); sc.close()


def _deregister_invoice(sm, code, no):
    sc = sm.connect()
    sc.execute("DELETE FROM supplier_invoices WHERE supplier=? AND country='Belgium' AND invoice_no=?",
               (code, no))
    sc.commit(); sc.close()


def _totals(vr, con):
    """Sum of net/VAT EUR across all claim lines (the recoverable VAT must be invariant
    to bucketing). Uses the real invoice_lines path."""
    lines = vr.invoice_lines(con, "Acme SIA", "Belgium", "2026-Q1")
    return (round(sum(L["net_eur"] for L in lines), 2),
            round(sum(L["vat_eur"] for L in lines), 2))


def _invoices_in_claim(vr, con):
    return {L["invoice"] for L in vr.invoice_lines(con, "Acme SIA", "Belgium", "2026-Q1")}


# --------------------------------------------------------------------------- core
def test_unmatched_line_then_resolved_by_override(tmp_path, monkeypatch):
    """A supplier with 2 registered invoices and a non-matching note -> UNMATCHED; an
    override to a registered ref resolves the line. The claim TOTAL net/VAT is IDENTICAL
    before vs after (amounts never move), and the audit row records the actor."""
    import audit
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _register_supplier(sm, "AMB", "Ambiguous SA",
                       invoices=[("R1", "2026-01-05"), ("R2", "2026-01-20")])
    _txn(vr, "AMB", "no-such-note", 800.0)

    con = vr.connect()
    # before: the line is UNMATCHED and unmatched_lines surfaces the exact note
    assert "UNMATCHED" in _invoices_in_claim(vr, con)
    uls = vr.unmatched_lines("Acme SIA", "Belgium", "2026-Q1")
    assert [(u["supplier"], u["note"]) for u in uls] == [("AMB", "no-such-note")]
    total_before = _totals(vr, con)

    # resolve: map the note to registered invoice R2
    assert vr.set_note_override("AMB", "Belgium", "no-such-note", "R2", "admin_alice") is True

    after = _invoices_in_claim(vr, con)
    assert "UNMATCHED" not in after and "R2" in after
    assert vr.unmatched_lines("Acme SIA", "Belgium", "2026-Q1") == []   # nothing left
    # the override changed ONLY the bucketing -> the claim total is identical
    assert _totals(vr, con) == total_before

    # the write is audited to the acting admin
    rows = audit.history(con, table="note_invoice_overrides", action="INSERT")
    assert rows and any(r["changed_by"] == "admin_alice" for r in rows)
    con.close()


def test_override_rejects_unregistered_and_synthetic_refs(tmp_path, monkeypatch):
    """set_note_override raises ValueError (no row written) for a ref that is NOT a
    registered invoice and for a synthetic (INPUT/ALL:/UNMATCHED) ref."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _register_supplier(sm, "AMB", "Ambiguous SA",
                       invoices=[("R1", "2026-01-05"), ("R2", "2026-01-20")])
    _txn(vr, "AMB", "no-such-note", 800.0)

    for bad in ("R9-NOT-REGISTERED", "UNMATCHED", "ALL: Belgium", "INPUT: Belgium invoice", ""):
        with pytest.raises(ValueError):
            vr.set_note_override("AMB", "Belgium", "no-such-note", bad, "admin")

    con = vr.connect()
    assert con.execute("SELECT COUNT(*) FROM note_invoice_overrides").fetchone()[0] == 0
    # the line is still UNMATCHED (no override injected anything)
    assert "UNMATCHED" in _invoices_in_claim(vr, con)
    con.close()


def test_stale_override_is_dropped_at_read_time(tmp_path, monkeypatch):
    """An override to ref R2 that LATER stops being registered is silently dropped by
    get_note_overrides (re-validation) and the line falls back to UNMATCHED -- the stale
    ref is never injected into the claim."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    # 3 registered invoices, so removing the override target still leaves >=2 (the
    # UNMATCHED fallback) rather than collapsing to the sole-registered fallback.
    _register_supplier(sm, "AMB", "Ambiguous SA",
                       invoices=[("R1", "2026-01-05"), ("R2", "2026-01-20"),
                                 ("R3", "2026-01-25")])
    _txn(vr, "AMB", "no-such-note", 800.0)

    assert vr.set_note_override("AMB", "Belgium", "no-such-note", "R2", "admin")
    con = vr.connect()
    assert "R2" in _invoices_in_claim(vr, con)              # resolves while registered
    assert vr.get_note_overrides("AMB", "Belgium") == {"no-such-note": "R2"}

    # now R2 is de-registered (e.g. corrected) -> the override is stale
    _deregister_invoice(sm, "AMB", "R2")
    assert vr.get_note_overrides("AMB", "Belgium") == {}    # dropped, not raised
    # the row still physically exists, but the read path ignores it
    assert con.execute("SELECT COUNT(*) FROM note_invoice_overrides").fetchone()[0] == 1
    # the line falls back to UNMATCHED (R2 NOT injected; R1+R3 remain -> not sole-fallback)
    invs = _invoices_in_claim(vr, con)
    assert "R2" not in invs and "UNMATCHED" in invs
    con.close()


def test_clear_override_restores_unmatched(tmp_path, monkeypatch):
    """clear_note_override removes the mapping (audited) and the line tags UNMATCHED again."""
    import audit
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _register_supplier(sm, "AMB", "Ambiguous SA",
                       invoices=[("R1", "2026-01-05"), ("R2", "2026-01-20")])
    _txn(vr, "AMB", "no-such-note", 800.0)

    vr.set_note_override("AMB", "Belgium", "no-such-note", "R1", "admin")
    con = vr.connect()
    assert "R1" in _invoices_in_claim(vr, con)
    ok, _ = vr.clear_note_override("AMB", "Belgium", "no-such-note", "admin_carol")
    assert ok
    assert con.execute("SELECT COUNT(*) FROM note_invoice_overrides").fetchone()[0] == 0
    assert "UNMATCHED" in _invoices_in_claim(vr, con)
    rows = audit.history(con, table="note_invoice_overrides", action="DELETE")
    assert rows and any(r["changed_by"] == "admin_carol" for r in rows)
    con.close()


# ----------------------------------------------------------------- regression / safety
def test_invoice_lines_unchanged_with_no_overrides(tmp_path, monkeypatch):
    """No override present -> the override consult is a no-op: a genuine note-match still
    resolves to its invoice, and a non-matching multi-invoice note is still UNMATCHED."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _register_supplier(sm, "BP", "B2Mobility",
                       invoices=[("INV1", "2026-01-10"), ("INV2", "2026-01-12")])
    _txn(vr, "BP", "INV1", 1000.0)        # genuine note-match -> INV1
    _txn(vr, "AMB-X", "no-match", 50.0)   # supplier with no invoices, note no-match
    _register_supplier(sm, "AMB-X", "Ambiguous X",
                       invoices=[("Z1", "2026-01-01"), ("Z2", "2026-01-02")])

    con = vr.connect()
    invs = _invoices_in_claim(vr, con)
    assert "INV1" in invs            # genuine match preserved
    assert "UNMATCHED" in invs       # multi-invoice non-match still UNMATCHED
    assert vr.get_note_overrides("BP", "Belgium") == {}
    con.close()


def test_override_never_displaces_a_genuine_note_match(tmp_path, monkeypatch):
    """The consult is `inv or override`: even with an override row present for a note that
    DOES note-match a registered invoice, the genuine match wins (the override only fires
    on the UNMATCHED fallback)."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _register_supplier(sm, "BP", "B2Mobility",
                       invoices=[("INV1", "2026-01-10"), ("INV2", "2026-01-12")])
    _txn(vr, "BP", "INV1", 1000.0)        # note 'INV1' genuinely matches INV1

    # an admin (perversely) sets an override for note 'INV1' -> INV2
    vr.set_note_override("BP", "Belgium", "INV1", "INV2", "admin")
    con = vr.connect()
    invs = _invoices_in_claim(vr, con)
    assert "INV1" in invs and "INV2" not in invs     # genuine match still wins
    con.close()


# ------------------------------------------------------------------------------- web
def test_web_admin_resolves_and_non_admin_blocked(admin_session, tmp_path, monkeypatch):
    """An admin POST to /vat/unmatched resolves a note (persisted); a non-admin is 403;
    a bad ref shows a red banner (no 500).

    `admin_session` ensures an admin exists in security.db so `_needs_setup()` is False
    and `_guard` reaches the role check (otherwise the setup gate 302-redirects first)."""
    import app as appmod
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    # point the app's vat_refund/master modules at the temp DBs
    monkeypatch.setattr(appmod, "_vr", vr, raising=False)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _register_supplier(sm, "AMB", "Ambiguous SA",
                       invoices=[("R1", "2026-01-05"), ("R2", "2026-01-20")])
    _txn(vr, "AMB", "no-such-note", 800.0)

    appmod.app.config["WTF_CSRF_ENABLED"] = False
    client = appmod.app.test_client()

    def _login(role):
        with client.session_transaction() as s:
            s["user"] = "tester"; s["role"] = role
            s["_csrf"] = "tok"

    # non-admin POST -> 403, nothing written
    _login("processor")
    r = client.post("/vat/unmatched", data={"_csrf": "tok", "__act": "set",
                    "entity": "Acme SIA", "country": "Belgium", "supplier": "AMB",
                    "note": "no-such-note", "invoice_ref": "R2"})
    assert r.status_code == 403
    assert vr.get_note_overrides("AMB", "Belgium") == {}

    # admin POST with a GOOD ref -> resolves, persists, success banner
    _login("admin")
    r = client.post("/vat/unmatched", data={"_csrf": "tok", "__act": "set",
                    "entity": "Acme SIA", "country": "Belgium", "supplier": "AMB",
                    "note": "no-such-note", "invoice_ref": "R2"})
    assert r.status_code == 200
    assert vr.get_note_overrides("AMB", "Belgium") == {"no-such-note": "R2"}

    # admin POST with a BAD ref -> red banner, NO 500, no new/changed row injected
    r = client.post("/vat/unmatched", data={"_csrf": "tok", "__act": "set",
                    "entity": "Acme SIA", "country": "Belgium", "supplier": "AMB",
                    "note": "another-note", "invoice_ref": "DOES-NOT-EXIST"})
    assert r.status_code == 200
    assert b"not a registered invoice" in r.data
    assert "another-note" not in vr.get_note_overrides("AMB", "Belgium")

"""
Decoupling D4 — supplier-statement REGISTRATION runs OFF the web request.

The /extract/confirm screen still validates synchronously (the operator sees errors
immediately) and still vaults the PDFs (vat_claims.db, app-owned), but the WRITE to
suppliers.db is ENQUEUED on the durable intake queue (kind='register') and performed by
the engine worker. The confirming user is propagated as the audit actor so the
suppliers.db audit triggers record changed_by=<user>, not 'system'.

These tests run the worker body IN-PROCESS (no live worker thread) against temp DBs.
"""
import importlib
import os

import pytest


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """Point every product DB the registration path touches at throwaway temp files,
    and reset the per-process schema caches so the temp DBs get a fresh schema."""
    import waiting_room
    importlib.reload(waiting_room)
    monkeypatch.setattr(waiting_room, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(waiting_room, "INBOX", str(tmp_path / "inbox"))
    waiting_room._SCHEMA_READY.clear()

    import supplier_master, customer_master, import_log
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "suppliers.db"))
    supplier_master._SCHEMA_READY.clear()
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "customers.db"))
    customer_master._SCHEMA_READY.clear()
    monkeypatch.setattr(import_log, "DB", str(tmp_path / "import_log.db"))
    import_log._READY.clear()
    return waiting_room


def _payload(supplier="DKV", ref="S-ENGINE-1", customer="OUR ENTITY"):
    # lines: (invoice_no, invoice_date, country, currency, net, vat) — a VAT-bearing
    # line is auto-synced into supplier_invoices (an AUDITED table).
    return {
        "supplier": supplier, "statement_ref": ref, "period": "2026-05",
        "statement_date": "2026-05-31",
        "lines": [["BE001", "2026-05-31", "Belgium", "EUR", 1000.0, 210.0]],
        "customer": customer, "notes": "imported via batch extraction", "draft": "tok-1",
    }


# ---------------------------------------------------------------------------
# (1) the worker writes the rows AND records the confirming user as audit actor
# ---------------------------------------------------------------------------
def test_worker_registers_with_confirming_user_as_audit_actor(engine):
    import supplier_master
    jid, st = engine.enqueue_registration(_payload(), user="alice")
    assert st == "queued"

    # run the worker body directly (no thread) — it dispatches on kind='register'
    assert engine.process_one() == (jid, "done")
    assert engine.process_one() is None              # queue idle
    assert engine.get_job(jid)["status"] == "done"

    con = supplier_master.connect()
    stmt = con.execute("SELECT * FROM supplier_statements WHERE statement_ref=?",
                       ("S-ENGINE-1",)).fetchone()
    assert stmt is not None and stmt["supplier"] == "DKV"
    line = con.execute("""SELECT * FROM statement_invoices WHERE statement_ref=?
                          AND invoice_no=?""", ("S-ENGINE-1", "BE001")).fetchone()
    assert line is not None and line["vat"] == 210.0

    # the VAT-bearing line was auto-synced into supplier_invoices (audited), and the
    # audit log attributes the write to the confirming user — NOT 'system'.
    by = [r["changed_by"] for r in con.execute(
        "SELECT changed_by FROM audit_log WHERE tbl='supplier_invoices'")]
    con.close()
    assert by, "expected an audit row for the auto-synced supplier_invoices write"
    assert all(b == "alice" for b in by), f"audit actor not propagated: {by}"


# ---------------------------------------------------------------------------
# (2) the confirm route ENQUEUES and does NOT write suppliers.db synchronously
# ---------------------------------------------------------------------------
def _csrf(client, path="/extract"):
    import re
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def test_confirm_route_enqueues_and_does_not_write_synchronously(client, engine, monkeypatch):
    import validate as VAL
    import supplier_master, app as A
    monkeypatch.setattr(VAL, "DB", str(os.path.dirname(engine.DB) + "/fuel_history.db"))

    # the confirm route must NOT call register_statement directly anymore — make any
    # in-request suppliers.db write blow up the test if it happens.
    import invoice_control as IC
    def _boom(*a, **k):
        raise AssertionError("register_statement was called synchronously in-request")
    monkeypatch.setattr(IC, "register_statement", _boom)

    form = {
        "_csrf": _csrf(client), "token": "no-such-token", "nlines": "1",
        "supplier": "DKV", "period": "2026-05", "stmt_ref": "S-WEB-1",
        "stmt_date": "2026-05-31", "customer": "OUR ENTITY",
        "inv_0": "BE001", "date_0": "2026-05-31", "ctry_0": "Belgium",
        "ccy_0": "EUR", "net_0": "1000", "vat_0": "210",
    }
    r = client.post("/extract/confirm", data=form)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "queued for registration" in body

    # a registration job is now sitting in the queue, unprocessed
    jobs = engine.jobs()
    reg = [j for j in jobs if j.get("kind") == engine.KIND_REGISTER]
    assert len(reg) == 1 and reg[0]["status"] == "queued"

    # and NOTHING was written to suppliers.db synchronously (no statement rows yet)
    con = supplier_master.connect()
    n = con.execute("SELECT COUNT(*) FROM supplier_statements WHERE statement_ref=?",
                    ("S-WEB-1",)).fetchone()[0]
    con.close()
    assert n == 0, "confirm route wrote suppliers.db in-request (should be deferred)"


# ---------------------------------------------------------------------------
# (3) a validation-failed draft is blocked synchronously — NO job queued
# ---------------------------------------------------------------------------
def test_confirm_route_blocks_invalid_draft_without_queueing(client, engine, monkeypatch):
    import validate as VAL
    # force the validation gate to refuse the commit
    monkeypatch.setattr(VAL, "validate_batch", lambda lines, coversheet_total=None: {
        "can_commit": False, "errors": 1, "warnings": 0,
        "lines": [{"line": {"invoice_no": "BE001", "country": "Belgium",
                            "net": 1000.0, "vat": 210.0},
                   "verdict": "error", "messages": ["bad VAT"]}]})

    form = {
        "_csrf": _csrf(client), "token": "no-such-token", "nlines": "1",
        "supplier": "DKV", "period": "2026-05", "stmt_ref": "S-BAD-1",
        "stmt_date": "2026-05-31", "customer": "OUR ENTITY",
        "inv_0": "BE001", "date_0": "2026-05-31", "ctry_0": "Belgium",
        "ccy_0": "EUR", "net_0": "1000", "vat_0": "210",
    }
    r = client.post("/extract/confirm", data=form)
    assert r.status_code == 200
    assert "Commit blocked" in r.get_data(as_text=True)
    # the synchronous gate refused — nothing was enqueued
    assert [j for j in engine.jobs() if j.get("kind") == engine.KIND_REGISTER] == []


# ---------------------------------------------------------------------------
# (4) re-running the worker on the same job is idempotent (no duplicate statement)
# ---------------------------------------------------------------------------
def test_reprocessing_is_idempotent(engine):
    import supplier_master
    jid, _ = engine.enqueue_registration(_payload(ref="S-IDEMP"), user="bob")
    assert engine.process_one() == (jid, "done")

    # the queue is at-least-once: force the SAME job back to 'queued' and re-run it.
    assert engine.requeue(jid) is True
    assert engine.process_one() == (jid, "done")

    con = supplier_master.connect()
    n_stmt = con.execute("SELECT COUNT(*) FROM supplier_statements WHERE statement_ref=?",
                         ("S-IDEMP",)).fetchone()[0]
    n_line = con.execute("""SELECT COUNT(*) FROM statement_invoices WHERE statement_ref=?
                            AND invoice_no=?""", ("S-IDEMP", "BE001")).fetchone()[0]
    n_inv = con.execute("SELECT COUNT(*) FROM supplier_invoices WHERE invoice_no=?",
                        ("BE001",)).fetchone()[0]
    con.close()
    assert n_stmt == 1, f"duplicate statement rows: {n_stmt}"
    assert n_line == 1, f"duplicate statement_invoices rows: {n_line}"
    assert n_inv == 1, f"duplicate supplier_invoices rows: {n_inv}"

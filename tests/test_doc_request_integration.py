"""WO4 — the document-request register wired into the OTHER modules.

Cross-module integration of customer_master.document_requests, verifying the COMPLIANCE
invariant that this WO only ANNOTATES (label) and SURFACES (worklist/notify) — it never
adds a second VAT gate and never auto-activates a refund country:

  * a RECEIVED power-of-attorney request vaults the signed original under the kind the
    country checklist recognises, so country_doc_checklist / country_ready_to_activate
    see it satisfied — WITHOUT auto-activating (country_active stays False).
  * vat_refund.submission_checklist ANNOTATES the PoA item's LABEL with an open request's
    status, but the boolean `ok` (and therefore the gate truth) is byte-for-byte
    identical with or without the open request.
  * the VAT submission gate (set_status_code(2)) is blocked identically on
    country_active=False whether or not a document_request exists — no double-gate.
"""
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
    monkeypatch.setattr(vat_refund, "stream_invoices", lambda *a, **k: [("BP", "INV1")])
    monkeypatch.setattr(vat_refund, "docs_index", lambda con: {("Acme SIA", "BP", "INV1")})
    return customer_master, supplier_master, vat_refund


def _seed_claim_data(vr):
    """Register supplier BP + invoice INV1 and seed the analytics transaction so the
    receipt-control + threshold gates PASS — leaving country_active as the sole blocker.
    Mirrors tests/test_claim_status._invoice_doc."""
    import supplier_master, sqlite3
    vc = vr.connect()
    vc.execute("""INSERT INTO invoice_documents (entity, supplier, invoice_ref, filename, sha256)
                  VALUES ('Acme SIA','BP','INV1','i.pdf','abc123')""")
    vc.commit(); vc.close()
    sc = supplier_master.connect()
    sc.execute("INSERT INTO suppliers (code, legal_name) VALUES ('BP','B2Mobility GmbH')")
    sc.execute("""INSERT INTO supplier_vat_registrations (supplier, country, vat_number, source)
                  VALUES ('BP','Belgium','BE0123456789','registry')""")
    sc.execute("""INSERT INTO supplier_invoices (supplier, country, invoice_no, invoice_date)
                  VALUES ('BP','Belgium','INV1','2026-01-10')""")
    sc.commit(); sc.close()
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
    ac.commit(); ac.close()


def _complete_customer_checklist(cm):
    """Everything the checklist needs EXCEPT the per-country PoA doc (left to the test)."""
    con = cm.connect()
    if not con.execute("SELECT 1 FROM customers WHERE code='ACME'").fetchone():
        con.close(); cm.add_customer("ACME", "Acme SIA", "LV"); con = cm.connect()
    con.execute("""UPDATE customers SET reg_number='LV123', vat_number='LV456',
                   legal_address='Riga 1', nace_code='49.41' WHERE code='ACME'"""); con.commit()
    cm.add_document(con, "ACME", "signed_contract", "c.pdf", b"C")
    cm.add_document(con, "ACME", "trade_registry", "t.pdf", b"T")
    con.execute("INSERT INTO customer_bank_accounts (customer, iban) VALUES ('ACME','LV99IBAN')")
    con.commit()
    con.close()


# ---------------------------------------------------------------- D1: received PoA
def test_received_poa_satisfies_country_checklist_without_activating(tmp_path, monkeypatch):
    """A received PoA request must vault the signed original under the EXACT kind the
    country checklist looks for ('power_of_attorney'), so country_doc_checklist sees it
    and country_ready_to_activate becomes True — but country_active stays False (no
    auto-activation; only an explicit activate_country flips it)."""
    cm, _sm, _vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    con = cm.connect()

    # before: the country has no PoA on file -> not ready, not active
    _items, ready = cm.country_doc_checklist(con, "ACME", "Belgium")
    assert ready is False
    assert cm.country_ready_to_activate(con, "ACME", "Belgium") is False

    # open a PoA request for Belgium, drive it through to RECEIVED with a signed original
    rid = cm.create_document_request(con, "ACME", "power_of_attorney", None, country="Belgium")
    cm.advance_document_request(con, rid, "cancelled")        # not this one
    rid = cm.create_document_request(con, "ACME", "power_of_attorney", None, country="Belgium")
    for s in ("generated", "sent_for_signature", "signed"):
        # no template_id -> jump statuses directly (generate requires a template)
        con.execute("UPDATE document_requests SET status=? WHERE id=?", (s, rid)); con.commit()
    ok, _ = cm.advance_document_request(con, rid, "received",
                                        signed_file=b"WET-SIGNED-POA",
                                        signed_filename="acme_be_poa.pdf")
    assert ok

    # KIND ALIGNMENT: the signed PoA was vaulted under kind 'power_of_attorney' for Belgium,
    # exactly the kind required_docs_for_country expects -> the checklist now sees it.
    items, ready = cm.country_doc_checklist(con, "ACME", "Belgium")
    assert ready is True
    assert any(ok for _label, ok in items)
    assert cm.country_ready_to_activate(con, "ACME", "Belgium") is True

    # NO auto-activation: country is still not active until an admin clicks activate.
    assert cm.country_active("ACME", "Belgium") is None or \
           cm.country_active("ACME", "Belgium") is False
    cm.activate_country(con, "ACME", "Belgium", True)         # the explicit admin click
    assert cm.country_active("ACME", "Belgium") is True
    con.close()


# ---------------------------------------------------------------- D2: gate unchanged
def test_vat_country_gate_identical_with_or_without_open_request(tmp_path, monkeypatch):
    """The canonical VAT activation gate is set_status(..., gate_activation=True) at
    vat_refund.py:595 (country_active is False -> a LOCKING transition is refused). WO4
    must NOT touch it: assert the gate boolean + message are byte-for-byte identical
    whether or not an open document_request exists for the (entity, country)."""
    cm, _sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_customer_checklist(cm)
    _seed_claim_data(vr)

    con = cm.connect()
    cm.add_country_document(con, "ACME", "Belgium", "power_of_attorney", "poa.pdf", b"P")
    cm.request_country(con, "ACME", "Belgium")          # started, not finished -> False
    con.close()
    assert cm.country_active("ACME", "Belgium") is False

    # WITHOUT any open document request: the activation gate refuses the locking move.
    vcon = vr.connect()
    ok_no_req, msg_no_req = vr.set_status(vcon, "Acme SIA", "Belgium", "2026-Q1",
                                          "submitted", gate_activation=True)
    vcon.close()
    assert ok_no_req is False and "not activated" in msg_no_req

    # open a PoA request (sent_for_signature) for the SAME (entity, country).
    ccon = cm.connect()
    rid = cm.create_document_request(ccon, "ACME", "power_of_attorney", None, country="Belgium")
    ccon.execute("UPDATE document_requests SET status='sent_for_signature' WHERE id=?", (rid,))
    ccon.commit(); ccon.close()

    # WITH the open request the gate is byte-for-byte identical — no double-gate, the open
    # request does not change the activation truth (only country_active does).
    vcon = vr.connect()
    ok_req, msg_req = vr.set_status(vcon, "Acme SIA", "Belgium", "2026-Q1",
                                    "submitted", gate_activation=True)
    vcon.close()
    assert (ok_req, msg_req) == (ok_no_req, msg_no_req)

    # And once an admin EXPLICITLY activates BOTH the customer and the country, the
    # activation gate opens — proving the open request never auto-activated it (the
    # explicit activation is what flips the gate, not WO4's annotation/surfacing).
    acon = cm.connect()
    cm.set_activation(acon, "ACME", True)
    cm.activate_country(acon, "ACME", "Belgium", True)
    acon.close()
    assert cm.country_active("ACME", "Belgium") is True
    vcon = vr.connect()
    ok_active, _ = vr.set_status(vcon, "Acme SIA", "Belgium", "2026-Q1",
                                 "submitted", gate_activation=True)
    vcon.close()
    assert ok_active is True


def test_submission_checklist_ok_booleans_unchanged_by_open_request(tmp_path, monkeypatch):
    """submission_checklist may ANNOTATE the PoA label when a request is open, but the
    boolean `ok` of EVERY item (the gate truth feeding derive_stage) is identical with or
    without the open request."""
    cm, _sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_customer_checklist(cm)
    _seed_claim_data(vr)
    con = cm.connect()
    cm.add_country_document(con, "ACME", "Belgium", "power_of_attorney", "poa.pdf", b"P")
    con.close()

    vcon = vr.connect()
    base_oks = [ok for _l, ok in vr.submission_checklist(vcon, "Acme SIA", "Belgium", "2026-Q1")]
    vcon.close()

    ccon = cm.connect()
    rid = cm.create_document_request(ccon, "ACME", "power_of_attorney", None, country="Belgium")
    ccon.execute("UPDATE document_requests SET status='sent_for_signature' WHERE id=?", (rid,))
    ccon.commit(); ccon.close()

    vcon = vr.connect()
    req_oks = [ok for _l, ok in vr.submission_checklist(vcon, "Acme SIA", "Belgium", "2026-Q1")]
    vcon.close()
    assert req_oks == base_oks


def test_open_request_annotates_poa_label_only(tmp_path, monkeypatch):
    """When an open PoA request exists AND the PoA checklist item is NOT-ok, its LABEL is
    enriched with the request status — but no item's `ok` changes. With the doc absent
    the PoA item is not-ok, so the annotation is observable."""
    cm, _sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_customer_checklist(cm)        # everything but the per-country PoA doc

    ccon = cm.connect()
    rid = cm.create_document_request(ccon, "ACME", "power_of_attorney", None, country="Belgium")
    ccon.execute("UPDATE document_requests SET status='sent_for_signature' WHERE id=?", (rid,))
    ccon.commit(); ccon.close()

    vcon = vr.connect()
    items = vr.submission_checklist(vcon, "Acme SIA", "Belgium", "2026-Q1")
    vcon.close()
    poa = [(l, ok) for l, ok in items if l.startswith("Power of attorney")]
    assert poa, "the PoA checklist item should be present"
    label, ok = poa[0]
    assert ok is False                                  # the doc is genuinely missing
    assert "sent for signature" in label                # the LABEL was annotated


def test_annotation_degrades_gracefully_when_request_read_fails(tmp_path, monkeypatch):
    """A failure to read the open request must NOT break the checklist — it falls back to
    the plain label and the gate booleans are untouched."""
    cm, _sm, vr = _modules(tmp_path, monkeypatch)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_customer_checklist(cm)

    def boom(*a, **k):
        raise RuntimeError("customers.db unreadable")
    monkeypatch.setattr(cm, "list_document_requests", boom)

    vcon = vr.connect()
    items = vr.submission_checklist(vcon, "Acme SIA", "Belgium", "2026-Q1")
    vcon.close()
    poa = [(l, ok) for l, ok in items if l.startswith("Power of attorney")]
    assert poa and poa[0][0] == "Power of attorney"     # plain label, no annotation/crash

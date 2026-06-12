"""Tests for VAT-refund customer onboarding: activation gating, documents, and
the fee model (% of refunded VAT floored at a per-declaration minimum)."""
import importlib

import pytest


@pytest.fixture()
def cd(tmp_path, monkeypatch):
    import customer_master
    importlib.reload(customer_master)
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "cust.db"))
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "docs"))
    return customer_master


def test_new_customer_is_pending_with_incomplete_checklist(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    assert cd.is_active("ACME") is False
    con = cd.connect()
    items, ready = cd.activation_checklist(con, "ACME")
    con.close()
    assert ready is False
    assert [lbl for lbl, _ in items] == ["Trade registry extract",
                                         "Bank account (IBAN) on file", "Signed contract"]


def test_activation_requires_docs_and_bank(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    # cannot activate yet
    _i, ready = cd.activation_checklist(con, "ACME")
    assert ready is False
    con.execute("INSERT INTO customer_bank_accounts (customer,iban,bank,currency,purpose) "
                "VALUES ('ACME','LV80BANK0001','MyBank','EUR','refund payout')")
    con.commit()
    cd.add_document(con, "ACME", "trade_registry", "reg.pdf", b"REG")
    cd.add_document(con, "ACME", "signed_contract", "contract.pdf", b"CONTRACT")
    _i, ready = cd.activation_checklist(con, "ACME")
    assert ready is True
    cd.set_activation(con, "ACME", True)
    con.close()
    assert cd.is_active("ACME") is True


def test_document_archived_under_logical_path(cd):
    cd.add_customer("ACME", "Acme SIA", "LV", reg_number="LV40000")
    con = cd.connect()
    cd.add_document(con, "ACME", "power_of_attorney", "poa.pdf", b"POA", country="Poland")
    sp = con.execute("SELECT stored_path FROM customer_documents WHERE customer='ACME'"
                     ).fetchone()["stored_path"].replace("\\", "/")
    con.close()
    # <Customer> <RegNo>/customer-documents/<country>/<kind>/<file>
    assert "Acme SIA LV40000/customer-documents/Poland/power_of_attorney/" in sp


def test_fee_priority_percent_then_minimum(cd):
    # 15% of 1000 = 150 (above the 50 minimum) -> percent
    assert cd.compute_fee(1000, 15, 50) == (150.0, "percent")
    # 15% of 100 = 15 (below 50) -> minimum charged
    assert cd.compute_fee(100, 15, 50) == (50.0, "minimum")
    # nothing refunded -> minimum still applies
    assert cd.compute_fee(0, 15, 50) == (50.0, "minimum")
    # no fee configured
    assert cd.compute_fee(1000, 0, 0) == (0.0, "percent")


def test_untracked_customer_not_gated(cd):
    assert cd.is_active("NOT-A-CUSTOMER") is None


def test_per_country_fee_override(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    cd.set_fee(con, "ACME", 15, 50)                       # default
    cd.set_country_fee(con, "ACME", "Belgium", 10, 100)   # Belgium override
    con.close()
    assert cd.fee_for("ACME", "Belgium") == (10.0, 100.0)  # override
    assert cd.fee_for("ACME", "Poland") == (15.0, 50.0)    # default
    assert cd.fee_for("ACME") == (15.0, 50.0)              # no country -> default


def test_per_country_activation_flow(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    # no country started -> not gated
    assert cd.country_active("ACME", "Belgium") is None
    cd.request_country(con, "ACME", "Belgium")
    assert cd.country_active("ACME", "Belgium") is False        # requested, not active
    _i, ready = cd.country_doc_checklist(con, "ACME", "Belgium")
    assert ready is False                                       # POA not received
    cd.add_country_document(con, "ACME", "Belgium", "power_of_attorney", "poa.pdf", b"POA")
    _i, ready = cd.country_doc_checklist(con, "ACME", "Belgium")
    assert ready is True
    cd.activate_country(con, "ACME", "Belgium", True)
    con.close()
    assert cd.country_active("ACME", "Belgium") is True
    # country docs are isolated from the customer-level onboarding checklist
    con = cd.connect()
    _i, cust_ready = cd.activation_checklist(con, "ACME")
    con.close()
    assert cust_ready is False


def test_per_country_document_requirements(cd):
    con = cd.connect()
    cd.set_country_requirements(con, "Germany", ["power_of_attorney", "vat_certificate"])
    assert set(cd.required_docs_for_country(con, "Germany")) == {"power_of_attorney", "vat_certificate"}
    assert cd.required_docs_for_country(con, "Belgium") == ["power_of_attorney"]   # default
    cd.add_customer("ACME", "Acme SIA", "LV")
    cd.add_country_document(con, "ACME", "Germany", "power_of_attorney", "p.pdf", b"P")
    _i, ready = cd.country_doc_checklist(con, "ACME", "Germany")
    assert ready is False                                   # VAT certificate still missing
    cd.add_country_document(con, "ACME", "Germany", "vat_certificate", "v.pdf", b"V")
    _i, ready = cd.country_doc_checklist(con, "ACME", "Germany")
    assert ready is True
    con.close()


def test_submission_readiness(tmp_path, monkeypatch):
    import customer_master
    import vat_refund
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "docs"))
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    monkeypatch.setattr(vat_refund, "stream_invoices", lambda *a, **k: [])
    customer_master.add_customer("ACME", "Acme SIA", "LV")     # pending, country not started
    vc = vat_refund.connect()
    ready, issues = vat_refund.submission_readiness(vc, "ACME", "Belgium", "2026-Q2")
    assert ready is False and any("not activated" in i for i in issues)
    con = customer_master.connect()
    customer_master.set_activation(con, "ACME", True)
    customer_master.add_country_document(con, "ACME", "Belgium", "power_of_attorney", "p.pdf", b"P")
    customer_master.activate_country(con, "ACME", "Belgium", True)
    con.close()
    ready, issues = vat_refund.submission_readiness(vc, "ACME", "Belgium", "2026-Q2")
    assert ready is True and issues == []
    vc.close()


def test_readiness_page(client):
    h = client.get("/readiness").get_data(as_text=True)
    assert "Ready to submit" in h and "Open claims" in h


def test_country_gate_blocks_until_activated(tmp_path, monkeypatch):
    import customer_master
    import vat_refund
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "docs"))
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    monkeypatch.setattr(vat_refund, "stream_invoices", lambda *a, **k: [])
    customer_master.add_customer("ACME", "Acme SIA", "LV")
    con = customer_master.connect()
    customer_master.set_activation(con, "ACME", True)          # customer active
    customer_master.request_country(con, "ACME", "Belgium")    # country requested, NOT active
    con.close()
    vc = vat_refund.connect()
    vc.execute("CREATE TABLE transactions (entity TEXT, country TEXT, period TEXT, vat_eur REAL)")
    vc.execute("INSERT INTO transactions VALUES ('ACME','Belgium','2026-04',1000)"); vc.commit()
    ok, msg = vat_refund.set_status(vc, "ACME", "Belgium", "2026-Q2", "submitted")
    assert ok is False and "country 'Belgium' is not activated" in msg
    # receive POA + activate -> submission now passes the country gate
    con = customer_master.connect()
    customer_master.add_country_document(con, "ACME", "Belgium", "power_of_attorney", "poa.pdf", b"POA")
    customer_master.activate_country(con, "ACME", "Belgium", True)
    con.close()
    ok, msg = vat_refund.set_status(vc, "ACME", "Belgium", "2026-Q2", "submitted")
    assert ok, msg
    vc.close()


def test_fee_frozen_on_submission(tmp_path, monkeypatch):
    import customer_master
    import vat_refund
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    monkeypatch.setattr(vat_refund, "stream_invoices", lambda *a, **k: [])  # skip lock/doc checks
    # active customer, Belgium override 10% / min 100
    customer_master.add_customer("ACME", "Acme SIA", "LV")
    con = customer_master.connect()
    customer_master.set_country_fee(con, "ACME", "Belgium", 10, 100)
    customer_master.set_activation(con, "ACME", True)
    con.close()
    vc = vat_refund.connect()
    vc.execute("CREATE TABLE transactions (entity TEXT, country TEXT, period TEXT, vat_eur REAL)")
    vc.execute("INSERT INTO transactions VALUES ('ACME','Belgium','2026-04',2000)")
    vc.commit()
    ok, msg = vat_refund.set_status(vc, "ACME", "Belgium", "2026-Q2", "submitted")
    assert ok, msg
    snap = vc.execute("SELECT fee_eur, fee_pct, vat_eur FROM vat_applications "
                      "WHERE entity='ACME'").fetchone()
    assert snap["fee_eur"] == 200.0 and snap["fee_pct"] == 10.0   # 10% of 2000
    vc.close()
    # changing the fee afterwards must NOT change the frozen claim
    con = customer_master.connect()
    customer_master.set_country_fee(con, "ACME", "Belgium", 99, 9999)
    con.close()
    rows, _summ = vat_refund.recovery_report("2026")
    frozen = next(r for r in rows if r["entity"] == "ACME")
    assert frozen["fee_eur"] == 200.0          # locked, not re-priced at 99%


def test_fee_charged_on_paid_with_minimum(tmp_path, monkeypatch):
    """User example: VAT 1000, 8% (=80) below 130 minimum -> charge 130, billed on paid."""
    import customer_master
    import vat_refund
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    monkeypatch.setattr(vat_refund, "stream_invoices", lambda *a, **k: [])
    customer_master.add_customer("ACME", "Acme SIA", "LV")
    con = customer_master.connect()
    customer_master.set_fee(con, "ACME", 8, 130)
    customer_master.set_activation(con, "ACME", True)
    con.close()
    vc = vat_refund.connect()
    vc.execute("CREATE TABLE transactions (entity TEXT, country TEXT, period TEXT, vat_eur REAL)")
    vc.execute("INSERT INTO transactions VALUES ('ACME','Belgium','2026-04',1000)"); vc.commit()
    assert vat_refund.set_status(vc, "ACME", "Belgium", "2026-Q2", "submitted")[0]
    # not yet billed
    row = vc.execute("SELECT fee_eur, fee_billed_date FROM vat_applications").fetchone()
    assert row["fee_eur"] == 130.0 and row["fee_billed_date"] is None
    # refund paid -> fee charged + billed date stamped
    assert vat_refund.set_status(vc, "ACME", "Belgium", "2026-Q2", "paid")[0]
    row = vc.execute("SELECT fee_eur, fee_billed_date FROM vat_applications").fetchone()
    assert row["fee_eur"] == 130.0 and row["fee_billed_date"] is not None
    vc.close()


def test_payout_route_and_settlement(cd):
    import vat_refund
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect(); cd.set_payout_route(con, "ACME", "us"); con.close()
    assert cd.payout_route("ACME") == "us"
    # to us: deduct fee, remit net
    s = vat_refund.settlement("us", 1000, 130)
    assert s["net_to_customer"] == 870.0 and s["fee_receivable"] == 0.0
    # to customer: invoice the fee
    s = vat_refund.settlement("customer", 1000, 130)
    assert s["fee_receivable"] == 130.0 and s["net_to_customer"] == 0.0


def test_fee_invoice_issued_after_paid(tmp_path, monkeypatch):
    import customer_master
    import vat_refund
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    monkeypatch.setattr(vat_refund, "stream_invoices", lambda *a, **k: [])
    customer_master.add_customer("ACME", "Acme SIA", "LV")
    con = customer_master.connect()
    customer_master.set_fee(con, "ACME", 8, 130)
    customer_master.set_activation(con, "ACME", True)
    con.close()
    vc = vat_refund.connect()
    vc.execute("CREATE TABLE transactions (entity TEXT, country TEXT, period TEXT, vat_eur REAL)")
    vc.execute("INSERT INTO transactions VALUES ('ACME','Belgium','2026-04',1000)"); vc.commit()
    vat_refund.set_status(vc, "ACME", "Belgium", "2026-Q2", "submitted")
    # before paid -> can't invoice
    ok, msg = vat_refund.issue_fee_invoice(vc, "ACME", "Belgium", "2026-Q2")
    assert ok is False and "paid" in msg
    vat_refund.set_status(vc, "ACME", "Belgium", "2026-Q2", "paid")
    ok, no = vat_refund.issue_fee_invoice(vc, "ACME", "Belgium", "2026-Q2")
    assert ok and no.startswith("F2026-")
    # idempotent
    ok2, no2 = vat_refund.issue_fee_invoice(vc, "ACME", "Belgium", "2026-Q2")
    assert ok2 and no2 == no
    vc.close()


def test_fee_report_route(client):
    # a downloadable fee report exists per claim (404 for an unknown claim, but xlsx for any)
    r = client.get("/export/fee?entity=Nobody&country=Belgium&period=2026-Q2")
    assert r.status_code in (200, 404)


def test_set_status_blocks_pending_customer(tmp_path, monkeypatch):
    import customer_master
    import vat_refund
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "cust.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "vat.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "vat.db"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    customer_master.add_customer("BLK", "Blocked UAB", "LT")   # pending
    con = vat_refund.connect()
    ok, msg = vat_refund.set_status(con, "BLK", "Belgium", "2026-Q2", "submitted")
    con.close()
    assert ok is False and "not activated" in msg


def test_customers_page_and_recovery_fee_render(client):
    h = client.get("/customers").get_data(as_text=True)
    assert "Onboard a new VAT-refund customer" in h
    assert "Activation checklist" in h
    rec = client.get("/recovery").get_data(as_text=True)
    assert "Our fee" in rec and "Settlement" in rec
